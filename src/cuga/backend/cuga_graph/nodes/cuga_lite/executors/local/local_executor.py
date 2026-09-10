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
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import BlockToolCallCounter


class _BlockSystemExit:
    """Carrier so exit()/SystemExit inside a Task does not escape the event loop."""

    __slots__ = ("exc", "locals")

    def __init__(self, exc: BaseException, locals_: dict[str, Any]):
        self.exc = exc
        self.locals = locals_


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
        stdout_buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_buf):
                benchmark_mode = is_relaxed_execution()

                restricted_import = RestrictedEnvironment.create_restricted_import(self.ALLOWED_MODULES)

                safe_builtins = RestrictedEnvironment.create_safe_builtins(restricted_import)

                # In benchmark mode, don't filter locals
                if benchmark_mode:
                    safe_locals = context_locals
                else:
                    safe_locals = SecurityValidator.filter_safe_locals(context_locals)

                restricted_globals = RestrictedEnvironment.create_restricted_globals(
                    safe_builtins, safe_locals
                )

                SecurityValidator.assert_safe_globals(restricted_globals)

                if context_locals:
                    SecurityValidator.validate_context_usage(wrapped_code, context_locals)

                exec_locals = {}
                exec(wrapped_code, restricted_globals, exec_locals)

                async_main = exec_locals['_async_main']
                BlockToolCallCounter.reset()

                # Run as a Task so a timeout can read the still-live frame before
                # cancelling. ``wait_for`` on a bare coroutine clears that frame,
                # which is why variables computed before the stall used to vanish.
                #
                # SystemExit inside a Task is re-raised into the event loop by
                # asyncio (it escapes ``except SystemExit`` around ``wait``), so
                # catch it inside the task and return a carrier instead.
                async def _run_block():
                    try:
                        return await async_main()
                    except SystemExit as e:
                        return _BlockSystemExit(e, LocalExecutor._locals_from_frame(e, "_async_main"))

                task = asyncio.create_task(_run_block())
                done, _pending = await asyncio.wait({task}, timeout=timeout)
                if not done:
                    recovered = self._locals_from_coro(task.get_coro(), "_async_main")
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                    context_locals.update(recovered)
                    # Preserve the evidence instead of discarding it: the agent
                    # otherwise sees a bare timeout, learns nothing, and re-runs
                    # the same loop until the step limit.
                    partial_stdout = stdout_buf.getvalue()
                    calls_made = BlockToolCallCounter.current_count()
                    kept = sorted(k for k in recovered if not callable(recovered[k]))
                    if kept:
                        kept_note = (
                            f"kept variables from this block: {', '.join(kept)} "
                            "(available in the next block).\n"
                        )
                    else:
                        kept_note = "no variables from this block were saved.\n"
                    guidance = (
                        f"Error during execution: Execution timed out after {timeout} seconds.\n"
                        f"This code block started {calls_made} tool call(s) before it was killed; "
                        f"{kept_note}"
                        "Do NOT rerun the same code — it will time out again. Restructure instead: "
                        "use a bulk/aggregate tool (find_tools), or process a small batch of items "
                        "per code block and store partial progress in a variable.\n"
                    )
                    if partial_stdout.strip():
                        guidance += f"Partial stdout before the timeout:\n{partial_stdout}"
                    else:
                        guidance += "(no stdout was printed before the timeout)"
                    return guidance
                outcome = task.result()
                if isinstance(outcome, _BlockSystemExit):
                    # `exit()` is a reasonable thing for generated code to reach —
                    # "stop this block, there is nothing more to do". Honour that
                    # intent: end the block early and keep variables computed
                    # before the exit (#629 / #630).
                    context_locals.update(outcome.locals)
                    reason = str(outcome.exc) if str(outcome.exc) and not str(outcome.exc).isdigit() else ""
                    note = "Block ended early: the code called exit()/quit() or raised SystemExit"
                    note += f" ({reason}).\n" if reason else ".\n"
                    note += (
                        "This ended the block only — variables defined before it were kept and "
                        "are available in the next block. Nothing after the exit ran.\n"
                    )
                    captured = stdout_buf.getvalue()
                    return f"{note}{captured}" if captured.strip() else f"{note}(no output printed)"
                context_locals.update(outcome)
        except SystemExit as e:
            # Safety net for SystemExit raised outside the task (e.g. during
            # setup). Task-scoped exit()/SystemExit is handled via _BlockSystemExit.
            context_locals.update(self._locals_from_frame(e, "_async_main"))
            reason = str(e) if str(e) and not str(e).isdigit() else ""
            note = "Block ended early: the code called exit()/quit() or raised SystemExit"
            note += f" ({reason}).\n" if reason else ".\n"
            note += (
                "This ended the block only — variables defined before it were kept and "
                "are available in the next block. Nothing after the exit ran.\n"
            )
            captured = stdout_buf.getvalue()
            return f"{note}{captured}" if captured.strip() else f"{note}(no output printed)"
        except Exception as e:
            # Preserve prints from earlier successful lines so the agent still
            # sees discovery output (e.g. find_tools) when a later line raises.
            captured = stdout_buf.getvalue()
            if captured:
                e.captured_stdout = captured  # type: ignore[attr-defined]
            raise

        result = stdout_buf.getvalue()
        if not result:
            result = "<code ran, no output printed to stdout>"

        return result

    @staticmethod
    def _locals_from_frame(error: BaseException, func_name: str) -> dict[str, Any]:
        """Read a still-live frame's locals off an exception's traceback.

        Used when the block stopped somewhere other than its own ``return
        locals()`` — the frame is kept alive by the traceback, so the variables
        computed up to that point are still readable and need not be discarded.
        """
        frame = None
        tb = error.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_name == func_name:
                frame = tb.tb_frame
            tb = tb.tb_next
        if frame is None:
            return {}
        return {k: v for k, v in frame.f_locals.items() if not k.startswith("__")}

    @staticmethod
    def _locals_from_coro(coro: Any, func_name: str) -> dict[str, Any]:
        """Read locals from a still-suspended coroutine frame.

        Used on the timeout path: the Task is stalled at an ``await``, so its
        frame is live until we cancel. Must be called before cancellation —
        afterwards the frame is cleared and recovery returns nothing. Walks
        ``cr_await`` so a thin wrapper around ``_async_main`` still works.
        """
        seen: set[int] = set()
        while coro is not None and id(coro) not in seen:
            seen.add(id(coro))
            frame = getattr(coro, "cr_frame", None)
            while frame is not None:
                if frame.f_code.co_name == func_name:
                    return {k: v for k, v in frame.f_locals.items() if not k.startswith("__")}
                frame = frame.f_back
            nxt = getattr(coro, "cr_await", None)
            coro = nxt if asyncio.iscoroutine(nxt) else None
        return {}

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
                *usage* apart from plain undefined *variables* — the correction
                fires when the missing name is invoked like a function or bound
                on an assignment RHS (alias pattern), so a NameError on an unset
                variable keeps its bare traceback (the right fix there is to
                define the variable, not to re-query find_tools).

        Returns:
            Formatted error string
        """
        if isinstance(error, asyncio.TimeoutError):
            error_msg = (
                f"Error during execution: Execution timed out after {self._timeout} seconds"
                + traceback.format_exc()
            )
            captured_stdout = getattr(error, "captured_stdout", None)
            if captured_stdout:
                error_msg = f"Output before error:\n{captured_stdout.rstrip()}\n\n{error_msg}"
            return error_msg

        if isinstance(error, CodeSyntaxError):
            return f"Error during execution: {error}"

        error_msg = f"Error during execution: {repr(error)}"
        error_msg += f"\n{traceback.format_exc()}"

        captured_stdout = getattr(error, "captured_stdout", None)
        if captured_stdout:
            error_msg = f"Output before error:\n{captured_stdout.rstrip()}\n\n{error_msg}"

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

        The hint is suppressed when the missing name is neither called like a
        function nor bound on an assignment RHS in ``code`` — a NameError on a
        plain variable reference (e.g. ``print(x)``) means the agent forgot to
        compute something, and tool guidance there would steer it away from the
        real fix. Assignment aliases like ``send_email = fabricated_tool_name``
        do count, since that is a common fabricated-tool pattern. The hint is
        also suppressed when ``code`` itself defines the name (def / class /
        assignment target / import): that NameError is an ordering or
        self-reference bug in the agent's own code, not a fabricated tool name.
        Both checks work on the AST, so names inside string literals or comments
        never count as usage.
        """
        if not isinstance(error, NameError) or not available_tools:
            return ""

        missing = getattr(error, "name", None)
        if not missing:
            return ""

        if code is not None:
            used, defined = LocalExecutor._missing_name_usage(missing, code)
            if not used or defined:
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
        """Return ``(used, defined)`` for *missing* in *code*, via the AST.

        ``used`` — the name is invoked as a bare function (directly or under
        ``await``) or appears as the value of an assignment / annotated
        assignment (alias pattern). ``defined`` — the code itself binds the
        name as a target (function / class definition, assignment target, or
        import). Code that raised a runtime ``NameError`` always parses; the
        text fallback covers callers that pass arbitrary snippets.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return bool(re.search(rf"\b{re.escape(missing)}\s*\(", code)), False

        used = False
        defined = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == missing:
                used = True
            elif (
                isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id == missing
            ):
                used = True
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.value, ast.Name)
                and node.value.id == missing
            ):
                used = True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == missing:
                    defined = True
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if (alias.asname or alias.name.split(".")[0]) == missing:
                        defined = True
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == missing:
                defined = True
        return used, defined
