"""Per-thread path-prefix wrapper for filesystem MCP tools.

When ``settings.skills.enabled`` is true, each chat thread gets an isolated
workspace at ``<cwd>/cuga_workspace/<safe_thread_id>/``. The filesystem MCP
server, however, is a single long-running subprocess anchored at
``<cwd>/cuga_workspace/`` (the parent), so it cannot know which thread is
calling.

This wrapper bridges that gap: at request time, for every ``filesystem_*``
tool, we wrap the underlying ``BaseTool.ainvoke`` so that any relative path
arg (``path``, ``source``, ``destination``, or ``paths``) is rewritten with
the per-thread subdir prefix before forwarding the call. Absolute paths are
passed through untouched — MCP's own allow-check handles them (and may
deny, which is the expected loud failure if the LLM ignores the
relative-paths instruction).

When skills are disabled the wrapper is a no-op pass-through: the workspace
is the shared ``<cwd>/cuga_workspace/`` directory and MCP's own CWD is set
to the same place, so relative paths land naturally.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional

from langchain_core.tools import BaseTool, StructuredTool

_FILESYSTEM_TOOL_PREFIX = "filesystem_"
# Arg names that carry filesystem paths in the MCP filesystem tools' schemas.
_PATH_ARG_NAMES = ("path", "source", "destination")
_PATH_LIST_ARG_NAMES = ("paths",)


def _safe_thread_id(thread_id: Optional[str]) -> str:
    raw = (thread_id or "_default").strip() or "_default"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


def _is_absolute(p: str) -> bool:
    """Treat platform-absolute paths as already-absolute. Relative paths are
    everything else, including a bare filename or ``./foo`` / ``foo/bar``."""
    if not p:
        return False
    if p.startswith("/"):
        return True
    # Windows-style drive letters (rare in this codebase but cheap to handle)
    return len(p) >= 2 and p[1] == ":" and p[0].isalpha()


def _rewrite_path(p: str, prefix: str) -> str:
    if _is_absolute(p):
        return p
    # Strip a leading "./" for cleaner joined paths; semantically equivalent.
    stripped = p[2:] if p.startswith("./") else p
    if not stripped or stripped == ".":
        return prefix
    return f"{prefix}/{stripped}"


def _rewrite_args(args: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Return a shallow copy of args with known path fields prefixed."""
    if not isinstance(args, dict):
        return args
    out = dict(args)
    for key in _PATH_ARG_NAMES:
        val = out.get(key)
        if isinstance(val, str):
            out[key] = _rewrite_path(val, prefix)
    for key in _PATH_LIST_ARG_NAMES:
        val = out.get(key)
        if isinstance(val, list):
            out[key] = [_rewrite_path(v, prefix) if isinstance(v, str) else v for v in val]
    return out


def _wrap_filesystem_tool(tool: BaseTool, prefix: str) -> BaseTool:
    """Return a StructuredTool clone whose invocations rewrite relative path
    args with the per-thread prefix. The agent sees the same name/description/
    schema as the underlying tool."""

    async def _ainvoke(**kwargs: Any) -> Any:
        return await tool.ainvoke(_rewrite_args(kwargs, prefix))

    def _invoke(**kwargs: Any) -> Any:
        return tool.invoke(_rewrite_args(kwargs, prefix))

    wrapped = StructuredTool.from_function(
        func=_invoke,
        coroutine=_ainvoke,
        name=tool.name,
        description=tool.description or "",
        args_schema=getattr(tool, "args_schema", None),
    )
    return wrapped


def _skills_enabled() -> bool:
    try:
        from cuga.config import settings

        return bool(getattr(getattr(settings, "skills", None), "enabled", False))
    except Exception:
        return False


def wrap_filesystem_tool_func(
    tool_name: str,
    tool_func,
    thread_id: Optional[str],
):
    """Return a function-level wrapper that rewrites filesystem path args
    with the per-thread prefix when ``settings.skills.enabled`` is true.

    Use this at injection points that drop into a raw Python namespace (e.g.
    the cuga-lite ``tools_context_dict`` injection) where there is no
    BaseTool layer to wrap. The returned callable has the same async/sync
    shape as the input ``tool_func``.

    When skills are disabled OR the tool isn't a filesystem tool, returns
    ``tool_func`` unchanged.
    """
    if not tool_name.startswith(_FILESYSTEM_TOOL_PREFIX) or not _skills_enabled():
        return tool_func

    # Per-thread workspace must exist before the agent's first filesystem
    # call so MCP writes don't fail with "no such file or directory".
    _ensure_thread_workspace_exists(thread_id)

    prefix = _safe_thread_id(thread_id)
    import inspect

    if inspect.iscoroutinefunction(tool_func):

        async def _async_wrapped(*args, **kwargs):
            # MCP tools usually take a single dict positional or **kwargs;
            # rewrite whichever form actually carries the path fields.
            if args and isinstance(args[0], dict):
                args = (_rewrite_args(args[0], prefix),) + args[1:]
            if kwargs:
                kwargs = _rewrite_args(kwargs, prefix)
            return await tool_func(*args, **kwargs)

        return _async_wrapped

    def _sync_wrapped(*args, **kwargs):
        if args and isinstance(args[0], dict):
            args = (_rewrite_args(args[0], prefix),) + args[1:]
        if kwargs:
            kwargs = _rewrite_args(kwargs, prefix)
        return tool_func(*args, **kwargs)

    return _sync_wrapped


def _ensure_thread_workspace_exists(thread_id: Optional[str]) -> None:
    """Materialize the per-thread workspace dir so MCP filesystem writes
    don't fail with ``No such file or directory``. No-op when skills are
    disabled — the workspace IS the parent and is already present.
    """
    try:
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor import (
            local_thread_workspace_root,
        )

        local_thread_workspace_root(thread_id).mkdir(parents=True, exist_ok=True)
    except Exception:
        # Best-effort: any failure falls through to natural MCP behavior.
        pass


def wrap_mcp_filesystem_tools(
    base_tools: Iterable[BaseTool],
    thread_id: Optional[str],
) -> List[BaseTool]:
    """When ``settings.skills.enabled`` is true, wrap each ``filesystem_*``
    tool to route relative paths under ``cuga_workspace/<safe_thread_id>/``.
    Otherwise return the input list unchanged.

    The wrapper does NOT mutate ``base_tools`` itself — the chat agent keeps
    the unwrapped reference for re-use across requests and per-request wraps
    happen in ``_build_runtime_context``.
    """
    tools = list(base_tools)

    if not _skills_enabled():
        return tools

    # Materialize the per-thread workspace dir so the first MCP filesystem
    # call doesn't fail with "no such file or directory".
    _ensure_thread_workspace_exists(thread_id)

    prefix = _safe_thread_id(thread_id)
    wrapped: List[BaseTool] = []
    for tool in tools:
        if getattr(tool, "name", "").startswith(_FILESYSTEM_TOOL_PREFIX):
            wrapped.append(_wrap_filesystem_tool(tool, prefix))
        else:
            wrapped.append(tool)
    return wrapped
