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

EXECUTE_PYTHON_NAME = "execute_python"

EXECUTE_PYTHON_DESCRIPTION = (
    "Run Python code with every tool available as an async function (`result = await tool_name(arg=...)`). "
    "Use it for anything iterative — paginating through results, looping over items, filtering, counting, "
    "summing, or acting on every item of a list — instead of many separate tool calls. Tools do not need to be "
    "loaded to be called from code. Variables from earlier steps (including saved results of earlier tool calls) "
    "are available by name, and variables you create persist. End with print() of what you need: printed output "
    "is what you get back. Print a sample of unfamiliar data before filtering on it; if the code fails or finds "
    "nothing, inspect, fix and rerun rather than concluding. When the code computes content an action needs, call "
    "that action from the same code instead of retyping the data. Allowed imports: json, re, typing, datetime, asyncio."
)


#: Registered names of the tools the current ``execute_python`` block called (in order).
CODE_CALLS: ContextVar[Optional[List[str]]] = ContextVar("execute_python_calls", default=None)


def _recording(name: str, fn: Any) -> Any:
    """Wrap a tool so calls from code are recorded — tool_exec can't see them otherwise."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls = CODE_CALLS.get()
        if calls is not None:
            calls.append(name)
        result = fn(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

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
        if name.startswith("_") or name == EXECUTE_PYTHON_NAME or not callable(fn):
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
    and stores new vars."""
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
        )
    finally:
        CODE_CALLS.reset(token)
    for name, value in (new_vars or {}).items():
        if is_find_tools_listing_markdown(value):
            continue
        state.variables_manager.add_variable(value, name=name, description="Created during code execution")
    return output, new_vars or {}, calls


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
