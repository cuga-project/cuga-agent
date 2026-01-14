from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.final_answer_agent import (
    FinalAnswerAgent,
)
from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.prompts.load_prompt import (
    FinalAnswerOutput,
)
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames


class FinalAnswerNode(BaseNode):
    """Node responsible for generating the final answer."""

    def __init__(self, final_answer_agent: FinalAnswerAgent) -> None:
        super().__init__(final_answer_agent)

    async def _invoke(
        self,
        state: AgentState,
        agent: FinalAnswerAgent,
        name: str,
    ) -> Command:
        """
        Invoke the final answer agent to generate the response.

        This method is called by `BaseNode.node_handler`.
        """
        await self._generate_and_process_response(
            state=state,
            agent=agent,
            name=name,
            output_model=FinalAnswerOutput,
        )

        state.sender = name
        return Command(
            update=state.model_dump(),
            goto=NodeNames.END,
        )

