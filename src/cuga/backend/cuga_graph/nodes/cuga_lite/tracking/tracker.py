"""Tool Call Tracker

Tracks tool/API calls during agent execution for observability.
Uses contextvars for thread-safe tracking across async execution.

For custom tool providers, use the `tracked_tool` decorator:

    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool

    @tracked_tool(app_name="my_api")
    async def get_users(limit: int = 10) -> list:
        return await fetch_users(limit)
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypeVar
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.arguments import merge_tool_call_args

_tool_calls_context: contextvars.ContextVar[List[Dict[str, Any]]] = contextvars.ContextVar(
    "tool_calls", default=None
)

_tracking_enabled_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tracking_enabled", default=False
)

_timings_only_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tracking_timings_only", default=False
)

# Three nested tool-call budgets, widest to narrowest. Each is a mutable [count]
# box so increments made inside child tasks (e.g. under asyncio.wait_for) stay
# visible to the seeding context, like the calls list above.
#
# The two scopes match LangChain's ToolCallLimitMiddleware (thread_limit /
# run_limit) — "run" is one graph invocation, i.e. one user turn. The block
# scope has no analogue there because that middleware counts tool calls the
# model *requests*, while CugaLite's are made by generated code inside a single
# request. Same reason it cannot be reused here: our runaway is one code block.
#
# Only the two that do NOT reset are ceilings:
#
#   thread  max_tool_calls_per_thread  never reset      → conversation cost ceiling
#   run     max_tool_calls_per_run     reset each turn  → per-turn cost ceiling
#   block   max_tool_calls_per_block   reset each block → fail-fast latency guard
#
# The block budget is deliberately NOT a bound: exceeding it is recoverable, so
# the model reflects and writes another block with a fresh block budget. Without
# the run ceiling above it, 70 blocks (cuga_lite_max_steps) x 100 calls = 7,000
# calls would still get through — which is roughly the runaway that motivated
# this cap in the first place. It exists to break one hung loop in seconds and
# hand control back, not to bound spend.
#
# All three are distinct from _block_tool_calls_context below, which counts a
# single code block for *timeout evidence* and has no cap attached.
_tool_call_budget_context: contextvars.ContextVar[Optional[List[int]]] = contextvars.ContextVar(
    "tool_call_budget", default=None
)

_thread_tool_call_budget_context: contextvars.ContextVar[Optional[List[int]]] = contextvars.ContextVar(
    "thread_tool_call_budget", default=None
)

_block_tool_call_budget_context: contextvars.ContextVar[Optional[List[int]]] = contextvars.ContextVar(
    "block_tool_call_budget", default=None
)

# Holds a mutable counter dict so the count survives context copies:
# ``asyncio.wait_for`` runs each code block in a new Task whose context is a
# *copy* of the executor's, but the copy references the SAME dict, so
# increments made inside the block are visible to the executor afterwards.
_block_tool_calls_context: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "block_tool_calls", default=None
)

# Set while inside a call that has already been charged to the budget, so nested
# enforcement (a named tool whose implementation calls call_api) doesn't double-count.
_counting_tool_call_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "counting_tool_call", default=False
)

F = TypeVar("F", bound=Callable[..., Any])


class ToolCallBudgetExceeded(RuntimeError):
    """A tool call was refused because a tool-call budget is spent.

    Subclasses ``RuntimeError`` so existing handlers — notably ``CodeExecutor``,
    which turns in-code exceptions into execution output — keep working
    unchanged. ``scope`` says which of the three budgets fired, which is what
    decides whether the situation is recoverable (block) or terminal for the
    turn (run/thread).
    """

    scope: str = "run"


class BlockToolCallBudgetExceeded(ToolCallBudgetExceeded):
    """One code block exceeded ``max_tool_calls_per_block`` — recoverable.

    The run budget survives, so the model is expected to reflect and retry
    with a narrower loop. This is a latency guard, not a ceiling.
    """

    scope = "block"


class RunToolCallBudgetExceeded(ToolCallBudgetExceeded):
    """The turn exceeded ``max_tool_calls_per_run`` — terminal for this turn."""

    scope = "run"


class ThreadToolCallBudgetExceeded(ToolCallBudgetExceeded):
    """The conversation exceeded ``max_tool_calls_per_thread`` — terminal."""

    scope = "thread"


class BlockToolCallCounter:
    """Counts the tool calls made by a single code block.

    The executor calls :meth:`reset` at the start of every block; every tool
    invocation path (registry ``call_api``, local ``call_api`` helper,
    combined-provider tools) calls :meth:`increment` before doing any work.
    When a block is killed at ``sandbox_execution_timeout`` the executor reads
    :meth:`current_count` so it can tell the agent how far the block actually
    got, instead of reporting a bare timeout.
    """

    @staticmethod
    def reset() -> None:
        _block_tool_calls_context.set({"n": 0})

    @staticmethod
    def current_count() -> int:
        holder = _block_tool_calls_context.get()
        return holder["n"] if holder else 0

    @staticmethod
    def increment() -> None:
        holder = _block_tool_calls_context.get()
        if holder is None:
            # No block scope was opened (direct tool use outside the code
            # executor) — nothing to count.
            return
        holder["n"] += 1


class ToolCallTracker:
    """Context manager for tracking tool calls during execution."""

    @staticmethod
    def is_enabled() -> bool:
        """Check if tool call tracking is enabled for this execution context."""
        return _tracking_enabled_context.get()

    @staticmethod
    def start_tracking(enabled: bool = True, timings_only: bool = False) -> None:
        """Start a new tracking session.

        Args:
            enabled: Whether tracking should be enabled for this session
            timings_only: Record only tool name/app/duration — never arguments,
                results, or error payloads. Used when tracking is forced for
                run-receipt metrics rather than requested by the caller.
        """
        _tracking_enabled_context.set(enabled)
        _timings_only_context.set(timings_only if enabled else False)
        if enabled:
            _tool_calls_context.set([])
            logger.debug(f"Tool call tracking started (timings_only={timings_only})")

    @staticmethod
    def stop_tracking() -> List[Dict[str, Any]]:
        """Stop tracking and return collected tool calls."""
        if not ToolCallTracker.is_enabled():
            return []

        calls = _tool_calls_context.get()
        _tool_calls_context.set(None)
        _tracking_enabled_context.set(False)
        _timings_only_context.set(False)
        logger.debug(f"Tool call tracking stopped, collected {len(calls) if calls else 0} calls")
        return calls or []

    @staticmethod
    def record_call(
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any = None,
        app_name: Optional[str] = None,
        operation_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Record a tool call.

        Args:
            tool_name: Name of the tool as used by the agent
            arguments: Arguments passed to the tool
            result: Result returned by the tool
            app_name: Name of the app/server
            operation_id: Original OpenAPI operationId (if available)
            duration_ms: Duration of the call in milliseconds
            error: Error message if the call failed
        """
        if not ToolCallTracker.is_enabled():
            return

        calls = _tool_calls_context.get()
        if calls is None:
            return

        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.variable_utils import VariableUtils

        timings_only = _timings_only_context.get()
        record = {
            "name": tool_name,
            "arguments": None if timings_only else VariableUtils.sanitize_value(arguments),
            "result": None if timings_only else VariableUtils.sanitize_value(result),
            "app_name": app_name,
            "operation_id": operation_id,
            "timestamp": datetime.now().isoformat(),
            "duration_ms": duration_ms,
            "error": None if timings_only else error,
        }

        calls.append(record)

    @staticmethod
    def get_current_calls() -> List[Dict[str, Any]]:
        """Get the current list of tracked calls without stopping tracking."""
        if not ToolCallTracker.is_enabled():
            return []
        return _tool_calls_context.get() or []

    @staticmethod
    def seed_call_budget(used: int, thread_used: int = 0) -> None:
        """Start counting tool calls for the current execution context.

        ``used`` carries the count accumulated by earlier steps of the *turn*
        (``max_tool_calls_per_run``); ``thread_used`` the count accumulated by earlier
        turns of the *conversation* (``max_tool_calls_per_thread``, which
        ``prepare`` never resets).

        Clears any block budget left behind by a previous execution so a stale
        one can never be inherited; :meth:`seed_block_budget` opens the next.

        **Inherits instead of replacing inside a delegated call.** A child graph
        (``delegate_to_*``, sync ``spawn_agent``) runs its own sandbox on the
        caller's Task, and that sandbox seeds a budget too. Replacing the boxes
        there did two bad things: the child got a fresh, effectively unbounded
        budget, and the parent's counters were destroyed — ``ContextVar.set`` has
        no token to unwind, so the caller came back to the child's boxes. Seeding
        while already inside a counted call therefore keeps the caller's boxes,
        so the whole delegation tree charges one ceiling.
        """
        if _counting_tool_call_context.get() and _tool_call_budget_context.get() is not None:
            return
        _tool_call_budget_context.set([used])
        _thread_tool_call_budget_context.set([thread_used])
        _block_tool_call_budget_context.set(None)

    @staticmethod
    def seed_block_budget() -> None:
        """Open a fresh per-block budget. Called once per executed code block."""
        _block_tool_call_budget_context.set([0])

    @staticmethod
    def get_run_budget_used() -> int:
        """Tool calls made so far this turn (0 when no budget is active)."""
        box = _tool_call_budget_context.get()
        return box[0] if box else 0

    @staticmethod
    def get_thread_budget_used() -> int:
        """Tool calls made so far this conversation (0 when none is active)."""
        box = _thread_tool_call_budget_context.get()
        return box[0] if box else 0

    @staticmethod
    def get_block_budget_used() -> int:
        """Tool calls made so far by the current code block (0 when none)."""
        box = _block_tool_call_budget_context.get()
        return box[0] if box else 0

    @staticmethod
    def budget_exhausted() -> bool:
        """True when a *terminal* budget (turn or conversation) is spent.

        The block budget is excluded on purpose: it resets, so hitting it is
        recoverable and the turn must be allowed to continue. Callers use this
        to set ``tool_budget_exhausted`` on graph state, which ends the turn
        after one final synthesis pass.
        """
        from cuga.config import settings

        max_tool_calls_per_run = getattr(settings.advanced_features, "max_tool_calls_per_run", 256)
        max_per_thread = getattr(settings.advanced_features, "max_tool_calls_per_thread", 2000)
        if max_tool_calls_per_run and ToolCallTracker.get_run_budget_used() >= max_tool_calls_per_run:
            return True
        return bool(max_per_thread) and ToolCallTracker.get_thread_budget_used() >= max_per_thread

    @staticmethod
    def enforce_call_budget() -> None:
        """Count one tool call against all three budgets; raise once any is spent.

        Checked widest-first — conversation, then turn, then block — so the
        error carries the most consequential advice. A block breach tells the
        model to retry more narrowly; a turn or conversation breach tells it to
        stop calling tools and answer from what it has.

        No-op outside a seeded execution context (e.g. tool calls made outside
        the CugaLite sandbox loop), and each level is individually disabled by
        setting its limit to 0.

        Also a no-op when already inside a counted call: tools are wrapped by
        :func:`counted_tool_call` where the sandbox namespace is built, and a
        registry-backed tool then calls ``call_api`` internally, so counting at
        both boundaries would charge a single logical call twice.
        """
        if _counting_tool_call_context.get():
            return
        box = _tool_call_budget_context.get()
        if box is None:
            return
        from cuga.config import settings

        max_tool_calls_per_run = getattr(settings.advanced_features, "max_tool_calls_per_run", 256)
        max_per_thread = getattr(settings.advanced_features, "max_tool_calls_per_thread", 2000)
        max_per_block = getattr(settings.advanced_features, "max_tool_calls_per_block", 100)

        thread_box = _thread_tool_call_budget_context.get()
        block_box = _block_tool_call_budget_context.get()

        # Check before counting so rejected attempts never inflate the counters.
        if max_per_thread and thread_box is not None and thread_box[0] >= max_per_thread:
            raise ThreadToolCallBudgetExceeded(
                f"Tool call limit reached: this conversation has already made {max_per_thread} tool calls. "
                "Do not call any more tools — produce a final answer from the data already retrieved. "
                "(Configurable via advanced_features.max_tool_calls_per_thread; 0 disables.)"
            )
        if max_tool_calls_per_run and box[0] >= max_tool_calls_per_run:
            raise RunToolCallBudgetExceeded(
                f"Tool call limit reached: this run (one user turn) has already made {max_tool_calls_per_run} tool calls. "
                "Do not call any more tools — produce a final answer from the data already retrieved. "
                "(Configurable via advanced_features.max_tool_calls_per_run; 0 disables.)"
            )
        if max_per_block and block_box is not None and block_box[0] >= max_per_block:
            raise BlockToolCallBudgetExceeded(
                f"Tool call limit reached for this code block: it made {max_per_block} tool calls. "
                f"{ToolCallTracker._run_budget_remaining_hint(max_tool_calls_per_run, box[0])} "
                "Rewrite it to fetch less — batch, filter, or page fewer items — or answer from what "
                "you already have. (Configurable via advanced_features.max_tool_calls_per_block; 0 disables.)"
            )

        box[0] += 1
        if thread_box is not None:
            thread_box[0] += 1
        if block_box is not None:
            block_box[0] += 1

    @staticmethod
    def _run_budget_remaining_hint(max_tool_calls_per_run: int, used: int) -> str:
        """Tell the model the block breach is recoverable, and by how much."""
        if not max_tool_calls_per_run:
            return "The run budget is not exhausted, so you can still call tools."
        return f"The run budget still has {max(0, max_tool_calls_per_run - used)} calls left."


