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
                config={"configurable": {"reflection_enabled": True, "llm": MagicMock()}},
            )
        eval_mock.assert_not_called()
        assert VERIFY_BLOCKED_PREFIX in skipped["chat_messages"][-1].content
        assert skipped["verify_revise_streak"] == 1

        eval_mock.reset_mock()
        with patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
            return_value=ok_chain,
        ):
            ran = await node(
                _state(),
                config={"configurable": {"reflection_enabled": True, "llm": MagicMock()}},
            )
        eval_mock.assert_awaited()
        assert ran["verify_revise_streak"] == 0
        assert "executed" in ran["chat_messages"][-1].content
