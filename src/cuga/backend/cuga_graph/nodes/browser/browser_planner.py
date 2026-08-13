import json
from typing import Literal

from langchain_core.messages import AIMessage

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState, SubTaskHistory
from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent.browser_planner_agent import (
    BrowserPlannerAgent,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.browser_models import NextAgentPlan
from loguru import logger
from langgraph.constants import END
from langgraph.types import Command

tracker = ActivityTracker()


class PlannerNode(BaseNode):
    def __init__(self, planner_agent: BrowserPlannerAgent):
        super().__init__()
        self.browser_planner_agent = planner_agent
        self.node = create_partial(
            PlannerNode.node_handler,
            agent=self.browser_planner_agent,
            name=self.browser_planner_agent.name,
        )

    @staticmethod
    async def node_handler(
        state: AgentState, agent: BrowserPlannerAgent, name: str
    ) -> Command[Literal["ActionAgent", "QaAgent", END, "BrowserPlannerAgent"]]:
        if tracker.actions_count >= 4 and state.task_analyzer_output is not None:
            logger.debug("Resetting navigation paths")
            state.task_analyzer_output.navigation_paths = None
        result: AIMessage = await agent.run(state)
        next_step_plan = NextAgentPlan(**json.loads(result.content))
        # Safely attach last image if available
        last_image = None
        if getattr(tracker, "images", None) and len(tracker.images) > 0:
            last_image = tracker.images[-1]
        tracker.collect_step(
            step=Step(
                name=name,
                data=next_step_plan.model_dump_json(),
                image_before=last_image,
                current_url=state.url,
                observation_before=state.elements_as_string,
            )
        )
        next_instruction = next_step_plan.instruction
        state.plan = next_step_plan  # next_step_plan.model_dump_json()
        state.previous_steps.append(next_step_plan)
        state.messages.append(result)
        state.sender = name
        state.next_step = next_step_plan.instruction
        state.plan_next_agent = next_step_plan.next_agent

        if next_step_plan.next_agent == "ConcludeTaskAgent":
            state.stm_all_history.append(
                SubTaskHistory(
                    sub_task=state.format_subtask(),
                    steps=[s.instruction for s in state.previous_steps],
                    final_answer=next_step_plan.instruction,
                )
            )
            state.last_planner_answer = next_instruction
            state.final_answer = next_instruction
            state.stm_steps_history.append(next_instruction)
            return Command(update=state.model_dump(), goto=END)
        elif next_step_plan.next_agent == "QaAgent":
            state.last_question = next_instruction
            state.stm_steps_history.append("(QaAgent): " + next_instruction)
        elif next_step_plan.next_agent == "MemorizeAgent":
            state.last_planner_answer = next_instruction
            state.stm_steps_history.append("(MemorizeAgent): " + next_instruction)
            return Command(update=state.model_dump(), goto="BrowserPlannerAgent")
        elif next_step_plan.next_agent == "ActionAgent":
            state.stm_steps_history.append("(ActionAgent): " + next_instruction)
        else:
            raise Exception("Unhandled agent")

        return Command(update=state.model_dump(), goto=next_step_plan.next_agent)