def thread_budget_exhausted(used: int) -> bool:
    """True when ``used`` has reached ``max_tool_calls_per_thread``.

    Read by both ``prepare`` nodes from the checkpointed
    ``tool_calls_used_thread``, so a conversation already over its ceiling opens
    the turn already exhausted rather than spending a step to find out.
    """
    from cuga.config import settings

    max_per_thread = getattr(settings.advanced_features, "max_tool_calls_per_thread", 2000)
    return bool(max_per_thread) and used >= max_per_thread


def counted_tool_call(awaitable_func: Callable[..., Any]) -> Callable[..., Any]:
    """Charge one budget unit per call of an already-awaitable tool function.

    ``call_api`` is only the choke point for registry-routed calls. Tools the
    sandbox invokes by name — MCP/SDK provider tools, direct LangChain tools,
    skills, filesystem/shell runtime tools, ``find_tools``, agent delegation —
    never pass through it, so without this they escape
    ``advanced_features.max_tool_calls_per_run`` entirely.

    Applied once, in ``CodeExecutor.eval_with_tools_async``, to every coroutine
    function in the namespace handed to generated code. That is the single point
    both the CugaLite and supervisor graphs share, so tools cannot escape the
    budget by being registered somewhere new.

    ``enforce_call_budget`` no-ops while nested, so a registry-backed tool that
    internally calls ``call_api`` is still charged exactly once.
    """

    # Idempotent. Now that each wrapper charges regardless of an outer counted
    # call, wrapping twice would charge one call twice — and a tool wrapped at a
    # registration site AND by the executor is exactly that. Before, the nested
    # guard absorbed the second layer; it deliberately no longer does.
    if getattr(awaitable_func, "_cuga_budget_counted", False):
        return awaitable_func

    @functools.wraps(awaitable_func)
    async def _counted(*args, **kwargs):
        # A wrapped tool is ALWAYS its own logical call, even when reached from
        # inside another counted call. The nested guard exists for exactly one
        # case — the un-wrapped ``call_api`` a registry-backed tool calls in its
        # own body — and must not extend to tools a *child graph* runs. Letting
        # it extend made every tool below a ``delegate_to_*`` / ``spawn_agent``
        # free: a child made 50 calls against a cap of 5.
        outer_nested = _counting_tool_call_context.get()
        _counting_tool_call_context.set(False)
        try:
            ToolCallTracker.enforce_call_budget()
            # Suppress only the body's own inner call_api, nothing deeper.
            _counting_tool_call_context.set(True)
            return await awaitable_func(*args, **kwargs)
        finally:
            _counting_tool_call_context.set(outer_nested)

    # Set after functools.wraps, which copies __dict__ from the wrapped function.
    _counted._cuga_budget_counted = True
    return _counted


