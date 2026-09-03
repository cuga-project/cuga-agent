"""Pre-execute VERIFY: skip ungrounded writes; fail open otherwise."""

from __future__ import annotations

import ast
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


@pytest.mark.unit
def test_verify_telemetry_failure_is_non_blocking():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        log_pre_execute_verify,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify_result import VerifyDecision

    tracker = MagicMock()
    tracker.collect_step.side_effect = RuntimeError("tracker unavailable")

    recorded = log_pre_execute_verify(tracker, VerifyDecision(gate="unknown"))

    assert recorded is False
    tracker.collect_step.assert_called_once()


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
                config={
                    "configurable": {
                        "reflection_enabled": True,
                        "pre_execute_verify_enabled": True,
                        "llm": MagicMock(spec=[]),
                    }
                },
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
                config={
                    "configurable": {
                        "reflection_enabled": True,
                        "pre_execute_verify_enabled": True,
                        "llm": MagicMock(spec=[]),
                    }
                },
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
    model_factory = MagicMock(side_effect=RuntimeError("model should not be resolved"))
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
            model=None,
            model_factory=model_factory,
            config={},
            max_chars=1000,
        )
    assert decision.gate == "ok"
    chain.ainvoke.assert_not_called()
    model_factory.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_model_resolution_failure_fails_open_to_executor():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    eval_mock = AsyncMock(return_value=("executed", {}))
    adapter = _adapter()
    node = create_sandbox_node(adapter, base_thread_id="t", base_apps_list=[])

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.CodeExecutor.eval_with_tools_async",
            eval_mock,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.settings.policy.enabled",
            False,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node._llm_manager.get_model",
            side_effect=RuntimeError("verify model unavailable"),
        ),
    ):
        result = await node(
            _state(),
            config={"configurable": {"reflection_enabled": True, "pre_execute_verify_enabled": True}},
        )

    eval_mock.assert_awaited_once()
    assert result.get("execution_complete", False) is False
    assert "executed" in result["chat_messages"][-1].content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_setup_failure_fails_open_to_executor():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    eval_mock = AsyncMock(return_value=("executed", {}))
    adapter = _adapter()
    node = create_sandbox_node(adapter, base_thread_id="t", base_apps_list=[])

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.CodeExecutor.eval_with_tools_async",
            eval_mock,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.settings.policy.enabled",
            False,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.reflection_current_task",
            side_effect=RuntimeError("task context unavailable"),
        ),
    ):
        result = await node(
            _state(),
            config={
                "configurable": {
                    "reflection_enabled": True,
                    "pre_execute_verify_enabled": True,
                    "llm": MagicMock(spec=[]),
                }
            },
        )

    eval_mock.assert_awaited_once()
    assert result.get("execution_complete", False) is False
    assert "executed" in result["chat_messages"][-1].content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_telemetry_failure_downgrades_revise_and_runs_executor():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    eval_mock = AsyncMock(return_value=("executed", {}))
    revise_chain = MagicMock()
    revise_chain.ainvoke = AsyncMock(return_value=SimpleNamespace(content="GATE: revise\nALERT: x"))
    noop_plan = MagicMock()
    noop_plan.ainvoke = AsyncMock(return_value=SimpleNamespace(content=""))
    adapter = _adapter()
    adapter._tracker.collect_step.side_effect = [RuntimeError("tracker unavailable"), None, None, None]
    node = create_sandbox_node(adapter, base_thread_id="t", base_apps_list=[])

    with (
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
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
            return_value=revise_chain,
        ),
    ):
        result = await node(
            _state(),
            config={
                "configurable": {
                    "reflection_enabled": True,
                    "pre_execute_verify_enabled": True,
                    "llm": MagicMock(spec=[]),
                }
            },
        )

    eval_mock.assert_awaited_once()
    assert result["verify_revise_streak"] == 0
    assert "executed" in result["chat_messages"][-1].content


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
@pytest.mark.parametrize("expression", ['"x" * 1_000_000_000', "[0] * 1_000_000_000"])
def test_fold_rejects_oversized_sequences_before_multiplication(expression):
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        _BIN_OPS,
        _safe_eval,
    )

    multiply = MagicMock(side_effect=AssertionError("oversized multiplication was evaluated"))
    with patch.dict(_BIN_OPS, {ast.Mult: multiply}):
        with pytest.raises(ValueError, match="folded sequence too large"):
            _safe_eval(ast.parse(expression, mode="eval").body)
    multiply.assert_not_called()


@pytest.mark.unit
def test_fold_rejects_unbounded_string_formatting_before_modulo():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        _BIN_OPS,
        _safe_eval,
    )

    modulo = MagicMock(side_effect=AssertionError("string formatting was evaluated"))
    with patch.dict(_BIN_OPS, {ast.Mod: modulo}):
        with pytest.raises(ValueError, match="formatted value too large"):
            _safe_eval(ast.parse('"%1000000000s" % "x"', mode="eval").body)
    modulo.assert_not_called()


@pytest.mark.unit
def test_fold_preserves_small_percent_formatting():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import _safe_eval

    assert _safe_eval(ast.parse('"hello %s" % "world"', mode="eval").body) == "hello world"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("expression", "function_name"),
    [
        ('str([["x" * 4096] * 4096] * 4096)', "str"),
        ("sum([[0] * 4096] * 4096, [])", "sum"),
    ],
)
def test_fold_rejects_nested_aggregate_amplification_before_builtin(expression, function_name):
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        _FOLD_NAMESPACE,
        _safe_eval,
    )

    function = MagicMock(side_effect=AssertionError(f"{function_name} was evaluated"))
    with patch.dict(_FOLD_NAMESPACE, {function_name: function}):
        with pytest.raises(ValueError, match="folded aggregate too large"):
            _safe_eval(ast.parse(expression, mode="eval").body)
    function.assert_not_called()


