from typing import Literal

from langgraph.types import Command, interrupt


from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step

tracker = ActivityTracker()


# Example usage models
# Updated node implementation example
class WaitForResponse(BaseNode):
    def __init__(self):
        super().__init__()
        self.name = "WaitForResponse"
        self.node = create_partial(
            WaitForResponse.node_handler,
        )

    @staticmethod
    async def node_handler(
        state: AgentState,
    ) -> Command[Literal["__end__", "FinalAnswerAgent", "ChatAgent", "CugaLite", "EntryRouter"]]:
        response = interrupt(state.hitl_action.model_dump())
        state.hitl_response = ActionResponse(**response)
        tracker.collect_step(Step(name="WaitForResponse", data=state.hitl_response.model_dump_json()))
        prev_sender = state.sender
        state.sender = "WaitForResponse"
        state.hitl_response.additional_data = state.hitl_action.additional_data
        state.hitl_action = None
        return Command(update=state.model_dump(), goto=prev_sender)
