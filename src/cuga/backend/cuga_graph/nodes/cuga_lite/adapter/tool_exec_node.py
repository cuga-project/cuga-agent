"""``tool_exec`` node — executes native ``tool_calls`` in function-calling mode.

The counterpart of ``sandbox_node`` for ``cuga_lite_execution_mode =
"function_calling"``: where the sandbox runs a generated Python block, this
node runs the ``tool_calls`` the model attached to its last ``AIMessage`` and
replies to each with a ``ToolMessage`` carrying the call's exact
``tool_call_id``, so the conversation stays replayable to the provider.

Guard envelope
--------------
The budgets and the tracker are *not* on the callables in ``_tools_context`` —
they are applied by ``eval_with_tools_async`` and the sandbox node. A node that
merely called the callable would bypass the per-run / per-thread / per-block
tool-call caps and the run receipt, exactly the bug #560 shipped with on the
supervisor graph. So this node mirrors the sandbox node's envelope in the same
order: ``start_tracking`` -> ``seed_call_budget`` -> ``seed_block_budget`` ->
each call under ``counted_tool_call`` and a timeout -> ``stop_tracking`` ->
``_budget_updates()`` on every exit.

Every outcome is a ``ToolMessage``, never a raised exception: unknown tool,
malformed arguments, a tool exception, a timeout, or a spent budget. One
failing call does not abort its siblings, and every id the model issued —
including provider-side ``invalid_tool_calls`` — receives a reply.
"""

from __future__ import annotations

import asyncio
import difflib
import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from loguru import logger
from pydantic import BaseModel, ValidationError

