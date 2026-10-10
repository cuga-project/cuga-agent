"""Function-calling actions: native ``tool_calls`` in, a sandbox block out, ``ToolMessage``s back.

``cuga_lite_execution_mode = "function_calling"`` has no executor of its own. The
calls the model attached to its ``AIMessage`` are translated into a short Python
block that the existing sandbox runs, so tool approval, the pre-execute VERIFY
gate, variables, output trimming, the block timeout and the tool-call budgets
apply to a native call exactly as they apply to a CodeAct block. The block's
variables are then read back into one ``ToolMessage`` per call id, so the
transcript the model sees stays a native one.

The block is internal: never shown to the model, never persisted in chat
history, never sent to a provider.

What keeps the tool side identical to a direct call:

- a provider-safe alias (``bind_tools/tool_names.py``) is resolved to the real
  tool name before anything else, so the block, the approval text scan and
  VERIFY see the real name while the reply keeps the alias the provider knows;
- a tool whose name is a Python identifier is called by that name; any other
  name goes through the injected ``_fc_tools`` lookup, budget-counted like the
  rest;
- arguments are always passed as ``**{...}`` so keys like ``from`` work, and
  only JSON values are encoded — providers deliver JSON, so nothing is lost;
- each call sits in its own ``try``: one failing call never aborts its siblings,
  and the exception is kept as a marker the reply builder formats;
- the result is kept through ``_fc_keep`` so a non-JSON value survives as the
  very text the reply carries instead of being dropped from the variables;
- calls that cannot run (no name, unknown tool, unparsable arguments,
  provider-rejected, deferred under step discipline) get their reply decided
  at planning time and emit no code.
"""

from __future__ import annotations

import json
import keyword
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.messages import ToolMessage

FC_PENDING_KEY = "fc_pending"
"""Metadata key: the turn's call plan while the sandbox runs the block; cleared on every sandbox exit."""

FC_KEEP_KEY = "_fc_keep"
FC_TOOLS_KEY = "_fc_tools"
FC_BUDGET_EXC_KEY = "_fc_budget_exc"
RESULT_VAR_PREFIX = "tool_result_"
_ERROR_KEY = "__cuga_fc_error__"
_TIMEOUT_PREFIX = "Error during execution: Execution timed out"

DEFERRED_CALL_MESSAGE = (
    "Deferred: step discipline is on, so only the first tool call of a turn is attempted. "
    "Read that result, then re-issue this call in your next turn if you still need it."
)

TRUNCATION_MARKER = "\n... [result truncated: {limit} characters of output remain for this batch]"

NOT_EXECUTED_REPLY = "Not executed: tool execution failed before this call ran ({reason})."


# ── Pieces shared with the direct-call contract ──────────────────────────────


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


def _error_message(text: str, *, call_id: str, name: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id, name=name or "unknown", status="error")


def fc_keep(value: Any) -> Any:
    """Keep a tool result as a block variable without losing it.

    JSON-shaped values are stored as they are. Anything else is stored as the
    text the reply carries (``_stringify``), so the variables filter never drops
    a result and the reply is the same text a direct call would have produced.
    """
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        return _stringify(value)


# ── Planning: tool_calls -> block ────────────────────────────────────────────


def _literal(value: Any) -> str:
    """Python source for a JSON value. Raises ``ValueError`` for anything that is not JSON."""
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, int):
        return repr(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("non-JSON number")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(
            f"{json.dumps(str(k), ensure_ascii=False)}: {_literal(v)}" for k, v in value.items()
        )
        return "{" + items + "}"
    raise ValueError(f"unsupported argument type {type(value).__name__}")


def _result_var(call_id: str, taken: set) -> str:
    base = RESULT_VAR_PREFIX + re.sub(r"\W", "_", call_id)
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)
    return name


def _pre_decided(content: str) -> Dict[str, str]:
    return {"content": content, "status": "error"}


_ERROR_MARKER_SRC = (
    "{" + json.dumps(_ERROR_KEY) + ': str(_fc_e), "kind": "TypeError" if isinstance(_fc_e, TypeError) '
    'else ("budget" if isinstance(_fc_e, ' + FC_BUDGET_EXC_KEY + ') else "error")}'
)


