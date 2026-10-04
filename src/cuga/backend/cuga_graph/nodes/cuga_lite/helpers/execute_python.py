"""``execute_python`` — CodeAct as one native tool (``cuga_lite_execution_mode = "hybrid"``).

Hybrid mode is function calling plus code: the model calls tools natively for single
lookups and actions, and calls ``execute_python`` for anything iterative — paginating,
looping over items, filtering, counting, summing, acting on every element of a list —
which native calls do one round-trip at a time (the AppWorld challenge-easy tasks pure
function calling lost were almost all of that shape).

The code runs exactly where CodeAct code runs: ``CodeExecutor.eval_with_tools_async``
over the stored variables plus every tool in ``_tools_context`` — so tool calls made
from code get the same per-block / run / thread budgets and tracking, and new
variables persist in state. Every tool is callable from code whether or not it is
loaded natively (deferral only limits what is *bound*); long names are also exposed
under their ≤64-char wire names, the names the model sees.

Native tool results are saved as variables too, so a later ``execute_python`` block
can process data a native call already fetched without calling the tool again.
"""

from __future__ import annotations

import functools
import inspect
import re
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import wire_name
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.large_output import SAVED_OUTPUT_TOOL_NAMES

EXECUTE_PYTHON_NAME = "execute_python"

EXECUTE_PYTHON_DESCRIPTION = (
    "Run Python code with every tool available as an async function (`result = await tool_name(arg=...)`). "
    "Use it for anything iterative — paginating through results, looping over items, filtering, counting, "
    "summing, or acting on every item of a list — instead of many separate tool calls. Tools do not need to be "
    "loaded to be called from code. Variables from earlier steps (including saved results of earlier tool calls) "
    "are available by name, and variables you create persist. End with print() of what you need: printed output "
    "is what you get back. Print a sample of unfamiliar data before filtering on it; if the code fails or finds "
    "nothing, inspect, fix and rerun rather than concluding. A tool call that fails raises an exception whose message says why. To delete, move or change items of a paged list, fetch every page first and then act on the collected ids — changing the list while paging through it shifts items between pages and skips some. When the code computes content an action needs, call "
    "that action from the same code instead of retyping the data. Allowed imports: json, re, typing, datetime, asyncio."
)


#: Registered names of the tools the current ``execute_python`` block called (in order).
CODE_CALLS: ContextVar[Optional[List[str]]] = ContextVar("execute_python_calls", default=None)


class ToolCallError(Exception):
    """A tool called from ``execute_python`` failed; the message says why."""


def _failure_message(result: Any) -> Optional[str]:
    """The reason when ``result`` is a tool's failure reply, else ``None``.

    Registry tools report failures (argument validation, HTTP errors) by *returning*
    ``{"error": "<reason>"}`` rather than raising. A native call shows that dict to the
    model; code does not — it goes on to iterate the dict's keys and dies on an
    unrelated ``TypeError`` (AppWorld ``4d12842``: ``page_limit=100`` against a cap of
    20, five failed blocks, the validator's "page_limit must be <= 20" never seen).
    Only that exact shape counts, so a real payload with an ``error`` field among
    others is left alone.
    """
    if isinstance(result, dict) and len(result) == 1:
        reason = result.get("error")
        if isinstance(reason, str) and reason.strip():
            return reason
    return None


