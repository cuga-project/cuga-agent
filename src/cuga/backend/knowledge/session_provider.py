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
            logger.warning("Failed to load knowledge state from %s", self._path, exc_info=True)

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
            logger.warning("Failed to persist knowledge state to %s", self._path, exc_info=True)

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
