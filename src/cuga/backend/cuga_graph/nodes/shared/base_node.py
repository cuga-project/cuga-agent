import functools
import json
from abc import ABC, abstractmethod
from typing import Dict, Callable, Awaitable, Optional

from langchain_core.messages import AIMessage
from langgraph.types import Command

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    create_save_reuse_action,
    create_get_more_utterances,
)
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import (
    NodeNames,
    ActionIds,
    MessagePrefixes,
)
from cuga.config import settings


# ------------------------------------------------------------------------------
# Feature flags & tracker
# ------------------------------------------------------------------------------
tracker = ActivityTracker()
ENABLE_SAVE_REUSE = settings.features.save_reuse
ENABLE_CHAT = settings.features.chat


# ------------------------------------------------------------------------------
# Human-in-the-loop handler
# ------------------------------------------------------------------------------
class HumanInTheLoopHandler:
    """Simple handler for human-in-the-loop interactions."""

    def __init__(self) -> None:
        self._action_handlers: Dict[str, Callable[[AgentState, str], Command]] = {
            ActionIds.SAVE_REUSE: self._handle_save_reuse,
            ActionIds.SAVE_REUSE_INTENT: self._handle_save_reuse_intent,
        }

    def handle_human_response(self, state: AgentState, node_name: str) -> Command:
        """Handle any human response based on action_id."""
        action_id = state.hitl_response.action_id

        if action_id in self._action_handlers:
            return self._action_handlers[action_id](state, node_name)

        # Default fallback
        return Command(update=state.model_dump(), goto=NodeNames.END)

    def add_action_handler(
        self,
        action_id: str,
        handler: Callable[[AgentState, str], Command],
    ) -> None:
        """Add a custom action handler."""
        self._action_handlers[action_id] = handler

    @staticmethod
    def _handle_save_reuse(state: AgentState, node_name: str) -> Command:
        """Handle save/reuse action — request more utterances."""
        state.hitl_action = create_get_more_utterances()
        state.sender = node_name
        return Command(
            update=state.model_dump(),
            goto=NodeNames.SUGGEST_HUMAN_ACTIONS,
        )

    @staticmethod
    def _handle_save_reuse_intent(state: AgentState, node_name: str) -> Command:
        """Handle save/reuse intent — route to reuse agent."""
        state.sender = node_name
        return Command(
            update=state.model_dump(),
            goto=NodeNames.REUSE_AGENT,
        )


# ------------------------------------------------------------------------------
# Base node
# ------------------------------------------------------------------------------
class BaseNode(ABC):
    def __init__(
        self,
        agent: BaseAgent,
        hitl_handler: Optional[HumanInTheLoopHandler] = None,
    ) -> None:
        if not agent:
            raise ValueError("Agent must be provided")

        self.agent = agent
        self.hitl_handler = hitl_handler or HumanInTheLoopHandler()
        self.name = agent.name

        self.node = functools.partial(
            self.node_handler,
            agent=self.agent,
            name=self.name,
            hitl_handler=self.hitl_handler,
            invoke_method=self._invoke,
        )

    @staticmethod
    async def node_handler(
        state: AgentState,
        agent: BaseAgent,
        name: str,
        hitl_handler: HumanInTheLoopHandler,
        invoke_method: Callable[
            [AgentState, BaseAgent, str],
            Awaitable[Command],
        ],
    ) -> Command:
        """Generic node handler that manages state, routing, and invocation."""

        # Handle human responses if HITL is enabled
        if ENABLE_SAVE_REUSE and state.sender == NodeNames.WAIT_FOR_RESPONSE:
            return hitl_handler.handle_human_response(state, name)

        # Pre-invocation routing
        pre_invoke_command = await BaseNode.pre_invoke_handler(state, name)
        if pre_invoke_command:
            return pre_invoke_command

        # Main invocation
        command = await invoke_method(state, agent, name)

        # Post-invocation HITL routing
        if ENABLE_SAVE_REUSE and state.sender == NodeNames.PLAN_CONTROLLER_AGENT:
            state.hitl_action = create_save_reuse_action()
            state.sender = name
            return Command(
                update=state.model_dump(),
                goto=NodeNames.SUGGEST_HUMAN_ACTIONS,
            )

        return command

    @staticmethod
    async def pre_invoke_handler(
        state: AgentState,
        name: str,
    ) -> Optional[Command]:
        """Handles specific cases before the main agent invocation."""

        if state.sender == NodeNames.CHAT_AGENT:
            state.sender = name
            final_answer = state.chat_agent_messages[-1].content

            state.final_answer = final_answer
            output = {
                "thoughts": ["Chat response provided directly."],
                "final_answer": final_answer,
            }

            state.messages.append(
                AIMessage(content=json.dumps(output), name=name)
            )
            tracker.collect_step(
                Step(name=name, data=json.dumps(output))
            )

            return Command(update=state.model_dump(), goto=NodeNames.END)

        if state.sender == NodeNames.TASK_ANALYZER_AGENT and state.final_answer:
            state.sender = name
            output = {
                "thoughts": [
                    "No applications matched the request. "
                    "Providing available applications information."
                ],
                "final_answer": state.final_answer,
            }

            state.messages.append(
                AIMessage(content=json.dumps(output), name=name)
            )
            tracker.collect_step(
                Step(name=name, data=json.dumps(output))
            )

            return Command(update=state.model_dump(), goto=NodeNames.END)

        if state.sender == NodeNames.CUGA_LITE:
            state.sender = name
            output = {
                "thoughts": ["Cuga lite response provided directly."],
                "final_answer": state.final_answer,
            }

            state.messages.append(
                AIMessage(content=json.dumps(output), name=name)
            )
            tracker.collect_step(
                Step(name=name, data=json.dumps(output))
            )

            return Command(update=state.model_dump(), goto=NodeNames.END)

        return None

    @abstractmethod
    async def _invoke(
        self,
        state: AgentState,
        agent: BaseAgent,
        name: str,
    ) -> Command:
        """Node-specific invocation logic."""
        raise NotImplementedError

    @staticmethod
    async def _generate_and_process_response(
        state: AgentState,
        agent: BaseAgent,
        name: str,
        response_parser: Callable,
    ) -> str:
        """Run agent, parse response, update state, and return final answer."""

        response = await agent.run(state)
        state.messages.append(response)

        parsed_output = response_parser(
            **json.loads(response.content)
        )

        if ENABLE_CHAT:
            chat_message = (
                f"{MessagePrefixes.ANSWER_PREFIX}"
                f"{parsed_output.final_answer}"
            )
            state.append_to_last_chat_message(chat_message)

        tracker.collect_step(
            Step(
                name=name,
                data=parsed_output.model_dump_json(),
            )
        )

        final_answer = state.variables_manager.replace_variables_placeholders(
            parsed_output.final_answer
        )
        state.final_answer = final_answer

        return final_answer