def plan_tool_calls(
    calls: List[Any],
    invalid: List[Any],
    tools_context: Dict[str, Any],
    *,
    one_per_step: bool,
    resolve_name: Optional[Callable[[str], str]] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Translate one turn's calls into ``(block, plan)``.

    ``plan`` has one JSON-serialisable entry per issued id, in order:
    ``{"id", "name", "target", "var", "lookup", "reply"}``. ``name`` is the
    name as the model issued it (an alias stays an alias, so the reply matches
    the provider's tool list); ``target`` is the real tool the block calls, as
    ``resolve_name`` maps it. ``var`` names the block variable that will hold
    the call's result; ``reply`` is set instead when the call was answered here
    and emits no code. ``block`` is ``None`` when nothing is left to execute.
    """
    plan: List[Dict[str, Any]] = []
    lines: List[str] = []
    taken: set = set()

    for index, call in enumerate(calls):
        call_id, name, raw_args = _tool_call_parts(call)
        call_id = call_id or f"call_{index}"
        entry: Dict[str, Any] = {
            "id": call_id,
            "name": name,
            "target": name,
            "var": None,
            "lookup": False,
            "reply": None,
        }
        plan.append(entry)

        if not name:
            entry["reply"] = _pre_decided("Tool call carried no tool name.")
            continue
        if one_per_step and index > 0:
            # By position, not by success: an error on the first call is the result
            # the model must read before it decides the next one.
            entry["reply"] = _pre_decided(DEFERRED_CALL_MESSAGE)
            continue
        target = name
        if resolve_name is not None:
            try:
                target = resolve_name(name) or name
            except Exception as exc:  # an ambiguous alias: answer it, never crash the turn
                entry["reply"] = _pre_decided(f"Tool name {name!r} could not be resolved: {exc}")
                continue
        entry["target"] = target
        fn = tools_context.get(target)
        if fn is None or not callable(fn):
            known = ", ".join(sorted(k for k in tools_context if not k.startswith("_")))
            entry["reply"] = _pre_decided(
                f"Unknown tool '{name}'. Choose one of the provided tools: {known or '(none)'}."
            )
            continue
        kwargs, arg_error = _coerce_args(raw_args)
        if arg_error:
            entry["reply"] = _pre_decided(arg_error)
            continue
        try:
            args_src = _literal(kwargs)
        except ValueError as exc:
            entry["reply"] = _pre_decided(
                f"Tool arguments could not be encoded ({exc}). Re-issue the call with plain JSON values."
            )
            continue

        var = _result_var(call_id, taken)
        entry["var"] = var
        if target.isidentifier() and not keyword.iskeyword(target):
            callee = target
        else:
            entry["lookup"] = True
            callee = f"{FC_TOOLS_KEY}[{json.dumps(target, ensure_ascii=False)}]"
        lines += [
            "try:",
            f"    {var} = {FC_KEEP_KEY}(await {callee}(**{args_src}))",
            "except Exception as _fc_e:",
            f"    {var} = {_ERROR_MARKER_SRC}",
        ]

    for index, bad in enumerate(invalid):
        call_id, name, _ = _tool_call_parts(bad)
        reason = (bad.get("error") if isinstance(bad, dict) else None) or "malformed tool call"
        plan.append(
            {
                "id": call_id or f"invalid_{index}",
                "name": name,
                "target": name,
                "var": None,
                "lookup": False,
                "reply": _pre_decided(
                    f"The provider could not parse this tool call ({reason}). Re-issue it with valid arguments."
                ),
            }
        )

    block = "\n".join(lines) + "\n" if lines else None
    return block, plan


# ── Execution side: what the sandbox injects and reads back ──────────────────


def fc_pending_plan(adapter: Any, state: Any) -> Optional[List[Dict[str, Any]]]:
    """The plan a function-calling turn left for this sandbox step, else ``None``."""
    plan = (adapter.get_metadata(state) or {}).get(FC_PENDING_KEY)
    return [dict(e) for e in plan] if plan else None


def metadata_without_plan(adapter: Any, state: Any) -> Dict[str, Any]:
    """Turn metadata with the pending plan cleared — every sandbox exit writes this back."""
    return {k: v for k, v in (adapter.get_metadata(state) or {}).items() if k != FC_PENDING_KEY}


def fc_context_overlay(tools_context: Dict[str, Any], plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Names the block needs beyond the tools: the keeper, the budget exception and the lookup table.

    All ``_``-prefixed, so the executor neither counts them as tools nor keeps
    them as variables. Lookup entries are budget-counted here because the
    executor only wraps top-level callables.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
        ToolCallBudgetExceeded,
        counted_tool_call,
    )

    overlay: Dict[str, Any] = {FC_KEEP_KEY: fc_keep, FC_BUDGET_EXC_KEY: ToolCallBudgetExceeded}
    lookup = {
        e["target"]: counted_tool_call(tools_context[e["target"]])
        for e in plan
        if e.get("lookup") and e.get("target") in tools_context
    }
    if lookup:
        overlay[FC_TOOLS_KEY] = lookup
    return overlay


def _format_error(name: str, marker: Dict[str, Any]) -> str:
    text = str(marker.get(_ERROR_KEY, ""))
    kind = marker.get("kind")
    if kind == "TypeError":
        return f"Tool '{name}' rejected these arguments: {text}. Check the parameter names and types."
    if kind == "budget":
        return text
    return f"Tool '{name}' failed: {text}"


def _unreached_reason(output: str, timeout: Any) -> str:
    """Why a planned call left no result: the block timed out, or something outside the per-call guard."""
    text = (output or "").strip()
    if text.startswith(_TIMEOUT_PREFIX):
        return f"Tool '{{name}}' timed out after {timeout}s. Try a narrower call or a different tool."
    first_line = text.splitlines()[0] if text else "no result was recorded"
    return NOT_EXECUTED_REPLY.format(reason=first_line.replace("{", "{{").replace("}", "}}"))


def replies_from_execution(
    plan: List[Dict[str, Any]],
    new_vars: Dict[str, Any],
    output: str,
    *,
    output_limit: Any,
    timeout: Any,
) -> Tuple[List[ToolMessage], Dict[str, Any], List[str]]:
    """One ``ToolMessage`` per issued id, read back from the block's variables.

    Returns ``(replies, variables_to_keep, variables_to_drop)``: error markers
    are formatted into their reply and dropped from the variables, and so is a
    find_tools listing — the reply carries it, CodeAct never keeps it either.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import is_find_tools_listing_markdown

    budget = _BatchOutputBudget(output_limit)
    keep = dict(new_vars)
    drop: List[str] = []
    unreached = _unreached_reason(output, timeout)
    replies: List[ToolMessage] = []
    for entry in plan:
        call_id, name = entry["id"], entry.get("name") or ""
        pre = entry.get("reply")
        if pre:
            replies.append(_error_message(pre["content"], call_id=call_id, name=name))
            continue
        var = entry.get("var")
        if var is not None and var in new_vars:
            value = new_vars[var]
            if isinstance(value, dict) and _ERROR_KEY in value:
                keep.pop(var, None)
                drop.append(var)
                replies.append(_error_message(_format_error(name, value), call_id=call_id, name=name))
            else:
                if is_find_tools_listing_markdown(value):
                    keep.pop(var, None)
                    drop.append(var)
                replies.append(
                    ToolMessage(content=budget.take(_stringify(value)), tool_call_id=call_id, name=name)
                )
            continue
        replies.append(_error_message(unreached.format(name=name), call_id=call_id, name=name))
    return replies, keep, drop


def replies_without_execution(plan: List[Dict[str, Any]], reason: str) -> List[ToolMessage]:
    """Every id answered when the block did not run (VERIFY revise, a failure outside the block).

    Pre-decided replies are delivered as they are; every call that would have
    run gets ``reason``.
    """
    out: List[ToolMessage] = []
    for entry in plan:
        pre = entry.get("reply")
        text = pre["content"] if pre else reason
        out.append(_error_message(text, call_id=entry["id"], name=entry.get("name") or ""))
    return out