from cuga.backend.activity_tracker.tracker import Step
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import TODOS_TOOL_NAME
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    append_chat_messages_with_step_limit as core_append_with_step_limit,
    create_error_command as core_create_error_command,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import _budget_updates
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import (
    DeferredToolSurface,
    discovered_in_history,
    merge_ordered,
    surface_from_meta,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.tool_rpc import SHELL_TOOLS
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import (
    CODE_CALLS,
    EXECUTE_PYTHON_NAME,
    describe_shape,
    run_code,
    save_native_result,
    shell_tools,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.large_output import (
    GREP_SAVED_NAME,
    QUERY_SAVED_OUTPUT_NAME,
    READ_SAVED_NAME,
    handle_large_result,
    large_output_enabled,
    large_output_threshold,
    query_saved_output,
    run_saved_file_tool,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import (
    WRITE_CHECK_BLOCKED,
    WRITE_CHECK_KEY,
    before_write_enabled,
    check_before_write,
    guard_code_tools,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_effects import page_note
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import (
    TOOL_SEARCH_NAME,
    ToolSearchResult,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_validation_errors import format_validation_error
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import (
    EXECUTION_MODE_HYBRID,
    STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
    resolve_execution_mode,
    resolve_step_discipline,
    resolved_runtime_model_name,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    ToolCallBudgetExceeded,
    ToolCallTracker,
    counted_tool_call,
)
from cuga.backend.tools_env.registry.utils.schema_model import (
    VALIDATION_MODE_OFF,
    validation_mode_from_settings,
)
from cuga.config import settings

DEFERRED_CALL_MESSAGE = (
    "Deferred: step discipline is on, so only the first tool call of a turn is attempted. "
    "Read that result, then re-issue this call in your next turn if you still need it."
)

TRUNCATION_MARKER = "\n... [result truncated: {limit} characters of output remain for this batch]"

#: What ``format_execution_output`` appends when it trims an ``execute_python`` block's output.
CODE_TRIM_MARKER = "[Output trimmed to "


def _tool_call_parts(call: Any) -> tuple[str, str, Any]:
    """``(id, name, args)`` from a LangChain tool_call dict or a raw OpenAI-shaped one."""
    if not isinstance(call, dict):
        return "", "", None
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    call_id = str(call.get("id") or "")
    name = str(call.get("name") or fn.get("name") or "")
    args = call.get("args") if "args" in call else fn.get("arguments")
    return call_id, name, args


def _coerce_args(args: Any) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return ``(kwargs, error)``. Strings are JSON-decoded; anything else is rejected."""
    if args is None:
        return {}, None
    if isinstance(args, dict):
        return args, None
    if isinstance(args, str):
        text = args.strip()
        if not text:
            return {}, None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return (
                None,
                f"Could not parse tool arguments as JSON ({exc}). Re-issue the call with valid JSON arguments.",
            )
        if isinstance(parsed, dict):
            return parsed, None
        return None, f"Tool arguments must be a JSON object, got {type(parsed).__name__}."
    return None, f"Tool arguments must be an object, got {type(args).__name__}."


def _stringify(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return str(result)


class _BatchOutputBudget:
    """One turn's batch of results shares ``execution_output_max_length``, like one block.

    Per-result truncation alone would let N calls return N x the limit; this keeps
    the whole batch under the ceiling the sandbox applies to a block's output.
    """

    def __init__(self, limit: Any):
        self.limit = int(limit or 0)
        self.remaining = self.limit

    def take(self, text: str) -> str:
        if not self.limit:
            return text
        if len(text) > self.remaining:
            cap = max(self.remaining, 0)
            text = text[:cap] + TRUNCATION_MARKER.format(limit=cap)
        self.remaining = max(self.remaining - len(text), 0)
        return text


def _validate_args(
    adapter: Any, name: str, kwargs: Dict[str, Any], display_name: Optional[str] = None
) -> Optional[str]:
    """Readable validation error for ``kwargs``, or ``None`` when they pass (or can't be checked).

    Rejection only: the tool still receives the model's original ``kwargs``, and
    registry tools re-validate (and coerce) with the same model inside. So a tool
    whose ``args_schema`` is looser than its function never changes behaviour
    here, and ``cuga_lite_tool_validation = "off"`` restores the old path exactly.
    """
    if validation_mode_from_settings() == VALIDATION_MODE_OFF:
        return None
    by_name = (getattr(adapter, "_tools_context_ref", None) or {}).get(
        "_lc_bind_tools_structured_by_name"
    ) or {}
    schema = getattr(by_name.get(name), "args_schema", None)
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        return None
    try:
        schema.model_validate(kwargs)
    except ValidationError as exc:
        return format_validation_error(display_name or name, exc, list(schema.model_fields))
    except Exception as exc:  # a broken schema must never block the call
        logger.debug(f"tool_exec: skipping validation for {name}: {exc}")
    return None


def _collect_step(adapter: Any, name: str, data: Dict[str, Any]) -> None:
    """Trajectory step for eval analysis; tracker failures never affect the call."""
    try:
        adapter._tracker.collect_step(
            step=Step(name=name, data=json.dumps(data, ensure_ascii=False, default=str))
        )
    except Exception as exc:
        logger.debug(f"tool_exec tracker error: {exc}")


def _variable_updates(state: Any) -> Dict[str, Any]:
    """The variable-store fields a node must return after changing variables (as the sandbox does)."""
    return {
        "variables_storage": state.variables_storage,
        "variable_counter_state": state.variable_counter_state,
        "variable_creation_order": state.variable_creation_order,
    }


async def _run_execute_python(
    adapter: Any,
    raw_args: Any,
    *,
    call_id: str,
    hybrid: Dict[str, Any],
    large_output: Optional[Dict[str, Any]] = None,
    write_check: Optional[Dict[str, Any]] = None,
    earlier: Optional[List[ToolMessage]] = None,
) -> ToolMessage:
    """Hybrid mode: run the block in the CodeAct sandbox executor and reply with its output."""
    kwargs, arg_error = _coerce_args(raw_args)
    code = (kwargs or {}).get("code") if not arg_error else None
    if not isinstance(code, str) or not code.strip():
        return _error_message(
            arg_error
            or (
                "execute_python arrived with no `code`, so nothing ran. Send the whole program as the "
                "`code` string argument, or call the tool you meant natively."
            ),
            call_id=call_id,
            name=EXECUTE_PYTHON_NAME,
        )
    tools_context = adapter._tools_context
    blocked: List[str] = []
    if write_check is not None:
        # Commit calls made from the code get the same pre-write check as native ones.
        tools_context = guard_code_tools(
            adapter._tools_context,
            (getattr(adapter, "_tools_context_ref", None) or {}).get("_lc_bind_tools_structured_by_name")
            or {},
            code=code,
            messages=[*write_check["messages"], *(earlier or [])],
            model=write_check["model"],
            invoke_config=write_check["invoke_config"],
            tracker=getattr(adapter, "_tracker", None),
            blocked=blocked,
        )
    discovered: List[str] = []
    try:
        output, new_vars, called = await run_code(
            hybrid["state"],
            code,
            tools_context,
            thread_id=hybrid.get("thread_id"),
            apps_list=hybrid.get("apps_list"),
            discovered=discovered,
        )
    except ToolCallBudgetExceeded as exc:
        return _error_message(str(exc), call_id=call_id, name=EXECUTE_PYTHON_NAME)
    except Exception as exc:
        return _error_message(f"execute_python failed: {exc}", call_id=call_id, name=EXECUTE_PYTHON_NAME)
    _collect_step(adapter, "Assistant_code", {"code": code, "new_variables": sorted(new_vars)})
    text = (output or "").strip() or "(no output — end the code with print() of what you need)"
    if large_output is not None and CODE_TRIM_MARKER in text:
        text = await _searched_code_output(text, new_vars, kwargs, call_id=call_id, large_output=large_output)
    # ``called``: tools the code ran — the did-anything check reads it (code calls
    # never appear as ToolMessages of their own).
    artifact: Dict[str, Any] = {"code_calls": called}
    if blocked:
        artifact[WRITE_CHECK_KEY] = blocked
    if discovered:
        # Tools the code found with tool_search: bound natively from the next step.
        artifact["discovered"] = discovered
    if new_vars:
        # What the block left behind, for the stub that replaces this reply once it is old.
        artifact["variables"] = {name: describe_shape(value) for name, value in list(new_vars.items())[:20]}
    return ToolMessage(content=text, tool_call_id=call_id, name=EXECUTE_PYTHON_NAME, artifact=artifact)


async def _searched_code_output(
    text: str, new_vars: Dict[str, Any], args: Any, *, call_id: str, large_output: Dict[str, Any]
) -> str:
    """A block printed something too large and the executor trimmed it: search the variable instead.

    The printed text is already cut, but the value behind it is the largest new
    variable, whole. Its trimmed print is replaced by the saved file plus the search
    subagent's findings; the variables summary after the trim marker is kept.
    """
    sized = [(len(_stringify(v)), name, v) for name, v in (new_vars or {}).items()]
    size, var_name, value = max(sized, key=lambda item: item[0], default=(0, None, None))
    if var_name is None or size <= large_output["threshold"]:
        return text
    persisted = await handle_large_result(
        value,
        tool_name=f"{EXECUTE_PYTHON_NAME} (variable `{var_name}`)",
        call_id=call_id,
        thread_id=large_output["thread_id"],
        messages=large_output["messages"],
        args=args,
        model=large_output["model"],
        invoke_config=large_output["invoke_config"],
        saved_as=var_name,
    )
    if persisted is None:
        return text
    _, _, after = text.partition(CODE_TRIM_MARKER)
    _, _, summary = after.partition("]")
    return f"{persisted.text}\n{summary.strip()}".strip()


async def _run_query_saved_output(
    adapter: Any, raw_args: Any, *, call_id: str, large_output: Dict[str, Any]
) -> ToolMessage:
    """A follow-up question about a result this thread saved; a meta call, never budgeted."""
    kwargs, arg_error = _coerce_args(raw_args)
    if not arg_error:
        arg_error = _validate_args(adapter, QUERY_SAVED_OUTPUT_NAME, kwargs)
    if arg_error:
        return _error_message(arg_error, call_id=call_id, name=QUERY_SAVED_OUTPUT_NAME)
    text, ok = await query_saved_output(
        str(kwargs.get("file") or ""),
        str(kwargs.get("question") or ""),
        thread_id=large_output["thread_id"],
        messages=large_output["messages"],
        model=large_output["model"],
        invoke_config=large_output["invoke_config"],
    )
    _collect_step(adapter, "Query_saved_output", {"arguments": kwargs, "ok": ok})
    if not ok:
        return _error_message(text, call_id=call_id, name=QUERY_SAVED_OUTPUT_NAME)
    return ToolMessage(content=text, tool_call_id=call_id, name=QUERY_SAVED_OUTPUT_NAME)


def _run_saved_file_tool(
    adapter: Any, name: str, raw_args: Any, *, call_id: str, large_output: Dict[str, Any]
) -> ToolMessage:
    """main_agent mode: the agent's own grep/read over a saved result; meta calls, never budgeted."""
    kwargs, arg_error = _coerce_args(raw_args)
    if not arg_error:
        arg_error = _validate_args(adapter, name, kwargs)
    if arg_error:
        return _error_message(arg_error, call_id=call_id, name=name)
    text, ok = run_saved_file_tool(name, kwargs, thread_id=large_output["thread_id"])
    _collect_step(adapter, "Saved_output_search", {"tool": name, "arguments": kwargs, "ok": ok})
    if not ok:
        return _error_message(text, call_id=call_id, name=name)
    return ToolMessage(content=text, tool_call_id=call_id, name=name)


def _unknown_tool_text(adapter: Any, name: str, surface: Optional[DeferredToolSurface]) -> str:
    """With tool search, never list the catalogue (hundreds of names): suggest close ones."""
    known = sorted(k for k in adapter._tools_context if not k.startswith("_"))
    if surface is None:
        return f"Unknown tool '{name}'. Choose one of the provided tools: {', '.join(known) or '(none)'}."
    close = [
        surface.to_wire(n) for n in difflib.get_close_matches(surface.to_real(name), known, n=5, cutoff=0.5)
    ]
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    return f"Unknown tool '{name}'.{hint} Use {TOOL_SEARCH_NAME} to find the right tool."


async def _run_tool_search(
    adapter: Any, raw_args: Any, *, call_id: str, timeout: Optional[float]
) -> ToolMessage:
    fn = adapter._tools_context.get(TOOL_SEARCH_NAME)
    kwargs, arg_error = _coerce_args(raw_args)
    if arg_error or fn is None:
        return _error_message(
            arg_error or "tool_search is unavailable.", call_id=call_id, name=TOOL_SEARCH_NAME
        )
    validation_error = _validate_args(adapter, TOOL_SEARCH_NAME, kwargs)
    if validation_error:
        return _error_message(validation_error, call_id=call_id, name=TOOL_SEARCH_NAME)
    try:
        result = await asyncio.wait_for(fn(**kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        return _error_message(
            f"tool_search timed out after {timeout}s. Try select:<name> or fewer keywords.",
            call_id=call_id,
            name=TOOL_SEARCH_NAME,
        )
    except Exception as exc:
        return _error_message(f"tool_search failed: {exc}", call_id=call_id, name=TOOL_SEARCH_NAME)
    if isinstance(result, ToolSearchResult):
        logger.info("[fc] tool_search stage={} loaded={}", result.stage, result.discovered)
        _collect_step(adapter, "Tool_search", {"arguments": kwargs, **result.artifact()})
        return ToolMessage(
            content=result.text, tool_call_id=call_id, name=TOOL_SEARCH_NAME, artifact=result.artifact()
        )
    return ToolMessage(content=_stringify(result), tool_call_id=call_id, name=TOOL_SEARCH_NAME)


TODOS_UPDATED_REPLY = (
    "Plan updated: {total} step(s) — {completed} completed, {in_progress} in progress, {pending} pending. "
    "It is shown under Current Plan; keep the statuses current as you work."
)


async def _run_update_todos(
    adapter: Any, raw_args: Any, *, call_id: str, timeout: Optional[float]
) -> ToolMessage:
    """``create_update_todos`` as a native call: bookkeeping, not work.

    Like free-code's TodoWrite it touches no external system, so — as with
    ``tool_search`` — it is never deferred by step discipline, never charged to the
    tool-call budgets, and never saved as a hybrid variable. The plan itself reaches the
    model through the run state (``prepare_turn_context``), so the reply is one line.
    """
    fn = adapter._tools_context.get(TODOS_TOOL_NAME)
    kwargs, arg_error = _coerce_args(raw_args)
    if arg_error or fn is None:
        return _error_message(
            arg_error or "create_update_todos is unavailable.", call_id=call_id, name=TODOS_TOOL_NAME
        )
    validation_error = _validate_args(adapter, TODOS_TOOL_NAME, kwargs)
    if validation_error:
        return _error_message(validation_error, call_id=call_id, name=TODOS_TOOL_NAME)
    awaitable = fn if inspect.iscoroutinefunction(fn) else make_tool_awaitable(fn)
    try:
        result = await asyncio.wait_for(awaitable(**kwargs), timeout=timeout)
    except Exception as exc:
        return _error_message(f"create_update_todos failed: {exc}", call_id=call_id, name=TODOS_TOOL_NAME)
    # The awaitable wrapper returns the tool's output model_dump'ed; accept either shape.
    items = result.get("todos") if isinstance(result, dict) else getattr(result, "todos", None)
    statuses = [
        str((t.get("status") if isinstance(t, dict) else getattr(t, "status", "")) or "").strip().lower()
        for t in (items or [])
    ]
    _collect_step(adapter, "Todos_updated", {"todos": kwargs.get("todos")})
    return ToolMessage(
        content=TODOS_UPDATED_REPLY.format(
            total=len(statuses),
            completed=statuses.count("completed"),
            in_progress=statuses.count("in_progress"),
            pending=len(statuses) - statuses.count("completed") - statuses.count("in_progress"),
        ),
        tool_call_id=call_id,
        name=TODOS_TOOL_NAME,
    )


RUN_COMMAND_NAME = "run_command"


def _shell_bridge(
    adapter: Any,
    kwargs: Dict[str, Any],
    *,
    write_check: Optional[Dict[str, Any]],
    earlier: List[ToolMessage],
) -> Dict[str, Any]:
    """The tools a native ``run_command`` (hybrid) lets its command call back into.

    The same set ``execute_python`` gets — recorded, failures raised — with commit tools
    behind the pre-write check, so a script cannot place an order the check would block.
    """
    blocked: List[str] = []
    tools_context = adapter._tools_context
    if write_check is not None:
        tools_context = guard_code_tools(
            adapter._tools_context,
            (getattr(adapter, "_tools_context_ref", None) or {}).get("_lc_bind_tools_structured_by_name")
            or {},
            code=str(kwargs.get("cmd") or ""),
            messages=[*write_check["messages"], *earlier],
            model=write_check["model"],
            invoke_config=write_check["invoke_config"],
            tracker=getattr(adapter, "_tracker", None),
            blocked=blocked,
        )
    return {"tools": shell_tools(tools_context), "calls": [], "blocked": blocked}


def _error_message(text: str, *, call_id: str, name: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id, name=name or "unknown", status="error")


def create_tool_exec_node(adapter: Any) -> Callable:
    async def tool_exec(state: Any, config: Optional[RunnableConfig] = None):
        configurable = config.get("configurable", {}) if config else {}
        from cuga.backend.cuga_graph.utils.langfuse_tracing import sync_langfuse_callbacks_from_config

        sync_langfuse_callbacks_from_config(config)
        track_tool_calls = configurable.get("track_tool_calls", False)
        max_steps = configurable.get("cuga_lite_max_steps") if "cuga_lite_max_steps" in configurable else None
        model_name = resolved_runtime_model_name(
            configurable_llm=configurable.get("llm"), graph_default_model=getattr(adapter, "_model", None)
        )
        one_per_step = resolve_step_discipline(configurable, model_name) == STEP_DISCIPLINE_ONE_TOOL_PER_STEP
        hybrid = resolve_execution_mode(configurable, model_name) == EXECUTION_MODE_HYBRID
        # One batch is the function-calling analogue of one code block, so the
        # outer bound per call is the block wall clock. Registry tools keep their
        # own inner ``tool_call_timeout``.
        timeout = getattr(settings.advanced_features, "sandbox_execution_timeout", 30) or None
        output_budget = _BatchOutputBudget(
            getattr(settings.advanced_features, "execution_output_max_length", 0)
        )

        messages = adapter.get_messages(state)
        last = messages[-1] if messages else None
        calls: List[Any] = (
            list(getattr(last, "tool_calls", None) or []) if isinstance(last, AIMessage) else []
        )
        invalid: List[Any] = (
            list(getattr(last, "invalid_tool_calls", None) or []) if isinstance(last, AIMessage) else []
        )

        # ── Guard envelope (mirrors sandbox_node, same order) ──────────────
        ToolCallTracker.start_tracking(
            enabled=bool(track_tool_calls),
            timings_only=track_tool_calls == "timings_only",
        )
        ToolCallTracker.seed_call_budget(
            getattr(state, "tool_calls_used_run", 0),
            getattr(state, "tool_calls_used_thread", 0),
        )
        # One turn's batch of calls is the function-calling analogue of one code block.
        ToolCallTracker.seed_block_budget()

        # Deferred loading (tool_search active): what the model has loaded so far.
        surface = surface_from_meta(getattr(adapter, "_tools_context_ref", None))
        discovered = (
            set(
                merge_ordered(
                    surface.seed,
                    getattr(state, "discovered_tools", None) or [],
                    discovered_in_history(messages, surface.deferred),
                )
            )
            if surface is not None
            else set()
        )

        thread_id = configurable.get("thread_id") or getattr(state, "thread_id", None)
        large_output = (
            {
                "threshold": large_output_threshold(),
                "model": configurable.get("llm") or getattr(adapter, "_model", None),
                "invoke_config": (
                    adapter.get_invoke_config(configurable) if hasattr(adapter, "get_invoke_config") else {}
                ),
                "thread_id": thread_id,
                "messages": messages,
            }
            if large_output_enabled()
            else None
        )

        write_check = (
            {
                "model": configurable.get("llm") or getattr(adapter, "_model", None),
                "invoke_config": (
                    adapter.get_invoke_config(configurable) if hasattr(adapter, "get_invoke_config") else {}
                ),
                "messages": messages,
            }
            if before_write_enabled(configurable)
            else None
        )

        results: List[ToolMessage] = []
        executed = 0
        execution_tool_calls: List[Dict[str, Any]] = []
        try:
            try:
                executed = await _run_batch(
                    adapter,
                    calls,
                    invalid,
                    results,
                    one_per_step=one_per_step,
                    timeout=timeout,
                    output_budget=output_budget,
                    surface=surface,
                    discovered=discovered,
                    hybrid=(
                        {
                            "state": state,
                            "thread_id": thread_id,
                            "apps_list": configurable.get("apps_list"),
                        }
                        if hybrid
                        else None
                    ),
                    large_output=large_output,
                    write_check=write_check,
                )
            finally:
                execution_tool_calls = ToolCallTracker.stop_tracking()

            if not calls and not invalid:
                # Nothing executable: advance the step and hand back to the model so a
                # mis-route can never become an infinite no-op loop.
                logger.warning("tool_exec entered with no tool_calls on the last message")
                return Command(
                    goto="call_model",
                    update={"step_count": state.step_count + 1, "script": None, **_budget_updates()},
                )

            try:
                adapter._tracker.collect_step(
                    step=Step(
                        name="Tool_results",
                        data=json.dumps(
                            [
                                {
                                    "tool_call_id": m.tool_call_id,
                                    "name": m.name,
                                    "status": m.status,
                                    "content": m.content,
                                }
                                for m in results
                            ],
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                )
            except Exception as exc:
                logger.debug(f"tool_exec tracker error: {exc}")

            updated_messages, error_message = core_append_with_step_limit(adapter, state, results, max_steps)
            accumulated_tool_calls = (state.tool_calls or []) + (
                execution_tool_calls if track_tool_calls else []
            )

            if error_message:
                return core_create_error_command(
                    adapter,
                    updated_messages,
                    error_message,
                    state.step_count,
                    additional_updates={"tool_calls": accumulated_tool_calls, **_budget_updates()},
                )

            logger.info(
                "[fc] tool_exec: {} call(s) -> {} ToolMessage(s), {} executed",
                len(calls),
                len(results),
                executed,
            )
            update = {
                adapter.messages_key: updated_messages,
                "script": None,
                "step_count": state.step_count + 1,
                "tool_calls": accumulated_tool_calls,
                **_budget_updates(),
            }
            if hybrid:
                # execute_python blocks and saved native results changed the variable store.
                update.update(_variable_updates(state))
            if surface is not None:
                newly = [n for n in discovered_in_history(results, surface.deferred) if n not in discovered]
                if newly:
                    update["discovered_tools"] = newly  # union_ordered reducer appends
            return Command(goto="call_model", update=update)
        except Exception as exc:
            # Mirrors the sandbox's outer guard: a failure outside the per-call
            # handling must still report the budget the batch spent, or the
            # checkpoint keeps the pre-batch counts (see _budget_updates).
            logger.error(f"tool_exec failed: {exc}")
            # Tools may already have run: keep their replies, and answer every id
            # the batch never reached, so the persisted turn has no dangling call.
            answered = {m.tool_call_id for m in results}
            replies = list(results)
            for index, call in enumerate(list(calls) + list(invalid)):
                call_id, name, _ = _tool_call_parts(call)
                call_id = call_id or f"call_{index}"
                if call_id not in answered:
                    answered.add(call_id)
                    replies.append(
                        _error_message(
                            f"Not executed: tool execution failed before this call ran ({exc}).",
                            call_id=call_id,
                            name=name,
                        )
                    )
            return core_create_error_command(
                adapter,
                adapter.get_messages(state) + replies,
                AIMessage(content=f"Error during tool execution: {exc}"),
                state.step_count,
                additional_updates={
                    "tool_calls": list(state.tool_calls or [])
                    + (execution_tool_calls if track_tool_calls else []),
                    **_budget_updates(),
                },
            )

    return tool_exec


async def _run_batch(
    adapter: Any,
    calls: List[Any],
    invalid: List[Any],
    results: List[ToolMessage],
    *,
    one_per_step: bool,
    timeout: Optional[float],
    output_budget: _BatchOutputBudget,
    surface: Optional[DeferredToolSurface] = None,
    discovered: Optional[set] = None,
    hybrid: Optional[Dict[str, Any]] = None,
    large_output: Optional[Dict[str, Any]] = None,
    write_check: Optional[Dict[str, Any]] = None,
) -> int:
    """Run one turn's calls in order, appending one ``ToolMessage`` per id to ``results``.

    Returns how many calls actually executed. Every outcome is a message, never
    an exception, so one failing call cannot take its siblings down.
    """
    executed = 0
    budget_stop: Optional[str] = None
    discovered = discovered if discovered is not None else set()
    attempted = 0  # real (non tool_search) calls reached so far, for step discipline
    for index, call in enumerate(calls):
        call_id, name, raw_args = _tool_call_parts(call)
        call_id = call_id or f"call_{index}"
        if not name:
            results.append(_error_message("Tool call carried no tool name.", call_id=call_id, name=""))
            continue
        if surface is not None and name != TOOL_SEARCH_NAME:
            # The model calls tools by their provider-safe wire name (≤64 chars);
            # dispatch, validation and the recorded ToolMessage use the registered one.
            name = surface.to_real(name)
        if surface is not None and name == TOOL_SEARCH_NAME:
            # A meta call: no side effects, so it is never deferred by step discipline,
            # never counted against the tool-call budgets, and never truncated.
            results.append(await _run_tool_search(adapter, raw_args, call_id=call_id, timeout=timeout))
            continue
        if name == TODOS_TOOL_NAME and name in adapter._tools_context:
            results.append(await _run_update_todos(adapter, raw_args, call_id=call_id, timeout=timeout))
            continue
        if budget_stop:
            results.append(_error_message(budget_stop, call_id=call_id, name=name))
            continue
        if one_per_step and attempted > 0:
            # By position, not by success: an error on the first call is the result
            # the model must read before it decides the next one.
            results.append(_error_message(DEFERRED_CALL_MESSAGE, call_id=call_id, name=name))
            continue
        attempted += 1

        if hybrid is not None and name == EXECUTE_PYTHON_NAME:
            reply = await _run_execute_python(
                adapter,
                raw_args,
                call_id=call_id,
                hybrid=hybrid,
                large_output=large_output,
                write_check=write_check,
                earlier=results,
            )
            executed += reply.status != "error"
            results.append(reply)
            continue

        if large_output is not None and name == QUERY_SAVED_OUTPUT_NAME:
            results.append(
                await _run_query_saved_output(adapter, raw_args, call_id=call_id, large_output=large_output)
            )
            continue
        if large_output is not None and name in (GREP_SAVED_NAME, READ_SAVED_NAME):
            results.append(
                await asyncio.to_thread(
                    _run_saved_file_tool, adapter, name, raw_args, call_id=call_id, large_output=large_output
                )
            )
            continue

        fn = adapter._tools_context.get(name)
        if fn is None or not callable(fn):
            results.append(
                _error_message(_unknown_tool_text(adapter, name, surface), call_id=call_id, name=name)
            )
            continue

        kwargs, arg_error = _coerce_args(raw_args)
        if arg_error:
            results.append(_error_message(arg_error, call_id=call_id, name=name))
            continue

        # Validate against the tool's schema before the call is counted, the order
        # Claude Code uses (safeParse -> permissions -> call): a malformed call spends
        # no run/thread budget and never reaches the remote API.
        shown = surface.to_wire(name) if surface is not None else name
        validation_error = _validate_args(adapter, name, kwargs, display_name=shown)
        if validation_error:
            if surface is not None and surface.is_deferred(name) and name not in discovered:
                # buildSchemaNotSentHint: the model called a tool whose schema it never saw.
                validation_error += (
                    f"\n{shown}'s schema is not loaded, so its parameters were guessed. "
                    f'Call {TOOL_SEARCH_NAME} with query "select:{shown}", then retry.'
                )
            _collect_step(
                adapter,
                "Tool_validation_error",
                {"tool": name, "arguments": kwargs, "error": validation_error},
            )
            results.append(_error_message(validation_error, call_id=call_id, name=name))
            continue

        if write_check is not None:
            # Before a call that commits something (order, payment, send): a subagent reads
            # the state it depends on and may block it (action_verifier.check_before_write).
            structured = (getattr(adapter, "_tools_context_ref", None) or {}).get(
                "_lc_bind_tools_structured_by_name"
            ) or {}
            blocked = await check_before_write(
                tool=structured.get(name),
                args=kwargs,
                messages=[*write_check["messages"], *results],
                tools_by_name=structured,
                tools_context=adapter._tools_context,
                model=write_check["model"],
                invoke_config=write_check["invoke_config"],
                tracker=getattr(adapter, "_tracker", None),
            )
            if blocked:
                results.append(
                    ToolMessage(
                        content=blocked,
                        tool_call_id=call_id,
                        name=name,
                        status="error",
                        artifact={WRITE_CHECK_KEY: WRITE_CHECK_BLOCKED},
                    )
                )
                continue

        awaitable = fn if inspect.iscoroutinefunction(fn) else make_tool_awaitable(fn)
        counted = counted_tool_call(awaitable)
        shell: Optional[Dict[str, Any]] = None
        if hybrid is not None and name == RUN_COMMAND_NAME:
            shell = _shell_bridge(adapter, kwargs, write_check=write_check, earlier=results)
        try:
            if shell is not None:
                tokens = (CODE_CALLS.set(shell["calls"]), SHELL_TOOLS.set(shell["tools"]))
                try:
                    # A command runs as long as its own timeout allows (120 s), not a tool call's.
                    result = await counted(**kwargs)
                finally:
                    SHELL_TOOLS.reset(tokens[1])
                    CODE_CALLS.reset(tokens[0])
            else:
                result = await asyncio.wait_for(counted(**kwargs), timeout=timeout)
            executed += 1
            saved = save_native_result(hybrid["state"], name, result) if hybrid is not None else None
            text = _stringify(result)
            persisted = None
            if large_output is not None and len(text) > large_output["threshold"]:
                # Before the batch budget would cut it: save it, search it, reply with the findings.
                persisted = await handle_large_result(
                    result,
                    tool_name=shown,
                    call_id=call_id,
                    thread_id=large_output["thread_id"],
                    messages=large_output["messages"],
                    args=kwargs,
                    model=large_output["model"],
                    invoke_config=large_output["invoke_config"],
                    saved_as=saved,
                )
            content = output_budget.take(persisted.text if persisted is not None else text)
            structured = (getattr(adapter, "_tools_context_ref", None) or {}).get(
                "_lc_bind_tools_structured_by_name"
            ) or {}
            note = page_note(structured.get(name), kwargs, result, hybrid=hybrid is not None)
            if note:
                content += f"\n{note}"
            artifact: Optional[Dict[str, Any]] = None
            if saved:
                if persisted is None:
                    content += f"\n[Result saved as variable `{saved}` (this call's output only) — usable in execute_python.]"
                # Old-result clearing (tool_result_clearing.py) points its stub here.
                artifact = {"saved_as": saved}
            if persisted is not None:
                artifact = {**(artifact or {}), "saved_file": persisted.path.name}
            if shell is not None:
                # Tools the command's scripts called, like execute_python's: the write
                # detection and the verifier read them from here.
                artifact = {**(artifact or {}), "code_calls": list(shell["calls"])}
                if shell["blocked"]:
                    artifact[WRITE_CHECK_KEY] = list(shell["blocked"])
            results.append(ToolMessage(content=content, tool_call_id=call_id, name=name, artifact=artifact))
        except asyncio.TimeoutError:
            results.append(
                _error_message(
                    f"Tool '{name}' timed out after {timeout}s. Try a narrower call or a different tool.",
                    call_id=call_id,
                    name=name,
                )
            )
        except ToolCallBudgetExceeded as exc:
            # Every remaining id still gets a reply — without invoking anything.
            if exc.scope != "block":
                budget_stop = str(exc)
            results.append(_error_message(str(exc), call_id=call_id, name=name))
        except TypeError as exc:
            results.append(
                _error_message(
                    f"Tool '{name}' rejected these arguments: {exc}. Check the parameter names and types.",
                    call_id=call_id,
                    name=name,
                )
            )
        except Exception as exc:  # one failing call must not abort its siblings
            results.append(_error_message(f"Tool '{name}' failed: {exc}", call_id=call_id, name=name))

    for index, bad in enumerate(invalid):
        call_id, name, _ = _tool_call_parts(bad)
        reason = (bad.get("error") if isinstance(bad, dict) else None) or "malformed tool call"
        results.append(
            _error_message(
                f"The provider could not parse this tool call ({reason}). Re-issue it with valid arguments.",
                call_id=call_id or f"invalid_{index}",
                name=name,
            )
        )
    return executed
