"""Persistent agent store — makes ReactRuntime agents survive restarts & be shared across
a FLEET of CUGA FastAPI replicas.

Without this, ReactRuntime keeps agents in a process dict → a second replica (or a restart)
doesn't know about a user's agents, so an AP callback could hit a replica that can't run the
target agent. With a shared store (same sqlite file / Postgres), any replica rebuilds any
(scope, agent) on demand. Keyed by ``scope`` (Principal.scope) for isolation.

SQLite or Postgres via ``db.connect``. For the ``cuga`` backend, CUGA's own ``config_store`` is the equivalent
(agents as versioned configs) — this is the lightweight store for the ``react`` backend.
"""

from __future__ import annotations

import json

try:
    from . import db as _db
except ImportError:  # flat load (tests put the events dir on sys.path)
    import db as _db

try:
    from .runtime import AgentSpec
except ImportError:  # flat load (offline tests put the events dir on sys.path)
    from runtime import AgentSpec

try:
    from . import mcp_catalog
except ImportError:  # flat load, as above
    import mcp_catalog


class AgentStore:
    def __init__(self, db_path: str = ":memory:"):
        # check_same_thread=False: shared by an async web server (handlers may run off-thread).
        self._db = _db.connect(db_path)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS agent (
                 scope TEXT NOT NULL,
                 name TEXT NOT NULL,
                 prompt TEXT NOT NULL DEFAULT '',
                 backend TEXT NOT NULL DEFAULT 'react',
                 mcp_servers TEXT NOT NULL DEFAULT '[]',
                 builtin_tools TEXT NOT NULL DEFAULT '[]',
                 channels TEXT NOT NULL DEFAULT '[]',
                 integrations TEXT NOT NULL DEFAULT '[]',
                 access TEXT NOT NULL DEFAULT '[]',
                 PRIMARY KEY (scope, name)
               )"""
        )
        # migrate: add columns older DBs predate
        cols = self._db.columns("agent")
        for col in ("integrations", "access"):
            if col not in cols:
                self._db.execute(f"ALTER TABLE agent ADD COLUMN {col} TEXT NOT NULL DEFAULT '[]'")
        self._db.commit()

    def upsert(self, scope: str, spec: AgentSpec) -> None:
        self._db.execute(
            """INSERT INTO agent (scope,name,prompt,backend,mcp_servers,builtin_tools,channels,integrations,access)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(scope,name) DO UPDATE SET
                 prompt=excluded.prompt, backend=excluded.backend,
                 mcp_servers=excluded.mcp_servers, builtin_tools=excluded.builtin_tools,
                 channels=excluded.channels, integrations=excluded.integrations,
                 access=excluded.access""",
            (
                scope,
                spec.name,
                spec.prompt,
                spec.backend,
                json.dumps(spec.mcp_servers),
                json.dumps(spec.builtin_tools),
                json.dumps(spec.channels),
                json.dumps(spec.integrations),
                json.dumps(spec.access),
            ),
        )
        self._db.commit()

    def _row(self, r: _db.Row) -> AgentSpec:
        k = r.keys()
        return AgentSpec(
            name=r["name"],
            prompt=r["prompt"] or "",
            backend=r["backend"],
            # Read-time migration of this catalog's pre-rename spellings (cuga-web → cuga_web).
            # Rows written before the rename would otherwise scope a backend="cuga" worker to a
            # key CUGA's registry does not have, leaving it with no tools and only a log warning.
            # Bounded to the seven names we renamed — see mcp_catalog.migrate_legacy_names.
            mcp_servers=mcp_catalog.migrate_legacy_names(json.loads(r["mcp_servers"])),
            builtin_tools=json.loads(r["builtin_tools"]),
            channels=json.loads(r["channels"]),
            integrations=json.loads(r["integrations"] if "integrations" in k else "[]"),
            access=json.loads(r["access"] if "access" in k else "[]"),
        )

    def get(self, scope: str, name: str) -> AgentSpec | None:
        r = self._db.execute("SELECT * FROM agent WHERE scope=? AND name=?", (scope, name)).fetchone()
        return self._row(r) if r else None

    def list(self, scope: str) -> list[AgentSpec]:
        rows = self._db.execute("SELECT * FROM agent WHERE scope=? ORDER BY name", (scope,)).fetchall()
        return [self._row(r) for r in rows]
