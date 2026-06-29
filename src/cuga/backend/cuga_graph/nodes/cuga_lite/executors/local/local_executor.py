import asyncio
import contextlib
import difflib
import io
import traceback
from typing import Any, Optional

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

    def format_error(self, error: Exception, available_tools: Optional[list[str]] = None) -> str:
        """Format an error for display.

        Args:
            error: The exception to format
            available_tools: Names of tools/functions actually present in the
                execution namespace. When given and the error is a ``NameError``
                for a tool-shaped name, the raw traceback is augmented with a
                correction listing the closest real tool names. This turns a
                silent retry-loop (the agent re-inventing the same bogus name
                until the step limit) into a single-step recovery.

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

        correction = self._unknown_tool_correction(error, available_tools)
        if correction:
            error_msg += correction
        return error_msg

    @staticmethod
    def _unknown_tool_correction(error: Exception, available_tools: Optional[list[str]]) -> str:
        """Build a correction hint when the agent calls a non-existent tool.

        The agent (notably gpt-oss) tends to fabricate generic tool names from
        its REST-API priors (e.g. ``get_countries_countries_get``) instead of
        using the exact name ``find_tools`` returned. The bare ``NameError`` is
        treated as an ordinary retryable error, so the agent re-fabricates until
        the step/token limit. Surfacing the real names breaks that loop.
        """
        if not isinstance(error, NameError) or not available_tools:
            return ""

        missing = getattr(error, "name", None)
        if not missing:
            return ""

        close = difflib.get_close_matches(missing, available_tools, n=5, cutoff=0.4)
        hint = (
            f"\n\n[tool-name correction] '{missing}' is NOT an available tool — tool names "
            "cannot be guessed or constructed. Call tools by the EXACT name returned by "
            "find_tools."
        )
        if close:
            hint += "\nDid you mean one of: " + ", ".join(close) + "?"
        else:
            hint += (
                "\nNo close match is loaded. Call find_tools again with a different query to "
                "discover the correct tool name."
            )
        hint += "\nDo not retry the same invented name."
        return hint
