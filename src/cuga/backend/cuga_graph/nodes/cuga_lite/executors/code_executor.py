from typing import Any, Dict, List, Literal, Optional

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.config import settings
from loguru import logger

from .common import SecurityValidator, CodeWrapper, VariableUtils, CallApiHelper
from .common.benchmark_mode import is_benchmark_mode
from .local import LocalExecutor, LocalSandboxExecutor
from .e2b import E2BExecutor
from .docker import DockerExecutor
from .opensandbox import OpenSandboxExecutor
from .native import NativeSandboxExecutor
from .base_executor import BaseExecutor, RemoteExecutor


def _skills_enabled() -> bool:
    return getattr(settings.skills, "enabled", False)


def is_find_tools_listing_markdown(value: Any) -> bool:
    """True if value is the markdown string produced for find_tools / matching-tool listings."""
    if not isinstance(value, str):
        return False
    return "# Found" in value and "Matching Tool(s)" in value and "**Query:**" in value


def _omit_find_tools_listing_vars(new_vars: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in new_vars.items() if not is_find_tools_listing_markdown(v)}


def format_execution_output(output: str, max_length: Optional[int] = None) -> str:
    """
    Format and trim execution output to prevent token overflow.

    Args:
        output: Raw execution output
        max_length: Maximum length for output (uses settings if None)

    Returns:
        Formatted execution output string
    """
    if max_length is None:
        max_length = settings.advanced_features.execution_output_max_length

    output_trimmed = output.strip()

    if len(output_trimmed) > max_length:
        return f"{output_trimmed[:max_length]}...\n\n[Output trimmed to {max_length} chars]"
    else:
        return output_trimmed


