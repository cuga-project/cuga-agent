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
        self.sent = []

    def bind_tools(self, specs):
        self.bound_names = [s["function"]["name"] for s in specs]
        return self

    async def ainvoke(self, messages, config=None):
        self.calls += 1
        self.sent.append(list(messages))
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
    # A pass with no reads is sent back once; a second evidence-free pass is accepted.
    model = _Model([AIMessage(content="VERDICT: PASS"), AIMessage(content="VERDICT: PASS")])
    ctx, _ = _ctx(model)
    assert await verify_actions(ctx) is None
    assert model.calls == 2
    # No verdict at all is not a pass: nothing to push back on.
    model = _Model([AIMessage(content="Looks fine to me.")])
    ctx, _ = _ctx(model)
    assert await verify_actions(ctx) is None
    assert model.calls == 1


async def test_a_pass_without_reads_is_sent_back_to_look_and_can_turn_into_a_fail():
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import NO_EVIDENCE

    tracker = _Tracker()
    model = _Model(
        [
            AIMessage(content="The order looks right.\nVERDICT: PASS"),
            AIMessage(
                content="", tool_calls=[{"name": wire_name(LONG_GET), "args": {"order_id": 3146}, "id": "v1"}]
            ),
            AIMessage(content="VERDICT: FAIL\n- order 3146 also holds a Picture Frame"),
        ]
    )
    ctx, seen = _ctx(model, tracker=tracker)
    correction = await verify_actions(ctx)
    assert "Picture Frame" in correction
    assert model.sent[1][-1].content == NO_EVIDENCE
    assert seen == [(LONG_GET, {"order_id": 3146})]
    data = json.loads(tracker.steps[-1].data)
    assert data["pushed_back"] is True and data["reads"] == 1


async def test_a_fail_without_reads_is_not_sent_back():
    model = _Model([AIMessage(content="VERDICT: FAIL\n- no order was placed")])
    ctx, _ = _ctx(model)
    assert "no order was placed" in await verify_actions(ctx)
    assert model.calls == 1


