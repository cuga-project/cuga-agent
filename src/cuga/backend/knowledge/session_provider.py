"""Session-level knowledge state management.

Provides in-memory and persistent providers for tracking per-session
knowledge state (uploaded documents, filters, overrides).

Routes MUST always go through the provider's save() method — never write
to the JSON file directly.  SessionProvider.save() is in-memory only;
StorageBackedSessionProvider (used by the server) writes through to the
relational store selected by ``storage.mode``; PersistentSessionProvider
writes through to a JSON file (tests / legacy import).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Naming conventions — single source of truth for prefix construction
# ---------------------------------------------------------------------------

_SESSION_PREFIX_ID_LEN = 16  # 64-bit hex space for collision resistance


def session_prefix(thread_id: str) -> str:
    """Build the filename prefix for a session's documents."""
    # Pad short thread_ids to avoid empty prefixes
    tid = thread_id.ljust(_SESSION_PREFIX_ID_LEN, "0")[:_SESSION_PREFIX_ID_LEN]
    return f"sess_{tid}/"


def agent_prefix(agent_id: str, config_version: str) -> str:
    """Build the filename prefix for an agent+version's documents."""
    return f"agent_{agent_id}_{config_version}/"


@dataclass
class SessionKnowledgeState:
    """State for a single chat session's knowledge scope."""

    thread_id: str
    user_id: str = ""  # Owner user ID for access control
    tenant_id: str = ""  # Tenant ID for multi-tenant isolation
    filter_id: str | None = None  # Knowledge filter ID (legacy)
    filenames: list[str] = field(default_factory=list)  # Original filenames (without prefix, for display)
    overrides: dict[str, Any] = field(
        default_factory=dict
    )  # Per-session config overrides (extension point — stored/patchable, not yet consumed by prompt/tools)
    created_at: str = ""  # ISO timestamp

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionKnowledgeState:
        return cls(
            thread_id=data.get("thread_id", ""),
            user_id=data.get("user_id", ""),
            tenant_id=data.get("tenant_id", ""),
            filter_id=data.get("filter_id"),
            filenames=data.get("filenames", []),
            overrides=data.get("overrides", {}),
            created_at=data.get("created_at", ""),
        )


@dataclass
class AgentKnowledgeState:
    """State for an agent+version's knowledge scope."""

    agent_id: str
    config_version: str
    filter_id: str | None = None  # Knowledge filter ID (legacy)
    filenames: list[str] = field(default_factory=list)  # Original filenames (without prefix, for display)
    created_at: str = ""  # ISO timestamp

    @property
    def key(self) -> str:
        return f"{self.agent_id}:{self.config_version}"

    @property
    def prefix(self) -> str:
        return agent_prefix(self.agent_id, self.config_version)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentKnowledgeState:
        return cls(
            agent_id=data.get("agent_id", ""),
            config_version=data.get("config_version", ""),
            filter_id=data.get("filter_id"),
            filenames=data.get("filenames", []),
            created_at=data.get("created_at", ""),
        )


