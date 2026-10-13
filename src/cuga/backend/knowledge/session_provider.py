"""Session-level knowledge state management.

Provides in-memory and persistent providers for tracking per-session
knowledge state (uploaded documents, filters, overrides).

Routes MUST always go through the provider's save() method — never write
to the JSON file directly.  SessionProvider.save() is in-memory only;
PersistentSessionProvider.save() writes through to disk automatically.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Naming conventions — single source of truth for prefix construction
# ---------------------------------------------------------------------------

_SESSION_PREFIX_ID_LEN = 16  # 64-bit hex space for collision resistance


def session_collection_name(thread_id: str) -> str:
    """Existing storage identity, shared by collection and ownership checks."""
    return "kb_sess_" + re.sub(r"[^a-zA-Z0-9_]", "_", thread_id.strip())


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
        # Collection name -> thread_ids stored under it. Lookalike thread_ids
        # ("a-b", "a_b") share a collection, so ownership checks must see every
        # record for that collection. The index makes that a lookup instead of
        # a scan of every session on every request.
        self._threads_by_collection: dict[str, set[str]] = {}
        self._indexed_count = 0

    # -- collection index ----------------------------------------------------

    def _index_add(self, thread_id: str) -> None:
        threads = self._threads_by_collection.setdefault(session_collection_name(thread_id), set())
        if thread_id not in threads:
            threads.add(thread_id)
            self._indexed_count += 1

    def _index_remove(self, thread_id: str) -> None:
        collection = session_collection_name(thread_id)
        threads = self._threads_by_collection.get(collection)
        if threads and thread_id in threads:
            threads.discard(thread_id)
            self._indexed_count -= 1
            if not threads:
                del self._threads_by_collection[collection]

    def _aliases(self, thread_id: str) -> list[str]:
        """Every stored thread_id that maps to the same collection as *thread_id*."""
        if self._indexed_count != len(self._sessions):
            # Something wrote ``_sessions`` without going through the methods
            # below; rebuild rather than trust a stale index.
            self._threads_by_collection = {}
            self._indexed_count = 0
            for existing_thread in self._sessions:
                self._index_add(existing_thread)
        return list(self._threads_by_collection.get(session_collection_name(thread_id), ()))

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
            self._index_add(thread_id)
        return self._sessions[thread_id]

    def check_session_access(
        self,
        thread_id: str,
        user_id: str = "",
        tenant_id: str = "",
    ) -> bool:
        """Check if user/tenant owns the session. Returns True if accessible."""
        # Legacy collection names are lossy: e.g. "a-b" and "a_b" share
        # storage. Check every alias, including records loaded before this
        # guard existed. Conflicting legacy owners fail closed for both users.
        for existing_thread in self._aliases(thread_id):
            state = self._sessions.get(existing_thread)
            if state is None:
                continue
            if state.user_id and user_id and state.user_id != user_id:
                return False
            if state.tenant_id and tenant_id and state.tenant_id != tenant_id:
                return False
        return True

    def claim_session(self, thread_id: str, user_id: str, tenant_id: str = "") -> bool:
        """Record *user_id* as owner if the session has none, then check access.

        Sessions created before ownership was enforced carry no owner, and
        ``check_session_access`` lets anyone through an ownerless session. The
        first authenticated caller claims it instead, so it cannot stay open to
        every user forever.
        """
        # Reject aliases before creating or changing an ownership record.
        if not self.check_session_access(thread_id, user_id, tenant_id):
            return False
        is_new = thread_id not in self._sessions
        state = self.get_or_create_session(thread_id, user_id=user_id, tenant_id=tenant_id)
        if is_new or not state.user_id:
            state.user_id = state.user_id or user_id
            state.tenant_id = state.tenant_id or tenant_id
            self.save_session(thread_id, state)
        return self.check_session_access(thread_id, user_id, tenant_id)

    def save_session(self, thread_id: str, state: SessionKnowledgeState) -> None:
        self._sessions[thread_id] = state
        self._index_add(thread_id)

    def delete_session(self, thread_id: str) -> None:
        if self._sessions.pop(thread_id, None) is not None:
            self._index_remove(thread_id)

    def clear_session(self, thread_id: str) -> None:
        """Clear knowledge metadata, retaining ownership of remaining thread data.

        Checkpoints and generated workspace files can outlive knowledge or chat
        deletion. Their thread must never become claimable by another user.
        All aliases refer to the collection being cleared.
        """
        for existing_thread in self._aliases(thread_id):
            state = self._sessions.get(existing_thread)
            if state is None:
                continue
            if not state.user_id and not state.tenant_id:
                self.delete_session(existing_thread)
                continue
            self.save_session(
                existing_thread,
                SessionKnowledgeState(
                    thread_id=state.thread_id,
                    user_id=state.user_id,
                    tenant_id=state.tenant_id,
                    created_at=state.created_at,
                ),
            )

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
        """Load state from disk on startup.

        A file that exists but cannot be read is an error, not an empty store.
        It holds session ownership, and starting empty would let the first
        caller claim every existing thread. Raising here makes knowledge
        startup fail and roll back (see ``run_knowledge_startup``), so the
        server still starts with knowledge off and nothing exposed.
        """
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            sessions = {
                thread_id: SessionKnowledgeState.from_dict(data)
                for thread_id, data in raw.get("sessions", {}).items()
            }
            agents = {key: AgentKnowledgeState.from_dict(data) for key, data in raw.get("agents", {}).items()}
        except Exception as exc:
            raise RuntimeError(
                f"Knowledge session state at {self._path} is unreadable; refusing to start with no "
                "session owners. Restore or remove the file to continue."
            ) from exc
        for thread_id, state in sessions.items():
            self._sessions[thread_id] = state
            self._index_add(thread_id)
        self._agents.update(agents)
        logger.info(
            "Loaded knowledge state: %d sessions, %d agents",
            len(self._sessions),
            len(self._agents),
        )

    def _persist(self) -> None:
        """Write full state to disk. Called on every mutation.

        Writes to a temporary file in the same directory and renames it over
        the old one, so a crash mid-write leaves the previous file intact
        instead of a truncated one that would fail to load.
        """
        tmp_path = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "sessions": {tid: s.to_dict() for tid, s in self._sessions.items()},
                "agents": {k: a.to_dict() for k, a in self._agents.items()},
            }
            fd, tmp_path = tempfile.mkstemp(
                dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
            )
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
            tmp_path = None
        except Exception:
            logger.warning(f"Failed to persist knowledge state to {self._path}", exc_info=True)
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

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
