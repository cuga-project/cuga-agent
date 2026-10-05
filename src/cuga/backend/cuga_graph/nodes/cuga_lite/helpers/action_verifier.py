"""Stop check ``verify_actions``: an independent subagent checks the agent's changes before it answers.

free-code's verification agent (``tools/AgentTool/built-in/verificationAgent.ts``) is a
separate agent whose job "is not to confirm the implementation works — it's to try to break
it": it gets the task and what was done, runs real commands against the result, and ends
with ``VERDICT: PASS|FAIL``. This is the same idea for API actions.

The AppWorld hybrid failures it targets: the agent made a change, the API said "success",
and the change was wrong — ``place_order`` checked out a cart that also held items nobody
asked for ($1155 charged for a $20 dish rack), or only one of two requested items was
moved before ordering. Nothing in the loop looked at the result of a write.

When the agent is about to answer and a state-changing call succeeded in this turn
(:func:`tool_effects.turn_made_a_write`), a subagent on the agent's model gets:

- the user's request, the turn's calls and results (native and ``execute_python``), and
  the agent's answer;
- the read-only (GET) tools of the apps the turn changed, so it checks the *current
  state* (the order's items, the cart, the sent message) instead of trusting the log.

``VERDICT: FAIL`` sends the agent back with the problems it listed; anything else —
PASS, an unparseable reply, a timeout, an error — lets the answer through. Like every stop
check it fires at most once per turn. Native modes only (CodeAct turns have no tool
messages to read). Opt in by adding ``"verify_actions"`` to ``cuga_lite_stop_checks``;
limits under ``[verify_actions]``.

A check after the fact cannot undo an order. :func:`check_before_write` (bottom of this
module, ``verify_actions.before_write``) runs the same subagent *before* a native call
that commits something, and can block it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.stop_checks import StopContext, register_stop_check
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_effects import (
    _attr,
    _get,
    _role,
    current_turn_start,
    is_write_tool,
    turn_made_a_write,
)
from cuga.config import settings

VERIFY_ACTIONS_NAME = "verify_actions"

_NATIVE_MODES = ("function_calling", "hybrid")

_SYSTEM = """\
You are a verification specialist. Another agent carried out a user's request by calling app APIs, and \
some of its calls changed data (created, updated, deleted, moved, ordered, sent, paid). Your job is not to \
confirm that it worked — it is to find what went wrong.

You get the user's request, the log of the agent's calls and their results in this turn, and the answer \
it is about to give. You also have read-only tools of the apps it changed. Use them to look at the state \
as it is now — the order's items, the cart, the list after the deletion, the message that was sent — \
instead of trusting the log or the agent's summary. You cannot change anything.

Check:
- Scope: did each change touch exactly what was asked, no more and no less? An order with items that \
were not requested, only some of the requested items handled, the wrong record or recipient changed.
- Effects: does what each write reports match what was intended? An amount charged far from the prices \
of the items asked for, a count moved or deleted different from the count intended.
- Completeness: is every part of the request done — every item, every page, every recipient?
- Failures: a write that returned an error, or did nothing, that the agent treated as done.
- The answer: does it claim something that did not happen, or give a number the data contradicts?

These are the excuses you will be tempted by; do the opposite:
- "The call returned success" — success means the API accepted the call, not that the call was right. \
Look at the resulting state.
- "The log looks right" — read the state with the tools before you pass.
- "It is probably fine" — probably is not checked.

Do not fail on wording, formatting or politeness. If the request was ambiguous and the agent made a \
reasonable choice, that is not a failure. Fail only on a concrete problem you can name.

