from typing import Any, Dict, List, Literal, Optional

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.config import settings
from loguru import logger

from .common import SecurityValidator, CodeWrapper, VariableUtils, CallApiHelper
from .common.benchmark_mode import is_benchmark_mode
from .local import LocalExecutor
from .e2b import E2BExecutor
from .docker import DockerExecutor
from .base_executor import BaseExecutor, RemoteExecutor


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
    _e2b_executor: RemoteExecutor = None
    _docker_executor: RemoteExecutor = None

    @classmethod
    def _get_local_executor(cls) -> BaseExecutor:
        """Get or create local executor instance."""
        if cls._local_executor is None:
            cls._local_executor = LocalExecutor()
        return cls._local_executor

    @classmethod
    def _get_e2b_executor(cls) -> RemoteExecutor:
        """Get or create E2B executor instance."""
        if cls._e2b_executor is None:
            cls._e2b_executor = E2BExecutor()
        return cls._e2b_executor

    @classmethod
    def _get_docker_executor(cls) -> RemoteExecutor:
        """Get or create Docker executor instance."""
        if cls._docker_executor is None:
            cls._docker_executor = DockerExecutor()
        return cls._docker_executor

    @classmethod
    async def eval_with_tools_async(
        cls,
        code: str,
        _locals: dict[str, Any],
        state: AgentState,
        thread_id: Optional[str] = None,
        apps_list: Optional[List[str]] = None,
        mode: Optional[Literal['local', 'e2b']] = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute code with async tools available in the local namespace.

        Args:
            code: Python code to execute
            _locals: Local variables/context for execution
            state: AgentState instance with variables_manager
            thread_id: Thread ID for E2B sandbox caching (optional)
            apps_list: List of app names for parsing tool names correctly (optional)
            mode: Execution mode ('local' or 'e2b'). If None, uses settings.

        Returns:
            Tuple of (execution result, new variables dictionary)
        """
        original_keys = set(_locals.keys())
        result = ""

        if mode is None:
            mode = 'e2b' if settings.advanced_features.e2b_sandbox else 'local'

        # Force local execution for short find_tools calls
        code_lines = [line.strip() for line in code.split('\n') if line.strip()]
        if len(code_lines) <= 3 and 'await find_tools' in code:
            mode = 'local'

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

        # Variables that should always be included even if they existed before
        # This ensures stateful variables get updated when reassigned across execution steps
        # Includes:
        # - 'todos': Stateful task list that gets updated incrementally
        # - Generic output variables: Common names for final results that may be reassigned
        always_include_keys = {'todos', 'result', 'results', 'output', 'outputs'}

        new_vars = VariableUtils.filter_new_variables(
            _locals, original_keys, always_include_keys=always_include_keys
        )

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
        cls, wrapped_code: str, state: AgentState, mode: Literal['e2b', 'docker']
    ) -> tuple[str, dict[str, Any]]:
        """Execute wrapped code in remote executor for CodeAgent."""
        try:
            if mode == 'e2b':
                executor = cls._get_e2b_executor()
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
        mode: Optional[Literal['local', 'e2b', 'docker']] = None,
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
            mode: Execution mode ('local', 'e2b', or 'docker'). If None, uses settings.

        Returns:
            Tuple of (execution result string, empty dict)
        """
        if mode is None:
            mode = 'e2b' if settings.advanced_features.e2b_sandbox else 'local'

        tracker = ActivityTracker()
        fake_datetime = tracker.current_date if tracker.current_date and is_benchmark_mode() else None
        wrapped_code = cls._wrap_code_for_code_agent(code, fake_datetime=fake_datetime)

        if mode in ('e2b', 'docker'):
            return await cls._execute_remotely_for_code_agent(wrapped_code, state, mode)
        else:
            context_locals = cls._prepare_locals_for_code_agent(state)
            return await cls._execute_locally_for_code_agent(wrapped_code, context_locals)
