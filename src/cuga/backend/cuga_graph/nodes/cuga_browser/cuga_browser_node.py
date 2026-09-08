"""Standalone browser runtime node wrapper."""

from typing import Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from loguru import logger

from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames


class CugaBrowserNode(BaseNode):
    def __init__(self):
        super().__init__()
        self.name = NodeNames.CUGA_BROWSER

    async def node(self, state: AgentState, config: Optional[RunnableConfig] = None) -> Command:
        # Entering this wrapper starts a new browser turn. Resumes continue
        # inside the interrupted browser subgraph and do not re-enter here.
        state.sub_task = state.input
        state.sub_task_type = "web"
        state.sub_task_app = state.current_app or ""
        logger.info("Routing to BrowserPlannerAgent")
        return Command(update=state.model_dump(), goto="BrowserPlannerAgent")

    async def callback_node(self, state: AgentState, config: Optional[RunnableConfig] = None) -> Command:
        if not state.final_answer and state.last_planner_answer:
            state.final_answer = state.last_planner_answer
        state.hybrid_phase = None
        state.sender = self.name
        logger.info("CugaBrowser execution complete, routing to FinalAnswerAgent")
        return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)
