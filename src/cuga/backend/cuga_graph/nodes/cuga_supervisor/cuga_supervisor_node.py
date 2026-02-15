"""
CugaSupervisor Node - Node wrapper for routing to CugaSupervisor subgraph
"""

from typing import Any, Optional
from langgraph.types import Command
from langchain_core.runnables import RunnableConfig
from loguru import logger

from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState


class CugaSupervisorNode(BaseNode):
    """Node wrapper for routing to CugaSupervisor subgraph."""

    def __init__(self, langfuse_handler: Optional[Any] = None):
        super().__init__()
        self.name = "CugaSupervisor"
        self.langfuse_handler = langfuse_handler
        self._subgraph = None

    def set_subgraph(self, subgraph):
        """Set the compiled supervisor subgraph."""
        self._subgraph = subgraph

    async def node(self, state: AgentState, config: Optional[RunnableConfig] = None) -> Command:
        """
        Route to CugaSupervisor subgraph.

        Converts AgentState to CugaSupervisorState and invokes the subgraph.
        """
        if not self._subgraph:
            logger.error("CugaSupervisor subgraph not set")
            state.final_answer = "Error: Supervisor subgraph not initialized"
            state.sender = self.name
            return Command(update=state.model_dump(), goto="FinalAnswerAgent")

        logger.info("Routing to CugaSupervisor subgraph")
        logger.info(
            f"  - Current state.supervisor_chat_messages: {len(state.supervisor_chat_messages) if state.supervisor_chat_messages else 0} messages"
        )

        # Convert AgentState to CugaSupervisorState
        supervisor_state_dict = state.model_dump()

        # Initialize supervisor_chat_messages - only use supervisor_chat_messages, never chat_messages
        # chat_messages is for internal sub-agents, supervisor has its own conversation history
        from langchain_core.messages import HumanMessage, AIMessage

        # Helper to convert dict messages back to message objects if needed
        def ensure_message_objects(messages):
            """Convert dict messages to message objects if needed."""
            if not messages:
                return []
            result = []
            for msg in messages:
                if isinstance(msg, dict):
                    # Restore from dict representation
                    msg_type = msg.get("type", msg.get("__class__", ""))
                    content = msg.get("content", "")
                    if "HumanMessage" in str(msg_type) or msg.get("role") == "human":
                        result.append(HumanMessage(content=content))
                    elif "AIMessage" in str(msg_type) or msg.get("role") == "ai":
                        result.append(AIMessage(content=content))
                    else:
                        # Try to reconstruct from available data
                        result.append(
                            HumanMessage(content=content) if content else AIMessage(content=content)
                        )
                elif hasattr(msg, 'content'):
                    # Already a message object
                    result.append(msg)
                else:
                    logger.warning(f"Unknown message format: {type(msg)}")
            return result

        # Always use supervisor_chat_messages - initialize as empty list if it doesn't exist
        # This maintains the full conversation history for the supervisor across turns
        if state.supervisor_chat_messages and len(state.supervisor_chat_messages) > 0:
            # Use existing supervisor_chat_messages (persisted from previous turn)
            # Ensure messages are proper objects (they might be dicts from checkpointer)
            supervisor_state_dict["supervisor_chat_messages"] = ensure_message_objects(
                state.supervisor_chat_messages
            )
            logger.info(
                f"Using existing supervisor_chat_messages: {len(supervisor_state_dict['supervisor_chat_messages'])} messages"
            )
            # Log a preview of the conversation history
            if supervisor_state_dict["supervisor_chat_messages"]:
                preview = []
                for msg in supervisor_state_dict["supervisor_chat_messages"][-3:]:  # Last 3 messages
                    msg_type = type(msg).__name__
                    content_preview = msg.content[:50] if hasattr(msg, 'content') else str(msg)[:50]
                    preview.append(f"{msg_type}: {content_preview}...")
                logger.info(f"  - Conversation preview: {' | '.join(preview)}")
        else:
            # Initialize as empty list - supervisor starts fresh on first turn
            supervisor_state_dict["supervisor_chat_messages"] = []
            logger.info("Initialized supervisor_chat_messages as empty (first turn)")

        # Ensure the latest user input is added to supervisor_chat_messages if not already there
        if state.input:
            from langchain_core.messages import HumanMessage

            # Check if the last message is already this input
            supervisor_messages = supervisor_state_dict["supervisor_chat_messages"]
            last_msg = supervisor_messages[-1] if supervisor_messages else None
            # Only add if it's not already the last message
            if not last_msg or not isinstance(last_msg, HumanMessage) or last_msg.content != state.input:
                # Add the current input as a HumanMessage
                supervisor_state_dict["supervisor_chat_messages"].append(HumanMessage(content=state.input))
                logger.info(f"Added new user input to supervisor_chat_messages: {state.input[:50]}...")
            else:
                logger.debug("User input already present in supervisor_chat_messages (last message matches)")

        # Create CugaSupervisorState
        supervisor_state = CugaSupervisorState(**supervisor_state_dict)

        # Invoke subgraph
        try:
            result = await self._subgraph.ainvoke(supervisor_state, config=config)

            # Convert result back to AgentState format for compatibility
            if isinstance(result, CugaSupervisorState):
                result_dict = result.model_dump()
            elif isinstance(result, dict):
                result_dict = result
            else:
                # Try to get model_dump if it's a Pydantic model
                result_dict = getattr(result, 'model_dump', lambda: dict(result))()

            logger.info(f"Supervisor subgraph completed. Result type: {type(result)}")
            logger.info(f"Result keys: {list(result_dict.keys())[:20]}...")  # Show first 20 keys
            logger.info(f"  - final_answer present: {'final_answer' in result_dict}")
            if "final_answer" in result_dict:
                final_ans = result_dict['final_answer']
                logger.info(f"  - final_answer type: {type(final_ans)}")
                logger.info(f"  - final_answer length: {len(final_ans) if final_ans else 0}")
                if final_ans:
                    logger.info(f"  - final_answer preview: {final_ans[:200]}...")

            # Preserve supervisor_chat_messages for next turn
            # Note: chat_messages is for internal sub-agents, supervisor has its own conversation history
            if "supervisor_chat_messages" in result_dict:
                supervisor_msgs = result_dict["supervisor_chat_messages"]
                # Ensure we have a list (not None or empty)
                if not supervisor_msgs:
                    supervisor_msgs = []
                # Make a copy to avoid reference issues
                supervisor_msgs = list(supervisor_msgs) if supervisor_msgs else []
                # Ensure supervisor_chat_messages is explicitly preserved in the update
                # (it's now a field in AgentState, so it will be persisted by the checkpointer)
                result_dict["supervisor_chat_messages"] = supervisor_msgs
                logger.info(
                    f"Preserving supervisor_chat_messages ({len(supervisor_msgs)} messages) for next turn"
                )
                # Log conversation history for debugging
                if supervisor_msgs:
                    logger.info(f"  - Conversation history: {len(supervisor_msgs)} messages")
                    # Log all messages, not just last 3, to help debug
                    for i, msg in enumerate(supervisor_msgs):
                        msg_type = type(msg).__name__
                        content_preview = msg.content[:80] if hasattr(msg, 'content') else str(msg)[:80]
                        logger.info(f"    [{i}] {msg_type}: {content_preview}...")
                else:
                    logger.warning("supervisor_chat_messages is empty after subgraph execution!")

            # Ensure final_answer is preserved (it should be set by finalize node)
            # The finalize node sets final_answer, so it should be in result_dict
            # But we'll also check supervisor_chat_messages as a fallback
            if "final_answer" not in result_dict or not result_dict.get("final_answer"):
                logger.warning("final_answer not found in subgraph result, checking supervisor_chat_messages")
                # Try to extract from supervisor_chat_messages
                if "supervisor_chat_messages" in result_dict and result_dict["supervisor_chat_messages"]:
                    last_msg = result_dict["supervisor_chat_messages"][-1]
                    if hasattr(last_msg, 'content') and last_msg.content:
                        result_dict["final_answer"] = last_msg.content
                        logger.info("Extracted final_answer from supervisor_chat_messages")

            # Log what we're returning
            logger.info(
                f"Returning from supervisor node with final_answer: {bool(result_dict.get('final_answer'))}"
            )
            if result_dict.get("final_answer"):
                logger.info(f"  - final_answer preview: {result_dict['final_answer'][:200]}...")

            return Command(update=result_dict, goto="CugaSupervisorCallback")
        except Exception as e:
            # Use % formatting to avoid issues with curly braces in error messages
            error_msg = str(e)
            logger.error("Error in CugaSupervisor subgraph: %s", error_msg, exc_info=True)
            state.final_answer = f"Error in supervisor execution: {error_msg}"
            state.sender = self.name
            return Command(update=state.model_dump(), goto="FinalAnswerAgent")

    async def callback_node(self, state: AgentState, config: Optional[RunnableConfig] = None) -> Command:
        """
        Callback node to process results after supervisor subgraph execution.
        Handles approval responses and routes accordingly.
        """
        from cuga.backend.cuga_graph.utils.nodes_names import ActionIds, NodeNames

        logger.info("CugaSupervisor callback - processing results")
        logger.info(
            f"  - state.final_answer: {state.final_answer[:100] if state.final_answer else 'None'}..."
        )
        logger.info(f"  - state.hitl_action: {state.hitl_action is not None}")
        logger.info(f"  - state.hitl_response: {state.hitl_response is not None}")
        logger.info(f"  - state.sender: {state.sender}")
        logger.info(
            f"  - state.supervisor_chat_messages: {len(state.supervisor_chat_messages) if state.supervisor_chat_messages else 0} messages"
        )

        # Handle human-in-the-loop responses (when coming back from WaitForResponse)
        # Only process if we don't already have a final_answer (to prevent loops)
        if state.sender == "WaitForResponse" and state.hitl_response and not state.final_answer:
            logger.info(f"Handling HITL response with action_id: {state.hitl_response.action_id}")

            if state.hitl_response.action_id == ActionIds.AGENT_APPROVAL:
                # User has responded to agent approval request
                confirmed = state.hitl_response.confirmed

                if confirmed:
                    logger.info("User approved agent execution - routing back to supervisor subgraph")
                    # Convert AgentState to CugaSupervisorState and route back to execute_agents
                    supervisor_state_dict = state.model_dump()

                    # Ensure supervisor_chat_messages exists (initialize as empty if not present)
                    # Note: chat_messages is for internal sub-agents, supervisor has its own conversation history
                    if (
                        "supervisor_chat_messages" not in supervisor_state_dict
                        or not supervisor_state_dict.get("supervisor_chat_messages")
                    ):
                        supervisor_state_dict["supervisor_chat_messages"] = []

                    # Clear hitl_action to prevent re-triggering approval UI
                    supervisor_state_dict["hitl_action"] = None

                    # Ensure hitl_response is preserved so prepare_agents/execute_agents can detect approval
                    # The execute_agents node will handle clearing it after processing

                    # Create CugaSupervisorState
                    supervisor_state = CugaSupervisorState(**supervisor_state_dict)

                    # Route back to supervisor subgraph - prepare_agents will detect approval and route to execute_agents
                    # The execute_agents node will detect the approval and continue execution
                    return Command(
                        update=supervisor_state.model_dump(),
                        goto="CugaSupervisorSubgraph",
                    )
                else:
                    logger.warning("User denied agent execution")
                    state.final_answer = "Agent execution was cancelled by user."
                    state.last_planner_answer = state.final_answer
                    state.sender = self.name
                    return Command(update=state.model_dump(), goto="FinalAnswerAgent")

        # Check if we need to route to HITL for agent approval (first time, after subgraph)
        if state.hitl_action and state.hitl_action.action_id == ActionIds.AGENT_APPROVAL:
            logger.info("Agent approval required - routing to SuggestHumanActions")
            # Set sender so WaitForResponse knows where to return to
            state.sender = self.name
            logger.info(f"Set sender to: {state.sender}")
            return Command(
                update=state.model_dump(),
                goto=NodeNames.SUGGEST_HUMAN_ACTIONS,
            )

        # If there's a final answer, route to FinalAnswerAgent
        if state.final_answer:
            # Set sender to CugaSupervisor so FinalAnswerAgent handles it properly
            state.sender = self.name
            logger.info("Supervisor execution complete, routing to FinalAnswerAgent")
            logger.info(f"  - Set state.sender = {state.sender}")
            return Command(update=state.model_dump(), goto="FinalAnswerAgent")

        # If no final answer, check if we got an answer from supervisor_chat_messages
        # The supervisor subgraph should have set final_answer, but if not, extract from messages
        # Note: chat_messages is for internal sub-agents, supervisor uses supervisor_chat_messages
        if state.supervisor_chat_messages:
            last_message = state.supervisor_chat_messages[-1]
            if hasattr(last_message, 'content') and last_message.content:
                state.final_answer = last_message.content
                state.sender = self.name
                logger.info("Extracted answer from supervisor_chat_messages, routing to FinalAnswerAgent")
                return Command(update=state.model_dump(), goto="FinalAnswerAgent")

        # Fallback: no answer found
        logger.warning("Supervisor callback: no final answer found")
        state.final_answer = "Supervisor execution completed but no answer was generated."
        state.sender = self.name
        return Command(update=state.model_dump(), goto="FinalAnswerAgent")
