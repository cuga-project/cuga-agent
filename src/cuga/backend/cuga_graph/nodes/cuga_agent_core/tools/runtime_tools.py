"""Shared runtime tool-injection orchestrator.

Extracted verbatim (behavior-preserving) from the inline block in
``cuga_lite_graph``. This module *orchestrates* the existing packages — it
never re-implements a filesystem or shell tool:

- filesystem: ``executors.filesystem.create_filesystem_tools`` with either
  the default host backend or a ``RemoteSandboxBackend``;
- shell: the existing ``*SandboxExecutor.create_sandbox_tools`` (returns
  only ``run_command``).

``resolve_runtime_backends`` reproduces the exact legacy gating so Lite,
Supervisor and Chat can share one injection path without behavior drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import AppDefinition

FilesystemChoice = Literal["none", "host", "sandbox_remote"]
ShellChoice = Literal["none", "local", "native", "opensandbox"]


@dataclass(frozen=True)
class RuntimeBackends:
    """Resolved injection decision (independent filesystem + shell axes)."""

    filesystem: FilesystemChoice
    shell: ShellChoice


@dataclass
class ToolBundle:
    """Tools split into prompt-facing vs execution-namespace, plus app meta."""

    prompt_tools: List[Any] = field(default_factory=list)
    execution_callables: Dict[str, Any] = field(default_factory=dict)
    app_definitions: List[AppDefinition] = field(default_factory=list)


def prompt_tool_dicts(tools: List[Any]) -> List[Dict[str, Any]]:
    """Render runtime ``StructuredTool``s into Supervisor's jinja tool dicts.

    Supervisor's prompt template iterates plain dicts (``name``,
    ``params_str``, ``description``, ``params_doc``, ``response_doc``), not
    LangChain tools, so runtime tools must be converted before they can be
    listed for the model.
    """
    dicts: List[Dict[str, Any]] = []
    for t in tools:
        try:
            arg_names = list(getattr(t, "args", {}) or {})
        except Exception:
            arg_names = []
        dicts.append(
            {
                "name": t.name,
                "description": t.description or "",
                "params_str": ", ".join(arg_names),
                "params_doc": "\n".join(f"- {n}" for n in arg_names)
                if arg_names
                else "No parameters required",
                "response_doc": "",
            }
        )
    return dicts


def resolve_runtime_backends(settings: Any, configurable: Dict[str, Any]) -> RuntimeBackends:
    """Reproduce exactly the legacy gating inlined in ``cuga_lite_graph``."""
    adv = settings.advanced_features
    _sandbox_mode = getattr(adv, "sandbox_mode", "opensandbox")
    _shell_tool_on = getattr(adv, "enable_shell_tool", False)
    _fs_tool_on = (
        configurable["enable_filesystem_tools"]
        if "enable_filesystem_tools" in configurable
        else getattr(adv, "enable_filesystem_tools", False)
    )
    _opensandbox_on = getattr(adv, "opensandbox_sandbox", False)
    _use_sandbox = _shell_tool_on and (
        (_sandbox_mode == "native")
        or (_sandbox_mode == "opensandbox" and _opensandbox_on)
        or (_sandbox_mode == "local")
    )

    if not _fs_tool_on:
        filesystem: FilesystemChoice = "none"
    elif _use_sandbox and _sandbox_mode == "opensandbox":
        filesystem = "sandbox_remote"
    else:
        filesystem = "host"

    shell: ShellChoice = _sandbox_mode if _use_sandbox else "none"
    return RuntimeBackends(filesystem=filesystem, shell=shell)


def build_runtime_tools(*, thread_id: Optional[str], backends: RuntimeBackends) -> ToolBundle:
    """Orchestrate filesystem + shell tool creation into a ToolBundle.

    Mirrors the original ``cuga_lite_graph`` injection (same backend
    selection, same ``coroutine or func`` extraction, same log lines).
    """
    bundle = ToolBundle()

    if backends.filesystem != "none":
        import cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem as fs_pkg

        fs_backend = None
        if backends.filesystem == "sandbox_remote":
            from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor

            fs_backend = fs_pkg.RemoteSandboxBackend(CodeExecutor._get_opensandbox_executor(), thread_id)

        fs_tools = fs_pkg.create_filesystem_tools(thread_id, backend=fs_backend)
        for ft in fs_tools:
            fn = ft.coroutine or ft.func
            if fn:
                bundle.execution_callables[ft.name] = fn
        bundle.prompt_tools.extend(fs_tools)
        bundle.app_definitions.append(
            AppDefinition(
                name="filesystem",
                type="runtime",
                description="Workspace filesystem tools: read, write, edit, list, search, move files and directories.",
            )
        )
        logger.info(f"Injected filesystem tools (thread_id={thread_id!r}): {[t.name for t in fs_tools]}")

    if backends.shell != "none":
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor

        if backends.shell == "native":
            sandbox_executor = CodeExecutor._get_native_executor()
            sandbox_label = "NativeSandbox"
        elif backends.shell == "local":
            sandbox_executor = CodeExecutor._get_local_sandbox_executor()
            sandbox_label = "LocalSandbox"
        else:
            sandbox_executor = CodeExecutor._get_opensandbox_executor()
            sandbox_label = "OpenSandbox"

        run_cmd_tools = sandbox_executor.create_sandbox_tools(thread_id=thread_id)
        for st in run_cmd_tools:
            fn = st.coroutine or st.func
            if fn:
                bundle.execution_callables[st.name] = fn
        bundle.prompt_tools.extend(run_cmd_tools)
        logger.info(f"[{sandbox_label}] Injected run_command (thread_id={thread_id!r})")

    return bundle