def _deep_merge(base: dict, patch: dict) -> dict:
    """Deep-merge *patch* into *base* (mutates base). Returns base."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class SessionProvider:
    """In-memory session knowledge state provider."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionKnowledgeState] = {}
        self._agents: dict[str, AgentKnowledgeState] = {}

    # -- session operations --------------------------------------------------

    def get_session(self, thread_id: str) -> SessionKnowledgeState | None:
        return self._sessions.get(thread_id)

    def get_or_create_session(
        self,
        thread_id: str,
        user_id: str = "",
        tenant_id: str = "",
    ) -> SessionKnowledgeState:
        if thread_id not in self._sessions:
            self._sessions[thread_id] = SessionKnowledgeState(
                thread_id=thread_id,
                user_id=user_id,
                tenant_id=tenant_id,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        return self._sessions[thread_id]

    def check_session_access(
        self,
        thread_id: str,
        user_id: str = "",
        tenant_id: str = "",
    ) -> bool:
        """Check if user/tenant owns the session. Returns True if accessible."""
        state = self._sessions.get(thread_id)
        if state is None:
            return True  # Session doesn't exist yet — will be created
        # If session has owner info, enforce it
        if state.user_id and user_id and state.user_id != user_id:
            return False
        if state.tenant_id and tenant_id and state.tenant_id != tenant_id:
            return False
        return True

    def save_session(self, thread_id: str, state: SessionKnowledgeState) -> None:
        self._sessions[thread_id] = state

    def delete_session(self, thread_id: str) -> None:
        self._sessions.pop(thread_id, None)

    def list_sessions(self) -> dict[str, SessionKnowledgeState]:
        return dict(self._sessions)

    def collect_expired_sessions(self, max_age_seconds: float = 7 * 24 * 3600) -> list[SessionKnowledgeState]:
        """Return sessions older than max_age_seconds. Does NOT delete them."""
        now = datetime.now(timezone.utc)
        expired = []
        for state in self._sessions.values():
            if not state.created_at:
                continue
            try:
                created = datetime.fromisoformat(state.created_at.replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if (now - created).total_seconds() > max_age_seconds:
                    expired.append(state)
            except (ValueError, TypeError):
                continue
        return expired

    def patch_session_overrides(
        self,
        thread_id: str,
        patch: dict[str, Any],
        user_id: str = "",
        tenant_id: str = "",
    ) -> SessionKnowledgeState:
        """Deep-merge *patch* into session overrides. Creates session if needed."""
        state = self.get_or_create_session(thread_id, user_id=user_id, tenant_id=tenant_id)
        _deep_merge(state.overrides, patch)
        self.save_session(thread_id, state)
        return state

    # -- agent operations ----------------------------------------------------

    def get_agent(self, key: str) -> AgentKnowledgeState | None:
        """Get agent state by key (agent_id:config_version)."""
        return self._agents.get(key)

    def get_or_create_agent(self, agent_id: str, config_version: str) -> AgentKnowledgeState:
        key = f"{agent_id}:{config_version}"
        if key not in self._agents:
            self._agents[key] = AgentKnowledgeState(
                agent_id=agent_id,
                config_version=config_version,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        return self._agents[key]

    def save_agent(self, state: AgentKnowledgeState) -> None:
        self._agents[state.key] = state

    def list_agents(self) -> dict[str, AgentKnowledgeState]:
        return dict(self._agents)


def _run_sync(coro):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


_SESSION_TABLE = "knowledge_session_state"


async def _ensure_session_schema(store) -> None:
    await store.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SESSION_TABLE} (
            tenant_id TEXT NOT NULL DEFAULT '',
            instance_id TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            record_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, instance_id, kind, record_key)
        )
        """
    )
    await store.commit()


class StorageBackedSessionProvider(SessionProvider):
    """Session knowledge persisted through ``storage.mode`` (SQLite local / Postgres prod).

    Every row is scoped by ``tenant_id`` + ``instance_id``, matching policies and
    other relational stores. The in-memory maps remain the API; mutations write
    through one row at a time.
    """

    def __init__(self) -> None:
        super().__init__()
        self._load()

    def _store(self):
        from cuga.backend.storage import get_storage

        return get_storage().get_relational_store("config")

    def _scope(self) -> tuple[str, str]:
        from cuga.config import get_service_instance_id, get_tenant_id

        return get_tenant_id(), get_service_instance_id()

    def _load(self) -> None:
        self._sessions, self._agents = _run_sync(self._load_async())

    async def _load_async(self) -> tuple[dict[str, SessionKnowledgeState], dict[str, AgentKnowledgeState]]:
        store = self._store()
        await _ensure_session_schema(store)
        tenant_id, inst_id = self._scope()
        rows = await store.fetchall(
            f"SELECT kind, record_key, payload FROM {_SESSION_TABLE} WHERE tenant_id = ? AND instance_id = ?",
            (tenant_id, inst_id),
        )
        sessions: dict[str, SessionKnowledgeState] = {}
        agents: dict[str, AgentKnowledgeState] = {}
        for row in rows:
            kind = row["kind"]
            try:
                data = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue
            if kind == "session":
                sessions[row["record_key"]] = SessionKnowledgeState.from_dict(data)
            elif kind == "agent":
                agents[row["record_key"]] = AgentKnowledgeState.from_dict(data)
        logger.info(
            "Loaded knowledge session state from storage: %d sessions, %d agents",
            len(sessions),
            len(agents),
        )
        return sessions, agents

    def _upsert(self, kind: str, record_key: str, payload: dict[str, Any]) -> None:
        _run_sync(self._upsert_async(kind, record_key, payload))

    async def _upsert_async(self, kind: str, record_key: str, payload: dict[str, Any]) -> None:
        store = self._store()
        await _ensure_session_schema(store)
        tenant_id, inst_id = self._scope()
        now = datetime.now(timezone.utc).isoformat()
        body = json.dumps(payload)
        is_prod = type(store).__name__ == "ProdRelationalStore"
        if is_prod:
            await store.execute(
                f"""
                INSERT INTO {_SESSION_TABLE} (tenant_id, instance_id, kind, record_key, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, instance_id, kind, record_key)
                DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
                """,
                (tenant_id, inst_id, kind, record_key, body, now),
            )
        else:
            await store.execute(
                f"""
                INSERT OR REPLACE INTO {_SESSION_TABLE}
                (tenant_id, instance_id, kind, record_key, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tenant_id, inst_id, kind, record_key, body, now),
            )
        await store.commit()

    def _delete_row(self, kind: str, record_key: str) -> None:
        _run_sync(self._delete_row_async(kind, record_key))

    async def _delete_row_async(self, kind: str, record_key: str) -> None:
        store = self._store()
        await _ensure_session_schema(store)
        tenant_id, inst_id = self._scope()
        await store.execute(
            f"DELETE FROM {_SESSION_TABLE} WHERE tenant_id = ? AND instance_id = ? "
            f"AND kind = ? AND record_key = ?",
            (tenant_id, inst_id, kind, record_key),
        )
        await store.commit()

    def save_session(self, thread_id: str, state: SessionKnowledgeState) -> None:
        super().save_session(thread_id, state)
        self._upsert("session", thread_id, state.to_dict())

    def delete_session(self, thread_id: str) -> None:
        super().delete_session(thread_id)
        self._delete_row("session", thread_id)

    def patch_session_overrides(
        self,
        thread_id: str,
        patch: dict[str, Any],
        user_id: str = "",
        tenant_id: str = "",
    ) -> SessionKnowledgeState:
        state = super().patch_session_overrides(thread_id, patch, user_id=user_id, tenant_id=tenant_id)
        self._upsert("session", thread_id, state.to_dict())
        return state

    def save_agent(self, state: AgentKnowledgeState) -> None:
        super().save_agent(state)
        self._upsert("agent", state.key, state.to_dict())


