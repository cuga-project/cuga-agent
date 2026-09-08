import json

import pytest
from langchain_core.messages import AIMessage

from cuga.backend.cuga_graph.nodes.browser import browser_planner as bp
from cuga.backend.cuga_graph.nodes.browser.browser_planner import PlannerNode
from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.task_models import AnalyzeTaskOutput
from cuga.backend.cuga_graph.state.agent_state import AgentState


class _FakePlannerAgent:
    name = "BrowserPlannerAgent"

    async def run(self, state):
        return AIMessage(
            content=json.dumps(
                {
                    "thoughts": ["t"],
                    "next_agent": "ActionAgent",
                    "instruction": "click the button",
                }
            )
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_planner_handler_does_not_crash_when_task_analyzer_output_is_none(monkeypatch):
    monkeypatch.setattr(bp.tracker, "actions_count", 4)
    monkeypatch.setattr(bp.tracker, "images", [])

    state = AgentState(input="do a task", url="http://example.test")
    assert state.task_analyzer_output is None

    command = await PlannerNode.node_handler(state, _FakePlannerAgent(), "BrowserPlannerAgent")

    assert command.goto == "ActionAgent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_planner_handler_resets_navigation_paths_when_present(monkeypatch):
    monkeypatch.setattr(bp.tracker, "actions_count", 4)
    monkeypatch.setattr(bp.tracker, "images", [])

    state = AgentState(input="do a task", url="http://example.test")
    state.task_analyzer_output = AnalyzeTaskOutput(
        navigation_paths={"approaches": [{"approach": "go to settings"}]}
    )

    await PlannerNode.node_handler(state, _FakePlannerAgent(), "BrowserPlannerAgent")

    assert state.task_analyzer_output.navigation_paths is None
