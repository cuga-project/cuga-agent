"""Knowledge scoping helper functions for CugaLite."""

from __future__ import annotations

from typing import Any, Awaitable, Callable


def _get_knowledge_tool_scope_context(
    engine: Any | None,
    thread_id: str | None,
) -> tuple[tuple[str, ...], str | None]:
    """Resolve allowed scopes + default scope for the cuga_lite knowledge tools.

    Mirrors ``KnowledgeClient.scope_context`` semantics — both must report
    the same shape because the same engine flags drive the LLM tool prompt
    (here) AND the search dispatcher in ``knowledge/client.py``.

    Behavior (preserved from the perf branch across the cuga_agent_core
    refactor merge):

      - Both scopes wired         → ``("all", "agent", "session")``, default ``"session"``.
        Narrowest plausible scope; engine auto-falls back to ``"all"`` if the
        session leg returns 0 hits (see ``client.py::KnowledgeClient.search``).
      - Only one scope usable     → ``(that one,)``, default = that one.
        ``"all"`` is not offered when there's no fan-out value.
      - Engine disabled / off     → ``((), None)`` — gate the tool out.
    """
    config = getattr(engine, "_config", None) if engine else None
    if not config or not getattr(config, "enabled", False):
        return (), None

    narrow_scopes: list[str] = []
    if getattr(config, "agent_level_enabled", True):
        narrow_scopes.append("agent")
    if getattr(config, "session_level_enabled", True) and thread_id:
        narrow_scopes.append("session")

    if len(narrow_scopes) >= 2:
        # Synthetic "all" only when there's actual fan-out value.
        return ("all", *narrow_scopes), "session"
    if len(narrow_scopes) == 1:
        return (narrow_scopes[0],), narrow_scopes[0]
    return (), None


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
    if "all" in allowed_scopes and "agent" in allowed_scopes and "session" in allowed_scopes:
        return (
            "Knowledge scope rules for this run: both knowledge scopes are available, plus the "
            "synthetic ``\"all\"`` scope that fans out across them. Default to ``scope=\"session\"`` "
            "for the narrowest plausible query; the engine auto-falls back to ``\"all\"`` if session "
            "returns nothing. Use ``\"agent\"`` only for queries clearly about permanent agent "
            "documents."
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


def _wrap_knowledge_tool(
    fn: Callable[..., Awaitable[Any]],
    tid: str | None,
    allowed_scopes: tuple[str, ...],
    default_scope: str | None,
) -> Callable[..., Awaitable[Any]]:
    """Bind knowledge calls to the trusted conversation and allowed scopes."""

    async def _wrapped(*args, **kwargs):
        scope = kwargs.get("scope")
        if scope is None and default_scope:
            kwargs["scope"] = default_scope
            scope = default_scope
        if scope is not None and scope not in allowed_scopes:
            allowed_text = ", ".join(allowed_scopes)
            return {
                "error": (
                    f"Knowledge scope '{scope}' is unavailable in this context. "
                    f"Allowed scopes: {allowed_text}"
                )
            }
        # The model must not select another conversation's collection or ledger.
        # Agent-scope searches also need the trusted id for citations.
        # An explicit empty id also overrides positional ids when ToolGuard
        # merges arguments without runtime context. Registry tools require str.
        kwargs["thread_id"] = tid or ""
        return await fn(*args, **kwargs)

    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    _wrapped._knowledge_allowed_scopes = allowed_scopes
    _wrapped._knowledge_default_scope = default_scope
    _wrapped._knowledge_thread_id = tid
    return _wrapped
