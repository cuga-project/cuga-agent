import ast
import asyncio
import inspect
import textwrap
from typing import Any, List, Optional
from loguru import logger

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.config import settings
from ..base_executor import RemoteExecutor


class E2BExecutor(RemoteExecutor):
    """Handles code execution in E2B remote sandbox."""

    # ``agent_id``/``thread_id`` are deliberately absent. They used to sit at the
    # tail of every list, which let model-written sandbox code set them
    # positionally and name another principal's collection
    # (GHSA-ffh4-qv86-xfxv). Dropping them from the tail means ``zip`` stops
    # early and extra positional arguments are discarded. Only ever trim the
    # tail — this map is position-sensitive, so removing an entry from the
    # middle would silently rebind every argument after it.
    _KNOWLEDGE_POSITIONAL_ARGS = {
        "knowledge_search_knowledge": ["query", "scope", "limit", "score_threshold"],
        "knowledge_ingest_knowledge": ["file_path", "scope", "replace_duplicates"],
        "knowledge_ingest_knowledge_url": ["url", "scope"],
        "knowledge_list_knowledge_documents": ["scope"],
        "knowledge_delete_knowledge_document": ["filename", "scope"],
        "knowledge_get_ingestion_status": ["task_id"],
        "knowledge_get_knowledge_status": [],
    }

    # Tools whose host-side counterpart takes a thread_id. The two status tools
    # do not, so injecting one would forward an argument they cannot accept.
    # Kept separate from the positional map now that identity is no longer in
    # it — the map can no longer answer this question.
    _KNOWLEDGE_THREAD_ID_TOOLS = frozenset(
        {
            "knowledge_search_knowledge",
            "knowledge_ingest_knowledge",
            "knowledge_ingest_knowledge_url",
            "knowledge_list_knowledge_documents",
            "knowledge_delete_knowledge_document",
        }
    )

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

            # int() cast: the value is substituted into a code template sent to
            # the e2b sandbox. A non-int (e.g. a misconfigured env var coerced
            # to string) would inject syntactically broken Python and surface
            # as a NameError at sandbox runtime — fail early with a clear
            # TypeError here instead.
            sandbox_timeout = int(settings.advanced_features.sandbox_execution_timeout)

            complete_code = f"""
{call_api_helper}
{tools_code}
{variables_code}
{wrapped_code}

# Execute and capture locals
async def main():
    __result_locals = await asyncio.wait_for(_async_main(), timeout={sandbox_timeout})
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
        from cuga.backend.knowledge.run_context import issue_run_token

        run_context = getattr(tool_func, "_knowledge_run_context", None)
        credential_arg = (
            f", knowledge_run_token={issue_run_token(run_context)!r}" if run_context is not None else ""
        )
        allowed_scopes = tuple(getattr(tool_func, "_knowledge_allowed_scopes", ()))
        default_scope = getattr(tool_func, "_knowledge_default_scope", None)
        thread_id = getattr(tool_func, "_knowledge_thread_id", "") or ""
        positional_names = self._KNOWLEDGE_POSITIONAL_ARGS.get(tool_name, [])
        allowed_scopes_repr = repr(list(allowed_scopes))
        positional_names_repr = repr(positional_names)
        # Only emit a scope/thread_id block for tools that actually accept
        # those args. ``knowledge_get_ingestion_status`` and
        # ``knowledge_get_knowledge_status`` have no scope/thread params —
        # silently injecting them would TypeError inside the sandbox.
        tool_has_scope = "scope" in positional_names
        tool_has_thread_id = tool_name in self._KNOWLEDGE_THREAD_ID_TOOLS

        # Scope validation: emit at function level so the block has its own
        # indent contract and isn't structurally coupled to thread_id below.
        # Generator-time conditions (tool_has_scope, allowed_scopes,
        # tool_has_thread_id) ensure we only emit blocks for tools that
        # accept them — no runtime guard needed.
        default_scope_line = (
            f'        kwargs["scope"] = "{default_scope}"\n' if default_scope else "        pass\n"
        )
        scope_validation_block = (
            f"""    allowed_scopes = {allowed_scopes_repr}
    if "scope" not in kwargs:
{default_scope_line}    scope = kwargs.get("scope")
    if scope not in allowed_scopes:
        allowed_text = ", ".join(allowed_scopes)
        return {{"error": f"Knowledge scope '{{scope}}' is unavailable in this context. Allowed scopes: {{allowed_text}}"}}
"""
            if tool_has_scope and allowed_scopes
            else ""
        )
        # Identity scrub, emitted for every knowledge tool. The code that calls
        # these stubs is written by the model, so an agent_id/thread_id it
        # passes is a claim to be a different principal — name someone else's
        # agent or thread and you read, overwrite or delete their collection
        # (GHSA-ffh4-qv86-xfxv). Dropping them from the positional map stops the
        # positional form; this stops the keyword form.
        identity_scrub_block = """    kwargs.pop("agent_id", None)
    kwargs.pop("thread_id", None)
"""
        # Thread-id injection: also at function level, and unconditional now
        # that the scrub above guarantees nothing survives to defer to. The old
        # "only fill a blank value" form was what let a model-supplied thread
        # stand. Emitted with ``!r`` so the value lands as a Python literal
        # rather than being pasted into a quoted string.
        thread_id_block = (
            f'    kwargs["thread_id"] = {thread_id!r}\n'
            if thread_id and "session" in allowed_scopes and tool_has_thread_id
            else ""
        )

        return f"""async def {tool_name}(*args, **kwargs):
    \"\"\"Context-aware knowledge tool stub for remote execution.\"\"\"
    positional_names = {positional_names_repr}
    for name, value in zip(positional_names, args):
        kwargs.setdefault(name, value)
{scope_validation_block}{identity_scrub_block}{thread_id_block}    return await call_api("knowledge", "{tool_name}", kwargs{credential_arg})
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
