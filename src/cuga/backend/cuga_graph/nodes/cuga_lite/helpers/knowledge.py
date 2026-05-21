"""Knowledge scoping helper functions for CugaLite."""

from __future__ import annotations

from typing import Any


def _get_knowledge_tool_scope_context(
    engine: Any | None,
    thread_id: str | None,
) -> tuple[tuple[str, ...], str | None]:
    config = getattr(engine, "_config", None) if engine else None
    if not config or not getattr(config, "enabled", False):
        return (), None

    scopes: list[str] = []
    if getattr(config, "agent_level_enabled", True):
        scopes.append("agent")
    if getattr(config, "session_level_enabled", True) and thread_id:
        scopes.append("session")

    default_scope = "agent" if "agent" in scopes else scopes[0] if scopes else None
    return tuple(scopes), default_scope


def _knowledge_scope_instruction(allowed_scopes: tuple[str, ...], thread_id: str | None) -> str:
    if allowed_scopes == ("agent",):
        return (
            "Knowledge scope rules for this run: only agent-level knowledge is available. "
            "Never call `knowledge_*` tools with `scope=\"session\"`."
        )
    if allowed_scopes == ("session",):
        return (
            "Knowledge scope rules for this run: only session-level knowledge is available. "
            "Never call `knowledge_*` tools with `scope=\"agent\"`. The conversation thread context is injected automatically."
        )
    if allowed_scopes == ("agent", "session"):
        return (
            "Knowledge scope rules for this run: both knowledge scopes are available. "
            "Use `scope=\"agent\"` for permanent agent documents and `scope=\"session\"` for this conversation's documents."
        )
    if thread_id:
        return "Knowledge tools are unavailable in this run. Do not call any `knowledge_*` tool."
    return (
        "Knowledge tools are unavailable in this run. "
        "Session scope cannot be used here because there is no conversation thread context."
    )


def _decorate_knowledge_tool(tool: Any, allowed_scopes: tuple[str, ...], thread_id: str | None) -> None:
    """Add a brief scope hint to the tool description.

    The full scope rules are already in the system instructions, so we only
    add a short reminder here to avoid bloating the prompt with repeated text.
    """
    base_description = getattr(tool, "description", "") or "Knowledge tool"
    scopes_str = ", ".join(f'"{s}"' for s in allowed_scopes)
    hint = f"Allowed scopes: {scopes_str}. See knowledge scope rules in instructions above."
    tool.description = f"{base_description}\n\n{hint}".strip()
