"""Monty executor: runs model-written Python in Pydantic Monty instead of the CUGA process.

[Pydantic Monty](https://pydantic.dev/docs/monty/) is a Python interpreter written in Rust. Code it runs has
no access to files, environment variables, the network, or processes; it can only call the host functions it
is given. Here those host functions are the agent's tools, so tools are the only way out of the sandbox.

The executor receives the same input as ``LocalExecutor``: ``wrapped_code`` (``async def _async_main(): ...``
from ``CodeWrapper``) and ``context_locals`` (tools plus variables from earlier blocks). It runs the model's
code once in a Monty worker process, then reads the block's variables back in a separate step, so a value
Monty cannot convert never forces the code to run twice.

Monty runs a subset of Python: no third-party packages (pandas, numpy), no generators, no class inheritance,
and a partial standard library; ``asyncio.sleep`` does not block. Values that Monty cannot represent (custom
objects, modules) are not passed in. ``pydantic-monty`` is an optional dependency: ``pip install "cuga[monty]"``.
"""

from __future__ import annotations

import ast
import asyncio
import datetime as _dt
import inspect
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from ..base_executor import BaseExecutor
from ..local.local_executor import LocalExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    BlockToolCallCounter,
    ToolCallBudgetExceeded,
)

# Name of the dict the executor builds after a block to read its variables back.
_VARS_NAME = "_cuga_vars"
# CodeWrapper adds this name when benchmark mode freezes the clock; Monty cannot run that shim.
_FROZEN_CLOCK_MARKER = "_cuga_ft"
_NO_OUTPUT = "<code ran, no output printed to stdout>"
# Memory cap for one block, counted by the Monty worker's allocator.
MAX_MEMORY_BYTES = 512 * 1024 * 1024
# Upper bound for reading variables back after a block.
_READ_BACK_TIMEOUT = 10.0
_PLAIN_SCALARS = (type(None), bool, int, float, str, bytes, _dt.date, _dt.time, _dt.timedelta)
_NAME_ERROR = re.compile(r"name '([^']+)' is not defined")


class MontyNotInstalledError(ImportError):
    """``advanced_features.monty_sandbox`` is on but ``pydantic-monty`` is not installed."""


class _ExitRequested(Exception):
    """Raised by the ``exit()`` / ``quit()`` host functions; Monty has no such builtins."""


