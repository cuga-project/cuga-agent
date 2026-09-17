"""Replay CI planning-only replies through the real Lite continuation hook."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph import shared_nodes
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter
from cuga.config import settings

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "Fetching all my accounts (using a high limit) to inspect the raw structure.",
        "List my accounts and count them.",
    ],
)
@pytest.mark.parametrize("enabled", [None, False])
async def test_planning_reply_continues_before_final_answer(monkeypatch, reply, enabled):
    # None deliberately uses the shipped default: the CI failures happened with
    # this feature disabled, before the classifier or any tool could run.
    if enabled is not None:
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", enabled)
    monkeypatch.setattr(settings.policy, "enabled", False)
    monkeypatch.setattr(
        shared_nodes,
        "apply_context_summarization",
        AsyncMock(side_effect=lambda messages, *args, **kwargs: messages),
    )
    adapter = AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )
    adapter.resolve_bind_tools = AsyncMock(return_value=None)
    model = SimpleNamespace()
    model.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content=reply),
            AIMessage(content='{"auto_continue": true}'),
            AIMessage(content='```python\naccounts = await get_my_accounts()\nprint(accounts)\n```'),
        ]
    )
    vm = MagicMock()
    vm.get_variable_names.return_value = []
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="Get my accounts")],
        step_count=0,
        prepared_prompt="Use tools to retrieve accounts.",
        cuga_lite_metadata={},
        mcp_few_shot_messages=[],
        variables_storage=None,
        variable_counter_state=None,
        variable_creation_order=None,
        variables_manager=vm,
    )
    node = shared_nodes.create_call_model_node(adapter, model, settings)
    result = await node(state)
    if enabled is False:
        assert result.goto == END
        assert result.update["final_answer"] == reply
        assert model.ainvoke.await_count == 1
        return

    assert result.goto == "call_model"
    assert result.update["execution_complete"] is False
    assert result.update["final_answer"] == ""
    assert model.ainvoke.await_count == 2
    for key, value in result.update.items():
        setattr(state, key, value)
    result = await node(state)
    assert result.goto == "sandbox"
    assert "await get_my_accounts()" in result.update["script"]
