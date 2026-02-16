"""
CugaLite Node - Fast execution node using CugaLite subgraph
"""

import asyncio
import json
from typing import Literal, Dict, Any, List, Optional, Callable
from langgraph.types import Command
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import (
    AgentState,
    SubTaskHistory,
    load_user_preferences,
)
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.nodes.api.api_planner_agent.prompts.load_prompt import ActionName
from cuga.backend.cuga_graph.state.api_planner_history import CoderAgentHistoricalOutput
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames, ActionIds
from langchain_core.messages import HumanMessage
from cuga.backend.llm.utils.helpers import load_one_prompt
from cuga.config import settings


tracker = ActivityTracker()


class CugaLiteHumanInTheLoopHandler:
    """Handler for CugaLite-specific human-in-the-loop interactions"""

    def __init__(self):
        self._action_handlers: Dict[str, Callable] = {
            ActionIds.TOOL_APPROVAL: self._handle_tool_approval,
        }

    def handle_human_response(self, state: AgentState, node_name: str) -> Command:
        """Handle any human response based on action_id"""
        action_id = state.hitl_response.action_id

        if action_id in self._action_handlers:
            result = self._action_handlers[action_id](state, node_name)
        else:
            # Default fallback - continue to final answer
            result = Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)

        # Reset hitl_response after processing
        state.hitl_response = None
        # Update the command with the reset state
        return Command(update=state.model_dump(), goto=result.goto)

    def add_action_handler(self, action_id: str, handler: Callable):
        """Add a custom action handler"""
        self._action_handlers[action_id] = handler

    def _handle_tool_approval(self, state: AgentState, node_name: str) -> Command:
        """Handle tool execution approval response"""
        logger.info("Handling tool approval response in CugaLite")

        # Check if user approved or denied
        confirmed = state.hitl_response.confirmed

        if confirmed:
            logger.info("User approved tool execution - continuing with code execution")
            # Clear the approval requirement and continue execution
            # The code is already in state.script from the previous interrupt
            state.cuga_lite_metadata = {
                **state.cuga_lite_metadata,
                "approval_required": False,
                "user_approved": True,
            }
            state.sender = node_name
            logger.debug(
                f"[APPROVAL DEBUG] Setting user_approved=True in metadata: {state.cuga_lite_metadata}"
            )
            logger.debug(
                f"[APPROVAL DEBUG] State before routing to subgraph: cuga_lite_metadata={state.cuga_lite_metadata}"
            )
            # Route back to CugaLite subgraph to continue execution
            return Command(update=state.model_dump(), goto="CugaLiteSubgraph")
        else:
            logger.warning("User denied tool execution - stopping execution")
            # User denied - set final answer and end
            policy_name = state.cuga_lite_metadata.get("policy_name", "Tool Approval Policy")
            state.final_answer = f"❌ **Execution Cancelled**\n\nYou denied the execution of restricted tools required by **{policy_name}**.\n\nThe agent will not proceed with this task."
            state.execution_complete = True
            state.sender = node_name
            return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)


