import asyncio
import contextlib
import io
import traceback
from typing import Any

from ..base_executor import BaseExecutor
from ..common.restricted_environment import RestrictedEnvironment
from ..common.security import SecurityValidator
from ..common.benchmark_mode import is_benchmark_mode


class LocalExecutor(BaseExecutor):
    """Handles local code execution with restricted environment."""

    _timeout = 30

    ALLOWED_MODULES = {
        'asyncio',
        'json',
        'pandas',
        'numpy',
        'pydantic',
        'datetime',
        '_strptime',
        'time',
        'math',
        'collections',
        'itertools',
        'functools',
        're',
        'typing',
    }

    async def execute(
        self,
        wrapped_code: str,
        context_locals: dict[str, Any],
        timeout: int = 30,
    ) -> str:
        """Execute code locally in a restricted environment.

        Args:
            wrapped_code: Wrapped Python code to execute
            context_locals: Dictionary of variables and tools
            timeout: Execution timeout in seconds

        Returns:
            Execution result string

        Raises:
            asyncio.TimeoutError: If execution times out
            Exception: For any execution errors
        """
        self._timeout = timeout
        with contextlib.redirect_stdout(io.StringIO()) as f:
            benchmark_mode = is_benchmark_mode()

            restricted_import = RestrictedEnvironment.create_restricted_import(self.ALLOWED_MODULES)

            safe_builtins = RestrictedEnvironment.create_safe_builtins(restricted_import)

            # In benchmark mode, don't filter locals
            if benchmark_mode:
                safe_locals = context_locals
            else:
                safe_locals = SecurityValidator.filter_safe_locals(context_locals)

            restricted_globals = RestrictedEnvironment.create_restricted_globals(safe_builtins, safe_locals)

            SecurityValidator.assert_safe_globals(restricted_globals)

            if context_locals:
                SecurityValidator.validate_context_usage(wrapped_code, context_locals)

            exec_locals = {}
            exec(wrapped_code, restricted_globals, exec_locals)

            async_main = exec_locals['_async_main']
            result_locals = await asyncio.wait_for(async_main(), timeout=timeout)
            context_locals.update(result_locals)

        result = f.getvalue()
        if not result:
            result = "<code ran, no output printed to stdout>"

        return result

    def format_error(self, error: Exception) -> str:
        """Format an error for display.

        Args:
            error: The exception to format

        Returns:
            Formatted error string
        """
        if isinstance(error, asyncio.TimeoutError):
            return (
                f"Error during execution: Execution timed out after {self._timeout} seconds"
                + traceback.format_exc()
            )

        error_msg = f"Error during execution: {repr(error)}"
        error_msg += f"\n{traceback.format_exc()}"
        return error_msg