def _recording(name: str, fn: Any) -> Any:
    """Wrap a tool for code: record the call (tool_exec can't see calls made from code)
    and raise :class:`ToolCallError` on a failure reply, so the reason reaches the model."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls = CODE_CALLS.get()
        if calls is not None:
            calls.append(name)
        result = fn(*args, **kwargs)
        result = await result if inspect.isawaitable(result) else result
        reason = _failure_message(result)
        if reason is not None:
            raise ToolCallError(f"{wire_name(name)} failed: {reason}")
        return result

    return wrapper


class ExecutePythonInput(BaseModel):
    code: str = Field(..., description="Python code to run. Await tool calls; end with print().")


async def _placeholder(**_: Any) -> str:  # pragma: no cover - tool_exec dispatches by name
    raise RuntimeError("execute_python is dispatched by tool_exec, not called directly")


def create_execute_python_tool() -> StructuredTool:
    """The bindable ``execute_python`` tool (its execution lives in :func:`run_code`)."""
    return StructuredTool.from_function(
        coroutine=_placeholder,
        name=EXECUTE_PYTHON_NAME,
        description=EXECUTE_PYTHON_DESCRIPTION,
        args_schema=ExecutePythonInput,
    )


def code_context(state: Any, tools_context: Dict[str, Any]) -> Dict[str, Any]:
    """Stored variables + callable tools, with ≤64-char wire-name aliases for long names."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import is_find_tools_listing_markdown

    context: Dict[str, Any] = {}
    for name in list(state.variables_manager.get_variable_names()):
        value = state.variables_manager.get_variable(name)
        if not is_find_tools_listing_markdown(value):
            context[name] = value
    for name, fn in tools_context.items():
        if (
            name.startswith("_")
            or name == EXECUTE_PYTHON_NAME
            or name in SAVED_OUTPUT_TOOL_NAMES
            or not callable(fn)
        ):
            continue
        wrapped = _recording(name, fn)
        context[name] = wrapped
        alias = wire_name(name)
        if alias != name:
            context.setdefault(alias, wrapped)
    return context


async def run_code(
    state: Any,
    code: str,
    tools_context: Dict[str, Any],
    *,
    thread_id: Optional[str] = None,
    apps_list: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, Any], List[str]]:
    """Execute ``code`` like the CodeAct sandbox; returns ``(output, new_vars, called_tools)``
    and stores new vars.

    The wall clock is ``cuga_lite_execute_python_timeout`` (90 s), not the sandbox's 30 s
    block limit: this is the tool for loops over many API calls, and a timeout halfway
    through a batch of writes leaves the job half done.
    """
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionRouter
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import is_find_tools_listing_markdown
    from cuga.config import settings

    calls: List[str] = []
    token = CODE_CALLS.set(calls)
    try:
        output, new_vars = await CodeExecutor.eval_with_tools_async(
            code=code,
            _locals=code_context(state, tools_context),
            state=state,
            thread_id=thread_id,
            apps_list=apps_list,
            plan=ExecutionRouter.resolve(settings),
            timeout=execute_python_timeout(),
        )
    finally:
        CODE_CALLS.reset(token)
    for name, value in (new_vars or {}).items():
        if is_find_tools_listing_markdown(value):
            continue
        state.variables_manager.add_variable(value, name=name, description="Created during code execution")
    return output, new_vars or {}, calls


def execute_python_timeout() -> float:
    """Seconds an ``execute_python`` block may run (falls back to the sandbox block limit)."""
    from cuga.config import settings

    features = settings.advanced_features
    value = getattr(features, "cuga_lite_execute_python_timeout", None)
    return float(value or getattr(features, "sandbox_execution_timeout", 30) or 30)


_IDENT = re.compile(r"[^0-9a-zA-Z_]+")


def save_native_result(state: Any, tool_name: str, result: Any) -> Optional[str]:
    """Store a native tool result as a variable (``<tool>_result``, numbered on repeats).

    Best effort: a result that cannot be stored never affects the call's reply.
    """
    try:
        base = _IDENT.sub("_", wire_name(tool_name)).strip("_")[:48] or "tool"
        base = f"{base}_result"
        existing = set(state.variables_manager.get_variable_names())
        name, n = base, 2
        while name in existing:
            name, n = f"{base}_{n}", n + 1
        return state.variables_manager.add_variable(
            result, name=name, description=f"Result of native call {tool_name}"
        )
    except Exception as exc:  # never let bookkeeping break a tool reply
        logger.debug(f"could not save native result of {tool_name}: {exc}")
        return None
