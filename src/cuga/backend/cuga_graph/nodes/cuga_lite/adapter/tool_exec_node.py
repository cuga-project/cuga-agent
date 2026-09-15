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
import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger

from cuga.backend.activity_tracker.tracker import Step
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    append_chat_messages_with_step_limit as core_append_with_step_limit,
    create_error_command as core_create_error_command,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import _budget_updates
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    ToolCallBudgetExceeded,
    ToolCallTracker,
    counted_tool_call,
)
from cuga.config import settings

TRUNCATION_MARKER = "\n... [result truncated to {limit} characters]"


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


def _stringify(result: Any, limit: int) -> str:
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            text = str(result)
    if limit and len(text) > limit:
        return text[:limit] + TRUNCATION_MARKER.format(limit=limit)
    return text


def _error_message(text: str, *, call_id: str, name: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id, name=name or "unknown", status="error")


def create_tool_exec_node(adapter: Any) -> Callable:
    async def tool_exec(state: Any, config: Optional[RunnableConfig] = None):
        configurable = config.get("configurable", {}) if config else {}
        track_tool_calls = configurable.get("track_tool_calls", False)
        max_steps = configurable.get("cuga_lite_max_steps") if "cuga_lite_max_steps" in configurable else None
        timeout = getattr(settings.advanced_features, "tool_call_timeout", 30) or None
        output_limit = int(getattr(settings.advanced_features, "execution_output_max_length", 0) or 0)

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

        results: List[ToolMessage] = []
        executed = 0
        budget_stop: Optional[str] = None
        try:
            for index, call in enumerate(calls):
                call_id, name, raw_args = _tool_call_parts(call)
                call_id = call_id or f"call_{index}"
                if not name:
                    results.append(
                        _error_message("Tool call carried no tool name.", call_id=call_id, name="")
                    )
                    continue
                if budget_stop:
                    results.append(_error_message(budget_stop, call_id=call_id, name=name))
                    continue

                fn = adapter._tools_context.get(name)
                if fn is None or not callable(fn):
                    known = ", ".join(sorted(k for k in adapter._tools_context if not k.startswith("_")))
                    results.append(
                        _error_message(
                            f"Unknown tool '{name}'. Choose one of the provided tools: {known or '(none)'}.",
                            call_id=call_id,
                            name=name,
                        )
                    )
                    continue

                kwargs, arg_error = _coerce_args(raw_args)
                if arg_error:
                    results.append(_error_message(arg_error, call_id=call_id, name=name))
                    continue

                awaitable = fn if inspect.iscoroutinefunction(fn) else make_tool_awaitable(fn)
                counted = counted_tool_call(awaitable)
                try:
                    result = await asyncio.wait_for(counted(**kwargs), timeout=timeout)
                    executed += 1
                    results.append(
                        ToolMessage(content=_stringify(result, output_limit), tool_call_id=call_id, name=name)
                    )
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
        finally:
            execution_tool_calls = ToolCallTracker.stop_tracking()

        if not calls and not invalid:
            # Nothing executable: advance the step and hand back to the model so a
            # mis-route can never become an infinite no-op loop.
            logger.warning("tool_exec entered with no tool_calls on the last message")
            return {"step_count": state.step_count + 1, "script": None, **_budget_updates()}

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
        accumulated_tool_calls = (state.tool_calls or []) + (execution_tool_calls if track_tool_calls else [])

        if error_message:
            return core_create_error_command(
                adapter,
                updated_messages,
                error_message,
                state.step_count,
                additional_updates={"tool_calls": accumulated_tool_calls, **_budget_updates()},
            )

        logger.info(
            "[fc] tool_exec: {} call(s) -> {} ToolMessage(s), {} executed", len(calls), len(results), executed
        )
        return {
            adapter.messages_key: updated_messages,
            "script": None,
            "step_count": state.step_count + 1,
            "tool_calls": accumulated_tool_calls,
            **_budget_updates(),
        }

    return tool_exec