def test_read_tools_cover_the_turns_apps_and_context_apps_but_never_credentials(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import (
        context_apps,
        read_tools,
        turn_apps,
        verifier_apps,
    )

    tools, context, _ = _setup()
    extra = {
        "phone_search_contacts": _tool("phone_search_contacts", "GET", "/contacts", app="phone"),
        "supervisor_profile": _tool("supervisor_profile", "GET", "/profile", app="supervisor"),
        "supervisor_account_passwords": _tool(
            "supervisor_account_passwords", "GET", "/account_passwords", app="supervisor"
        ),
        "spotify_show_song": _tool("spotify_show_song", "GET", "/song", app="spotify"),
    }
    tools.update(extra)
    context.update({name: context["gmail_show_inbox"] for name in extra})
    messages = [
        HumanMessage(content="Email the photos to my roommates."),
        ToolMessage(
            content="[...]",
            tool_call_id="p",
            name="execute_python",
            artifact={"code_calls": ["phone_search_contacts"]},
        ),
    ]
    monkeypatch.setattr(
        action_verifier, "_cfg", lambda key, default: ["supervisor"] if key == "context_apps" else default
    )
    apps = verifier_apps(["gmail"], turn_apps(messages, 1, tools), context_apps())
    assert apps == ["gmail", "phone", "supervisor"]
    names = [t.name for t in read_tools(messages, 1, tools, context, apps, max_tools=10)]
    assert names == ["phone_search_contacts", "gmail_show_inbox", "supervisor_profile"], (
        "used first, then by app priority; no passwords, no unrelated app"
    )
    assert [t.name for t in read_tools(messages, 1, tools, context, apps, max_tools=2)] == names[:2]


async def test_verifier_cannot_call_a_write_tool():
    model = _Model(
        [
            AIMessage(content="", tool_calls=[{"name": "amazon_place_order", "args": {}, "id": "v1"}]),
            AIMessage(content="VERDICT: PASS"),
            AIMessage(content="VERDICT: PASS"),  # after the push-back: the refused call was no read
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


# ── before a committing write ────────────────────────────────────────────────


def test_commit_calls_are_writes_whose_name_or_path_names_a_commitment():
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import is_commit_call

    patterns = ["order", "send"]
    assert is_commit_call(_tool("amazon_place_order", "POST", "/orders"), patterns)
    assert is_commit_call(_tool("gmail_x", "POST", "/messages/send"), patterns), "the path counts"
    assert not is_commit_call(_tool("amazon_delete_from_cart", "DELETE", "/cart/{id}"), patterns)
    assert not is_commit_call(_tool("amazon_show_orders", "GET", "/orders"), patterns), "reads never"


def _pending(call_id="c3"):
    return AIMessage(
        content="Ordering the dish rack now.",
        tool_calls=[{"name": "amazon_place_order", "args": {"payment_card_id": 1}, "id": call_id}],
    )


async def _check(model, messages):
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import check_before_write

    tools, context, seen = _setup()
    reply = await check_before_write(
        tool=tools["amazon_place_order"],
        args={"payment_card_id": 1},
        messages=messages,
        tools_by_name=tools,
        tools_context=context,
        model=model,
    )
    return reply, seen


async def test_block_reads_the_state_and_answers_the_call_with_the_problems():
    model = _Model(
        [
            AIMessage(content="", tool_calls=[{"name": "amazon_show_cart", "args": {}, "id": "v1"}]),
            AIMessage(content="VERDICT: BLOCK\n- the cart also holds products 96 and 756; remove them first"),
        ]
    )
    reply, seen = await _check(model, _turn()[:5] + [_pending()])
    assert reply.startswith("Not run: a check before this call found problems:")
    assert "products 96 and 756" in reply
    assert seen == [("amazon_show_cart", {})], "read the cart; the order itself never ran"
    assert set(model.bound_names) == {"amazon_show_cart", wire_name(LONG_GET)}


async def test_proceed_and_no_verdict_run_the_call():
    for replies in (["VERDICT: PROCEED", "VERDICT: PROCEED"], ["fine"]):
        model = _Model([AIMessage(content=text) for text in replies])
        reply, _ = await _check(model, _turn()[:5] + [_pending()])
        assert reply is None and model.calls == len(replies)


async def test_an_unread_proceed_is_sent_back_and_the_look_can_block_the_call():
    model = _Model(
        [
            AIMessage(content="VERDICT: PROCEED"),
            AIMessage(content="", tool_calls=[{"name": "amazon_show_cart", "args": {}, "id": "v1"}]),
            AIMessage(content="VERDICT: BLOCK\n- the cart also holds a workbench (1686)"),
        ]
    )
    reply, seen = await _check(model, _turn()[:5] + [_pending()])
    assert "workbench (1686)" in reply
    assert seen == [("amazon_show_cart", {})]


async def test_block_limit_per_tool_per_turn_lets_the_call_run(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import (
        WRITE_CHECK_BLOCKED,
        WRITE_CHECK_KEY,
    )

    monkeypatch.setattr(
        action_verifier, "_cfg", lambda key, default: 1 if key == "before_write_max_blocks" else default
    )
    blocked = ToolMessage(
        content="Not run",
        tool_call_id="c2",
        name="amazon_place_order",
        status="error",
        artifact={WRITE_CHECK_KEY: WRITE_CHECK_BLOCKED},
    )
    model = _Model([AIMessage(content="VERDICT: BLOCK\n- x")])
    reply, _ = await _check(model, _turn()[:5] + [blocked, _pending()])
    assert reply is None and model.calls == 0


async def test_tool_exec_blocks_a_commit_call_before_it_runs(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.tool_exec_node import create_tool_exec_node
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

    tools, context, seen = _setup()
    model = _Model([AIMessage(content="VERDICT: BLOCK\n- the cart holds unrequested items")])

    class _Adapter:
        messages_key = "chat_messages"
        metadata_key = "cuga_lite_metadata"
        execute_node_name = "sandbox"
        sender_name = "CugaLite"
        _tools_context = context
        _tools_context_ref = {"_lc_bind_tools_structured_by_name": tools}
        _tracker = MagicMock()
        _model = model

        def get_messages(self, state):
            return list(state.chat_messages)

        def resolve_max_steps(self, state, override):
            return 50

        def get_invoke_config(self, configurable):
            return {}

    state = SimpleNamespace(
        chat_messages=_turn()[:5] + [_pending()],
        step_count=0,
        tool_calls=[],
        tool_calls_used_run=0,
        tool_calls_used_thread=0,
    )
    try:
        node = create_tool_exec_node(_Adapter())
        result = await node(
            state,
            config={
                "configurable": {
                    "thread_id": "t",
                    "cuga_lite_execution_mode": "function_calling",
                    "cuga_lite_verify_before_write": True,
                }
            },
        )
    finally:
        for name in (
            "_tool_call_budget_context",
            "_thread_tool_call_budget_context",
            "_block_tool_call_budget_context",
            "_block_tool_call_cap_override_context",
        ):
            getattr(tracker_module, name).set(None)
    reply = [m for m in result.update["chat_messages"] if isinstance(m, ToolMessage)][-1]
    assert reply.status == "error" and "unrequested items" in reply.content
    assert reply.artifact == {"write_check": "blocked"}
    assert ("amazon_place_order", {"payment_card_id": 1}) not in seen


async def test_commit_calls_from_code_are_checked_and_a_block_raises_in_the_code():
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import guard_code_tools
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import (
        CODE_CALLS,
        ToolCallError,
        _recording,
    )

    tools, context, seen = _setup()
    model = _Model([AIMessage(content="VERDICT: BLOCK\n- the cart holds products 96 and 756")])
    blocked: list = []
    guarded = guard_code_tools(
        context,
        tools,
        code="await amazon_place_order(payment_card_id=1)",
        messages=_turn()[:5],
        model=model,
        blocked=blocked,
    )
    assert guarded["amazon_show_cart"] is context["amazon_show_cart"], "reads are not wrapped"
    calls: list = []
    token = CODE_CALLS.set(calls)
    try:
        with pytest.raises(ToolCallError, match="products 96 and 756"):
            await _recording("amazon_place_order", guarded["amazon_place_order"])(payment_card_id=1)
    finally:
        CODE_CALLS.reset(token)
    assert blocked == ["amazon_place_order"]
    assert calls == [], "a blocked call is not recorded as made"
    assert ("amazon_place_order", {"payment_card_id": 1}) not in seen

    model.replies.append(AIMessage(content="VERDICT: PROCEED"))
    assert await guarded["amazon_place_order"](payment_card_id=1) == {"message": "placed"}


def test_blocks_inside_code_count_toward_the_limit():
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.action_verifier import blocks_this_turn

    messages = _turn()[:5] + [
        ToolMessage(
            content="ToolCallError",
            tool_call_id="p",
            name="execute_python",
            artifact={"code_calls": [], "write_check": ["amazon_place_order"]},
        ),
        ToolMessage(
            content="Not run",
            tool_call_id="c9",
            name="amazon_place_order",
            status="error",
            artifact={"write_check": "blocked"},
        ),
    ]
    assert blocks_this_turn(messages, "amazon_place_order") == 2
    assert blocks_this_turn(messages, "amazon_show_cart") == 0
