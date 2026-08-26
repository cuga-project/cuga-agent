import json
from typing import Literal

from opentelemetry import trace as otel_trace

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.api.api_code_planner_agent.api_code_planner_agent import (
    APICodePlannerAgent,
)
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from langchain_core.messages import AIMessage
from loguru import logger
from langgraph.types import Command

from cuga.backend.cuga_graph.state.api_planner_history import CoderAgentHistoricalOutput

tracker = ActivityTracker()


class ApiCodePlanner(BaseNode):
    def __init__(self, code_agent: APICodePlannerAgent):
        super().__init__()
        self.name = code_agent.name
        self.agent = code_agent
        self.node = create_partial(
            ApiCodePlanner.node_handler,
            agent=self.agent,
            name=self.name,
        )

    @staticmethod
    async def node_handler(
        state: AgentState, agent: APICodePlannerAgent, name: str
    ) -> Command[Literal['CodeAgent', 'APIPlannerAgent']]:
        # First time visit
        res = await agent.run(state)
        missing_api_reported = bool(
            res.tool_calls
            and len(res.tool_calls) > 0
            and res.tool_calls[0].get("name") == "report_missing_api"
        )
        otel_trace.get_current_span().set_attribute(
            "cuga.api_code_planner.missing_api_reported", missing_api_reported
        )
        if missing_api_reported:
            logger.debug("** Tool call ** missing apis")
            missing_apis_msg = res.tool_calls[0].get("args").get("message")
            logger.debug(f"missing_apis_msg: {missing_apis_msg}")
            state.api_planner_codeagent_plan = ""
            tracker.collect_step(step=Step(name=name, data=json.dumps(res.tool_calls[0])))
            state.messages.append(AIMessage(content=json.dumps({"data": res.tool_calls[0]})))
            state.api_planner_history[-1].agent_output = CoderAgentHistoricalOutput(
                final_output=missing_apis_msg + "\n *Please use ApiShortlistingAgent with refined task*",
            )
            return Command(update=state.model_dump(), goto="APIPlannerAgent")

        else:
            state.api_planner_codeagent_plan = res.content
            logger.debug(f"\ncode_planner_plan:\n {res.content}")
            tracker.collect_step(step=Step(name=name, data=res.content))
            state.messages.append(AIMessage(content=json.dumps({"data": res.content})))
            return Command(update=state.model_dump(), goto="CodeAgent")
