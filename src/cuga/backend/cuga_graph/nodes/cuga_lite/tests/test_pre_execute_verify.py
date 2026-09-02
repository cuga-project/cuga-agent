"""Pre-execute VERIFY: skip ungrounded writes; fail open otherwise."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import VERIFY_BLOCKED_PREFIX
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify_result import parse_verify_output


@pytest.mark.unit
def test_parse_verify_output_ok_revise_unknown():
    assert parse_verify_output("GATE: ok").gate == "ok"
    revise = parse_verify_output("GATE: revise\nALERT: amount 35.0 contradicts 46.67")
    assert revise.gate == "revise"
    assert "46.67" in revise.alert
    assert parse_verify_output("ship it").gate == "unknown"


def _adapter():
    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._weak_schema_tool_names = frozenset()
    adapter._observed_tool_shapes = {}
    adapter._tracker = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.get_messages = MagicMock(return_value=[])
    adapter.resolve_max_steps = MagicMock(return_value=1000)
    return adapter


def _state(**kwargs):
    variables_manager = MagicMock()
    variables_manager.get_variable_names = MagicMock(return_value=[])
    variables_manager.get_variable = MagicMock(return_value=None)
    variables_manager.remove_variable = MagicMock()
    variables_manager.add_variable = MagicMock()
    variables_manager.get_variables_summary = MagicMock(return_value="txn 8216 amount=46.67")
    base = dict(
        variables_manager=variables_manager,
        chat_messages=[HumanMessage(content="split the amazon prime bill")],
        tool_calls=[],
        step_count=0,
        script="await pay(amount=35.0)",
        thread_id="t",
        variables_storage={},
        variable_counter_state=0,
        variable_creation_order=[],
        reflection_apps=[],
        reflection_enable_find_tools=False,
        reflection_skills_enabled=False,
        reflection_skills_prompt_section="",
        verify_revise_streak=0,
        tool_calls_used_run=0,
        tool_calls_used_thread=0,
        sub_task="split the amazon prime bill",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_revise_skips_executor_ok_runs():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    eval_mock = AsyncMock(return_value=("executed", {}))
    revise_chain = MagicMock()
    revise_chain.ainvoke = AsyncMock(
        return_value=SimpleNamespace(content="GATE: revise\nALERT: amount 35.0 contradicts 46.67")
    )
    ok_chain = MagicMock()
    ok_chain.ainvoke = AsyncMock(return_value=SimpleNamespace(content="GATE: ok"))
    noop_plan = MagicMock()
    noop_plan.ainvoke = AsyncMock(return_value=SimpleNamespace(content=""))

    adapter = _adapter()
    node = create_sandbox_node(adapter, base_thread_id="t", base_apps_list=[])
    patches = (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.CodeExecutor.eval_with_tools_async",
            eval_mock,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.settings.policy.enabled",
            False,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.reflection_task",
            return_value=noop_plan,
        ),
    )

    with patches[0], patches[1], patches[2]:
        with patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
            return_value=revise_chain,
        ):
            skipped = await node(
                _state(),
                config={"configurable": {"reflection_enabled": True, "llm": MagicMock(spec=[])}},
            )
        eval_mock.assert_not_called()
        assert VERIFY_BLOCKED_PREFIX in skipped["chat_messages"][-1].content
        assert skipped["verify_revise_streak"] == 1
        verify_steps = [
            c.kwargs["step"]
            for c in adapter._tracker.collect_step.call_args_list
            if c.kwargs.get("step") and c.kwargs["step"].name == "PreExecuteVerify"
        ]
        assert verify_steps
        assert "revise" in (verify_steps[0].data or "")

        eval_mock.reset_mock()
        with patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
            return_value=ok_chain,
        ):
            ran = await node(
                _state(),
                config={"configurable": {"reflection_enabled": True, "llm": MagicMock(spec=[])}},
            )
        eval_mock.assert_awaited()
        assert ran["verify_revise_streak"] == 0
        assert "executed" in ran["chat_messages"][-1].content


@pytest.mark.unit
def test_has_write_call_skips_read_only_blocks():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import has_write_call

    assert not has_write_call('tools = await find_tools("x", "phone")\nprint(tools)')
    assert not has_write_call("orders = await amazon_show_orders_orders_get(page_index=0)")
    assert has_write_call("await venmo_create_payment_request_payment_requests_post(amount=1)")
    # unknown callables and unparseable code are verified, never skipped
    assert has_write_call("await pay(amount=35.0)")
    assert has_write_call("this is not python(")
    assert not has_write_call("")


@pytest.mark.unit
def test_describe_write_arguments_resolves_values_through_variables():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        describe_write_arguments,
    )

    # 92fe421_1: the wrong share is invisible at the call site.
    out = describe_write_arguments(
        "total_paid = 140.0\n"
        "total_people = len(roommates) + 1\n"
        "share = round(total_paid / total_people, 2)\n"
        'await venmo_create_payment_request_payment_requests_post('
        'user_email=e, amount=share, description="Amazon Subscription")\n'
    )
    assert "round(140.0 / (len(roommates) + 1), 2)" in out
    assert "'Amazon Subscription'" in out
    assert "roommates" in out  # flagged as coming from an earlier block

    # A fully constant expression folds to the value that will be written.
    folded = describe_write_arguments("n = 4\nawait send_money(amount=round(140.0 / n, 2))")
    assert "-> 35.0" in folded


@pytest.mark.unit
def test_describe_write_arguments_exposes_aggregation_source():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        describe_write_arguments,
    )

    # fa327a6_1: summing every transaction, not only the Amazon one.
    out = describe_write_arguments(
        'paid = sum(tx["amount"] for tx in brenda_txs)\n'
        'total = sum(o["paid_amount"] for o in amazon_orders)\n'
        "diff = round(total - paid, 2)\n"
        "await venmo_create_transaction_transactions_post(receiver_email=e, amount=abs(diff))\n"
    )
    assert "brenda_txs" in out and "amazon_orders" in out
    assert "sum(" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_read_only_block_skips_the_verify_call():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        decide_pre_execute_verify,
    )

    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=SimpleNamespace(content="GATE: revise\nALERT: x"))
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
        return_value=chain,
    ):
        decision = await decide_pre_execute_verify(
            enabled=True,
            streak=0,
            script='tools = await find_tools("orders", "amazon")\nprint(tools)',
            chat_messages=[],
            variables_snapshot="",
            current_task="t",
            model=MagicMock(),
            config={},
            max_chars=1000,
        )
    assert decision.gate == "ok"
    chain.ainvoke.assert_not_called()


@pytest.mark.unit
def test_fold_never_evaluates_attribute_chains_or_subscripts():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        describe_write_arguments,
    )

    # No free names, so a name-based check would let this reach eval().
    hostile = (
        "await pay(amount=(c for c in ().__class__.__base__.__subclasses__() "
        "if c.__name__ == 'catch_warnings').__next__())"
    )
    out = describe_write_arguments(hostile)
    assert "__subclasses__" in out and "->" in out
    assert "pay(amount=) -> <" not in out  # not folded to an object repr
    assert "-> 35.0" in describe_write_arguments("await pay(amount=round(140.0 / 4, 2))")
    assert "-> 2" in describe_write_arguments("await pay(n=len([1, 2]))")
    assert "-> " + repr(10**64) not in describe_write_arguments("await pay(n=10 ** 64 ** 2)")


@pytest.mark.unit
def test_nested_scope_assignment_does_not_shadow_the_call_scope():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        describe_write_arguments,
    )

    outer_call = (
        "async def helper():\n"
        "    amount = 46.67\n"
        "    return amount\n"
        "amount = 35.0\n"
        "await pay(amount=amount)\n"
    )
    assert "pay(amount=) -> 35.0" in describe_write_arguments(outer_call)

    inner_call = "amount = 35.0\nasync def helper():\n    amount = 46.67\n    await pay(amount=amount)\n"
    assert "pay(amount=) -> 46.67" in describe_write_arguments(inner_call)


@pytest.mark.unit
def test_mutator_method_on_unknown_receiver_is_a_write():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import has_write_call

    assert has_write_call("await client.update(amount=35.0)")
    assert has_write_call("state.items.append(x)")
    assert not has_write_call("rows = []\nrows.append(1)")
    assert not has_write_call("d = {}\nd.update(a=1)\nd.get('a')")
    assert not has_write_call("for req in reqs:\n    req['items'].append(1)")
    assert not has_write_call("await client.get(url)")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_revise_streak_cap_fails_open_without_calling_the_verifier():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        VERIFY_REVISE_STREAK_CAP,
        decide_pre_execute_verify,
    )

    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=SimpleNamespace(content="GATE: revise\nALERT: x"))
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
        return_value=chain,
    ):
        decision = await decide_pre_execute_verify(
            enabled=True,
            streak=VERIFY_REVISE_STREAK_CAP,
            script="await pay(amount=35.0)",
            chat_messages=[],
            variables_snapshot="",
            current_task="t",
            model=MagicMock(spec=[]),
            config={},
            max_chars=1000,
        )
    assert decision.gate == "ok"
    chain.ainvoke.assert_not_called()


@pytest.mark.unit
def test_reflection_current_task_skips_verify_feedback():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.response_utils import (
        reflection_current_task,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        verify_blocked_message,
    )

    state = SimpleNamespace(
        sub_task="",
        chat_messages=[
            HumanMessage(content="split the amazon prime bill"),
            HumanMessage(content=verify_blocked_message("amount 35.0 contradicts 46.67")),
            HumanMessage(content=verify_blocked_message("still ungrounded")),
        ],
    )
    assert reflection_current_task(state) == "split the amazon prime bill"