@pytest.mark.unit
def test_fold_rejects_oversized_integer_before_final_power():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        _BIN_OPS,
        _safe_eval,
    )

    power = MagicMock(side_effect=_BIN_OPS[ast.Pow])
    with patch.dict(_BIN_OPS, {ast.Pow: power}):
        with pytest.raises(ValueError, match="folded integer too large"):
            _safe_eval(ast.parse("(10 ** 64) ** 64", mode="eval").body)
    assert power.call_count == 1


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


@pytest.mark.unit
def test_describe_write_arguments_does_not_fold_loop_accumulators():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        describe_write_arguments,
    )

    out = describe_write_arguments(
        "total = 0\n"
        "for tx in txs:\n"
        '    total += tx["amount"]\n'
        'await venmo_send_payment(amount=total, note="split")\n'
    )
    assert "-> 0" not in out
    assert "amount=) -> total" in out


@pytest.mark.unit
def test_describe_write_arguments_does_not_fold_if_else_assignments():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        describe_write_arguments,
    )

    fallback = describe_write_arguments(
        "amount = 0.0\n"
        "if found:\n"
        '    amount = round(found["total"] / n, 2)\n'
        "else:\n"
        "    amount = 0.0\n"
        "await venmo_send_payment(amount=amount)\n"
    )
    assert "-> 0.0" not in fallback
    assert "amount=) -> amount" in fallback

    branched = describe_write_arguments(
        "amount = 35.0\nif cond:\n    amount = 46.67\nawait venmo_send_payment(amount=amount)\n"
    )
    assert "-> 35.0" not in branched
    assert "-> 46.67" not in branched
    assert "amount=) -> amount" in branched


@pytest.mark.unit
def test_expander_visit_budget_keeps_diamond_fanout_unexpanded():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
        describe_write_arguments,
    )

    lines = ["v0 = (1, 1, 1, 1, 1, 1, 1, 1)"]
    for i in range(1, 4):
        prev = f"v{i - 1}"
        lines.append(f"v{i} = ({', '.join([prev] * 8)})")
    lines.append("await pay(amount=v3)")
    out = describe_write_arguments("\n".join(lines))
    assert "pay(amount=) -> v3" in out


@pytest.mark.unit
def test_verify_blocked_message_does_not_tell_model_to_change_the_value():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        verify_blocked_message,
    )

    msg = verify_blocked_message("amount 35.0 contradicts 46.67")
    assert VERIFY_BLOCKED_PREFIX in msg
    assert "Do not re-send the same value" not in msg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_history_drops_blocked_feedback():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        decide_pre_execute_verify,
        verify_blocked_message,
    )

    captured = {}
    chain = MagicMock()

    async def _ainvoke(payload, config=None):
        captured.update(payload)
        return SimpleNamespace(content="GATE: ok")

    chain.ainvoke = _ainvoke
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
        return_value=chain,
    ):
        await decide_pre_execute_verify(
            enabled=True,
            streak=0,
            script="await pay(amount=35.0)",
            chat_messages=[
                HumanMessage(content="split the amazon prime bill"),
                HumanMessage(content=verify_blocked_message("amount 35.0 is ungrounded")),
            ],
            variables_snapshot="",
            current_task="split the amazon prime bill",
            model=MagicMock(spec=[]),
            config={},
            max_chars=10_000,
        )
    history = captured.get("agent_history", "")
    assert "split the amazon prime bill" in history
    assert VERIFY_BLOCKED_PREFIX not in history
    assert "35.0 is ungrounded" not in history


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_flag_is_independent_of_reflection():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    eval_mock = AsyncMock(return_value=("executed", {}))
    revise_chain = MagicMock()
    revise_chain.ainvoke = AsyncMock(
        return_value=SimpleNamespace(content="GATE: revise\nALERT: amount 35.0 contradicts 46.67")
    )
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
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
            return_value=revise_chain,
        ),
    )
    with patches[0], patches[1], patches[2], patches[3]:
        held_out = await node(
            _state(),
            config={
                "configurable": {
                    "reflection_enabled": True,
                    "pre_execute_verify_enabled": False,
                    "llm": MagicMock(spec=[]),
                }
            },
        )
        eval_mock.assert_awaited()
        assert VERIFY_BLOCKED_PREFIX not in held_out["chat_messages"][-1].content
        revise_chain.ainvoke.assert_not_called()

        eval_mock.reset_mock()
        blocked = await node(
            _state(),
            config={
                "configurable": {
                    "reflection_enabled": False,
                    "pre_execute_verify_enabled": True,
                    "llm": MagicMock(spec=[]),
                }
            },
        )
        eval_mock.assert_not_called()
        assert VERIFY_BLOCKED_PREFIX in blocked["chat_messages"][-1].content


@pytest.mark.unit
def test_policy_user_input_skips_verify_feedback():
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable

    state = SimpleNamespace(
        intent=None,
        goal=None,
        input=None,
        chat_messages=[
            HumanMessage(content="split the bill"),
            HumanMessage(content=f"{VERIFY_BLOCKED_PREFIX}\namount 35.0"),
        ],
        tools=None,
        apps=None,
        current_agent=None,
        current_node=None,
        sub_task=None,
        current_task=None,
        final_answer=None,
        messages=None,
    )
    ctx = PolicyConfigurable.create_context_from_state(state, {"configurable": {}})
    assert ctx.user_input == "split the bill"