def create_session_provider(*, json_legacy_path: Path | None = None) -> SessionProvider:
    """Build the session provider that follows ``storage.mode``.

    If a legacy ``session_knowledge.json`` exists and this tenant+instance has
    no rows yet, import it once so a switch to prod (or the new table) keeps
    existing session state.
    """
    provider = StorageBackedSessionProvider()
    if (
        json_legacy_path is not None
        and json_legacy_path.exists()
        and not provider.list_sessions()
        and not provider.list_agents()
    ):
        legacy = PersistentSessionProvider(json_legacy_path)
        for session in legacy.list_sessions().values():
            provider.save_session(session.thread_id, session)
        for agent in legacy.list_agents().values():
            provider.save_agent(agent)
        logger.info("Imported legacy session_knowledge.json into storage-backed session state")
    return provider


async def delete_scoped_session_knowledge() -> None:
    """Drop session/agent knowledge rows for the current tenant+instance (demo reset)."""
    from cuga.backend.storage import get_storage
    from cuga.config import get_service_instance_id, get_tenant_id

    store = get_storage().get_relational_store("config")
    await _ensure_session_schema(store)
    await store.execute(
        f"DELETE FROM {_SESSION_TABLE} WHERE tenant_id = ? AND instance_id = ?",
        (get_tenant_id(), get_service_instance_id()),
    )
    await store.commit()


class PersistentSessionProvider(SessionProvider):
    """Session provider with write-through persistence to JSON file.

    All mutations automatically persist to disk. Routes should NEVER
    write to the JSON file directly — only call provider methods.
    """

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._load()

    def _load(self) -> None:
        """Load state from disk on startup."""
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            for thread_id, data in raw.get("sessions", {}).items():
                self._sessions[thread_id] = SessionKnowledgeState.from_dict(data)
            for key, data in raw.get("agents", {}).items():
                self._agents[key] = AgentKnowledgeState.from_dict(data)
            logger.info(
                "Loaded knowledge state: %d sessions, %d agents",
                len(self._sessions),
                len(self._agents),
            )
        except Exception:
            logger.warning(f"Failed to load knowledge state from {self._path}", exc_info=True)

    def _persist(self) -> None:
        """Write full state to disk. Called on every mutation."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "sessions": {tid: s.to_dict() for tid, s in self._sessions.items()},
                "agents": {k: a.to_dict() for k, a in self._agents.items()},
            }
            self._path.write_text(json.dumps(payload, indent=2))
        except Exception:
            logger.warning(f"Failed to persist knowledge state to {self._path}", exc_info=True)

    # -- override mutating methods to add write-through ----------------------

    def save_session(self, thread_id: str, state: SessionKnowledgeState) -> None:
        super().save_session(thread_id, state)
        self._persist()

    def delete_session(self, thread_id: str) -> None:
        super().delete_session(thread_id)
        self._persist()

    def patch_session_overrides(
        self,
        thread_id: str,
        patch: dict[str, Any],
        user_id: str = "",
        tenant_id: str = "",
    ) -> SessionKnowledgeState:
        state = super().patch_session_overrides(thread_id, patch, user_id=user_id, tenant_id=tenant_id)
        self._persist()
        return state

    def save_agent(self, state: AgentKnowledgeState) -> None:
        super().save_agent(state)
        self._persist()
