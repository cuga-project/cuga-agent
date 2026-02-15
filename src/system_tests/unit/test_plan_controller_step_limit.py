"""
Tests for PlanController iteration limit to prevent unbounded agent loops.

Verifies that PlanController forces task conclusion when max_plan_iterations is exceeded,
preventing the agent from running 800+ steps (issue #21).
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from langchain_core.messages import AIMessage
from langgraph.types import Command

from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller import PlanControllerNode
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller_agent.prompts.load_prompt import (
    PlanControllerOutput,
)


def _make_state(**overrides) -> AgentState:
    """Create a minimal AgentState for testing."""
    defaults = {
        "input": "test task",
        "url": "",
        "sender": "APIPlannerAgent",
        "plan_controller_iteration_count": 0,
        "sub_tasks_progress": [],
    }
    defaults.update(overrides)
    return AgentState(**defaults)


def _make_llm_response(conclude_task=False) -> AIMessage:
    """Create a mock PlanControllerAgent LLM response."""
    output = PlanControllerOutput(
        thoughts=["thinking"],
        next_subtask="do something" if not conclude_task else "",
        subtasks_progress=["in-progress"],
        conclude_task=conclude_task,
        conclude_final_answer="done" if conclude_task else "",
        next_subtask_app="test_app" if not conclude_task else "",
        next_subtask_type="api" if not conclude_task else "",
    )
    return AIMessage(content=output.model_dump_json())


@pytest.mark.asyncio
async def test_plan_controller_forces_conclusion_when_max_iterations_exceeded():
    """PlanController should route to FinalAnswerAgent when iteration count exceeds max_plan_iterations."""
    state = _make_state(
        plan_controller_iteration_count=15,  # Already at limit
        sender="APIPlannerAgent",
        last_planner_answer=None,
    )

    # Need at least 2 subtasks so ignore_controller is False
    mock_task_decomposition = MagicMock()
    mock_task_decomposition.task_decomposition = [MagicMock(), MagicMock()]
    state.task_decomposition = mock_task_decomposition

    mock_agent = AsyncMock()
    mock_config = MagicMock()

    with patch(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller.settings"
    ) as mock_settings:
        mock_settings.advanced_features.max_plan_iterations = 15

        result = await PlanControllerNode.node_handler(
            state=state,
            agent=mock_agent,
            name="PlanControllerAgent",
            config=mock_config,
        )

    assert isinstance(result, Command)
    assert result.goto == "FinalAnswerAgent"
    assert state.plan_controller_iteration_count == 16
    assert "exceeded maximum" in state.last_planner_answer
    # LLM should NOT have been called
    mock_agent.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_controller_resets_count_on_first_entry():
    """PlanController should reset iteration count when entering from TaskDecompositionAgent."""
    state = _make_state(
        plan_controller_iteration_count=10,  # Leftover from previous run
        sender="TaskDecompositionAgent",
        api_intent_relevant_apps=[],
    )

    # Single subtask so ignore_controller is True -> takes fast path
    mock_subtask = MagicMock()
    mock_subtask.task = "do something"
    mock_subtask.app = "test_app"
    mock_subtask.type = "api"
    mock_task_decomposition = MagicMock()
    mock_task_decomposition.task_decomposition = [mock_subtask]
    state.task_decomposition = mock_task_decomposition

    mock_agent = AsyncMock()
    mock_config = MagicMock()

    with patch(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller.settings"
    ) as mock_settings:
        mock_settings.advanced_features.max_plan_iterations = 15
        mock_settings.advanced_features.force_lite_mode_apps = []

        with patch(
            "cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller.get_apis",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await PlanControllerNode.node_handler(
                state=state,
                agent=mock_agent,
                name="PlanControllerAgent",
                config=mock_config,
            )

    # Count should have been reset to 0
    assert state.plan_controller_iteration_count == 0


@pytest.mark.asyncio
async def test_plan_controller_allows_iterations_within_limit():
    """PlanController should proceed normally when iteration count is within limit."""
    state = _make_state(
        plan_controller_iteration_count=5,  # Well within limit
        sender="APIPlannerAgent",
        last_planner_answer=None,
    )

    mock_task_decomposition = MagicMock()
    mock_task_decomposition.task_decomposition = [MagicMock(), MagicMock()]
    state.task_decomposition = mock_task_decomposition

    # LLM returns conclude_task=True
    mock_agent = AsyncMock()
    mock_agent.run.return_value = _make_llm_response(conclude_task=True)

    mock_config = MagicMock()

    with patch(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller.settings"
    ) as mock_settings:
        mock_settings.advanced_features.max_plan_iterations = 15

        with patch(
            "cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller.tracker"
        ) as mock_tracker:
            mock_tracker.collect_step = MagicMock()

            result = await PlanControllerNode.node_handler(
                state=state,
                agent=mock_agent,
                name="PlanControllerAgent",
                config=mock_config,
            )

    assert isinstance(result, Command)
    assert result.goto == "FinalAnswerAgent"
    assert state.plan_controller_iteration_count == 6
    # LLM should have been called (iteration was within limit)
    mock_agent.run.assert_awaited_once()
