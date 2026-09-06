"""Isolated checkpoint keys for supervisor-internal CugaAgent invocations.

Registry-cached supervisor graphs reuse one in-memory checkpointer per
sub-agent. The child thread ID must therefore include tenant, user,
supervisor, parent conversation, and sub-agent identity so one delegation
cannot load another user's or conversation's state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from cuga.config import settings

MEMORY_SCOPE_CONVERSATION = "conversation"
MEMORY_SCOPE_CALL = "call"
CHILD_CHECKPOINT_PREFIX = "sup_child_"

_VALID_SCOPES = frozenset({MEMORY_SCOPE_CONVERSATION, MEMORY_SCOPE_CALL})


@dataclass
class _LockLease:
    lock: asyncio.Lock
    waiters: int = 0


_checkpoint_locks: dict[tuple[int, str], _LockLease] = {}
_agent_map_scopes: dict[int, dict[str, str]] = {}


def attach_agent_memory_scopes(agents: dict, scopes: dict[str, str]) -> None:
    """Bind per-agent memory scopes to one supervisor agent map (not the agents)."""
    if scopes:
        _agent_map_scopes[id(agents)] = dict(scopes)


def agent_map_memory_scopes(agents: Any) -> dict[str, str]:
    if agents is None:
        return {}
    return dict(_agent_map_scopes.get(id(agents)) or {})


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def normalize_memory_scope(value: Any) -> str:
    scope = _as_str(value).strip().lower()
    if scope in _VALID_SCOPES:
        return scope
    return MEMORY_SCOPE_CONVERSATION


def child_checkpoint_id(
    *,
    tenant_id: str = "",
    user_id: str = "",
    supervisor_id: str = "",
    parent_thread_id: str = "",
    sub_agent_id: str = "",
    call_nonce: Optional[str] = None,
) -> str:
    """Return an opaque checkpoint ID for one child-agent invocation.

    The digest is keyed by isolation fields plus an optional per-call nonce
    (call-scoped / stateless mode). Raw identity values are not embedded in
    the ID so logs and checkpoint stores do not leak them.
    """
    payload = {
        "v": 1,
        "tenant": _as_str(tenant_id),
        "user": _as_str(user_id),
        "supervisor": _as_str(supervisor_id),
        "parent": _as_str(parent_thread_id),
        "agent": _as_str(sub_agent_id),
    }
    if call_nonce:
        payload["nonce"] = _as_str(call_nonce)
    material = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{CHILD_CHECKPOINT_PREFIX}{digest}"


def resolve_memory_scope(
    agent_or_config: Any,
    adapter: Any = None,
    agent_name: Optional[str] = None,
) -> str:
    scopes = getattr(adapter, "_agent_memory_scopes", None) or {}
    if agent_name and agent_name in scopes:
        return normalize_memory_scope(scopes[agent_name])
    for attr in ("_memory_scope", "memory_scope"):
        raw = getattr(agent_or_config, attr, None)
        if raw:
            return normalize_memory_scope(raw)
    overrides = getattr(agent_or_config, "_feature_overrides", None) or {}
    if isinstance(overrides, dict) and overrides.get("sub_agent_memory_scope"):
        return normalize_memory_scope(overrides["sub_agent_memory_scope"])
    return normalize_memory_scope(getattr(settings.supervisor, "sub_agent_memory_scope", None))


def _identity_from_state(state: Any) -> tuple[str, str, str]:
    if state is None:
        return "", "", ""
    user_id = _as_str(getattr(state, "user_id", None))
    parent_thread_id = _as_str(getattr(state, "thread_id", None))
    scope = getattr(state, "service_scope", None) or {}
    tenant_id = _as_str(scope.get("tenant_id")) if isinstance(scope, dict) else ""
    return tenant_id, user_id, parent_thread_id


def resolve_child_checkpoint_id(
    *,
    state: Any,
    adapter: Any,
    agent_name: str,
    agent_or_config: Any = None,
) -> str:
    """Derive the child checkpoint ID for one internal delegation.

    Conversation scope reuses the same ID within one parent conversation.
    Call scope (or a missing parent thread) issues a unique ID so state is
    never shared across an unknown identity.
    """
    tenant_id, user_id, parent_thread_id = _identity_from_state(state)
    supervisor_id = _as_str(getattr(adapter, "_supervisor_id", None))
    memory_scope = resolve_memory_scope(agent_or_config, adapter=adapter, agent_name=agent_name)
    if not parent_thread_id or memory_scope == MEMORY_SCOPE_CALL:
        return child_checkpoint_id(
            tenant_id=tenant_id,
            user_id=user_id,
            supervisor_id=supervisor_id,
            parent_thread_id=parent_thread_id,
            sub_agent_id=agent_name,
            call_nonce=uuid.uuid4().hex,
        )
    return child_checkpoint_id(
        tenant_id=tenant_id,
        user_id=user_id,
        supervisor_id=supervisor_id,
        parent_thread_id=parent_thread_id,
        sub_agent_id=agent_name,
    )


def supervisor_instance_id(name: Optional[str] = None) -> str:
    """Return a stable supervisor identity for child checkpoint keys.

    An explicit name is preserved. An omitted or blank name gets a unique
    per-instance ID so two unnamed SDK supervisors that share a CugaAgent
    cannot collide on the same child checkpoint.
    """
    stripped = str(name).strip() if name is not None else ""
    if stripped:
        return stripped
    return f"cuga-supervisor-{uuid.uuid4().hex}"


@asynccontextmanager
async def child_checkpoint_lock(agent: Any, checkpoint_id: str) -> AsyncIterator[None]:
    """Serialize concurrent invokes that share one agent checkpointer + thread.

    The lease is reference-counted so call-scoped unique IDs do not accumulate
    unused lock entries after the last waiter exits.
    """
    key = (id(agent), checkpoint_id)
    lease = _checkpoint_locks.get(key)
    if lease is None:
        lease = _LockLease(lock=asyncio.Lock())
        _checkpoint_locks[key] = lease
    lease.waiters += 1
    try:
        async with lease.lock:
            yield
    finally:
        lease.waiters -= 1
        if lease.waiters == 0 and _checkpoint_locks.get(key) is lease:
            del _checkpoint_locks[key]
