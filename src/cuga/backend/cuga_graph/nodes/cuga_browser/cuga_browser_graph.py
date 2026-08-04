from langgraph.constants import END, START
from langgraph.graph import StateGraph

from cuga.backend.cuga_graph.nodes.browser.action import ActionNode
from cuga.backend.cuga_graph.nodes.browser.action_agent.action_agent import ActionAgent
from cuga.backend.cuga_graph.nodes.browser.browser_planner import PlannerNode
from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent.browser_planner_agent import (
    BrowserPlannerAgent,
)
from cuga.backend.cuga_graph.nodes.browser.qa_agent.qa_agent import QaAgent
from cuga.backend.cuga_graph.nodes.browser.qa_agent_node import QaNode
from cuga.backend.cuga_graph.nodes.shared.interrupt_tool_node import InterruptToolNode
from cuga.backend.cuga_graph.state.agent_state import AgentState


def create_cuga_browser_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    planner = PlannerNode(BrowserPlannerAgent.create())
    action = ActionNode(ActionAgent.create())
    qa = QaNode(QaAgent.create())
    interrupt_tool_node = InterruptToolNode()

    graph.add_node(planner.browser_planner_agent.name, planner.node)
    graph.add_node(action.action_agent.name, action.node)
    graph.add_node(qa.qa_agent.name, qa.node)
    graph.add_node(interrupt_tool_node.name, interrupt_tool_node.node)

    graph.add_edge(START, planner.browser_planner_agent.name)
    graph.add_edge(qa.qa_agent.name, planner.browser_planner_agent.name)
    graph.add_edge(action.action_agent.name, planner.browser_planner_agent.name)
    graph.add_edge(interrupt_tool_node.name, planner.browser_planner_agent.name)

    return graph
