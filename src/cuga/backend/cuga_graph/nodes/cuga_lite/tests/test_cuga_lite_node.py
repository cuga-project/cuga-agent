import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
from cuga.backend.cuga_graph.state.agent_state import AgentState


@pytest.mark.asyncio
async def test_callback_node_does_not_require_agent_state_error_field():
    state = AgentState(
        input="test request",
        url="https://example.com",
        chat_messages=[HumanMessage(content="hello")],
        final_answer="Task completed successfully.",
        sub_task="task_1",
    )
    node = CugaLiteNode()

    with (
        patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.save_trajectory",
            new_callable=AsyncMock,
        ) as mock_save_trajectory,
        patch.object(node, "_process_results", new_callable=AsyncMock) as mock_process_results,
        patch("cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node.settings.evolve.async_save", False),
    ):
        mock_process_results.return_value = Command(update={}, goto="FinalAnswerAgent")

        result = await node.callback_node(state)

    assert result.goto == "FinalAnswerAgent"
    mock_save_trajectory.assert_awaited_once()
    saved_messages, task_id, success = mock_save_trajectory.await_args.args
    assert saved_messages is not state.chat_messages
    assert [message.content for message in saved_messages] == ["hello"]
    assert task_id == "task_1"
    assert success is True
    mock_process_results.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_node_async_save_uses_chat_message_snapshot():
    state = AgentState(
        input="test request",
        url="https://example.com",
        chat_messages=[HumanMessage(content="hello")],
        final_answer="Task completed successfully.",
        sub_task="task_1",
    )
    node = CugaLiteNode()

    with (
        patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True),
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.save_trajectory",
            new_callable=AsyncMock,
        ) as mock_save_trajectory,
        patch.object(node, "_process_results", new_callable=AsyncMock) as mock_process_results,
        patch("cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node.settings.evolve.async_save", True),
    ):
        mock_process_results.return_value = Command(update={}, goto="FinalAnswerAgent")

        result = await node.callback_node(state)
        state.chat_messages.append(HumanMessage(content="mutated later"))
        await asyncio.sleep(0)

    assert result.goto == "FinalAnswerAgent"
    mock_save_trajectory.assert_awaited_once()
    saved_messages, task_id, success = mock_save_trajectory.await_args.args
    assert saved_messages is not state.chat_messages
    assert [message.content for message in saved_messages] == ["hello"]
    assert task_id == "task_1"
    assert success is True
    mock_process_results.assert_awaited_once()