def _convert_sets_to_lists(value: Any) -> Any:
    """Recursively convert sets to lists for JSON serialization."""
    if isinstance(value, set):
        return list(value)
    elif isinstance(value, dict):
        return {k: _convert_sets_to_lists(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [_convert_sets_to_lists(item) for item in value]
    else:
        return value


class CugaLiteOutput(BaseModel):
    """Output model for CugaLite execution (similar to CodeAgentOutput)."""

    code: str = ""
    execution_output: str = ""
    steps_summary: List[str] = Field(default_factory=list)
    summary: str = ""
    metrics: Dict[str, Any] = Field(default_factory=dict)
    final_answer: str = ""


class CugaLiteNode(BaseNode):
    """Node wrapper for routing to CugaLite subgraph."""

    def __init__(self, langfuse_handler: Optional[Any] = None, prompt_template: Optional[str] = None):
        super().__init__()
        self.name = "CugaLite"
        if settings.advanced_features.enable_todos:
            prompt_filename = 'prompts/mcp_prompt_todos.jinja2'
        else:
            prompt_filename = 'prompts/mcp_prompt.jinja2'
        self.prompt_template = load_one_prompt(prompt_filename)
        self.langfuse_handler = langfuse_handler
        self.hitl_handler = CugaLiteHumanInTheLoopHandler()

    @staticmethod
    async def read_text_file(file_path: str) -> Optional[str]:
        """Read text file content using filesystem tool via registry.

        Args:
            file_path: Path to the file to read

        Returns:
            File content as string, or None if failed
        """
        try:
            from cuga.backend.cuga_graph.nodes.cuga_lite.tool_registry_provider import call_api

            result = await call_api(
                app_name="filesystem", api_name="filesystem_read_text_file", args={"path": file_path}
            )

            if isinstance(result, dict):
                if "error" in result:
                    logger.error(f"Error reading file {file_path}: {result['error']}")
                    return None
                if "result" in result:
                    return result["result"]
                if "content" in result:
                    return result["content"]
                return str(result)
            elif isinstance(result, str):
                return result
            else:
                logger.error(f"Unexpected result type from read_text_file: {type(result)}")
                return None

        except Exception as e:
            logger.error(f"Exception reading file {file_path}: {e}")
            return None

    @staticmethod
    def _get_new_variable_names(state: AgentState, initial_var_names: List[str]) -> List[str]:
        """Get names of variables created during execution in chronological order.

        Args:
            state: Current agent state
            initial_var_names: Variable names before execution

        Returns:
            List of new variable names in chronological creation order
        """
        # Use creation_order to ensure chronological ordering
        current_var_names = state.variable_creation_order
        return [name for name in current_var_names if name not in initial_var_names]

    @staticmethod
    def _log_variable_changes(state: AgentState, initial_var_names: List[str]) -> None:
        """Log variable changes after execution.

        Args:
            state: Current agent state
            initial_var_names: Variable names before execution
        """
        current_var_names = state.variables_manager.get_variable_names()
        new_var_names = CugaLiteNode._get_new_variable_names(state, initial_var_names)
        logger.info(
            f"Variables before: {len(initial_var_names)}, "
            f"after: {len(current_var_names)}, "
            f"new: {len(new_var_names)}"
        )

    def _generate_fallback_answer(self, state: AgentState, new_var_names: List[str]) -> str:
        """Generate fallback answer when execution result is empty.

        Args:
            state: Current agent state
            new_var_names: List of newly created variable names

        Returns:
            Fallback answer string
        """
        if not new_var_names:
            return "Task completed successfully."

        last_var_name = new_var_names[-1]
        last_var_value = state.variables_manager.get_variable(last_var_name)

        if last_var_value is None:
            return "Task completed successfully."

        if isinstance(last_var_value, (list, dict)):
            try:
                answer = json.dumps(last_var_value, indent=2, default=str)
            except Exception:
                answer = str(last_var_value)
        else:
            answer = str(last_var_value)

        logger.info(f"Using last variable '{last_var_name}' value as fallback answer")
        return answer

    @staticmethod
    def _has_error(answer: str) -> bool:
        """Check if answer contains error indicators.

        Args:
            answer: Answer string to check

        Returns:
            True if error indicators found, False otherwise
        """
        if not answer:
            return False

        error_indicators = ['Error during execution:', 'Error:', 'Exception:', 'Traceback', 'Failed to']
        return any(indicator in answer for indicator in error_indicators)

    @staticmethod
    async def _load_user_facts_for_top_level(state: AgentState, query: str) -> None:
        """Generate and load memory facts for top-level CugaLite requests."""
        try:
            from cuga.backend.memory.memory import Memory
            from cuga.backend.memory.agentic_memory import NamespaceNotFoundException
            from cuga.backend.memory.agentic_memory.schema import Message

            memory = Memory()
            namespace_id = "memory"
            memory_user_id = (
                state.user_id if state.user_id and state.user_id not in {"default", ""} else "default"
            )
            if not state.user_id:
                state.user_id = memory_user_id

            try:
                memory.get_namespace_details(namespace_id=namespace_id)
            except NamespaceNotFoundException:
                logger.info(f"Creating namespace for CugaLite facts: {namespace_id}")
                memory.create_namespace(namespace_id=namespace_id, user_id=memory_user_id)

            if query:
                messages = [Message(role="user", content=query)]
                task = asyncio.create_task(
                    memory.memory_client.extract_facts_from_messages_async(
                        namespace_id=namespace_id,
                        messages=messages,
                        metadata={"user_id": memory_user_id},
                        enable_conflict_resolution=False,
                    )
                )

                def _log_task_result(done_task: asyncio.Task) -> None:
                    try:
                        done_task.result()
                    except Exception:
                        logger.exception("CugaLite categorized fact extraction task failed")

                task.add_done_callback(_log_task_result)
                logger.debug(f"Queued CugaLite categorized fact extraction for user {state.user_id}")

            load_user_preferences(state, memory, namespace_id, query=query)
            if state.user_preferences:
                categories = list(state.user_preferences.keys())
                total_facts = sum(
                    len(facts) for facts in state.user_preferences.values() if isinstance(facts, list)
                )
                fact_preview = []
                for category, facts in state.user_preferences.items():
                    if not isinstance(facts, list):
                        continue
                    for fact in facts:
                        if not isinstance(fact, dict):
                            continue
                        key = str(fact.get("key") or "").strip()
                        value = str(fact.get("value") or "").strip()
                        content = str(fact.get("content") or "").strip()
                        pointer = f"{category}.{key}" if key else category
                        payload = value if value else content
                        if payload:
                            payload = payload[:80]
                        fact_preview.append(f"{pointer}={payload}")
                        if len(fact_preview) >= 10:
                            break
                    if len(fact_preview) >= 10:
                        break
                logger.info(
                    f"[FACT DEBUG][CugaLiteNode] Loaded user preferences: categories={categories}, total_facts={total_facts}"
                )
                if fact_preview:
                    logger.debug(
                        "[FACT DEBUG][CugaLiteNode] Loaded facts preview: "
                        + "; ".join(fact_preview)
                    )
            else:
                logger.warning(
                    "[FACT DEBUG][CugaLiteNode] No user preferences loaded from memory for this query"
                )
        except Exception as e:
            logger.error(f"Error handling facts in CugaLiteNode: {e}")

    async def node(
        self, state: AgentState
    ) -> Command[
        Literal['FinalAnswerAgent', 'PlanControllerAgent', 'CugaLiteSubgraph', 'SuggestHumanActions']
    ]:
        """Execute CugaLite graph wrapper and process results.

        Args:
            state: Current agent state

        Returns:
            Command to route to FinalAnswerAgent, PlanControllerAgent, or CugaLiteSubgraph
        """
        # Handle human-in-the-loop responses

        if state.sender == NodeNames.WAIT_FOR_RESPONSE and state.hitl_response:
            logger.info(f"Handling HITL response with action_id: {state.hitl_response.action_id}")
            return self.hitl_handler.handle_human_response(state, self.name)

        logger.info(f"CugaLite executing - state.input: {state.input}")
        logger.info(f"CugaLite executing - state.sub_task: {state.sub_task}")

        # Add initialization message
        # state.messages.append(
        #     AIMessage(
        #         content=json.dumps(
        #             {
        #                 "status": "initializing",
        #                 "message": f"Initializing CugaLite with {len(state.api_intent_relevant_apps) if state.api_intent_relevant_apps else 'all'} apps",
        #             }
        #         )
        #     )
        # )

        # Use sub_task as the input if available (preferred over state.input)
        task_input = state.sub_task if state.sub_task else state.input
        is_autonomous_subtask = settings.advanced_features.force_autonomous_mode or (
            state.sub_task is not None and state.sub_task.strip() != ""
        )
        logger.info(f"Using task_input: {task_input}")
        logger.info(f"is_autonomous_subtask: {is_autonomous_subtask}")

        is_top_level_request = not (state.sub_task and state.sub_task.strip())
        logger.info(
            f"[FACT DEBUG][CugaLiteNode] enable_fact={settings.advanced_features.enable_fact}, is_top_level_request={is_top_level_request}, user_id={state.user_id}"
        )
        if settings.advanced_features.enable_fact and is_top_level_request:
            await self._load_user_facts_for_top_level(state, query=state.input)

        # Check if task_input is just a markdown file path and replace with file content
        task_input_stripped = task_input.strip()
        if (
            task_input_stripped.endswith('.md')
            and '\n' not in task_input_stripped
            and ' ' not in task_input_stripped
        ):
            logger.info(f"Detected markdown file path: {task_input_stripped}")
            try:
                file_content = await self.read_text_file(task_input_stripped)
                if file_content:
                    task_input = file_content
                    task_input += "\n\nDo not use cuga_kowledge.md for the above task."
                    logger.info(f"Replaced task input with file content from {task_input_stripped}")
                else:
                    logger.warning(f"Failed to read file {task_input_stripped}, using original task input")
            except Exception as e:
                logger.warning(
                    f"Error reading markdown file {task_input_stripped}: {e}, using original task input"
                )

        # Determine app configuration
        app_names = None
        if state.sub_task_app:
            app_names = [state.sub_task_app]
            logger.info(f"Using app from state.sub_task_app: {app_names}")
        elif state.api_intent_relevant_apps:
            app_names = [app.name for app in state.api_intent_relevant_apps if app.type == 'api']
            logger.info(f"Using apps from state.api_intent_relevant_apps: {app_names}")

        # Store initial state in metadata for callback tracking
        initial_var_names = state.variables_manager.get_variable_names()
        logger.info(f"Storing {len(initial_var_names)} initial variable names for tracking")

        # Add the user's input to chat_messages (shared key)
        # Preserve existing chat history for multi-turn conversations
        updated_chat_messages = list(state.chat_messages) if state.chat_messages else []
        logger.info(f"Existing chat_messages count: {len(updated_chat_messages)}")
        if updated_chat_messages:
            logger.debug(f"Previous messages: {[type(msg).__name__ for msg in updated_chat_messages]}")

        updated_chat_messages.append(HumanMessage(content=task_input))
        logger.info(f"Added user input to chat_messages: {task_input[:100]}...")
        logger.info(f"Total chat_messages count after append: {len(updated_chat_messages)}")
        logger.info(
            f"[FACT DEBUG][CugaLiteNode] Forwarding user_preferences to subgraph: categories={list(state.user_preferences.keys()) if state.user_preferences else []}"
        )

        # Route to CugaLite subgraph with updated state
        logger.info("Routing to CugaLiteSubgraph")
        return Command(
            goto="CugaLiteSubgraph",
            update={
                "chat_messages": updated_chat_messages,
                "variables_storage": state.variables_storage,
                "variable_counter_state": state.variable_counter_state,
                "variable_creation_order": state.variable_creation_order,
                "sub_task": state.sub_task,
                "sub_task_app": state.sub_task_app,
                "api_intent_relevant_apps": state.api_intent_relevant_apps,
                "user_preferences": state.user_preferences,
                "cuga_lite_metadata": {
                    "initial_var_names": initial_var_names,
                    "is_autonomous_subtask": is_autonomous_subtask,
                },
            },
        )

    async def _apply_output_formatter(
        self, state: AgentState, config: Optional[RunnableConfig] = None
    ) -> None:
        """
        Apply OutputFormatter policies to the final answer from CugaLite execution.

        Args:
            state: Current agent state with final_answer set
            config: Optional LangGraph config (may contain policy_system)
        """
        # Get policy system from config (use provided config or create one with thread_id)
        if config is None:
            config = RunnableConfig(configurable={"thread_id": getattr(state, "thread_id", None)})

        # Use shared utility function for consistent OutputFormatter policy application
        from cuga.backend.cuga_graph.policy.output_formatter_utils import apply_output_formatter_policies

        await apply_output_formatter_policies(state, config, context="CugaLiteCallback")

    async def callback_node(
        self, state: AgentState, config: Optional[RunnableConfig] = None
    ) -> Command[
        Literal['FinalAnswerAgent', 'PlanControllerAgent', 'SuggestHumanActions', 'CugaLiteSubgraph']
    ]:
        """Process results after CugaLite subgraph execution."""
        logger.info("CugaLite callback node - processing subgraph results")
        logger.info(f"  - Current state.sender: {state.sender}")
        logger.info(
            f"  - state.final_answer: {state.final_answer[:100] if state.final_answer else 'None'}..."
        )

        # Handle human-in-the-loop responses (when coming back from WaitForResponse)
        if state.sender == NodeNames.WAIT_FOR_RESPONSE and state.hitl_response:
            logger.info(f"Callback handling HITL response with action_id: {state.hitl_response.action_id}")
            return self.hitl_handler.handle_human_response(state, self.name)

        # Check if we need to route to HITL for tool approval (first time, after subgraph)
        if state.hitl_action and state.hitl_action.action_id == ActionIds.TOOL_APPROVAL:
            logger.info("Tool approval required - routing to SuggestHumanActions")
            # IMPORTANT: Set sender so WaitForResponse knows where to return to
            state.sender = self.name  # Update state object directly
            logger.info(f"Set sender to: {state.sender}")
            return Command(
                update=state.model_dump(),  # Pass full state to ensure sender is included
                goto=NodeNames.SUGGEST_HUMAN_ACTIONS,
            )

        # Get metadata from state
        metadata = state.cuga_lite_metadata or {}
        initial_var_names = metadata.get("initial_var_names", [])
        is_autonomous_subtask = metadata.get("is_autonomous_subtask", False)

        answer = state.final_answer or "No answer found"
        logger.info(f"Extracted answer: {answer[:200]}...")

        # Log variable changes
        self._log_variable_changes(state, initial_var_names)

        # Process the results using the existing logic
        result = await self._process_results(
            state=state,
            answer=answer,
            initial_var_names=initial_var_names,
            is_autonomous_subtask=is_autonomous_subtask,
            config=config,
        )
        logger.info(f"CugaLiteCallback: Routing to {result.goto}, state.sender = {state.sender}")
        return result

    async def _process_results(
        self,
        state: AgentState,
        answer: str,
        initial_var_names: List[str],
        is_autonomous_subtask: bool,
        config: Optional[RunnableConfig] = None,
    ) -> Command[Literal['FinalAnswerAgent', 'PlanControllerAgent']]:
        """Process results from CugaLite graph execution and route to appropriate next node.

        Args:
            state: Agent state
            answer: Final answer from graph execution
            initial_var_names: Variable names before execution
            is_autonomous_subtask: Whether this is a subtask

        Returns:
            Command to route to FinalAnswerAgent or PlanControllerAgent
        """
        logger.info("Processing CugaLite execution results")
        logger.info(f"Answer: {answer[:200] if answer else 'None'}...")
        is_autonomous_subtask = settings.advanced_features.force_autonomous_mode or (
            state.sub_task is not None and state.sub_task.strip() != ""
        )
        # Check for errors
        has_error = self._has_error(answer)
        if has_error:
            logger.warning(f"Detected error in answer content: {answer[:200]}...")

        if has_error:
            logger.error("CugaLite execution failed with error")
            logger.error(f"Full answer: {answer}")

            # Update state with error information
            if is_autonomous_subtask:
                # For sub-tasks, add error to history and return to plan controller

                if state.api_planner_history:
                    state.api_planner_history[-1].agent_output = CoderAgentHistoricalOutput(
                        variables_summary="Execution failed",
                        final_output=answer,
                    )

                state.stm_all_history.append(
                    SubTaskHistory(
                        sub_task=state.format_subtask(),
                        steps=[],
                        final_answer=answer,
                    )
                )
                state.last_planner_answer = answer
                state.sender = self.name
                logger.info("CugaLite sub-task execution failed, returning error to PlanControllerAgent")
                return Command(update=state.model_dump(), goto="PlanControllerAgent")
            else:
                # For regular execution, set final answer with error
                state.final_answer = answer
                state.sender = NodeNames.CUGA_LITE

                # Apply OutputFormatter policies before routing to FinalAnswerAgent
                await self._apply_output_formatter(state, config)

                logger.info("CugaLite execution failed, proceeding to FinalAnswerAgent with error")
                logger.info(f"  - Set state.sender = {state.sender} (should be '{NodeNames.CUGA_LITE}')")
                logger.info("  - Routing to: FinalAnswerAgent")
                return Command(update=state.model_dump(), goto="FinalAnswerAgent")

        # chat_messages should already be synced since it's a shared key
        # But ensure they're properly formatted as BaseMessage objects
        if state.chat_messages:
            logger.info(f"Chat messages synced from subgraph: {len(state.chat_messages)} messages")

        # Variables are already synced via variables_storage (shared key)
        new_var_names = self._get_new_variable_names(state, initial_var_names)
        logger.info(
            f"After execution, variables_manager has {state.variables_manager.get_variable_count()} variables ({len(new_var_names)} new)"
        )

        # Check if answer is empty and provide a fallback
        if not answer or not answer.strip():
            logger.warning("Empty final answer detected, using fallback")
            answer = self._generate_fallback_answer(state, new_var_names)

        # Check if we're executing a sub-task
        if is_autonomous_subtask:
            # Sub-task execution - return to PlanControllerAgent

            # Keep only last N generated variables
            keep_last_n = settings.advanced_features.sub_task_keep_last_n
            if len(new_var_names) > keep_last_n:
                original_count = len(new_var_names)
                vars_to_keep = new_var_names[-keep_last_n:]
                vars_to_remove = new_var_names[:-keep_last_n]
                for var_name in vars_to_remove:
                    if state.variables_manager.remove_variable(var_name):
                        logger.debug(
                            f"Removed variable '{var_name}' to keep only last {keep_last_n} generated variables"
                        )
                new_var_names = vars_to_keep
                logger.info(f"Kept only last {keep_last_n} of {original_count} generated variables")

            state.api_last_step = ActionName.CONCLUDE_TASK
            state.guidance = None

            # Update api_planner_history with CoderAgentHistoricalOutput
            if state.api_planner_history:
                state.api_planner_history[-1].agent_output = CoderAgentHistoricalOutput(
                    variables_summary=state.variables_manager.get_variables_summary(
                        new_var_names, max_length=5000
                    )
                    if new_var_names
                    else "No new variables",
                    final_output=answer,
                )

            state.stm_all_history.append(
                SubTaskHistory(
                    sub_task=state.format_subtask(),
                    steps=[],
                    final_answer=answer,
                )
            )
            state.last_planner_answer = answer
            state.sender = self.name

            logger.info("CugaLite sub-task execution successful, proceeding to PlanControllerAgent")
            return Command(update=state.model_dump(), goto="PlanControllerAgent")
        else:
            # Regular execution - proceed to FinalAnswerAgent
            state.final_answer = answer
            state.sender = NodeNames.CUGA_LITE

            # Apply OutputFormatter policies before routing to FinalAnswerAgent
            await self._apply_output_formatter(state, config)

            logger.info("CugaLite execution successful, proceeding to FinalAnswerAgent")
            logger.info(f"  - Set state.sender = {state.sender} (should be '{NodeNames.CUGA_LITE}')")
            logger.info(
                f"  - state.final_answer length: {len(state.final_answer) if state.final_answer else 0}"
            )
            logger.info("  - Routing to: FinalAnswerAgent")
            return Command(update=state.model_dump(), goto="FinalAnswerAgent")
