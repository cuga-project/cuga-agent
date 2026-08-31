import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import START
from langgraph.graph import StateGraph

from cuga.backend.cuga_graph.nodes.browser.action import ActionNode
from cuga.backend.cuga_graph.nodes.browser.browser_planner import PlannerNode
from cuga.backend.cuga_graph.nodes.cuga_browser.cuga_browser_node import CugaBrowserNode
from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.browser_models import NextAgentPlan
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames


class _PlannerThatRequestsClick:
    name = "BrowserPlannerAgent"

    async def run(self, state):
        plan = NextAgentPlan(
            thoughts=["Use the visible login control"],
            next_agent="ActionAgent",
            instruction="Click the login button",
        )
        return AIMessage(content=plan.model_dump_json(), name=self.name)


class _ActionAgentThatClicks:
    name = "ActionAgent"

    def run(self, state):
        return AIMessage(
            content="",
            name=self.name,
            tool_calls=[{"name": "click", "args": {"bid": "a1"}, "id": "call-login"}],
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cuga_browser_node_routes_to_parent_browser_planner():
    node = CugaBrowserNode()
    state = AgentState(input="open the dashboard", url="http://example.test")

    command = await node.node(state)

    assert command.goto == "BrowserPlannerAgent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cuga_browser_node_refreshes_task_for_followup_turn():
    node = CugaBrowserNode()
    state = AgentState(
        input="open the dashboard",
        current_app="dashboard",
        url="http://example.test",
    )

    await node.node(state)
    state.input = "open the reports page"
    state.current_app = "reports"

    command = await node.node(state)

    assert state.sub_task == "open the reports page"
    assert state.sub_task_type == "web"
    assert state.sub_task_app == "reports"
    assert command.goto == "BrowserPlannerAgent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cuga_browser_callback_promotes_last_planner_answer():
    node = CugaBrowserNode()
    state = AgentState(input="done", url="http://example.test")
    state.last_planner_answer = "the account count is 12"
    state.final_answer = ""
    state.hybrid_phase = "web"

    command = await node.callback_node(state)

    assert state.final_answer == "the account count is 12"
    assert state.hybrid_phase is None
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_action_interrupt_keeps_tool_call_in_parent_checkpoint():
    browser = CugaBrowserNode()
    planner = PlannerNode(_PlannerThatRequestsClick(), conclude_target="CugaBrowserCallback")
    action = ActionNode(_ActionAgentThatClicks())
    graph_builder = StateGraph(AgentState)
    graph_builder.add_node(NodeNames.CUGA_BROWSER, browser.node)
    graph_builder.add_node(planner.browser_planner_agent.name, planner.node)
    graph_builder.add_node(action.action_agent.name, action.node)
    graph_builder.add_node("QaAgent", lambda state: state)
    graph_builder.add_node("CugaBrowserCallback", browser.callback_node)
    graph_builder.add_edge(START, NodeNames.CUGA_BROWSER)
    graph_builder.add_edge(action.action_agent.name, planner.browser_planner_agent.name)
    graph = graph_builder.compile(
        checkpointer=MemorySaver(),
        interrupt_after=[action.action_agent.name],
    )
    config = {"configurable": {"thread_id": "browser-parent-interrupt"}}

    await graph.ainvoke(AgentState(input="click login"), config=config)

    snapshot = graph.get_state(config)
    messages = AgentState(**snapshot.values).messages
    assert snapshot.next == (planner.browser_planner_agent.name,)
    assert messages[-1].tool_calls == [
        {"name": "click", "args": {"bid": "a1"}, "id": "call-login", "type": "tool_call"}
    ]
