"""Stop check verify_actions: a verifier subagent checks the turn's state changes (AppWorld 245cb43, 0d22252)."""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.stop_checks import StopContext, run_stop_checks
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import wire_name
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers import action_verifier
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import (
    action_log,
    changed_apps,
    parse_verdict,
    verify_actions,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.harness_messages import harness_message
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict

pytestmark = pytest.mark.unit

LONG_GET = "amazon_show_order_order_order_id_get_with_a_name_long_enough_to_need_a_wire_name"


def _tool(name, method="GET", path="/x", params=(), app="amazon"):
    return create_tool_from_api_dict(
        name, {"description": "d", "parameters": list(params), "method": method, "path": path}, app
    )


ORDER_ID = [{"name": "order_id", "type": "integer", "required": True}]


class _Model:
    """Scripted chat model: hands out ``replies`` in order and records what was bound."""

    def __init__(self, replies, delay=0.0):
        self.replies = list(replies)
        self.bound_names = None
        self.calls = 0
        self.delay = delay

    def bind_tools(self, specs):
        self.bound_names = [s["function"]["name"] for s in specs]
        return self

    async def ainvoke(self, messages, config=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.replies.pop(0)


class _Tracker:
    def __init__(self):
        self.steps = []

    def collect_step(self, step):
        self.steps.append(step)


def _setup():
    tools = {
        "amazon_place_order": _tool(
            "amazon_place_order",
            "POST",
            "/orders",
            [{"name": "payment_card_id", "type": "integer", "required": False}],
        ),
        "amazon_show_cart": _tool("amazon_show_cart", "GET", "/cart"),
        LONG_GET: _tool(LONG_GET, "GET", "/orders/{order_id}", ORDER_ID),
        "gmail_show_inbox": _tool("gmail_show_inbox", "GET", "/inbox", app="gmail"),
        "amazon_login_auth_token_post": _tool("amazon_login_auth_token_post", "POST", "/auth/token"),
    }
    seen = []

    def _fn(name, result):
        async def call(**kwargs):
            seen.append((name, kwargs))
            return result

        return call

    context = {
        "amazon_place_order": _fn("amazon_place_order", {"message": "placed"}),
        "amazon_show_cart": _fn("amazon_show_cart", {"cart_items": []}),
        LONG_GET: _fn(LONG_GET, {"order_items": [{"product_name": "Picture Frame"}]}),
        "gmail_show_inbox": _fn("gmail_show_inbox", []),
        "amazon_login_auth_token_post": _fn("amazon_login_auth_token_post", {}),
    }
    return tools, context, seen


def _turn(write_ok=True):
    return [
        HumanMessage(content="earlier request"),
        ToolMessage(content="ok", tool_call_id="old", name="amazon_place_order"),
        HumanMessage(content="Buy the dish rack from my wish list."),
        AIMessage(content="", tool_calls=[{"name": "amazon_show_cart", "args": {}, "id": "c1"}]),
        ToolMessage(content='{"cart_items": []}', tool_call_id="c1", name="amazon_show_cart"),
        AIMessage(
            content="",
            tool_calls=[{"name": "amazon_place_order", "args": {"payment_card_id": 1}, "id": "c2"}],
        ),
        ToolMessage(
            content='{"message": "Debitted 1155.0", "order_id": 3146}' if write_ok else "422",
            tool_call_id="c2",
            name="amazon_place_order",
            status="success" if write_ok else "error",
        ),
    ]


def _ctx(model, messages=None, mode="hybrid", tracker=None):
    tools, context, seen = _setup()
    ctx = StopContext(
        answer="Ordered the OXO dish rack ($20).",
        messages=messages if messages is not None else _turn(),
        execution_mode=mode,
        tools_by_name=tools,
        tools_context=context,
        model=model,
        tracker=tracker,
    )
    return ctx, seen


# ── verdict parsing ──────────────────────────────────────────────────────────


def test_parse_verdict_pass_fail_and_none():
    assert parse_verdict("Looked at order 3146.\nVERDICT: PASS").passed is True
    assert parse_verdict("**VERDICT:** PARTIAL").passed is True, "nothing concrete to send back"
    assert parse_verdict("no verdict here").passed is None
    v = parse_verdict("VERDICT: FAIL\n- order 3146 has 3 items, only 1 requested\n- charged 1155, not 20")
    assert v.passed is False
    assert v.issues == ["order 3146 has 3 items, only 1 requested", "charged 1155, not 20"]


def test_parse_verdict_problems_above_the_line_and_think_blocks():
    v = parse_verdict("<think>VERDICT: PASS</think>\n- the cart had other items\nVERDICT: FAIL")
    assert v.passed is False and v.issues == ["the cart had other items"]


# ── what the verifier sees ───────────────────────────────────────────────────


def test_action_log_marks_writes_and_drops_oldest_first():
    tools, _, _ = _setup()
    messages = _turn()
    log = action_log(messages, 3, tools, result_chars=100, total_chars=10_000)
    assert "amazon_show_cart({}) (ok)" in log
    assert 'amazon_place_order({"payment_card_id": 1}) [WRITE] (ok)' in log
    assert "Debitted 1155.0" in log
    short = action_log(messages, 3, tools, result_chars=100, total_chars=150)
    assert short.startswith("[1 earlier call(s) omitted]") and "[WRITE]" in short


def test_action_log_shows_execute_python_code_and_its_writes():
    tools, _, _ = _setup()
    messages = [
        HumanMessage(content="q"),
        AIMessage(
            content="", tool_calls=[{"name": "execute_python", "args": {"code": "await x()"}, "id": "p"}]
        ),
        ToolMessage(
            content="done",
            tool_call_id="p",
            name="execute_python",
            artifact={"code_calls": ["amazon_place_order"]},
        ),
    ]
    log = action_log(messages, 1, tools, result_chars=100, total_chars=10_000)
    assert "execute_python [WRITES: amazon_place_order] (ok)" in log and "await x()" in log


def test_changed_apps_only_successful_writes_in_this_turn():
    tools, _, _ = _setup()
    assert changed_apps(_turn(), 3, tools) == ["amazon"]
    assert changed_apps(_turn(write_ok=False), 3, tools) == []


# ── the stop check ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "messages,mode",
    [
        (_turn(write_ok=False), "hybrid"),  # the write failed: nothing changed
        (_turn()[:5], "hybrid"),  # reads only
        (_turn(), "codeact"),  # no tool messages to read in CodeAct
    ],
)
async def test_no_check_without_a_successful_write_in_native_mode(messages, mode):
    model = _Model([])
    ctx, _ = _ctx(model, messages=messages, mode=mode)
    assert await verify_actions(ctx) is None
    assert model.calls == 0


async def test_fail_sends_the_agent_back_after_reading_state_with_get_tools_only():
    tracker = _Tracker()
    model = _Model(
        [
            AIMessage(
                content="", tool_calls=[{"name": wire_name(LONG_GET), "args": {"order_id": 3146}, "id": "v1"}]
            ),
            AIMessage(
                content="VERDICT: FAIL\n- order 3146 also contains a Picture Frame that was not requested"
            ),
        ]
    )
    ctx, seen = _ctx(model, tracker=tracker)
    correction = await verify_actions(ctx)

    assert "Picture Frame that was not requested" in correction
    assert "cannot be undone" in correction
    # Read tools of the changed app only: no writes, no auth, no other app; wire names on the wire.
    assert set(model.bound_names) == {"amazon_show_cart", wire_name(LONG_GET)}
    assert model.bound_names[0] == "amazon_show_cart", "tools the turn already used come first"
    assert seen == [(LONG_GET, {"order_id": 3146})]
    step = tracker.steps[-1]
    assert step.name == "Action_verification"
    data = json.loads(step.data)
    assert data["verdict"] == "FAIL" and data["reads"] == 1 and data["apps"] == ["amazon"]


async def test_pass_and_no_verdict_let_the_answer_through():
    for reply in ("Checked order 3146.\nVERDICT: PASS", "Looks fine to me."):
        ctx, _ = _ctx(_Model([AIMessage(content=reply)]))
        assert await verify_actions(ctx) is None


async def test_verifier_cannot_call_a_write_tool():
    model = _Model(
        [
            AIMessage(content="", tool_calls=[{"name": "amazon_place_order", "args": {}, "id": "v1"}]),
            AIMessage(content="VERDICT: PASS"),
        ]
    )
    ctx, seen = _ctx(model)
    assert await verify_actions(ctx) is None
    assert seen == [], "the write was refused, not run"


async def test_timeout_lets_the_answer_through(monkeypatch):
    monkeypatch.setattr(
        action_verifier, "_cfg", lambda key, default: 0.01 if key == "subagent_timeout" else default
    )
    ctx, _ = _ctx(_Model([AIMessage(content="VERDICT: FAIL\n- x")], delay=1.0))
    assert await verify_actions(ctx) is None


async def test_runs_through_the_stop_check_registry_once_per_turn():
    messages = _turn() + [harness_message("a runtime note", "repeat_call")]  # notes do not start a turn
    ctx, _ = _ctx(_Model([AIMessage(content="VERDICT: FAIL\n- wrong item")]), messages=messages)
    name, correction = await run_stop_checks(ctx, ["verify_actions"], fired=[])
    assert name == "verify_actions" and correction.startswith("Before you finish: ")
    assert await run_stop_checks(ctx, ["verify_actions"], fired=["verify_actions"]) is None
