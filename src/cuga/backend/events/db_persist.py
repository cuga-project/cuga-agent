"""Durable state for the events SQLite DB — snapshot to a mounted store, restore on boot.

WHY THIS EXISTS
---------------
On Code Engine the container filesystem is ephemeral. When the platform replaces the instance —
a new revision, a node drain, a reschedule — the new pod gets an EMPTY disk and every armed flow
is silently gone. This is not the crash-restart counter you see in ``ibmcloud ce app get``
(``Restarts: 0`` refers to the *current* pod); pod REPLACEMENT resets the disk with no restart
recorded. Observed 2026-08-05: a cron armed from Slack at 11:12 vanished when a new pod started
at 11:24, with the scheduler none the wiser.

WHY NOT JUST MOUNT THE VOLUME AND POINT EVENTS_DB AT IT
------------------------------------------------------
Code Engine's persistent data store is backed by a **COS bucket** (object storage, mounted via
s3fs). SQLite on object storage is a known corruption hazard: POSIX advisory locking is not
honoured, and a page-level write turns into a whole-object rewrite. So the live database stays on
LOCAL disk, where locking and durability behave correctly, and we copy a consistent snapshot to
the mounted store instead.

THE SHAPE
---------
    boot     restore()   backup → local, only when local is missing or older
    running  a debounced background loop snapshots local → backup when the file actually changes
    exit     snapshot() one last time

Snapshots use SQLite's **online backup API**, not ``shutil.copy``: it takes a read lock and
produces a consistent file even mid-write, which a naive copy does not (it can catch a torn page
or miss a rollback journal). The write to the mounted store is temp-then-replace, so a snapshot
interrupted halfway can never leave a truncated backup where a good one used to be.

SAFETY ENVELOPE
---------------
Correct for exactly ONE writer. That is what we deploy (``min-scale 1 / max-scale 1``, because the
scheduler and channel loops are process-wide singletons anyway). It is NOT a substitute for a real
database once there are multiple replicas — see events_docs/ARCHITECTURE.md for the Postgres path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import tempfile
import time

log = logging.getLogger("events.db_persist")

_SNAPSHOT_INTERVAL = 15.0  # seconds between change checks; a lost tail is at most this long


def backup_path() -> str:
    """Where the durable copy lives (a mounted data store on CE). "" disables persistence.

    Deliberately a separate variable from EVENTS_DB: the live DB must stay on local disk, so
    these two must never be the same file. We refuse that case loudly rather than corrupting.
    """
    return (os.environ.get("EVENTS_DB_BACKUP", "") or "").split(" #", 1)[0].strip()


def _usable(local: str, backup: str) -> bool:
    # A Postgres DSN is not a file. Without this guard restore()/snapshot() would hand the DSN to
    # sqlite3.connect, which happily creates a FILE named "postgresql://…" in the working directory
    # and then reports success — durability that silently protects nothing.
    from . import db as _db

    if _db.is_postgres(local):
        return False
    if not backup or local == ":memory:" or not local:
        return False
    if os.path.abspath(local) == os.path.abspath(backup):
        log.error(
            "EVENTS_DB_BACKUP == EVENTS_DB (%s) — refusing to snapshot a file onto itself; "
            "the live DB must stay on LOCAL disk and the backup on the mounted store",
            local,
        )
        return False
    return True


def restore(local: str, backup: str = "") -> bool:
    """Copy the durable snapshot into place at boot. Returns True if a restore happened.

    Only restores when the local file is absent or empty. A local file that already has content
    wins: it is the live database of a process that is merely reconnecting, and clobbering it with
    an older snapshot would *cause* the data loss this module exists to prevent.
    """
    backup = backup or backup_path()
    if not _usable(local, backup):
        return False
    if not os.path.exists(backup) or os.path.getsize(backup) == 0:
        log.info("no durable snapshot at %s yet — starting fresh", backup)
        return False
    if os.path.exists(local) and os.path.getsize(local) > 0:
        log.info("local DB %s already has data — keeping it, not restoring over it", local)
        return False
    try:
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(local)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        n = _count_subscriptions(local)
        log.info(
            "restored events DB from %s (%s bytes, %s subscription(s))",
            backup,
            os.path.getsize(local),
            n if n is not None else "?",
        )
        return True
    except Exception as e:  # noqa: BLE001 — a bad snapshot must not stop the service booting
        log.warning("could not restore from %s: %s: %s — starting fresh", backup, type(e).__name__, e)
        return False


def snapshot(local: str, backup: str = "") -> bool:
    """Write a consistent copy of the live DB to the durable store. Returns True on success."""
    backup = backup or backup_path()
    if not _usable(local, backup) or not os.path.exists(local):
        return False
    tmp = ""
    try:
        os.makedirs(os.path.dirname(backup) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(backup) or ".", suffix=".snap")
        os.close(fd)
        os.unlink(tmp)  # sqlite wants to create it itself
        src = sqlite3.connect(f"file:{local}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)  # online backup API — consistent under concurrent writes
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(tmp, backup)  # atomic swap — never a truncated backup
        tmp = ""
        return True
    except Exception as e:  # noqa: BLE001 — persistence must never take the service down
        log.warning("snapshot to %s failed: %s: %s", backup, type(e).__name__, e)
        return False
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _count_subscriptions(path: str):
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return db.execute("SELECT COUNT(*) FROM subscription").fetchone()[0]
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — diagnostics only
        return None


def _fingerprint(path: str) -> tuple:
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


async def run_snapshot_loop(local: str, backup: str = "", interval: float = _SNAPSHOT_INTERVAL) -> None:
    """Background task: snapshot whenever the live DB has actually changed.

    Change-detected rather than unconditional, because the durable store is object storage and a
    snapshot every tick would mean a constant stream of full-object writes for a database that
    changes a few times an hour.
    """
    backup = backup or backup_path()
    if not _usable(local, backup):
        return
    log.info("db snapshots ON — %s → %s every %.0fs (on change)", local, backup, interval)
    last = _fingerprint(local)
    # An initial snapshot so a DB armed before the first change is already durable.
    snapshot(local, backup)
    while True:
        try:
            await asyncio.sleep(interval)
            cur = _fingerprint(local)
            if cur != last and cur != (0, 0):
                t0 = time.time()
                if snapshot(local, backup):
                    last = cur
                    log.info(
                        "db snapshot written (%s bytes, %.0f ms)",
                        os.path.getsize(backup),
                        (time.time() - t0) * 1000,
                    )
        except asyncio.CancelledError:
            snapshot(local, backup)  # final flush on shutdown
            raise
        except Exception as e:  # noqa: BLE001 — the loop must survive a transient store outage
            log.warning("snapshot loop: %s: %s", type(e).__name__, e)


def status(local: str, backup: str = "") -> dict:
    """What the capability report / GET /api/events/status shows about durability."""
    from . import db as _db

    # Postgres is durable by construction — no snapshot, no mount, nothing to configure. Reporting
    # "durable: false" here because EVENTS_DB_BACKUP is unset would be exactly backwards.
    if _db.is_postgres(local):
        return {
            "durable": True,
            "backend": "postgres",
            "mechanism": "database",
            "note": "state lives in PostgreSQL — an instance replace is a non-event",
        }
    backup = backup or backup_path()
    if not backup:
        return {
            "durable": False,
            "backend": "sqlite",
            "reason": "SQLite with no EVENTS_DB_BACKUP — armed flows are lost when the instance "
            "is replaced. Point EVENTS_DB at a postgresql:// URL, or set a backup.",
        }
    if not _usable(local, backup):
        return {
            "durable": False,
            "backend": "sqlite",
            "reason": f"EVENTS_DB_BACKUP={backup!r} is not usable with EVENTS_DB={local!r}",
        }
    exists = os.path.exists(backup)
    return {
        "durable": True,
        "backend": "sqlite",
        "mechanism": "snapshot",
        "backup": backup,
        "snapshot_exists": exists,
        "snapshot_bytes": os.path.getsize(backup) if exists else 0,
        "subscriptions_in_snapshot": _count_subscriptions(backup) if exists else 0,
    }
