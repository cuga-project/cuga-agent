"""Shared helpers for manage config load/save, secrets, and draft locking."""

import asyncio
from typing import Any, Optional

from fastapi import Request

DEFAULT_AGENT_ID = "cuga-default"


def resolve_agent_id(agent_id: Optional[str]) -> str:
    return agent_id or DEFAULT_AGENT_ID


def app_state(request: Request):
    return getattr(request.app.state, "app_state", None)


def draft_state(request: Request):
    return getattr(request.app.state, "draft_app_state", None)


def policies_list_from_config(raw_policies: Any) -> list:
    """Accept manage policies as either {policies: [...]} or a bare list."""
    if isinstance(raw_policies, dict) and "policies" in raw_policies:
        policies = raw_policies.get("policies", [])
        return list(policies) if isinstance(policies, list) else []
    if isinstance(raw_policies, list):
        return raw_policies
    return []


def extract_agent_feature_overrides(config: dict[str, Any]) -> dict[str, bool | int | None]:
    """Extract enable_todos, reflection_enabled, shortlisting_tool_threshold, cuga_lite_max_steps from config.

    Frontend uses feature_flags.max_steps -> cuga_lite_max_steps.
    """
    out: dict[str, bool | int | None] = {
        "enable_todos": None,
        "reflection_enabled": None,
        "shortlisting_tool_threshold": None,
        "cuga_lite_max_steps": None,
        "enable_filesystem_tools": None,
    }
    feature_flags = config.get("feature_flags") or {}
    advanced = config.get("advanced_features") or {}
    out["enable_todos"] = (
        feature_flags.get("enable_todos") if "enable_todos" in feature_flags else advanced.get("enable_todos")
    )
    out["reflection_enabled"] = (
        feature_flags.get("reflection")
        if "reflection" in feature_flags
        else advanced.get("reflection_enabled")
    )
    out["enable_filesystem_tools"] = (
        feature_flags.get("enable_filesystem_tools")
        if "enable_filesystem_tools" in feature_flags
        else advanced.get("enable_filesystem_tools")
    )
    val = (
        feature_flags.get("shortlisting_tool_threshold")
        if "shortlisting_tool_threshold" in feature_flags
        else advanced.get("shortlisting_tool_threshold")
    )
    out["shortlisting_tool_threshold"] = int(val) if val is not None else None
    max_steps_val = (
        feature_flags.get("max_steps")
        if "max_steps" in feature_flags
        else advanced.get("cuga_lite_max_steps")
    )
    out["cuga_lite_max_steps"] = int(max_steps_val) if max_steps_val is not None else None
    return out


_SECRET_FIELD_SUBSTRINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD")  # KEY covers APIKEY + API_KEY both


def is_secret_field_name(name: str) -> bool:
    """One rule for "is this field name a secret?" — used by the env-presets
    endpoint, the GET-redactor, and the PATCH-preserver so they can't drift
    when a sixth secret substring gets added."""
    return any(s in (name or "").upper() for s in _SECRET_FIELD_SUBSTRINGS)


def redact_secrets_in_config(config: dict[str, Any]) -> None:
    """In-place: replace secret-named non-empty string fields with ''.
    Walks nested dicts. PATCH preserves stored values on empty incoming
    (see patch_draft_knowledge)."""

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if is_secret_field_name(k) and isinstance(v, str) and v:
                    node[k] = ""
                elif isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(node, list):
            # Recurse into list items too (review): a shape like
            # {"tools": [{"api_key": "..."}]} would otherwise leak the nested secret.  # pragma: allowlist secret
            for item in node:
                _walk(item)

    _walk(config)


