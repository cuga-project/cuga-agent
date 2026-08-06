"""One database seam for the events layer — SQLite or PostgreSQL, same code above it.

WHY THIS EXISTS
---------------
The events layer keeps its state (subscriptions, runs, users, identities, OAuth apps, agents) in
six small stores that all share ONE database. Those stores spoke ``sqlite3`` directly, which meant:

  * local dev  — a SQLite file that survives everything
  * Code Engine — a SQLite file on an EPHEMERAL container disk

Two different durability stories, and the risky one was the only one nobody exercised while
developing. A pod replacement on 2026-08-05 silently deleted a cron armed from Slack twelve minutes
earlier. The lesson is not "add a backup"; it is that **local and deployed must run the same
mechanism**, or local testing proves nothing about production.

So: point ``EVENTS_DB`` at a Postgres URL and every environment uses a real database.

    EVENTS_DB=postgresql://cuga:pw@localhost:5432/cuga_events   # dev AND prod — same engine
    EVENTS_DB=/abs/path/events.db                               # SQLite file
    EVENTS_DB=:memory:                                          # SQLite, tests

SQLite is retained deliberately for the hermetic offline suite (360 tests that must not need a
database server) and for a zero-infrastructure quickstart. It is not the deployed configuration.

WHAT THE PORT NEEDED
--------------------
Very little, because the SQL was already close to portable — ``ON CONFLICT … DO UPDATE SET …
excluded.col`` is Postgres syntax that SQLite adopted, and the schema uses only TEXT/REAL/INTEGER.
Three real differences are handled here so no store has to care:

  1. placeholders   ``?`` (SQLite) vs ``%s`` (psycopg)      → rewritten, quote-aware
  2. introspection  ``PRAGMA table_info`` vs information_schema → ``conn.columns(table)``
  3. row access     stores use BOTH ``r["col"]`` and ``r[0]``   → :class:`Row` supports both

CONCURRENCY
-----------
A single connection guarded by a lock, with every result fully materialised before the lock is
released. The stores are a thin index touched a few times a second at most, and psycopg
connections are not safe for concurrent use across threads — returning a live cursor would let a
second thread invalidate it mid-read. Materialising is simpler than a pool and removes the class
of bug entirely. Revisit if this ever becomes a throughput bottleneck; it is nowhere near.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading

log = logging.getLogger("events.db")

_PG_PREFIXES = ("postgresql://", "postgres://", "postgresql+psycopg://")

# Set once the float4→float8 repair has run for this process (see connect()).
_WIDENED = False


class Row(dict):
    """A result row addressable by name **or** position.

    Stores use both: ``r["prompt"]`` almost everywhere, ``r[0]`` in the OAuth store. ``sqlite3.Row``
    supported both; a plain dict does not, and psycopg's dict rows do not either. Subclassing dict
    keeps ``dict(r)``, ``r.keys()`` and ``**r`` working unchanged.
    """

    __slots__ = ()

    def __getitem__(self, key):
        if isinstance(key, int):
            try:
                return list(self.values())[key]
            except IndexError:
                raise IndexError(f"row has {len(self)} column(s), asked for index {key}") from None
        return super().__getitem__(key)


class Result:
    """Materialised rows. Mirrors the slice of the DB-API cursor the stores actually use."""

    __slots__ = ("_rows",)

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)


def is_postgres(dsn: str) -> bool:
    return str(dsn or "").strip().lower().startswith(_PG_PREFIXES)


def _driver_errors():
    """(IntegrityError, OperationalError) covering whichever drivers are importable.

    The stores catch these to turn a unique-index violation into DuplicateSubscription and to
    survive a legacy DB that already holds duplicates. Catching ``sqlite3.IntegrityError`` alone
    would sail straight past the Postgres equivalent and surface as an unhandled 500 the first time
    two channels raced to arm the same flow — exactly the case the handler exists for.
    """
    integrity = [sqlite3.IntegrityError]
    operational = [sqlite3.OperationalError, sqlite3.DatabaseError]
    try:
        import psycopg

        integrity.append(psycopg.errors.IntegrityError)
        operational.append(psycopg.errors.OperationalError)
        operational.append(psycopg.errors.ProgrammingError)   # PG raises this for a bad DDL
    except Exception:  # noqa: BLE001 — psycopg is optional when running on SQLite
        pass
    return tuple(integrity), tuple(operational)


IntegrityError, OperationalError = _driver_errors()


def _to_pg_placeholders(sql: str) -> str:
    """``?`` → ``%s``, ignoring anything inside single-quoted literals, and escaping literal ``%``.

    A blunt ``sql.replace("?", "%s")`` is correct for today's SQL (there are no ``?`` characters
    inside string literals) but silently corrupts the first query that adds one — e.g. a DEFAULT of
    ``'?'`` or a LIKE pattern. Cheap to do properly, and it is done once per statement.
    """
    out, in_str, i = [], False, 0
    while i < len(sql):
        c = sql[i]
        if c == "'":
            # '' inside a literal is an escaped quote, not a terminator
            if in_str and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_str = not in_str
            out.append(c)
        elif in_str:
            out.append("%%" if c == "%" else c)
        elif c == "?":
            out.append("%s")
        elif c == "%":
            out.append("%%")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _to_pg_types(sql: str) -> str:
    """``REAL`` → ``DOUBLE PRECISION`` in DDL. The single most damaging portability trap here.

    ``REAL`` means different things in the two engines: SQLite's REAL is an **8-byte** IEEE double,
    Postgres's REAL is **float4** — about 7 significant digits. Every timestamp in this schema is a
    Unix epoch (~1.79e9, ten significant digits), so on Postgres the low bits were thrown away and
    every stored instant snapped to a ~100-second grid, off by up to ±50s. Measured, not theorised:
    five instants spanning 66 seconds came back as **two** distinct values.

    What that broke, silently, only in the cloud:
      · ``next_fire``/``last_fire`` — a "1 minute" cron actually fired on a ~100s grid
      · ``now_run.ts``              — the Runs log's ordering and times were wrong by up to a minute
      · ``web_inbox.ts``            — the browser's delivery cursor is a ts and ``since`` is
                                      EXCLUSIVE, so two fires landing in one bucket meant the second
                                      was skipped forever: a dropped message, no error anywhere

    SQLite is unaffected, which is exactly why the offline suite never saw it.

    Applied to DDL only (CREATE TABLE / ALTER TABLE), word-boundary and quote-aware, so a column
    named ``real_name`` or the string ``'REAL'`` is untouched.
    """
    head = sql.lstrip()[:12].upper()
    if not (head.startswith("CREATE TABL") or head.startswith("ALTER TABLE")):
        return sql
    out, in_str, i = [], False, 0
    while i < len(sql):
        c = sql[i]
        if c == "'":
            in_str = not in_str
            out.append(c)
            i += 1
            continue
        if not in_str and (c in "rR") and sql[i:i + 4].upper() == "REAL":
            before_ok = i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")
            after = sql[i + 4:i + 5]
            after_ok = after == "" or not (after.isalnum() or after == "_")
            if before_ok and after_ok:
                out.append("DOUBLE PRECISION")
                i += 4
                continue
        out.append(c)
        i += 1
    return "".join(out)


class _Connection:
    """Shared surface: execute / commit / close / columns."""

    backend = "?"

    def __init__(self):
        self._lock = threading.RLock()

    def execute(self, sql, params=()):  # pragma: no cover - overridden
        raise NotImplementedError

    def commit(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def close(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def columns(self, table: str) -> set:
        """Column names of ``table`` — the portable replacement for ``PRAGMA table_info``.

        Returns an empty set when the table does not exist, which is what the migrate-on-open code
        wants: "no columns yet" and "table absent" both mean "add everything".
        """
        raise NotImplementedError


class _SqliteConnection(_Connection):
    backend = "sqlite"

    def __init__(self, path: str):
        super().__init__()
        # check_same_thread=False: the store is shared by an async web server whose handlers may run
        # on a threadpool (and TestClient does). Our own lock provides the serialisation.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self.path = path

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._db.execute(sql, tuple(params))
            rows = [Row(zip(r.keys(), tuple(r))) for r in cur.fetchall()] if cur.description else []
            return Result(rows)

    def commit(self):
        with self._lock:
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    def columns(self, table: str) -> set:
        return {r[1] for r in self._raw(f"PRAGMA table_info({table})")}

    def _raw(self, sql):
        with self._lock:
            cur = self._db.execute(sql)
            return [tuple(r) for r in cur.fetchall()] if cur.description else []


class _PostgresConnection(_Connection):
    backend = "postgres"

    def __init__(self, dsn: str):
        super().__init__()
        self.dsn = dsn
        self._db = None
        self._connect()
        log.info("events db: postgres @ %s", _redact(dsn))

    def _connect(self):
        import psycopg

        # autocommit=False so the stores' explicit commit() keeps meaning what it meant on SQLite.
        self._db = psycopg.connect(self.dsn, autocommit=False)

    def _dead(self) -> bool:
        return self._db is None or getattr(self._db, "closed", 0)

    def execute(self, sql, params=()):
        """Run one statement, reconnecting once if the server has dropped the connection.

        WHY THE RETRY. A managed Postgres closes idle connections (and a failover or a restart
        closes all of them). We hold ONE long-lived connection per store, so without this the first
        query after an idle period raises ``the connection is closed`` and — because nothing ever
        reconnected — that store stayed broken for the life of the process. It surfaced as the
        Studio's Agents tab returning 500 while other tabs worked, since each store has its own
        connection and they go idle at different rates. Reconnect-and-retry is deliberately limited
        to ONE attempt and only for connection-level failures: a genuine SQL error must still
        propagate on the first try rather than being run twice.
        """
        import psycopg

        with self._lock:
            for attempt in (0, 1):
                if self._dead():
                    self._connect()
                try:
                    with self._db.cursor() as cur:
                        cur.execute(_to_pg_placeholders(_to_pg_types(sql)), tuple(params))
                        if cur.description:
                            names = [d.name for d in cur.description]
                            return Result([Row(zip(names, r)) for r in cur.fetchall()])
                        return Result([])
                except (psycopg.OperationalError, psycopg.InterfaceError) as e:
                    # Connection-level: the socket is gone. Retry once on a fresh connection.
                    if attempt == 0:
                        log.warning("postgres connection lost (%s) — reconnecting", e)
                        try:
                            self._db.close()
                        except Exception:  # noqa: BLE001
                            pass
                        self._db = None
                        continue
                    raise
                except Exception:
                    # A failed statement poisons the transaction in Postgres ("current transaction
                    # is aborted") and every later query fails with a misleading error. Roll back so
                    # the real exception is the one the caller sees.
                    try:
                        self._db.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    raise

    def commit(self):
        # A commit on a dropped connection is not an error worth raising: execute() already
        # reconnected and re-ran the statement, and a fresh connection has nothing to commit.
        with self._lock:
            if self._dead():
                return
            try:
                self._db.commit()
            except Exception as e:  # noqa: BLE001
                import psycopg

                if isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError)):
                    log.warning("postgres commit on a lost connection (%s) — dropping it", e)
                    self._db = None
                    return
                raise

    def close(self):
        with self._lock:
            if not self._dead():
                self._db.close()
            self._db = None

    def columns(self, table: str) -> set:
        res = self.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,))
        return {r["column_name"] for r in res.fetchall()}

    def widen_real_columns(self) -> list[str]:
        """Repair columns an OLDER build already created as ``real`` (float4).

        ``_to_pg_types`` only fixes tables created from now on. A database that has been running has
        float4 timestamp columns already, and ``CREATE TABLE IF NOT EXISTS`` will never revisit them
        — so without this the deployed schedule stays on its ~100-second grid forever. Widening is
        safe and online (float4 → float8 is a lossless widening; Postgres rewrites the table, which
        for an index this size is milliseconds).

        It does NOT recover precision already lost: rows written as float4 keep their rounded value.
        Only new writes are exact. Returns the columns it altered, for the boot log.
        """
        fixed: list[str] = []
        try:
            rows = self.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type = 'real'").fetchall()
        except Exception as e:  # noqa: BLE001 — a permissions-limited role must not break boot
            log.warning("could not inspect column types (%s) — skipping the float4 repair", e)
            return fixed
        for r in rows:
            t, c = r["table_name"], r["column_name"]
            try:
                self.execute(f'ALTER TABLE "{t}" ALTER COLUMN "{c}" TYPE DOUBLE PRECISION')
                fixed.append(f"{t}.{c}")
            except Exception as e:  # noqa: BLE001
                log.warning("could not widen %s.%s to double precision: %s", t, c, e)
        if fixed:
            self.commit()
            log.info("events db: widened %d float4 column(s) to double precision — %s",
                     len(fixed), ", ".join(fixed))
        return fixed


def _redact(dsn: str) -> str:
    """postgresql://user:pw@host/db → postgresql://user:***@host/db (never log a password)."""
    try:
        head, sep, tail = dsn.partition("://")
        creds, at, hostpart = tail.rpartition("@")
        if not at:
            return dsn
        user = creds.split(":", 1)[0]
        return f"{head}{sep}{user}:***@{hostpart}"
    except Exception:  # noqa: BLE001
        return "<dsn>"


