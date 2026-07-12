import asyncio
import ast
import contextlib
import difflib
import io
import re
import traceback
from typing import Any, Optional

from ..base_executor import BaseExecutor
from ..common.restricted_environment import RestrictedEnvironment
from ..common.security import CodeSyntaxError, SecurityValidator
from ..common.benchmark_mode import is_relaxed_execution


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
            benchmark_mode = is_relaxed_execution()

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

    def format_error(
        self,
        error: Exception,
        available_tools: Optional[list[str]] = None,
        code: Optional[str] = None,
    ) -> str:
        """Format an error for display.

        Args:
            error: The exception to format
            available_tools: Names of tools/functions actually present in the
                execution namespace. When given and the error is a ``NameError``
                for a tool-shaped name, the raw traceback is augmented with a
                correction listing the closest real tool names. This turns a
                silent retry-loop (the agent re-inventing the same bogus name
                until the step limit) into a single-step recovery.
            code: The code that raised the error. Used to tell fabricated tool
                *calls* apart from plain undefined *variables* — the correction
                only fires when the missing name is invoked like a function, so
                a NameError on an unset variable keeps its bare traceback (the
                right fix there is to define the variable, not to re-query
                find_tools).

        Returns:
            Formatted error string
        """
        if isinstance(error, asyncio.TimeoutError):
            return (
                f"Error during execution: Execution timed out after {self._timeout} seconds"
                + traceback.format_exc()
            )

        if isinstance(error, CodeSyntaxError):
            return f"Error during execution: {error}"

        error_msg = f"Error during execution: {repr(error)}"
        error_msg += f"\n{traceback.format_exc()}"

        correction = self._unknown_tool_correction(error, available_tools, code)
        if correction:
            error_msg += correction
        return error_msg

    @staticmethod
    def _unknown_tool_correction(
        error: Exception,
        available_tools: Optional[list[str]],
        code: Optional[str] = None,
    ) -> str:
        """Build a correction hint when the agent calls a non-existent tool.

        The agent (notably gpt-oss) tends to fabricate generic tool names from
        its REST-API priors (e.g. ``get_countries_countries_get``) instead of
        using the exact name ``find_tools`` returned. The bare ``NameError`` is
        treated as an ordinary retryable error, so the agent re-fabricates until
        the step/token limit. Surfacing the real names breaks that loop.

        The hint is suppressed when the missing name is not called like a
        function in ``code`` — a NameError on a plain variable reference means
        the agent forgot to compute something, and tool guidance there would
        steer it away from the real fix. It is also suppressed when ``code``
        itself defines the name (def / class / assignment / import): that
        NameError is an ordering or self-reference bug in the agent's own code,
        not a fabricated tool name. Both checks work on the AST, so names
        inside string literals or comments never count as calls.
        """
        if not isinstance(error, NameError) or not available_tools:
            return ""

        missing = getattr(error, "name", None)
        if not missing:
            return ""

        if code is not None:
            called, defined = LocalExecutor._missing_name_usage(missing, code)
            if not called or defined:
                return ""

        close = difflib.get_close_matches(missing, available_tools, n=5, cutoff=0.6)
        if close:
            return (
                f"\n\n[tool-name correction] '{missing}' is NOT an available tool — tool names "
                "cannot be guessed or constructed. Call tools by the EXACT name returned by "
                "find_tools."
                "\nDid you mean one of: " + ", ".join(close) + "?"
                "\nSimilarly named tools can do very different things (e.g. delete vs get) — "
                "pick only a suggestion whose action matches your intent."
                "\nDo not retry the same invented name."
            )
        # No similar tool is loaded: the name may equally be an agent-written
        # helper whose definition did not make it into this execution, so do
        # not steer unconditionally toward find_tools.
        return (
            f"\n\n[tool-name correction] '{missing}' is not defined in this execution and is "
            "not an available tool."
            "\nIf it is a helper function you wrote, include its definition in this script "
            "before the call."
            "\nIf you meant a tool, do not guess names and do not substitute a similar-looking "
            "one from memory (similarly named tools can do very different things, e.g. delete "
            "vs get) — call find_tools to discover the exact name. Do not retry the same "
            "undefined name."
        )

    @staticmethod
    def _missing_name_usage(missing: str, code: str) -> tuple[bool, bool]:
        """Return ``(called, defined)`` for *missing* in *code*, via the AST.

        ``called`` — the name is invoked as a bare function (directly or under
        ``await``). ``defined`` — the code itself binds the name (function /
        class definition, assignment, or import). Code that raised a runtime
        ``NameError`` always parses; the text fallback covers callers that pass
        arbitrary snippets.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return bool(re.search(rf"\b{re.escape(missing)}\s*\(", code)), False

        called = False
        defined = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == missing:
                called = True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == missing:
                    defined = True
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if (alias.asname or alias.name.split(".")[0]) == missing:
                        defined = True
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == missing:
                defined = True
        return called, defined