def make_recording_awaitable(
    awaitable_func: Callable[..., Any],
    tool_name: str,
    app_name: Optional[str] = None,
    param_names: Optional[List[str]] = None,
) -> Callable[..., Any]:
    """Wrap an already-awaitable tool function so each call is recorded.

    Used for direct LangChain tools, which (unlike registry/combined provider
    tools) have no built-in recorder. Apply AFTER make_tool_awaitable so sync
    tools record in the event-loop context, where this tracker's contextvars
    are visible. Recording is a no-op unless a tracking session is active.

    ``param_names`` (from the tool's args schema) maps positional arguments to
    their real parameter names, so traces read the same as registry-tool traces.
    """

    @functools.wraps(awaitable_func)
    async def _recorded(*args, **kwargs):
        # Direct tools bypass the registry/combined call paths, so without this
        # they are missing from the per-block count the executor reports as
        # timeout evidence.
        BlockToolCallCounter.increment()
        start_time = time.time()
        result = None
        error_msg = None

        try:
            result = await awaitable_func(*args, **kwargs)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so it would skip `except
            # Exception` and be recorded in `finally` as a success with no
            # error — a cancelled/timed-out call must not look like one.
            error_msg = "cancelled"
            raise
        except Exception as e:
            error_msg = str(e)
            raise
        finally:
            duration_ms = (time.time() - start_time) * 1000
            ToolCallTracker.record_call(
                tool_name=tool_name,
                arguments=merge_tool_call_args(args, kwargs, param_names or []),
                result=result,
                app_name=app_name,
                operation_id=tool_name,
                duration_ms=duration_ms,
                error=error_msg,
            )

    return _recorded