def _ca_file() -> str:
    """Materialise the managed database's CA certificate, if one was supplied.

    IBM Cloud Databases hand out a DSN with ``sslmode=verify-full`` and a base64 CA in the service
    credentials. verify-full without the CA fails to connect at all; the tempting "fix" is to
    downgrade to ``sslmode=require``, which still encrypts but stops verifying who is on the other
    end — a man-in-the-middle then reads every armed prompt and channel id. So we carry the CA
    instead: ``EVENTS_DB_CA_B64`` in the secret, written to a 0600 file at first connect.
    """
    b64 = (os.environ.get("EVENTS_DB_CA_B64", "") or "").strip()
    if not b64:
        return ""
    import base64
    import tempfile

    path = os.path.join(tempfile.gettempdir(), "cuga_events_db_ca.crt")
    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(b64))
            os.chmod(path, 0o600)
        return path
    except Exception as e:  # noqa: BLE001
        log.warning("could not write the DB CA certificate (%s) — connection will likely fail", e)
        return ""


def _with_ca(dsn: str) -> str:
    """Append ``sslrootcert=<path>`` when the DSN verifies TLS and we hold the CA."""
    if "sslrootcert=" in dsn or "sslmode=verify" not in dsn:
        return dsn
    ca = _ca_file()
    if not ca:
        return dsn
    return dsn + ("&" if "?" in dsn else "?") + f"sslrootcert={ca}"


def connect(dsn: str = ":memory:") -> _Connection:
    """Open the events database. A ``postgres(ql)://`` URL gets Postgres; anything else is a path."""
    dsn = (dsn or ":memory:").strip()
    if is_postgres(dsn):
        conn = _PostgresConnection(_with_ca(dsn))
        # Once per process, on the FIRST Postgres connection: repair float4 timestamp columns left
        # by an older build (see widen_real_columns). Guarded because every store opens its own
        # connection and the scan is pointless after the first — but it must run before any store
        # reads a timestamp, so connect() is the right seam.
        global _WIDENED
        if not _WIDENED:
            _WIDENED = True
            conn.widen_real_columns()
        return conn
    if dsn != ":memory:":
        d = os.path.dirname(dsn)
        if d:
            os.makedirs(d, exist_ok=True)
    return _SqliteConnection(dsn)