def merge_feature_flags_defaults(config: dict[str, Any]) -> None:
    """Fill config.feature_flags from stored advanced_features, then global settings.

    Manage stores agent overrides under ``advanced_features`` (e.g. demo_manage_setup
    sets enable_filesystem_tools there only). The UI reads ``feature_flags``. When a
    key is missing from feature_flags, prefer the persisted advanced_features dict
    over global settings so per-agent demo/publish state is reflected correctly.
    """
    from cuga.config import settings

    ff = config.setdefault("feature_flags", {})
    raw_adv = config.get("advanced_features")
    stored_adv: dict[str, Any] = raw_adv if isinstance(raw_adv, dict) else {}
    af = getattr(settings, "advanced_features", None)

    def _bool(v: Any) -> bool:
        return bool(v)

    if "enable_todos" not in ff:
        if "enable_todos" in stored_adv:
            ff["enable_todos"] = _bool(stored_adv["enable_todos"])
        elif af:
            ff["enable_todos"] = getattr(af, "enable_todos", False)
        else:
            ff["enable_todos"] = False
    if "reflection" not in ff:
        if "reflection" in stored_adv:
            ff["reflection"] = _bool(stored_adv["reflection"])
        elif "reflection_enabled" in stored_adv:
            ff["reflection"] = _bool(stored_adv["reflection_enabled"])
        elif af:
            ff["reflection"] = getattr(af, "reflection_enabled", False)
        else:
            ff["reflection"] = False
    if "enable_filesystem_tools" not in ff:
        if "enable_filesystem_tools" in stored_adv:
            ff["enable_filesystem_tools"] = _bool(stored_adv["enable_filesystem_tools"])
        elif af:
            ff["enable_filesystem_tools"] = getattr(af, "enable_filesystem_tools", False)
        else:
            ff["enable_filesystem_tools"] = False
    if "shortlisting_tool_threshold" not in ff:
        if "shortlisting_tool_threshold" in stored_adv:
            v = stored_adv["shortlisting_tool_threshold"]
            ff["shortlisting_tool_threshold"] = int(v) if v is not None else 35
        elif af:
            ff["shortlisting_tool_threshold"] = getattr(af, "shortlisting_tool_threshold", 35)
        else:
            ff["shortlisting_tool_threshold"] = 35
    if "max_steps" not in ff:
        if "max_steps" in stored_adv:
            v = stored_adv["max_steps"]
            ff["max_steps"] = int(v) if v is not None else 70
        elif "cuga_lite_max_steps" in stored_adv:
            v = stored_adv["cuga_lite_max_steps"]
            ff["max_steps"] = int(v) if v is not None else 70
        elif af:
            ff["max_steps"] = getattr(af, "cuga_lite_max_steps", 70)
        else:
            ff["max_steps"] = 70
    if "builtin_tools" not in ff:
        if "builtin_tools" in stored_adv:
            bt = stored_adv["builtin_tools"]
            ff["builtin_tools"] = list(bt) if isinstance(bt, (list, tuple)) else bt
        elif af:
            ff["builtin_tools"] = list(getattr(af, "builtin_tools", ["knowledge"]))
        else:
            ff["builtin_tools"] = ["knowledge"]


def merge_mcp_yaml_into_config(config: dict[str, Any]) -> None:
    from cuga.backend.server.managed_mcp import get_managed_mcp_path, read_managed_mcp_servers

    tools_list = config.get("tools") or []
    if not tools_list:
        return
    yaml_servers = read_managed_mcp_servers(get_managed_mcp_path())
    for t in tools_list:
        if (t.get("type") or "mcp").lower() != "mcp":
            continue
        if t.get("command"):
            continue
        name = t.get("name")
        if not name or name not in yaml_servers:
            continue
        existing = yaml_servers[name]
        if isinstance(existing, dict):
            for key in ("command", "args", "transport", "description", "env"):
                if key in existing and key not in t:
                    t[key] = existing[key]


# Per-agent serialization for draft load-modify-write. Without this, two
# concurrent PATCHes to the SAME agent (e.g. user clicks Use on Watsonx —
# autosave fires PATCH /draft/knowledge — types in a tools field —
# autosave fires PATCH /draft/tools) interleave their read-modify-write:
#
#   PATCH knowledge: load(draft_v0) → set knowledge=watsonx → save(v0+watsonx)
#   PATCH tools:     load(draft_v0) → set tools=new        → save(v0+tools) ← knowledge LOST
#
# Result: tools updated, knowledge silently reverted. User-visible as
# "I clicked Use on Watsonx but the draft still shows fastembed".
#
# The patch_draft_knowledge handler ALSO acquires this lock for a wider
# critical section (engine apply + draft save together) so the live
# engine and the persisted draft can't desync if a PATCH is aborted
# between them. asyncio.Lock is NON-reentrant — callers that already
# hold the lock MUST use the *_unlocked variant of the save helper.
AGENT_DRAFT_LOCKS: dict[str, asyncio.Lock] = {}


def agent_draft_lock(agent_id: str) -> asyncio.Lock:
    """Get-or-create the per-agent draft lock. Creating on demand
    keeps us out of import-time event-loop dependency issues."""
    lock = AGENT_DRAFT_LOCKS.get(agent_id)
    if lock is None:
        lock = asyncio.Lock()
        AGENT_DRAFT_LOCKS[agent_id] = lock
    return lock


async def save_draft_section_unlocked(agent_id: str, section: str, value: Any) -> dict[str, Any]:
    """Lock-free load-modify-write. ONLY use this when the caller already
    holds ``agent_draft_lock(agent_id)``. External callers should use
    ``_load_and_patch_draft`` instead."""
    from cuga.backend.server.config_store import load_draft, save_draft

    existing = await load_draft(agent_id) or {}
    existing[section] = value
    await save_draft(existing, agent_id)
    return existing


async def load_and_patch_draft(agent_id: str, section: str, value: Any) -> dict[str, Any]:
    """Locked load-modify-write. Serializes concurrent PATCHes to the
    same agent so cross-section writes don't clobber each other."""
    async with agent_draft_lock(agent_id):
        return await save_draft_section_unlocked(agent_id, section, value)