def unwrap_code(wrapped_code: str) -> str:
    """Return the model's code from ``CodeWrapper`` output.

    Keeps the wrapper's module-level imports (``asyncio``, ``json``) and the body of ``_async_main`` without
    its trailing ``return locals()``. ``from __future__`` imports are dropped: Monty does not need them.
    """
    tree = ast.parse(wrapped_code)
    imports: List[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.append(node)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_main":
            body = list(node.body)
            if body and isinstance(body[-1], ast.Return):
                body = body[:-1]
            return ast.unparse(ast.Module(body=[*imports, *body] or [ast.Pass()], type_ignores=[]))
    return wrapped_code


def assigned_names(code: str) -> List[str]:
    """Top-level names a block assigns, in order. Names inside functions and lambdas are not included."""
    names: List[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return  # comprehension variables have their own scope
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id not in names:
            names.append(node.id)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in ast.parse(code).body:
        visit(stmt)
    return [n for n in names if not n.startswith("_")]


def capture_suffix(names: List[str]) -> str:
    """Code that collects ``names`` into one dict, skipping names the block never assigned."""
    lines = [f"{_VARS_NAME} = {{}}"]
    for name in names:
        lines += ["try:", f"    {_VARS_NAME}[{name!r}] = {name}", "except NameError:", "    pass"]
    lines.append(_VARS_NAME)
    return "\n".join(lines)


def is_plain(value: Any, depth: int = 0) -> bool:
    """True when Monty can receive ``value`` as an input: scalars, dates, and lists/tuples/sets/dicts of them."""
    if depth > 20:
        return False
    if isinstance(value, _PLAIN_SCALARS):
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(is_plain(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(is_plain(k, depth + 1) and is_plain(v, depth + 1) for k, v in value.items())
    return False


def split_context(context_locals: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """Split ``context_locals`` into host functions (tools), Monty inputs, and names that cannot be passed."""
    tools: Dict[str, Any] = {}
    inputs: Dict[str, Any] = {}
    skipped: List[str] = []
    for name, value in context_locals.items():
        if name.startswith("_"):
            continue
        if callable(value) and not inspect.isclass(value) and not inspect.ismodule(value):
            tools[name] = value
        elif is_plain(value):
            inputs[name] = value
        else:
            skipped.append(name)
    return tools, inputs, skipped


def _import_monty():
    try:
        import pydantic_monty
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise MontyNotInstalledError(
            'advanced_features.monty_sandbox is on, but pydantic-monty is not installed. Install it with: pip install "cuga[monty]"'
        ) from exc
    return pydantic_monty


def _exit_note(reason: str, captured: str) -> str:
    """Same wording as ``LocalExecutor`` when generated code calls ``exit()``."""
    note = "Block ended early: the code called exit()/quit() or raised SystemExit"
    note += f" ({reason}).\n" if reason else ".\n"
    note += (
        "This ended the block only — variables defined before it were kept and "
        "are available in the next block. Nothing after the exit ran.\n"
    )
    return f"{note}{captured}" if captured.strip() else f"{note}(no output printed)"


class MontyExecutor(BaseExecutor):
    """Execute wrapped CUGA code in a Pydantic Monty worker process."""

    def __init__(self, max_processes: int = 4) -> None:
        self._max_processes = max_processes
        self._pool: Optional[Any] = None
        self._pool_loop: Optional[asyncio.AbstractEventLoop] = None
        self._local = LocalExecutor()  # only for format_error, so errors read the same in both modes

    async def _get_pool(self):
        """One worker pool per event loop: a pool cannot be shared across loops."""
        loop = asyncio.get_running_loop()
        if self._pool is None or self._pool_loop is not loop:
            monty = _import_monty()
            pool = monty.AsyncMonty(min_processes=1, max_processes=self._max_processes)
            await pool.__aenter__()
            self._pool, self._pool_loop = pool, loop
        return self._pool

    async def execute(self, wrapped_code: str, context_locals: dict[str, Any], timeout: int = 30) -> str:
        if _FROZEN_CLOCK_MARKER in wrapped_code:
            raise RuntimeError(
                "The Monty sandbox cannot run the frozen clock that benchmark mode adds. "
                "Turn off advanced_features.monty_sandbox for benchmark runs."
            )
        monty = _import_monty()
        code = unwrap_code(wrapped_code)
        tools, inputs, skipped = split_context(context_locals)
        if skipped:
            logger.debug(f"Monty sandbox: not passing values Monty cannot represent: {', '.join(skipped)}")
        # Read back what the block assigned, plus the inputs: the block may have changed them in place.
        names = list(dict.fromkeys([*assigned_names(code), *inputs]))

        # Tool errors cross the sandbox as plain exceptions; remember the ones whose type matters.
        budget_errors: List[ToolCallBudgetExceeded] = []
        exit_requests: List[str] = []

        def as_host_function(fn):
            # Keep the tool's kind: model code awaits async tools and calls plain helpers directly.
            if inspect.iscoroutinefunction(fn):

                async def call_async(*args, **kwargs):
                    try:
                        return await fn(*args, **kwargs)
                    except ToolCallBudgetExceeded as exc:
                        budget_errors.append(exc)
                        raise

                return call_async

            def call(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except ToolCallBudgetExceeded as exc:
                    budget_errors.append(exc)
                    raise

            return call

        def request_exit(*args):
            exit_requests.append(str(args[0]) if args else "")
            raise _ExitRequested("exit() was called")

        lookup = {"exit": request_exit, "quit": request_exit}
        lookup.update({name: as_host_function(fn) for name, fn in tools.items()})

        chunks: List[str] = []
        BlockToolCallCounter.reset()
        pool = await self._get_pool()
        limits = {"max_memory": MAX_MEMORY_BYTES, "max_feed_duration_secs": float(timeout)}
        async with pool.checkout(limits=limits) as session:
            try:
                await asyncio.wait_for(
                    session.feed_run(
                        code + "\nNone",  # the block's own result is never converted back
                        inputs=inputs or None,
                        external_lookup=lookup,
                        print_callback=lambda _stream, text: chunks.append(text),
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                partial = "".join(chunks)
                message = (
                    f"Error during execution: Execution timed out after {timeout} seconds.\n"
                    f"This code block started {BlockToolCallCounter.current_count()} tool call(s) before it was stopped. "
                    "Do NOT rerun the same code — restructure it or process a smaller batch per block.\n"
                )
                return message + (
                    f"Partial stdout before the timeout:\n{partial}"
                    if partial.strip()
                    else "(no stdout was printed before the timeout)"
                )
            except monty.MontyRuntimeError as exc:
                inner = exc.exception()
                if exit_requests or isinstance(inner, SystemExit):
                    # exit() ends the block, not the run: keep what was computed before it.
                    context_locals.update(await self._read_back(session, names, monty))
                    reason = exit_requests[0] if exit_requests else (str(inner) if inner is not None else "")
                    return _exit_note("" if reason.isdigit() else reason, "".join(chunks))
                if budget_errors:
                    # A refused tool call is not a bug in the block: keep what it computed before the refusal.
                    context_locals.update(await self._read_back(session, names, monty))
                    error: BaseException = budget_errors[-1]
                else:
                    error = inner if inner is not None else RuntimeError(exc.display())
                    if isinstance(error, NameError) and getattr(error, "name", None) is None:
                        match = _NAME_ERROR.search(str(error))
                        if match:
                            error.name = match.group(1)  # format_error uses it to suggest the right tool
                self._attach_stdout(error, chunks)
                raise error from None
            except monty.MontySyntaxError as exc:
                raise SyntaxError(str(exc)) from None
            except monty.MontyError as exc:
                error = RuntimeError(f"{type(exc).__name__}: {exc}")
                self._attach_stdout(error, chunks)
                raise error from None
            context_locals.update(await self._read_back(session, names, monty))
        output = "".join(chunks)
        return output if output else _NO_OUTPUT

    @staticmethod
    async def _read_back(session: Any, names: List[str], monty: Any) -> Dict[str, Any]:
        """Read the block's variables from the Monty session. Values that cannot be converted are skipped."""
        if not names:
            return {}
        try:
            values = await asyncio.wait_for(
                session.feed_run(capture_suffix(names)), timeout=_READ_BACK_TIMEOUT
            )
            return values if isinstance(values, dict) else {}
        except monty.MontyError:
            pass  # one value could not be converted: read them one by one
        values: Dict[str, Any] = {}
        for name in names:
            try:
                values[name] = await asyncio.wait_for(session.feed_run(name), timeout=_READ_BACK_TIMEOUT)
            except monty.MontyError:
                logger.debug(f"Monty sandbox: could not read back variable {name!r}")
        return values

    @staticmethod
    def _attach_stdout(error: BaseException, chunks: List[str]) -> None:
        captured = "".join(chunks)
        if captured:
            try:
                error.captured_stdout = captured  # type: ignore[attr-defined]
            except AttributeError:
                pass

    def format_error(
        self, error: Exception, available_tools: Optional[list[str]] = None, code: Optional[str] = None
    ) -> str:
        return self._local.format_error(error, available_tools=available_tools, code=code)

    async def close(self) -> None:
        """Stop the worker processes."""
        if self._pool is not None:
            pool, self._pool, self._pool_loop = self._pool, None, None
            await pool.__aexit__(None, None, None)
