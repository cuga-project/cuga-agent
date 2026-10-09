"""Function-calling through the sandbox: the translator, the reply builder, and equivalence.

The direct-call contract these pin is the one ``tool_exec`` shipped with: ids
preserved, one failing call never aborts its siblings, provider-side invalid
calls answered, step discipline defers everything but the first call, one output
budget per batch, and the exact reply texts. ``_run_batch`` here is what the
sandbox node does with a plan — translate, execute the block on the real local
executor, read the replies back — so every fixture crosses the executor.

The last test is the equivalence property: thousands of random tool names,
JSON arguments, results and exceptions through the block, compared reply for
reply against a reference of the direct-call contract.
"""

from __future__ import annotations

import asyncio
import json
import random
import string
from typing import Any, Dict, List, Tuple

import pytest
from langchain_core.messages import ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.fc_actions import (
    DEFERRED_CALL_MESSAGE,
    FC_TOOLS_KEY,
    TRUNCATION_MARKER,
    _coerce_args,
    _stringify,
    _tool_call_parts,
    fc_context_overlay,
    plan_tool_calls,
    replies_from_execution,
    replies_without_execution,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

pytestmark = pytest.mark.unit

RAN: list = []


async def add(a: int, b: int) -> int:
    RAN.append(("add", a, b))
    return a + b


async def boom(x: Any = None) -> None:
    RAN.append(("boom", x))
    raise ValueError("bad x")


async def echo_args(**kwargs: Any) -> Dict[str, Any]:
    RAN.append(("echo_args", kwargs))
    return kwargs


async def typed(a: int) -> Dict[str, int]:
    RAN.append(("typed", a))
    return {"a": a}


async def dashed(q: str) -> str:
    RAN.append(("dashed", q))
    return "dashed:" + q


async def slow(seconds: float) -> str:
    await asyncio.sleep(seconds)
    return "slept"


async def as_set() -> set:
    return {1}


def _tools(*fns, **named) -> Dict[str, Any]:
    tools = {fn.__name__: make_tool_awaitable(fn) for fn in fns}
    tools.update({name: make_tool_awaitable(fn) for name, fn in named.items()})
    return tools


def _call(name: str, args: Any, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


@pytest.fixture(autouse=True)
def _reset():
    RAN.clear()
    yield
    RAN.clear()
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_cap_override_context.set(None)


def _caps(monkeypatch, *, block=0, run=0, thread=0):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_block", block, raising=False)
    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_run", run, raising=False)
    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_thread", thread, raising=False)


async def _run_batch(
    tools: Dict[str, Any],
    calls: List[dict],
    invalid: List[dict] | None = None,
    *,
    one_per_step: bool = False,
    output_limit: int = 0,
    timeout: float = 30,
) -> Tuple[List[ToolMessage], Dict[str, Any], str | None]:
    """What the sandbox node does with a function-calling plan, on the real local executor."""
    block, plan = plan_tool_calls(calls, list(invalid or []), tools, one_per_step=one_per_step)
    if block is None:
        return replies_without_execution(plan, ""), {}, None
    context = {**tools, **fc_context_overlay(tools, plan)}
    ToolCallTracker.start_tracking(enabled=False, timings_only=False)
    ToolCallTracker.seed_call_budget(0, 0)
    try:
        output, new_vars = await CodeExecutor.eval_with_tools_async(
            code=block,
            _locals=context,
            state=CugaLiteState(chat_messages=[]),
            mode="local",
            keep_listing_vars=True,
        )
    finally:
        ToolCallTracker.stop_tracking()
    replies, keep, _drop = replies_from_execution(
        plan, new_vars, output, output_limit=output_limit, timeout=timeout
    )
    return replies, keep, block


def _shape(replies: List[ToolMessage]) -> List[Tuple[str, str, str, str]]:
    return [(m.tool_call_id, m.name, m.status, m.content) for m in replies]


# ── the direct-call contract, through the block ─────────────────────────────


@pytest.mark.asyncio
async def test_every_call_runs_and_ids_are_preserved():
    replies, kept, block = await _run_batch(
        _tools(add), [_call("add", {"a": 1, "b": 2}, "c1"), _call("add", {"a": 3, "b": 4}, "c2")]
    )
    assert _shape(replies) == [("c1", "add", "success", "3"), ("c2", "add", "success", "7")]
    assert RAN == [("add", 1, 2), ("add", 3, 4)]
    assert kept == {"tool_result_c1": 3, "tool_result_c2": 7}, "results persist as variables"
    assert "await add(**" in block and FC_TOOLS_KEY not in block, "a real name is called as itself"


@pytest.mark.asyncio
async def test_raw_provider_shaped_calls_with_json_string_args_are_decoded():
    raw = {"id": "c1", "type": "function", "function": {"name": "add", "arguments": '{"a": 1, "b": 2}'}}
    replies, _, _ = await _run_batch(_tools(add), [raw])
    assert _shape(replies) == [("c1", "add", "success", "3")]


@pytest.mark.asyncio
async def test_unknown_tool_lists_the_known_names():
    replies, _, block = await _run_batch(_tools(add, boom), [_call("nope", {}, "c1")])
    assert block is None, "nothing to execute"
    assert _shape(replies) == [
        ("c1", "nope", "error", "Unknown tool 'nope'. Choose one of the provided tools: add, boom.")
    ]


@pytest.mark.asyncio
async def test_a_failing_call_does_not_abort_its_siblings():
    replies, kept, _ = await _run_batch(
        _tools(add, boom), [_call("boom", {"x": 1}, "c1"), _call("add", {"a": 1, "b": 2}, "c2")]
    )
    assert _shape(replies) == [
        ("c1", "boom", "error", "Tool 'boom' failed: bad x"),
        ("c2", "add", "success", "3"),
    ]
    assert kept == {"tool_result_c2": 3}, "the error marker is not kept as a variable"


@pytest.mark.asyncio
async def test_wrong_arguments_are_reported_as_rejected():
    replies, _, _ = await _run_batch(_tools(typed), [_call("typed", {"a": 1, "zzz": 2}, "c1")])
    ((call_id, name, status, content),) = _shape(replies)
    assert (call_id, name, status) == ("c1", "typed", "error")
    assert content.startswith("Tool 'typed' rejected these arguments: ") and content.endswith(
        ". Check the parameter names and types."
    )


@pytest.mark.asyncio
async def test_unparsable_json_arguments_are_an_error_reply():
    raw = {"id": "c1", "type": "function", "function": {"name": "add", "arguments": "{not json"}}
    replies, _, block = await _run_batch(_tools(add), [raw])
    assert block is None
    ((_, _, status, content),) = _shape(replies)
    assert status == "error" and content.startswith("Could not parse tool arguments as JSON")


@pytest.mark.asyncio
async def test_block_timeout_keeps_finished_results_and_answers_the_rest(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "sandbox_execution_timeout", 0.3, raising=False)
    replies, kept, _ = await _run_batch(
        _tools(add, slow),
        [
            _call("add", {"a": 1, "b": 2}, "c1"),
            _call("slow", {"seconds": 5}, "c2"),
            _call("add", {"a": 3, "b": 4}, "c3"),
        ],
        timeout=0.3,
    )
    timed_out = "Tool '{name}' timed out after 0.3s. Try a narrower call or a different tool."
    assert _shape(replies) == [
        ("c1", "add", "success", "3"),
        ("c2", "slow", "error", timed_out.format(name="slow")),
        ("c3", "add", "error", timed_out.format(name="add")),
    ]
    assert kept.get("tool_result_c1") == 3, "the result computed before the timeout is kept"
    assert RAN == [("add", 1, 2)], "nothing after the stalled call ran"


@pytest.mark.asyncio
async def test_provider_side_invalid_tool_calls_are_answered():
    invalid = [
        {
            "name": "add",
            "args": "{bad",
            "id": "bad1",
            "error": "Unterminated string",
            "type": "invalid_tool_call",
        }
    ]
    replies, _, _ = await _run_batch(_tools(add), [_call("add", {"a": 1, "b": 2}, "c1")], invalid)
    assert _shape(replies) == [
        ("c1", "add", "success", "3"),
        (
            "bad1",
            "add",
            "error",
            "The provider could not parse this tool call (Unterminated string). Re-issue it with valid arguments.",
        ),
    ]


@pytest.mark.asyncio
async def test_bare_callables_are_still_budget_capped(monkeypatch):
    _caps(monkeypatch, run=1)
    replies, _, _ = await _run_batch(
        _tools(add), [_call("add", {"a": 1, "b": 2}, "c1"), _call("add", {"a": 3, "b": 4}, "c2")]
    )
    assert _shape(replies)[0] == ("c1", "add", "success", "3")
    c2 = replies[1]
    assert c2.status == "error" and "limit" in c2.content.lower(), c2.content
    assert RAN == [("add", 1, 2)], "the refused call never ran"


@pytest.mark.asyncio
async def test_step_discipline_runs_only_the_first_call_and_defers_the_rest():
    replies, _, _ = await _run_batch(
        _tools(add),
        [_call("add", {"a": 1, "b": 2}, "c1"), _call("add", {"a": 3, "b": 4}, "c2")],
        one_per_step=True,
    )
    assert _shape(replies) == [("c1", "add", "success", "3"), ("c2", "add", "error", DEFERRED_CALL_MESSAGE)]
    assert RAN == [("add", 1, 2)]


@pytest.mark.asyncio
async def test_step_discipline_defers_by_position_even_when_the_first_call_fails():
    replies, _, _ = await _run_batch(
        _tools(add, boom), [_call("boom", {}, "c1"), _call("add", {"a": 1, "b": 2}, "c2")], one_per_step=True
    )
    assert _shape(replies) == [
        ("c1", "boom", "error", "Tool 'boom' failed: bad x"),
        ("c2", "add", "error", DEFERRED_CALL_MESSAGE),
    ]


@pytest.mark.asyncio
async def test_step_discipline_off_runs_every_call():
    replies, _, _ = await _run_batch(
        _tools(add), [_call("add", {"a": 1, "b": 2}, "c1"), _call("add", {"a": 3, "b": 4}, "c2")]
    )
    assert [m.content for m in replies] == ["3", "7"]


@pytest.mark.asyncio
async def test_one_batch_shares_one_output_limit():
    async def text(n: int) -> str:
        return "x" * n

    replies, _, _ = await _run_batch(
        _tools(text), [_call("text", {"n": 8}, "c1"), _call("text", {"n": 8}, "c2")], output_limit=10
    )
    assert replies[0].content == "x" * 8
    assert replies[1].content == "xx" + TRUNCATION_MARKER.format(limit=2)


def test_calls_the_block_never_reached_are_all_answered():
    _, plan = plan_tool_calls(
        [_call("add", {"a": 1, "b": 2}, "c1"), _call("nope", {}, "c2")], [], _tools(add), one_per_step=False
    )
    replies = replies_without_execution(plan, "Not executed: boom.")
    assert _shape(replies) == [
        ("c1", "add", "error", "Not executed: boom."),
        ("c2", "nope", "error", "Unknown tool 'nope'. Choose one of the provided tools: add."),
    ], "pre-decided replies are delivered as they are"


# ── what only the block form has to get right ───────────────────────────────


@pytest.mark.asyncio
async def test_a_tool_name_that_is_not_an_identifier_goes_through_the_lookup():
    tools = _tools(**{"my-tool": dashed})
    replies, kept, block = await _run_batch(tools, [_call("my-tool", {"q": "hi"}, "c1")])
    assert f'{FC_TOOLS_KEY}["my-tool"]' in block
    assert _shape(replies) == [("c1", "my-tool", "success", "dashed:hi")]
    assert kept == {"tool_result_c1": "dashed:hi"}


@pytest.mark.asyncio
async def test_python_keywords_as_tool_or_argument_names_work():
    tools = _tools(**{"class": echo_args})
    args = {"from": "2026-01-01", "user-id": 5, "class": True}
    replies, _, block = await _run_batch(tools, [_call("class", args, "c1")])
    assert f'{FC_TOOLS_KEY}["class"]' in block
    assert _shape(replies) == [("c1", "class", "success", json.dumps(args, ensure_ascii=False))]


@pytest.mark.asyncio
async def test_argument_values_survive_the_round_trip():
    args = {
        "s": 'it\'s "quoted" \\ back\nslash\ttab   ünïcödé 🙂 {braces} ```',
        "n": -3,
        "f": 2.5,
        "b": False,
        "z": None,
        "l": [1, "two", {"three": [3.0, None]}],
        "d": {"k": {"kk": "v"}, "": "empty key"},
    }
    replies, _, _ = await _run_batch(_tools(echo_args), [_call("echo_args", args, "c1")])
    assert json.loads(replies[0].content) == args


@pytest.mark.asyncio
async def test_non_json_numbers_are_refused_before_any_code_is_written():
    replies, _, block = await _run_batch(_tools(echo_args), [_call("echo_args", {"n": float("nan")}, "c1")])
    assert block is None and replies[0].status == "error"
    assert replies[0].content.startswith("Tool arguments could not be encoded (non-JSON number)")


@pytest.mark.asyncio
async def test_result_variable_names_are_derived_from_the_call_id():
    _, plan = plan_tool_calls(
        [_call("add", {"a": 1, "b": 2}, "toolu_01-AB"), _call("add", {"a": 1, "b": 2}, "toolu_01-AB")],
        [],
        _tools(add),
        one_per_step=False,
    )
    assert [e["var"] for e in plan] == ["tool_result_toolu_01_AB", "tool_result_toolu_01_AB_2"]


@pytest.mark.asyncio
async def test_a_non_json_result_is_kept_as_the_reply_text():
    replies, kept, _ = await _run_batch(_tools(as_set), [_call("as_set", {}, "c1")])
    assert replies[0].content == _stringify({1}) == '"{1}"', "json.dumps(default=str), as a direct call gave"
    assert kept == {"tool_result_c1": '"{1}"'}, "kept as the text the reply carries, never dropped"


@pytest.mark.asyncio
async def test_a_find_tools_listing_reaches_the_reply():
    listing = "# Found 1 Matching Tool(s)\n**Query:** q\n## 1. `x`"

    async def find_tools(query: str, app_name: str) -> str:
        return listing

    replies, _, _ = await _run_batch(
        _tools(find_tools), [_call("find_tools", {"query": "q", "app_name": "a"}, "c1")]
    )
    assert replies[0].content == listing


# ── equivalence with the direct-call contract ───────────────────────────────


async def _reference(tools: Dict[str, Any], calls: List[dict]) -> List[Tuple[str, str, str, str]]:
    """The reply each call got from a direct ``fn(**kwargs)``, as ``tool_exec`` produced it."""
    out = []
    for index, call in enumerate(calls):
        call_id, name, raw = _tool_call_parts(call)
        call_id = call_id or f"call_{index}"
        if not name:
            out.append((call_id, "unknown", "error", "Tool call carried no tool name."))
            continue
        fn = tools.get(name)
        if fn is None:
            known = ", ".join(sorted(k for k in tools if not k.startswith("_")))
            out.append(
                (call_id, name, "error", f"Unknown tool '{name}'. Choose one of the provided tools: {known}.")
            )
            continue
        kwargs, err = _coerce_args(raw)
        if err:
            out.append((call_id, name, "error", err))
            continue
        try:
            result = await fn(**kwargs)
        except TypeError as exc:
            out.append(
                (
                    call_id,
                    name,
                    "error",
                    f"Tool '{name}' rejected these arguments: {exc}. Check the parameter names and types.",
                )
            )
        except Exception as exc:
            out.append((call_id, name, "error", f"Tool '{name}' failed: {exc}"))
        else:
            out.append((call_id, name, "success", _stringify(result)))
    return out


_ALPHABET = string.ascii_letters + string.digits + " \"'\\\n\t{}[]():,.-_ü🙂"


def _rand_str(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 12)))


