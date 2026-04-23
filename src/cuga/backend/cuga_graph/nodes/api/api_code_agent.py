import json
from typing import Literal

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.api.code_agent.code_agent import CodeAgent
from cuga.backend.cuga_graph.nodes.api.code_agent.model import CodeAgentOutput
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from langchain_core.messages import AIMessage

from cuga.backend.cuga_graph.state.api_planner_history import CoderAgentHistoricalOutput
from langgraph.types import Command
from cuga.backend.llm.models import LLMManager


tracker = ActivityTracker()
llm_manager = LLMManager()


class ApiCoder(BaseNode):
    def __init__(self, code_agent: CodeAgent):
        super().__init__()
        self.name = code_agent.name
        self.agent = code_agent
        self.node = create_partial(
            ApiCoder.node_handler,
            agent=self.agent,
            name=self.name,
        )

    @staticmethod
    async def node_handler(
        state: AgentState, agent: CodeAgent, name: str
    ) -> Command[Literal['APIPlannerAgent']]:
        # First time visit
        res = await agent.run(state)
        tracker.reload_steps(tracker.task_id)
        res_obj = CodeAgentOutput(**json.loads(res.content))
        res_obj.steps_summary.extend([res_obj.summary])
        state.api_planner_history[-1].agent_output = CoderAgentHistoricalOutput(
            variables_summary=state.variables_manager.get_variables_summary(
                [res_obj.variables.get("variable_name")], max_length=5000
            ),
            final_output=res_obj.summary,
        )
        # state.last_planner_answer = res_obj.summary
        tracker.collect_step(step=Step(name=name, data=res.content))
        msg = AIMessage(content=res_obj.model_dump_json())
        state.messages.append(msg)
        state.sender = name
        return Command(update=state.model_dump(), goto="APIPlannerAgent")