When you are done, reply with no tool call, ending with exactly one line:
VERDICT: PASS
or
VERDICT: FAIL
followed by one line per problem, each starting with "- ", naming the records, ids and values involved.
"""

CORRECTION = (
    "an independent check of the changes you made in this turn found problems:\n{issues}\n"
    "Fix them now if they can be fixed — undo or redo the wrong change, finish what is missing — and "
    "check the result. If something cannot be undone, say so plainly in your answer instead of reporting "
    "it as done."
)

_VERDICT_RE = re.compile(
    r"^[\s\-*#>`]*VERDICT[\s*`]*[:：][\s*_`]*(PASS|FAIL|PARTIAL|PROCEED|BLOCK)\b", re.IGNORECASE
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


# ── settings ─────────────────────────────────────────────────────────────────


def _cfg(key: str, default: Any) -> Any:
    section = getattr(settings, "verify_actions", None)
    value = getattr(section, key, None) if section is not None else None
    return default if value is None else value


# ── the verdict ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    passed: Optional[bool]  # None: no verdict line
    issues: List[str] = field(default_factory=list)
    raw: str = ""


def parse_verdict(text: str) -> Verdict:
    """The last ``VERDICT:`` line decides; ``- `` lines after a FAIL (BLOCK) are the problems.

    The pre-write check answers PROCEED / BLOCK; they read as PASS / FAIL.

    PARTIAL (free-code's third verdict: could not check everything) counts as a pass —
    it names no concrete problem to send the agent back with.
    """
    raw = (text or "").strip()
    lines = _THINK_RE.sub("", raw).splitlines()
    index, verdict = None, None
    for i, line in enumerate(lines):
        match = _VERDICT_RE.match(line.strip())
        if match:
            index, verdict = i, match.group(1).upper()
    if verdict is None:
        return Verdict(passed=None, raw=raw)
    if verdict not in ("FAIL", "BLOCK"):
        return Verdict(passed=True, raw=raw)
    after = [line.strip() for line in lines[index + 1 :] if line.strip()]
    issues = [line.lstrip("-*• ").strip() for line in after if line.startswith(("-", "*", "•"))]
    if not issues:
        # Problems written above the verdict line instead of below it.
        before = [line.strip() for line in lines[:index] if line.strip().startswith(("-", "*", "•"))]
        issues = [line.lstrip("-*• ").strip() for line in before]
    return Verdict(passed=False, issues=[i for i in issues if i], raw=raw)


# ── what the verifier sees ──────────────────────────────────────────────────


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
            if not isinstance(part, dict) or part.get("type") in (None, "text")
        )
    return str(content or "")


def _cut(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + f" … [{len(text) - limit:,} more chars]"


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return repr(value)


def user_request(messages: Sequence[Any], start: int) -> str:
    return _text_of(_get(messages[start - 1], "content")) if start > 0 else ""


def action_log(
    messages: Sequence[Any], start: int, tools_by_name: Dict[str, Any], *, result_chars: int, total_chars: int
) -> str:
    """The turn's calls and results, in order. Writes are marked; long results are cut.

    Over ``total_chars`` the oldest entries are dropped first — the writes and what
    followed them are what the verifier needs.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import EXECUTE_PYTHON_NAME

    entries: List[str] = []
    calls: Dict[str, tuple] = {}
    for message in list(messages)[start:]:
        for call in _get(message, "tool_calls") or []:
            if isinstance(call, dict) and call.get("id"):
                calls[call["id"]] = (str(call.get("name") or ""), call.get("args"))
        if _role(message) != "tool":
            continue
        name = _get(message, "name") or calls.get(_get(message, "tool_call_id"), ("", None))[0]
        args = calls.get(_get(message, "tool_call_id"), ("", None))[1]
        status = "error" if _get(message, "status") == "error" else "ok"
        result = _cut(_text_of(_get(message, "content")), result_chars)
        if name == EXECUTE_PYTHON_NAME:
            code = args.get("code") if isinstance(args, dict) else ""
            artifact = _get(message, "artifact")
            called = list((artifact or {}).get("code_calls") or []) if isinstance(artifact, dict) else []
            writes = [c for c in called if is_write_tool(tools_by_name.get(c))]
            mark = f" [WRITES: {', '.join(writes)}]" if writes else ""
            entries.append(
                f"### execute_python{mark} ({status})\nCode:\n{_cut(str(code or ''), result_chars)}\nOutput:\n{result}"
            )
        else:
            mark = " [WRITE]" if is_write_tool(tools_by_name.get(name)) else ""
            entries.append(f"### {name}({_cut(_dump(args or {}), 1000)}){mark} ({status})\n{result}")

    dropped = 0
    while len(entries) > 1 and sum(len(e) + 2 for e in entries) > total_chars:
        entries.pop(0)
        dropped += 1
    head = f"[{dropped} earlier call(s) omitted]\n\n" if dropped else ""
    return head + "\n\n".join(entries)


def _app_of(tool: Any) -> str:
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.doc import app_name_for_tool

    return app_name_for_tool(tool) if tool is not None else ""


def changed_apps(messages: Sequence[Any], start: int, tools_by_name: Dict[str, Any]) -> List[str]:
    """Apps a successful write in this turn belonged to, in first-write order."""
    apps: List[str] = []
    for message in list(messages)[start:]:
        if _role(message) != "tool" or _get(message, "status") == "error":
            continue
        names = [_get(message, "name")]
        artifact = _get(message, "artifact")
        if isinstance(artifact, dict):
            names += list(artifact.get("code_calls") or [])
        for name in names:
            tool = tools_by_name.get(name) if name else None
            if is_write_tool(tool):
                app = _app_of(tool)
                if app and app not in apps:
                    apps.append(app)
    return apps


def read_tools(
    messages: Sequence[Any],
    start: int,
    tools_by_name: Dict[str, Any],
    tools_context: Dict[str, Any],
    apps: Sequence[str],
    *,
    max_tools: int,
) -> List[Any]:
    """GET tools of ``apps`` the run can call; the ones the turn already used come first."""
    wanted = set(apps)
    candidates = [
        tool
        for name, tool in tools_by_name.items()
        if callable(tools_context.get(name))
        and _attr(tool, "_http_method").upper() == "GET"
        and _app_of(tool) in wanted
    ]
    used = set()
    for message in list(messages)[start:]:
        if _role(message) == "tool":
            used.add(_get(message, "name"))
            artifact = _get(message, "artifact")
            if isinstance(artifact, dict):
                used.update(artifact.get("code_calls") or [])
    candidates.sort(key=lambda t: (t.name not in used, t.name))
    return candidates[:max_tools]


# ── the subagent ─────────────────────────────────────────────────────────────


@dataclass
class Verification:
    verdict: Verdict
    tool_calls: int


async def _call_tool(fn: Any, args: Any, timeout: float) -> str:
    kwargs = args if isinstance(args, dict) else {}
    try:
        result = await asyncio.wait_for(fn(**kwargs), timeout=timeout)
        return result if isinstance(result, str) else _dump(result)
    except asyncio.TimeoutError:
        return f"Timed out after {timeout}s."
    except Exception as exc:
        return f"Failed: {exc}"


async def verify(
    model: Any,
    task: str,
    tools: List[Any],
    tools_context: Dict[str, Any],
    *,
    invoke_config: Optional[Dict[str, Any]] = None,
    max_steps: int = 10,
    result_chars: int = 4000,
    tool_timeout: float = 30.0,
    system: str = _SYSTEM,
    run_name: str = VERIFY_ACTIONS_NAME,
) -> Verification:
    """Run the verifier: model -> read calls -> ... -> a reply with a verdict."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.minify import minified_tools
    from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import wire_name
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import _safe_bind

    invoke_config = dict(invoke_config or {})
    invoke_config.setdefault("run_name", run_name)
    by_wire = {wire_name(t.name): t.name for t in tools}
    bound = _safe_bind(model, minified_tools(tools)) if tools else model
    messages: List[BaseMessage] = [SystemMessage(content=system), HumanMessage(content=task)]

    calls = 0
    for _ in range(max_steps + 1):
        reply = await bound.ainvoke(messages, config=invoke_config)
        messages.append(reply)
        tool_calls = list(getattr(reply, "tool_calls", None) or [])
        if not tool_calls:
            return Verification(parse_verdict(_text_of(reply.content)), calls)
        for call in tool_calls:
            # Every call gets a reply, run or not, so the transcript stays valid for the provider.
            real = by_wire.get(call.get("name") or "", call.get("name") or "")
            fn = tools_context.get(real) if real in by_wire.values() else None
            if calls >= max_steps:
                output = "Not run: the check limit is reached."
            elif fn is None:
                output = (
                    f"Unknown or not allowed tool {call.get('name')!r}: only the read tools bound to you."
                )
            else:
                calls += 1
                output = _cut(await _call_tool(fn, call.get("args"), tool_timeout), result_chars)
            messages.append(
                ToolMessage(content=output, tool_call_id=call.get("id") or "", name=call.get("name"))
            )
        if calls >= max_steps:
            break
    messages.append(
        HumanMessage(
            content="You have used all your checks. Reply now, with no tool call, with your verdict."
        )
    )
    reply = await model.ainvoke(messages, config=invoke_config)
    return Verification(parse_verdict(_text_of(reply.content)), calls)


def build_task(request: str, log: str, answer: str, apps: Sequence[str], tools: Sequence[Any]) -> str:
    tool_note = (
        f"Read-only tools of {', '.join(apps)} are bound to you ({len(tools)})."
        if tools
        else "No read tools are available: judge from the log alone."
    )
    return (
        f"User request:\n{request or '(unavailable)'}\n\n"
        f"The agent's calls in this turn (changes marked [WRITE]):\n\n{log}\n\n"
        f"The answer the agent is about to give:\n{answer or '(empty)'}\n\n"
        f"{tool_note} Check the changes, then give your verdict."
    )


def _record(ctx: StopContext, data: Dict[str, Any]) -> None:
    _record_step(ctx.tracker, "Action_verification", data)


def _record_step(tracker: Any, name: str, data: Dict[str, Any]) -> None:
    if tracker is None:
        return
    try:
        from cuga.backend.activity_tracker.tracker import Step

        tracker.collect_step(step=Step(name=name, data=json.dumps(data, default=str)))
    except Exception as exc:
        logger.debug(f"{name} tracker error: {exc}")


async def verify_actions(ctx: StopContext) -> Optional[str]:
    """Stop check: a state-changing call succeeded this turn — have a subagent try to find what went wrong."""
    if ctx.execution_mode not in _NATIVE_MODES or ctx.model is None:
        return None
    messages = list(ctx.messages or [])
    start = current_turn_start(messages)
    if not turn_made_a_write(messages, start, ctx.tools_by_name):
        return None

    apps = changed_apps(messages, start, ctx.tools_by_name)
    tools = read_tools(
        messages, start, ctx.tools_by_name, ctx.tools_context, apps, max_tools=int(_cfg("max_tools", 40))
    )
    result_chars = int(_cfg("max_result_chars", 4000))
    log = action_log(
        messages,
        start,
        ctx.tools_by_name,
        result_chars=result_chars,
        total_chars=int(_cfg("max_log_chars", 24000)),
    )
    task = build_task(user_request(messages, start), log, ctx.answer, apps, tools)
    timeout = float(_cfg("subagent_timeout", 120))
    try:
        outcome = await asyncio.wait_for(
            verify(
                ctx.model,
                task,
                tools,
                ctx.tools_context,
                invoke_config=ctx.invoke_config,
                max_steps=int(_cfg("max_steps", 10)),
                result_chars=result_chars,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"verify_actions: verifier timed out after {timeout}s; letting the answer through")
        _record(ctx, {"verdict": "timeout", "apps": apps})
        return None

    verdict = outcome.verdict
    label = {True: "PASS", False: "FAIL", None: "none"}[verdict.passed]
    logger.info("[verify_actions] verdict={} reads={} issues={}", label, outcome.tool_calls, verdict.issues)
    _record(
        ctx,
        {
            "verdict": label,
            "issues": verdict.issues,
            "apps": apps,
            "read_tools": len(tools),
            "reads": outcome.tool_calls,
            "reply": _cut(verdict.raw, 4000),
        },
    )
    if verdict.passed is not False:
        return None
    issues = verdict.issues or ["(the verifier gave no details) re-check every change against the request"]
    return CORRECTION.format(issues="\n".join(f"- {i}" for i in issues))


register_stop_check(VERIFY_ACTIONS_NAME, verify_actions)


# ── before a committing write ────────────────────────────────────────────────
#
# The stop check above runs after the fact: in the AppWorld runs it named every problem
# (order 3146 held five products nobody asked for), but ``place_order`` had already
# checked out the whole cart and AppWorld scores the database. free-code refuses an Edit
# to a file the model has not Read; the same moment here is just before a call that
# commits something (an order, a payment, a message). With ``verify_actions.before_write``
# on, ``tool_exec`` asks the same subagent — with the call's app's read tools — whether
# running *this* call *now* does what the user asked. BLOCK answers the call with the
# problems instead of running it; the agent fixes them and calls again (checked again,
# up to ``before_write_max_blocks`` blocks per tool per turn, then it runs unchecked).
# Calls made from ``execute_python`` code go through :func:`guard_code_tools`: the same
# check, and a block raises ``ToolCallError`` inside the code (AppWorld 0d22252 placed
# its wrong order from code, after which every native order call was moot).

WRITE_CHECK_KEY = "write_check"
WRITE_CHECK_BLOCKED = "blocked"

_DEFAULT_COMMIT_PATTERNS = ("order", "checkout", "purchase", "buy", "pay", "send", "transfer")

_BEFORE_WRITE_SYSTEM = """\
You check an API call before it runs. Another agent is working on a user's request and is about to make \
the call below, which changes data and cannot be undone once it runs. You have read-only tools of the \
call's app. Use them to look at the state the call depends on — for an order, what is in the cart right \
now; for a payment or a message, the recipient and the amount — and decide whether running this call now \
does exactly what the user asked: no more, no less.

Read the call's description: it says what the call acts on. An order call that checks out "the cart" \
orders everything in the cart, not only the items the agent has in mind.

BLOCK only for a concrete problem you can name:
- the call would include records or items that were not requested, or leave out ones that were;
- the wrong target, recipient, amount or quantity;
- a step the call depends on is not done yet (for example the requested items are not in the cart yet).
Otherwise PROCEED. Do not block over choices the user left open (which saved card or address to use), \
wording, or style.

Reply with no tool call when you are done, ending with exactly one line:
VERDICT: PROCEED
or
VERDICT: BLOCK
followed by one line per problem, each starting with "- ", saying what is wrong and what has to be done \
before the call (name the records, ids and values).
"""

BLOCKED_REPLY = (
    "Not run: a check before this call found problems:\n{issues}\n"
    "Fix them first, then make the call again (it is checked again before it runs)."
)


def before_write_enabled(configurable: Optional[Dict[str, Any]] = None) -> bool:
    cfg = configurable or {}
    if "cuga_lite_verify_before_write" in cfg:
        return bool(cfg["cuga_lite_verify_before_write"])
    return bool(_cfg("before_write", False))


def is_commit_call(tool: Any, patterns: Optional[Sequence[str]] = None) -> bool:
    """A state-changing tool whose name or path names a commitment (order, pay, send, …)."""
    if not is_write_tool(tool):
        return False
    pats = [
        p.lower() for p in (patterns if patterns is not None else _cfg("before_write_patterns", None)) or []
    ]
    pats = pats or list(_DEFAULT_COMMIT_PATTERNS)
    where = f"{getattr(tool, 'name', '')} {_attr(tool, '_http_path')}".lower()
    return any(p in where for p in pats)


def blocks_this_turn(messages: Sequence[Any], name: str) -> int:
    """Blocks of ``name`` this turn: native replies marked blocked, and ``execute_python``
    replies whose ``write_check`` artifact lists the tools blocked inside the code."""
    start = current_turn_start(messages)
    count = 0
    for message in list(messages)[start:]:
        artifact = _get(message, "artifact")
        if _role(message) != "tool" or not isinstance(artifact, dict):
            continue
        marker = artifact.get(WRITE_CHECK_KEY)
        if marker == WRITE_CHECK_BLOCKED and _get(message, "name") == name:
            count += 1
        elif isinstance(marker, list):
            count += marker.count(name)
    return count


def _agent_note(messages: Sequence[Any]) -> str:
    for message in reversed(list(messages)):
        if _role(message) == "ai":
            text = _text_of(_get(message, "content")).strip()
            if not text:
                reasoning = (_get(message, "additional_kwargs") or {}).get("reasoning_content")
                text = reasoning if isinstance(reasoning, str) else ""
            return _cut(text, 1500)
    return ""


def build_write_task(
    request: str, log: str, note: str, tool: Any, args: Any, tools: Sequence[Any], code: str = ""
) -> str:
    description = _cut(str(getattr(tool, "description", "") or ""), 2000)
    tool_note = (
        f"Read-only tools of {_app_of(tool) or 'the app'} are bound to you ({len(tools)})."
        if tools
        else "No read tools are available: judge from the log alone."
    )
    parts = [
        f"User request:\n{request or '(unavailable)'}",
        f"The agent's calls so far in this turn:\n\n{log or '(none)'}",
    ]
    if note:
        parts.append(f"The agent's note before this call:\n{note}")
    parts.append(
        f"The call about to run:\n{tool.name}({_cut(_dump(args or {}), 1500)})\n"
        f"Its description:\n{description or '(none)'}"
    )
    if code:
        parts.append(
            "The call is made from this execute_python code, which is running now (calls before it in the "
            f"code have already run):\n{_cut(code, 4000)}"
        )
    parts.append(f"{tool_note} Check the state this call depends on, then give your verdict.")
    return "\n\n".join(parts)


async def check_before_write(
    *,
    tool: Any,
    args: Any,
    messages: Sequence[Any],
    tools_by_name: Dict[str, Any],
    tools_context: Dict[str, Any],
    model: Any,
    invoke_config: Optional[Dict[str, Any]] = None,
    tracker: Any = None,
    code: str = "",
    earlier_blocks: int = 0,
) -> Optional[str]:
    """The reply to give instead of running ``tool``, or ``None`` to run it.

    ``code``: the ``execute_python`` block making the call, if any. ``earlier_blocks``:
    blocks of this tool not yet visible in ``messages`` (inside the running block).

    ``None`` whenever the check does not apply or cannot decide: not a commit call, no
    model, the block limit for this tool reached, a timeout, an error, or no verdict.
    """
    if model is None or tool is None or not is_commit_call(tool):
        return None
    messages = list(messages or [])
    max_blocks = int(_cfg("before_write_max_blocks", 3))
    if blocks_this_turn(messages, tool.name) + earlier_blocks >= max_blocks:
        logger.info("[verify_before_write] {}: block limit reached, running unchecked", tool.name)
        return None
    start = current_turn_start(messages)
    app = _app_of(tool)
    tools = read_tools(
        messages,
        start,
        tools_by_name,
        tools_context,
        [app] if app else [],
        max_tools=int(_cfg("max_tools", 40)),
    )
    result_chars = int(_cfg("max_result_chars", 4000))
    log = action_log(
        messages,
        start,
        tools_by_name,
        result_chars=result_chars,
        total_chars=int(_cfg("max_log_chars", 24000)),
    )
    task = build_write_task(
        user_request(messages, start), log, _agent_note(messages), tool, args, tools, code
    )
    timeout = float(_cfg("subagent_timeout", 120))
    try:
        outcome = await asyncio.wait_for(
            verify(
                model,
                task,
                tools,
                tools_context,
                invoke_config=invoke_config,
                max_steps=int(_cfg("max_steps", 10)),
                result_chars=result_chars,
                system=_BEFORE_WRITE_SYSTEM,
                run_name="verify_before_write",
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"verify_before_write: timed out after {timeout}s; running {tool.name} unchecked")
        _record_step(tracker, "Write_check", {"tool": tool.name, "verdict": "timeout"})
        return None
    except Exception as exc:
        logger.warning(f"verify_before_write: check failed ({exc}); running {tool.name} unchecked")
        return None

    verdict = outcome.verdict
    label = {True: "PROCEED", False: "BLOCK", None: "none"}[verdict.passed]
    logger.info(
        "[verify_before_write] {} verdict={} reads={} issues={}",
        tool.name,
        label,
        outcome.tool_calls,
        verdict.issues,
    )
    _record_step(
        tracker,
        "Write_check",
        {
            "tool": tool.name,
            "arguments": args,
            "from_code": bool(code),
            "verdict": label,
            "issues": verdict.issues,
            "read_tools": len(tools),
            "reads": outcome.tool_calls,
            "reply": _cut(verdict.raw, 4000),
        },
    )
    if verdict.passed is not False:
        return None
    issues = verdict.issues or ["(the check gave no details) re-check the state this call depends on"]
    return BLOCKED_REPLY.format(issues="\n".join(f"- {i}" for i in issues))


def guard_code_tools(
    tools_context: Dict[str, Any],
    tools_by_name: Dict[str, Any],
    *,
    code: str,
    messages: Sequence[Any],
    model: Any,
    invoke_config: Optional[Dict[str, Any]] = None,
    tracker: Any = None,
    blocked: List[str],
) -> Dict[str, Any]:
    """``tools_context`` with every commit tool checked before it runs from ``code``.

    A block raises ``ToolCallError`` with the problems (the code stops there, the reason
    reaches the model) and appends the tool's name to ``blocked``, which the caller puts
    in the reply's ``write_check`` artifact so later blocks are counted.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import CODE_CALLS, ToolCallError

    guarded = dict(tools_context)
    for name, fn in tools_context.items():
        tool = tools_by_name.get(name)
        if not callable(fn) or tool is None or not is_commit_call(tool):
            continue

        def _guard(fn: Any = fn, tool: Any = tool, name: str = name) -> Any:
            async def call(*args: Any, **kwargs: Any) -> Any:
                reply = await check_before_write(
                    tool=tool,
                    args=kwargs,
                    messages=messages,
                    tools_by_name=tools_by_name,
                    tools_context=tools_context,
                    model=model,
                    invoke_config=invoke_config,
                    tracker=tracker,
                    code=code,
                    earlier_blocks=blocked.count(name),
                )
                if reply:
                    blocked.append(name)
                    # The recording wrapper logged the call before it reached us; it never ran.
                    calls = CODE_CALLS.get()
                    if calls and calls[-1] == name:
                        calls.pop()
                    raise ToolCallError(f"{name}: {reply}")
                result = fn(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result

            return call

        guarded[name] = _guard()
    return guarded