def _rand_json(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.randint(0, 8 if depth < 2 else 5)
    if kind == 0:
        return None
    if kind == 1:
        return rng.choice([True, False])
    if kind == 2:
        return rng.randint(-(10**6), 10**6)
    if kind == 3:
        return rng.choice([0.0, -1.5, 3.25, 1e21, 1e-7])
    if kind in (4, 5):
        return _rand_str(rng)
    if kind == 6:
        return [_rand_json(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {_rand_key(rng): _rand_json(rng, depth + 1) for _ in range(rng.randint(0, 3))}


_KEYS = ["a", "b", "x", "from", "class", "user-id", "q", "", "with space", "λ", "n"]


def _rand_key(rng: random.Random) -> str:
    return rng.choice(_KEYS)


async def _fail(**kwargs: Any) -> None:
    raise RuntimeError(json.dumps(kwargs, ensure_ascii=False, sort_keys=True))


async def _ab(a: int, b: int = 0) -> Dict[str, Any]:
    return {"sum": a + b if isinstance(a, (int, float)) and isinstance(b, (int, float)) else [a, b]}


@pytest.mark.asyncio
async def test_random_calls_get_the_same_replies_as_a_direct_call():
    rng = random.Random(777)
    tools = _tools(echo_args, _fail, _ab, **{"odd-name": echo_args, "class": echo_args})
    names = list(tools) + ["missing", ""]
    for case in range(400):
        calls = []
        for i in range(rng.randint(1, 4)):
            name = rng.choice(names)
            args: Any = {_rand_key(rng): _rand_json(rng) for _ in range(rng.randint(0, 3))}
            if rng.random() < 0.2:
                args = json.dumps(args, ensure_ascii=False)  # the raw provider form
            call_id = rng.choice([f"call_{case}_{i}", f"toolu_{rng.randint(0, 99)}", ""])
            calls.append(_call(name, args, call_id))
        expected = await _reference(tools, calls)
        replies, _, _ = await _run_batch(tools, calls)
        assert _shape(replies) == expected, f"case {case}: {calls}"
