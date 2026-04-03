"""OpenSandbox executor — runs agent code inside an isolated Docker container.

Uses the opensandbox Python SDK (https://github.com/alibaba/OpenSandbox) with the
code-interpreter image, which bundles Python, Node.js, and system tools (bash, npm,
pip, etc.).  Tool calls back to the cuga registry use the same HTTP call_api pattern
as E2BExecutor so the agent can still call connected MCP/OpenAPI tools from inside
the container.

Enable via settings:
    [advanced_features]
    opensandbox_sandbox = true          # use this executor instead of local

    [skills]
    opensandbox_image = "opensandbox/code-interpreter:v1.0.2"
    opensandbox_entrypoint = "/opt/opensandbox/code-interpreter.sh"
    opensandbox_python_version = "3.11"
    opensandbox_timeout_seconds = 120
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from datetime import timedelta
from typing import Any, List, Optional

from loguru import logger

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.config import settings
from ..base_executor import RemoteExecutor


def _skills_cfg():
    return settings.skills


class OpenSandboxExecutor(RemoteExecutor):
    """Executes agent Python code inside an OpenSandbox Docker container."""

    # ------------------------------------------------------------------ #
    # RemoteExecutor interface                                             #
    # ------------------------------------------------------------------ #

    async def execute_for_cuga_lite(
        self,
        wrapped_code: str,
        context_locals: dict[str, Any],
        state: AgentState,
        thread_id: Optional[str] = None,
        apps_list: Optional[List[str]] = None,
    ) -> tuple[str, dict[str, Any]]:
        """Run wrapped code in an OpenSandbox container and return (stdout, new_locals)."""
        from ..common import CallApiHelper

        if context_locals is None:
            context_locals = {}

        var_manager = state.variables_manager if state else VariablesManager()
        variables_code = var_manager.get_variables_formatted()
        tools_code = self._serialize_tools(context_locals, apps_list=apps_list)

        function_call_url = CallApiHelper.get_function_call_url()
        trajectory_path = CallApiHelper.get_trajectory_path()
        call_api_code = CallApiHelper.create_remote_call_api_code(function_call_url, trajectory_path)

        complete_code = f"""\
{call_api_code}
{tools_code}
{variables_code}
{wrapped_code}

async def main():
    __result_locals = await asyncio.wait_for(_async_main(), timeout=30)
    print("!!!===!!!")
    print(__result_locals)

import asyncio as _asyncio
_asyncio.run(main())
"""

        logger.debug(
            f"[OpenSandboxExecutor] Executing with {var_manager.get_variable_count()} variables"
        )

        raw = await self._run_in_container(complete_code, lang="python")
        stdout, result_locals = self._parse_output(raw)
        if not result_locals:
            logger.debug("[OpenSandboxExecutor] No parseable locals in output")
        return stdout, result_locals

    async def execute_for_code_agent(
        self,
        wrapped_code: str,
        state: AgentState,
        thread_id: Optional[str] = None,
    ) -> str:
        """Run wrapped code in an OpenSandbox container for CodeAgent mode."""
        from ..common import CallApiHelper

        function_call_url = CallApiHelper.get_function_call_url()
        trajectory_path = CallApiHelper.get_trajectory_path()
        call_api_code = CallApiHelper.create_remote_call_api_code(function_call_url, trajectory_path)

        variables_code = state.variables_manager.get_variables_formatted() if state.variables_manager else ""

        complete_code = f"""\
{call_api_code}
{variables_code}
{wrapped_code}

import asyncio as _asyncio
_asyncio.run(_async_main())
"""
        return await self._run_in_container(complete_code, lang="python")

    # ------------------------------------------------------------------ #
    # Container execution                                                  #
    # ------------------------------------------------------------------ #

    async def _run_in_container(self, code: str, lang: str = "python") -> str:
        """Run code in a fresh OpenSandbox container and return stdout+stderr."""
        try:
            from opensandbox import Sandbox  # type: ignore[import]
            from code_interpreter import CodeInterpreter, SupportedLanguage  # type: ignore[import]
        except ImportError as exc:
            return (
                f"[OpenSandboxExecutor] SDK not installed ({exc}). "
                "Run: pip install opensandbox opensandbox-code-interpreter"
            )

        cfg = _skills_cfg()
        timeout_s = int(cfg.opensandbox_timeout_seconds)

        try:
            sandbox = await Sandbox.create(
                cfg.opensandbox_image,
                entrypoint=[cfg.opensandbox_entrypoint],
                env={"PYTHON_VERSION": cfg.opensandbox_python_version},
                timeout=timedelta(seconds=timeout_s),
            )
        except Exception as exc:
            return f"[OpenSandboxExecutor] Failed to create sandbox (is Docker running?): {exc}"

        try:
            async with sandbox:
                interpreter = await CodeInterpreter.create(sandbox)
                lang_enum = (
                    SupportedLanguage.PYTHON
                    if lang == "python"
                    else SupportedLanguage.JAVASCRIPT
                )
                result = await interpreter.codes.run(code, language=lang_enum)
                stdout = "".join(line.text for line in result.logs.stdout)
                stderr = "".join(line.text for line in result.logs.stderr)
                result_vals = "".join(
                    r.text for r in result.result if hasattr(r, "text") and r.text
                )
                return stdout + (f"\n[stderr]\n{stderr}" if stderr.strip() else "") + result_vals
        except Exception as exc:
            return f"[OpenSandboxExecutor] Execution error: {exc}"

    # ------------------------------------------------------------------ #
    # Tool serialization (same approach as E2BExecutor)                   #
    # ------------------------------------------------------------------ #

    def _serialize_tools(
        self,
        locals_dict: dict[str, Any],
        apps_list: Optional[List[str]] = None,
    ) -> str:
        lines = ["# Tool functions"]
        sorted_apps = sorted(apps_list or [], key=len, reverse=True)

        for tool_name, tool_func in locals_dict.items():
            if not callable(tool_func) or tool_name.startswith("_"):
                continue
            if not asyncio.iscoroutinefunction(tool_func):
                continue
            try:
                source = inspect.getsource(tool_func)
                dedented = textwrap.dedent(source)
                if f"def {tool_name}" in dedented or f"async def {tool_name}" in dedented:
                    lines.append(dedented)
                else:
                    app_name = "unknown"
                    for app in sorted_apps:
                        if tool_name.startswith(app + "_"):
                            app_name = app
                            break
                    lines.append(
                        f'async def {tool_name}(**kwargs):\n'
                        f'    return await call_api("{app_name}", "{tool_name}", kwargs)\n'
                    )
            except (OSError, TypeError):
                lines.append(
                    f'async def {tool_name}(*args, **kwargs):\n'
                    f'    return await call_api("unknown", "{tool_name}", kwargs)\n'
                )

        return "\n".join(lines) + "\n\n" if len(lines) > 1 else ""

    # ------------------------------------------------------------------ #
    # Output parsing (same delimiter protocol as E2BExecutor)             #
    # ------------------------------------------------------------------ #

    def _parse_output(self, raw: str) -> tuple[str, dict[str, Any]]:
        if "!!!===!!!" not in raw:
            return raw, {}
        stdout, locals_str = raw.split("!!!===!!!", 1)
        result_locals: dict[str, Any] = {}
        for line in reversed(locals_str.strip().split("\n")):
            if line.strip().startswith("{"):
                try:
                    result_locals = ast.literal_eval(line.strip())
                    break
                except (ValueError, SyntaxError):
                    continue
        return stdout.strip(), result_locals
