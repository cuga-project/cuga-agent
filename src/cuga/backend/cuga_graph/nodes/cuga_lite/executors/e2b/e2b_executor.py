import ast
import asyncio
import inspect
import textwrap
from typing import Any, List, Optional
from loguru import logger

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from ..base_executor import RemoteExecutor


class E2BExecutor(RemoteExecutor):
    """Handles code execution in E2B remote sandbox."""

    _KNOWLEDGE_POSITIONAL_ARGS = {
        "knowledge_search_knowledge": [
            "query",
            "scope",
            "limit",
            "score_threshold",
            "agent_id",
            "thread_id",
        ],
        "knowledge_ingest_knowledge": [
            "file_path",
            "scope",
            "replace_duplicates",
            "agent_id",
            "thread_id",
        ],
        "knowledge_ingest_knowledge_url": ["url", "scope", "agent_id", "thread_id"],
        "knowledge_list_knowledge_documents": ["scope", "agent_id", "thread_id"],
        "knowledge_delete_knowledge_document": ["filename", "scope", "agent_id", "thread_id"],
        "knowledge_get_ingestion_status": ["task_id", "agent_id"],
        "knowledge_get_knowledge_status": ["agent_id"],
    }

    async def execute_for_cuga_lite(
        self,
        wrapped_code: str,
        context_locals: dict[str, Any],
        state: AgentState,
        thread_id: Optional[str] = None,
        apps_list: Optional[List[str]] = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute code for cuga_lite mode in E2B sandbox.

        Args:
            wrapped_code: Wrapped Python code to execute
            context_locals: Dictionary of variables and tools
            state: AgentState instance
            thread_id: Thread ID for sandbox caching
            apps_list: List of app names for parsing tool names

        Returns:
            Tuple of (execution result, new variables dictionary)

        Raises:
            RuntimeError: If E2B is not available or execution fails
        """
        from cuga.backend.tools_env.code_sandbox.e2b_sandbox import execute_code_in_e2b
        from ..common import CallApiHelper

        if context_locals is None:
            context_locals = {}

        try:
            var_manager = state.variables_manager if state else VariablesManager()
            variables_code = var_manager.get_variables_formatted()

            tools_code = self._serialize_tools(context_locals, apps_list=apps_list)

            function_call_url = CallApiHelper.get_function_call_url()
            trajectory_path = CallApiHelper.get_trajectory_path()
            call_api_helper = CallApiHelper.create_remote_call_api_code(function_call_url, trajectory_path)

            complete_code = f"""
{call_api_helper}
{tools_code}
{variables_code}
{wrapped_code}

# Execute and capture locals
async def main():
    __result_locals = await asyncio.wait_for(_async_main(), timeout=30)
    print("!!!===!!!")
    print(__result_locals)

if __name__ == "__main__":
    await main()
"""

            logger.debug(f"Executing in E2B with {var_manager.get_variable_count()} variables and tools")

            result = await execute_code_in_e2b(
                code_content=complete_code,
                thread_id=thread_id,
            )

            result, result_locals = self._parse_execution_output(result)

            if not result_locals:
                logger.warning("E2B execution returned no parseable locals")

            return result, result_locals

        except Exception as e:
            raise RuntimeError(f"E2B sandbox execution failed: {e}")

    async def execute_for_code_agent(
        self,
        wrapped_code: str,
        state: AgentState,
        thread_id: Optional[str] = None,
    ) -> str:
        """Execute code for CodeAgent mode in E2B sandbox.

        Args:
            wrapped_code: Wrapped Python code to execute
            state: AgentState instance
            thread_id: Thread ID for sandbox caching

        Returns:
            Execution result string (stdout)
        """
        from cuga.backend.tools_env.code_sandbox.e2b_sandbox import execute_code_in_e2b
        from ..common import CallApiHelper

        function_call_url = CallApiHelper.get_function_call_url()
        trajectory_path = CallApiHelper.get_trajectory_path()
        call_api_helper = CallApiHelper.create_remote_call_api_code(function_call_url, trajectory_path)

        variables_code = state.variables_manager.get_variables_formatted() if state.variables_manager else ""

        complete_code = f"""
{call_api_helper}

{variables_code}

{wrapped_code}

async def main():
    await _async_main()

if __name__ == "__main__":
    await main()
"""

        result = await execute_code_in_e2b(
            code_content=complete_code,
            thread_id=thread_id,
        )
        return result

    def _serialize_tools(self, locals_dict: dict[str, Any], apps_list: Optional[List[str]] = None) -> str:
        """Serialize async tool functions into Python source code."""
        lines = ["# Tool functions from previous execution"]
        sorted_apps = sorted(apps_list or [], key=len, reverse=True)

        for tool_name, tool_func in locals_dict.items():
            if not callable(tool_func) or tool_name.startswith('_'):
                continue

            if not asyncio.iscoroutinefunction(tool_func):
                continue

            try:
                knowledge_scopes = getattr(tool_func, "_knowledge_allowed_scopes", None)
                if knowledge_scopes is not None:
                    lines.append(self._serialize_knowledge_tool_stub(tool_name, tool_func))
                    continue

                source = inspect.getsource(tool_func)
                dedented_source = textwrap.dedent(source)

                if f"def {tool_name}" in dedented_source or f"async def {tool_name}" in dedented_source:
                    lines.append(dedented_source)
                else:
                    logger.debug(f"Tool '{tool_name}' is a registry wrapper, generating call_api stub")

                    app_name_guess = "unknown"
                    for app in sorted_apps:
                        if tool_name.startswith(app + '_'):
                            app_name_guess = app
                            break

                    if app_name_guess == "unknown":
                        parts = tool_name.split('_', 1)
                        if len(parts) >= 2:
                            app_name_guess = parts[0]

                    api_name_guess = tool_name

                    stub = f"""async def {tool_name}(**kwargs):
    \"\"\"Registry tool: {tool_name}\"\"\"
    return await call_api("{app_name_guess}", "{api_name_guess}", kwargs)
"""
                    lines.append(stub)

            except (OSError, TypeError) as e:
                logger.debug(f"Could not get source for tool '{tool_name}': {e}")
                stub = f"""async def {tool_name}(*args, **kwargs):
    \"\"\"Tool stub for {tool_name}\"\"\"
    return await call_api("unknown", "{tool_name}", kwargs)
"""
                lines.append(stub)

        return "\n".join(lines) + "\n\n" if len(lines) > 1 else ""

    def _serialize_knowledge_tool_stub(self, tool_name: str, tool_func: Any) -> str:
        allowed_scopes = tuple(getattr(tool_func, "_knowledge_allowed_scopes", ()))
        default_scope = getattr(tool_func, "_knowledge_default_scope", None)
        thread_id = getattr(tool_func, "_knowledge_thread_id", "") or ""
        positional_names = self._KNOWLEDGE_POSITIONAL_ARGS.get(tool_name, [])
        allowed_scopes_repr = repr(list(allowed_scopes))
        positional_names_repr = repr(positional_names)
        default_scope_line = f'            kwargs["scope"] = "{default_scope}"\n' if default_scope else ""
        thread_id_line = (
            f'        kwargs.setdefault("thread_id", "{thread_id}")\n'
            if thread_id and "session" in allowed_scopes
            else ""
        )
        default_scope_block = default_scope_line if default_scope_line else "            pass\n"
        thread_id_block = thread_id_line if thread_id_line else ""

        return f"""async def {tool_name}(*args, **kwargs):
    \"\"\"Context-aware knowledge tool stub for remote execution.\"\"\"
    positional_names = {positional_names_repr}
    for name, value in zip(positional_names, args):
        kwargs.setdefault(name, value)
    allowed_scopes = {allowed_scopes_repr}
    if allowed_scopes:
        if "scope" not in kwargs:
{default_scope_block}
        scope = kwargs.get("scope")
        if scope not in allowed_scopes:
            allowed_text = ", ".join(allowed_scopes)
            return {{"error": f"Knowledge scope '{{scope}}' is unavailable in this context. Allowed scopes: {{allowed_text}}"}}
{thread_id_block}
    return await call_api("knowledge", "{tool_name}", kwargs)
"""

    def _parse_execution_output(self, raw_output: str) -> tuple[str, dict[str, Any]]:
        """Parse execution output to extract result and locals.

        Args:
            raw_output: Raw stdout string from execute_code_in_e2b

        Returns:
            Tuple of (result string, locals dictionary)
        """
        if "!!!===!!!" not in raw_output:
            return raw_output, {}

        result, locals_str = raw_output.split("!!!===!!!", 1)
        result = result.strip()

        result_locals = {}
        lines = locals_str.strip().split('\n')
        for line in reversed(lines):
            if line.strip().startswith('{'):
                try:
                    result_locals = ast.literal_eval(line.strip())
                    break
                except (ValueError, SyntaxError):
                    continue

        return result, result_locals
