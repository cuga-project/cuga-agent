import pytest

from cuga.backend.cuga_graph.nodes.cuga_browser.cuga_browser_node import CugaBrowserNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cuga_browser_node_routes_to_subgraph():
    node = CugaBrowserNode()
    state = AgentState(input="open the dashboard", url="http://example.test")

    command = await node.node(state)

    assert command.goto == "CugaBrowserSubgraph"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cuga_browser_callback_promotes_last_planner_answer():
    node = CugaBrowserNode()
    state = AgentState(input="done", url="http://example.test")
    state.last_planner_answer = "the account count is 12"
    state.final_answer = ""

    command = await node.callback_node(state)

    assert state.final_answer == "the account count is 12"
    assert state.sender == NodeNames.CUGA_BROWSER
    assert command.goto == NodeNames.FINAL_ANSWER_AGENT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cuga_browser_callback_keeps_existing_final_answer():
    node = CugaBrowserNode()
    state = AgentState(input="done", url="http://example.test")
    state.last_planner_answer = "stale"
    state.final_answer = "already set"

    command = await node.callback_node(state)

    assert state.final_answer == "already set"
    assert command.goto == NodeNames.FINAL_ANSWER_AGENT