def tracked_tool(
    _func: Optional[F] = None,
    *,
    app_name: Optional[str] = None,
) -> Callable[[F], F]:
    """Decorator to automatically track tool calls in custom tool providers.

    Use this decorator on tool functions to enable tracking when
    `track_tool_calls=True` is passed to `agent.invoke()`.

    Args:
        app_name: Optional name of the app/service this tool belongs to

    Example:
        ```python
        from cuga import tracked_tool

        # Simple usage - just add the decorator
        @tracked_tool
        def multiply(a: int, b: int) -> int:
            return a * b

        # With app_name for grouping
        @tracked_tool(app_name="calculator")
        def add(a: int, b: int) -> int:
            return a * b

        # Works with async functions too
        @tracked_tool(app_name="user_service")
        async def get_user(user_id: int) -> dict:
            return {"id": user_id, "name": "John"}

        # Can combine with LangChain @tool decorator
        from langchain_core.tools import tool

        @tool
        @tracked_tool(app_name="math")
        def divide(a: int, b: int) -> float:
            '''Divide two numbers'''
            return a / b
        ```

    The decorator automatically captures:
    - Tool name (from function name, used as operation_id)
    - Arguments passed to the tool
    - Result or error
    - Duration in milliseconds
    - Timestamp
    """

    def decorator(func: F) -> F:
        func_name = func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            result = None
            error_msg = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                error_msg = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                ToolCallTracker.record_call(
                    tool_name=func_name,
                    arguments=kwargs if kwargs else dict(zip(func.__code__.co_varnames, args)),
                    result=result,
                    app_name=app_name,
                    operation_id=func_name,
                    duration_ms=duration_ms,
                    error=error_msg,
                )

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            result = None
            error_msg = None

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error_msg = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                ToolCallTracker.record_call(
                    tool_name=func_name,
                    arguments=kwargs if kwargs else dict(zip(func.__code__.co_varnames, args)),
                    result=result,
                    app_name=app_name,
                    operation_id=func_name,
                    duration_ms=duration_ms,
                    error=error_msg,
                )

        # Mark so callers that add their own recording (e.g. prepare_node's
        # direct-tool wrapper) can avoid recording the same call twice.
        async_wrapper._cuga_tracked = True
        sync_wrapper._cuga_tracked = True

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    # Support both @tracked_tool and @tracked_tool() syntax
    if _func is not None:
        return decorator(_func)
    return decorator