class CodeExecutor:
    """Unified interface for executing Python code with tools in different modes."""

    _local_executor: BaseExecutor = None
    _local_sandbox_executor: LocalSandboxExecutor = None
    _e2b_executor: RemoteExecutor = None
    _docker_executor: RemoteExecutor = None
    _opensandbox_executor: RemoteExecutor = None
    _native_executor: NativeSandboxExecutor = None

    @classmethod
    def _get_local_executor(cls) -> BaseExecutor:
        if cls._local_executor is None:
            cls._local_executor = LocalExecutor()
        return cls._local_executor

    @classmethod
    def _get_local_sandbox_executor(cls) -> LocalSandboxExecutor:
        if cls._local_sandbox_executor is None:
            cls._local_sandbox_executor = LocalSandboxExecutor()
        return cls._local_sandbox_executor

    @classmethod
    def _get_e2b_executor(cls) -> RemoteExecutor:
        if cls._e2b_executor is None:
            cls._e2b_executor = E2BExecutor()
        return cls._e2b_executor

    @classmethod
    def _get_docker_executor(cls) -> RemoteExecutor:
        if cls._docker_executor is None:
            cls._docker_executor = DockerExecutor()
        return cls._docker_executor

    @classmethod
    def _get_opensandbox_executor(cls) -> RemoteExecutor:
        if cls._opensandbox_executor is None:
            cls._opensandbox_executor = OpenSandboxExecutor()
        return cls._opensandbox_executor

    @classmethod
    def _get_native_executor(cls) -> NativeSandboxExecutor:
        if cls._native_executor is None:
            cls._native_executor = NativeSandboxExecutor()
        return cls._native_executor

    @classmethod
    async def eval_with_tools_async(
        cls,
        code: str,
        _locals: dict[str, Any],
        state: AgentState,
        thread_id: Optional[str] = None,
        apps_list: Optional[List[str]] = None,
        mode: Optional[Literal['local', 'e2b', 'opensandbox']] = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute code with async tools available in the local namespace.

        Args:
            code: Python code to execute
            _locals: Local variables/context for execution
            state: AgentState instance with variables_manager
            thread_id: Thread ID for sandbox caching (optional)
            apps_list: List of app names for parsing tool names correctly (optional)
            mode: Execution mode ('local', 'e2b', or 'opensandbox'). If None, uses settings.

        Returns:
            Tuple of (execution result, new variables dictionary)
        """
        original_keys = set(_locals.keys())
        result = ""

        if mode is None:
            mode = 'e2b' if settings.advanced_features.e2b_sandbox else 'local'

        # Force local execution for short find_tools or load_skill calls
        code_lines = [line.strip() for line in code.split('\n') if line.strip()]
        if len(code_lines) <= 3 and 'await find_tools' in code:
            mode = 'local'
        if _skills_enabled() and 'load_skill' in code:
            mode = 'local'

        # opensandbox: Python runs locally with run_command in context (forwarded to sandbox)
        # Security checks must run for every execution mode, including E2B turns.
        SecurityValidator.validate_imports(code)

        tracker = ActivityTracker()
        fake_datetime = tracker.current_date if tracker.current_date and is_benchmark_mode() else None
        wrapped_code = CodeWrapper.wrap_code(code, fake_datetime=fake_datetime)

        SecurityValidator.validate_wrapped_code(wrapped_code)

        try:
            if mode == 'e2b':
                executor = cls._get_e2b_executor()
                result, parsed_locals = await executor.execute_for_cuga_lite(
                    wrapped_code=wrapped_code,
                    context_locals=_locals,
                    state=state,
                    thread_id=thread_id,
                    apps_list=apps_list,
                )
                _locals.update(parsed_locals)
            else:
                executor = cls._get_local_executor()
                result = await executor.execute(
                    wrapped_code=wrapped_code,
                    context_locals=_locals,
                    timeout=30,
                )

        except Exception as e:
            executor = cls._get_local_executor()
            result = executor.format_error(e)

        # Variables that should always be included even if they existed before.
        # Task todos are not stored here — they are shown in the todos system prompt section.
        # find_tools `tools_output` is stripped below — discovery text is not kept as a variable.
        always_include_keys = {'result', 'results', 'output', 'outputs'}

        new_vars = VariableUtils.filter_new_variables(
            _locals, original_keys, always_include_keys=always_include_keys
        )

        if _skills_enabled():
            new_vars = VariableUtils.strip_todo_confirmation_only_vars(new_vars)

        new_vars = VariableUtils.strip_tools_output_var(new_vars, code)

        new_vars = VariableUtils.reorder_variables_by_print(new_vars, code)

        # TODO: Uncomment this when we have a way to handle single-letter variable names inside loops etc.
        # new_vars = VariableUtils.filter_single_letter_variables(new_vars)

        # Limit variables to keep based on configuration
        keep_last_n = settings.advanced_features.code_executor_keep_last_n
        new_vars = VariableUtils.limit_variables_to_keep(new_vars, keep_last_n)
        new_vars = _omit_find_tools_listing_vars(new_vars)

        # Format/trim the output before adding variables
        result = format_execution_output(result)

        # Add variables summary to the formatted output
        result = VariableUtils.add_variables_to_manager(
            new_vars,
            state.variables_manager,
            result,
            skip_summary_keys={'todos'},
        )

        return result, new_vars

    @classmethod
    def _wrap_code_for_code_agent(cls, code: str, fake_datetime: Optional[str] = None) -> str:
        """Wrap code for CodeAgent execution."""
        indented_code = '\n'.join('    ' + line for line in code.split('\n'))

        datetime_mock = CodeWrapper.create_datetime_mock(fake_datetime)

        wrapped_code = f"""
import asyncio
import json
{datetime_mock}
async def _async_main():
{indented_code}
    return locals()
"""
        SecurityValidator.validate_dangerous_modules(wrapped_code)
        return wrapped_code

    @classmethod
    def _prepare_locals_for_code_agent(cls, state: AgentState) -> dict[str, Any]:
        """Prepare local variables for CodeAgent execution."""
        # Build call_api function internally
        call_api_function = CallApiHelper.create_local_call_api_function()
        _locals = {'call_api': call_api_function}

        if state.variables_manager:
            for var_name in state.variables_manager.get_variable_names():
                var_value = state.variables_manager.get_variable(var_name)
                if var_value is not None:
                    _locals[var_name] = var_value

        return _locals

    @classmethod
    async def _execute_remotely_for_code_agent(
        cls, wrapped_code: str, state: AgentState, mode: Literal['e2b', 'docker', 'opensandbox']
    ) -> tuple[str, dict[str, Any]]:
        """Execute wrapped code in remote executor for CodeAgent."""
        try:
            if mode == 'e2b':
                executor = cls._get_e2b_executor()
            elif mode == 'opensandbox':
                executor = cls._get_opensandbox_executor()
            else:  # docker
                executor = cls._get_docker_executor()

            result = await executor.execute_for_code_agent(
                wrapped_code=wrapped_code,
                state=state,
                thread_id=state.thread_id if hasattr(state, 'thread_id') else None,
            )
            return result, {}
        except Exception as e:
            logger.error(f"Error executing code in {mode}: {e}")
            return f"Error during execution: {repr(e)}", {}

    @classmethod
    async def _execute_locally_for_code_agent(
        cls, wrapped_code: str, context_locals: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Execute wrapped code locally for CodeAgent."""
        try:
            executor = cls._get_local_executor()
            result = await executor.execute(
                wrapped_code=wrapped_code,
                context_locals=context_locals,
                timeout=30,
            )
            return result, {}
        except Exception as e:
            logger.error(f"Error executing code: {e}")
            executor = cls._get_local_executor()
            return executor.format_error(e), {}

    @classmethod
    async def eval_for_code_agent(
        cls,
        code: str,
        state: AgentState,
        mode: Optional[Literal['local', 'e2b', 'docker', 'opensandbox']] = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute code for CodeAgent - expects JSON output on last line only.

        This is different from eval_with_tools_async in that:
        1. Does NOT automatically capture all new variables
        2. Expects code to print JSON on last line: {"variable_name": "...", "description": "...", "value": ...}
        3. Progress prints are preserved in output
        4. Uses less restrictive security validation (allows dunder methods, etc)

        Args:
            code: Python code to execute
            state: AgentState instance with variables_manager
            mode: Execution mode ('local', 'e2b', 'docker', or 'opensandbox'). If None, uses settings.
            'opensandbox' is remapped to 'local' here because OpenSandboxExecutor.execute_for_code_agent is not implemented.

        Returns:
            Tuple of (execution result string, empty dict)
        """
        if mode is None:
            if _skills_enabled():
                if settings.advanced_features.e2b_sandbox:
                    mode = 'e2b'
                elif getattr(settings.advanced_features, 'opensandbox_sandbox', False):
                    mode = 'opensandbox'
                else:
                    mode = 'local'
            else:
                mode = 'e2b' if settings.advanced_features.e2b_sandbox else 'local'
        # When skills + opensandbox_sandbox we temporarily select opensandbox above; CodeAgent still
        # must not use OpenSandboxExecutor.execute_for_code_agent (it is not implemented — raises).
        # OpenSandbox remains for shell tools in CugaLite; CodeAgent Python runs locally like eval_with_tools_async.
        if mode == 'opensandbox':
            mode = 'local'

        tracker = ActivityTracker()
        fake_datetime = tracker.current_date if tracker.current_date and is_benchmark_mode() else None
        wrapped_code = cls._wrap_code_for_code_agent(code, fake_datetime=fake_datetime)

        if _skills_enabled():
            if mode in ('e2b', 'docker'):
                return await cls._execute_remotely_for_code_agent(wrapped_code, state, mode)
        else:
            if mode in ('e2b', 'docker'):
                return await cls._execute_remotely_for_code_agent(wrapped_code, state, mode)
        context_locals = cls._prepare_locals_for_code_agent(state)
        return await cls._execute_locally_for_code_agent(wrapped_code, context_locals)
