"""The after_plan lifecycle hook in the sandbox node: a blocked block does not run, and the model is told why."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.fc_actions import FC_PENDING_KEY, plan_tool_calls
from cuga.backend.hooks import AgentHooks, HookRegistry

pytestmark = pytest.mark.unit

GATE = "Read the record before you change it."
BUDGET_KEYS = ("tool_calls_used_run", "tool_calls_used_thread", "tool_budget_exhausted")


class _Gate(AgentHooks):
    def __init__(self):
        self.plans = []

    def after_plan(self, code):
        self.plans.append(code)
        return GATE


async def update(record: str, value: str) -> str:
    return "updated"


def _adapter(metadata=None, max_steps=1000):
    adapter = MagicMock()
    adapter._tools_context = {"update": make_tool_awaitable(update)}
    adapter._weak_schema_tool_names = frozenset()
    adapter._observed_tool_shapes = {}
    adapter._tracker = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.metadata_key = "cuga_lite_metadata"
    adapter.get_messages = MagicMock(return_value=[HumanMessage(content="change the record")])
    adapter.get_metadata = MagicMock(return_value=metadata or {})
    adapter.resolve_max_steps = MagicMock(return_value=max_steps)
    return adapter


def _state(script, step_count=0):
    variables_manager = MagicMock()
    variables_manager.get_variable_names = MagicMock(return_value=[])
    return SimpleNamespace(
        variables_manager=variables_manager,
        chat_messages=[HumanMessage(content="change the record")],
        tool_calls=[],
        step_count=step_count,
        script=script,
        thread_id="t",
        variables_storage={},
        variable_counter_state=0,
        variable_creation_order=[],
        tool_calls_used_run=0,
        tool_calls_used_thread=0,
    )


async def _run_node(adapter, state, hook):
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    eval_mock = AsyncMock(return_value=("executed", {}))
    node = create_sandbox_node(adapter, base_thread_id="t", base_apps_list=[])
    config = {
        "configurable": {
            "cuga_hooks": HookRegistry([hook]),
            "reflection_enabled": False,
            "pre_execute_verify_enabled": False,
        }
    }
    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.CodeExecutor.eval_with_tools_async",
            eval_mock,
        ),
        patch("cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.settings.policy.enabled", False),
    ):
        out = await node(state, config=config)
    return out, eval_mock


@pytest.mark.asyncio
async def test_a_blocked_codeact_block_does_not_run_and_the_model_gets_the_message():
    hook = _Gate()
    script = 'await update(record="r1", value="v")'
    out, eval_mock = await _run_node(_adapter(), _state(script), hook)

    eval_mock.assert_not_called()
    assert hook.plans == [script]
    last = out["chat_messages"][-1]
    assert isinstance(last, HumanMessage) and last.content == GATE
    assert out["step_count"] == 1
    assert all(key in out for key in BUDGET_KEYS)


@pytest.mark.asyncio
async def test_a_blocked_function_calling_plan_answers_every_call_id():
    calls = [
        {"name": "update", "args": {"record": "r1", "value": "v"}, "id": "c1", "type": "tool_call"},
        {"name": "update", "args": {"record": "r2", "value": "v"}, "id": "c2", "type": "tool_call"},
    ]
    adapter = _adapter()
    block, plan = plan_tool_calls(calls, [], adapter._tools_context, one_per_step=False)
    adapter.get_metadata = MagicMock(return_value={FC_PENDING_KEY: plan})

    out, eval_mock = await _run_node(adapter, _state(block), _Gate())

    eval_mock.assert_not_called()
    replies = [m for m in out["chat_messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in replies] == ["c1", "c2"]
    assert all(GATE in m.content for m in replies)
    assert FC_PENDING_KEY not in out[adapter.metadata_key]  # the plan is cleared
    assert all(key in out for key in BUDGET_KEYS)


@pytest.mark.asyncio
async def test_a_blocked_block_at_the_step_limit_ends_the_run():
    out, eval_mock = await _run_node(
        _adapter(max_steps=3), _state('await update(record="r1", value="v")', step_count=3), _Gate()
    )

    eval_mock.assert_not_called()
    assert isinstance(out, Command)
    assert out.update["error"] and out.update["execution_complete"] is True
    assert out.update["chat_messages"][-2].content == GATE  # the hook message, then the step-limit error
    assert all(key in out.update for key in BUDGET_KEYS)


@pytest.mark.asyncio
async def test_a_hook_that_allows_the_block_lets_it_run():
    class Allow(AgentHooks):
        def after_plan(self, code):
            return None

    _, eval_mock = await _run_node(_adapter(), _state('await update(record="r1", value="v")'), Allow())

    eval_mock.assert_called_once()
