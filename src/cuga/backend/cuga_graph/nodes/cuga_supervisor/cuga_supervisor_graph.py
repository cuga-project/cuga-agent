"""
CugaSupervisor LangGraph - Supervisor subgraph for orchestrating multiple CugaAgent instances

This subgraph coordinates multiple CugaAgent instances, delegating tasks and aggregating results.
Similar structure to cuga_lite_graph.py but focused on multi-agent orchestration.

Uses conversational mode: Supervisor acts as a single agent with delegation tools (similar to cuga_lite).
"""

import re
import inspect
import asyncio
from typing import Any, Dict, List, Optional, Union, Tuple
from loguru import logger
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
    CugaSupervisorState,
    AgentInfo,
)
from cuga.backend.cuga_graph.utils.context_management_utils import apply_context_summarization
from cuga.sdk import CugaAgent
from cuga.config import settings
from cuga.configurations.instructions_manager import get_all_instructions_formatted
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor

# Pattern for extracting Python code blocks
BACKTICK_PATTERN = r'```python(.*?)```'


def _resolve_names_from_caller_frame(variable_names: List[str]) -> Dict[str, Any]:
    """Resolve names from the delegated code's caller frame.

    LocalExecutor injects supervisor context into ``_async_main``'s globals; only
    using ``f_locals`` missed those bindings, so sub-agents received no variables
    and tasks showed e.g. ``amount=None``.
    """
    resolved: Dict[str, Any] = {}
    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame is not None else None
        if caller is None:
            return resolved
        for name in variable_names:
            if name in caller.f_locals:
                resolved[name] = caller.f_locals[name]
            elif name in caller.f_globals:
                resolved[name] = caller.f_globals[name]
    finally:
        del frame
    return resolved


def extract_and_combine_codeblocks(text: str) -> str:
    """Extract all codeblocks from a text string and combine them."""
    code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)

    if code_blocks:
        processed_blocks = []
        for block in code_blocks:
            block = block.strip()
            processed_blocks.append(block)

        combined_code = "\n\n".join(processed_blocks)

        if "print(" not in combined_code:
            return ""

        return combined_code

    stripped_text = text.strip()

    if "print(" not in stripped_text:
        return ""

    try:
        compile(stripped_text.replace('await ', ''), '<string>', 'exec')
        return stripped_text
    except SyntaxError:
        return ""


def make_tool_awaitable(func):
    """Wrap a sync function to make it awaitable (since agent always uses await)."""
    if inspect.iscoroutinefunction(func):
        return func

    async def async_wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    return async_wrapper


def append_chat_messages_with_step_limit(
    state: CugaSupervisorState, new_messages: List[BaseMessage]
) -> Tuple[List[BaseMessage], Optional[AIMessage]]:
    """Append new messages to supervisor_chat_messages with step counting and limit checking."""
    max_steps = (
        state.cuga_lite_max_steps
        if state.cuga_lite_max_steps is not None
        else getattr(settings.advanced_features, 'cuga_lite_max_steps', 50)
    )
    new_step_count = state.step_count + 1

    if new_step_count > max_steps:
        error_msg = (
            f"Maximum step limit ({max_steps}) reached. "
            f"The task has exceeded the allowed number of execution cycles. "
            f"Please simplify your request or break it into smaller tasks."
        )
        logger.warning(f"Step limit reached: {new_step_count} > {max_steps}")
        error_ai_message = AIMessage(content=error_msg)
        return (state.supervisor_chat_messages or []) + new_messages + [error_ai_message], error_ai_message

    logger.debug(f"Step count: {new_step_count}/{max_steps}")
    return (state.supervisor_chat_messages or []) + new_messages, None


def create_error_command(
    updated_messages: List[BaseMessage],
    error_message: AIMessage,
    step_count: int,
    additional_updates: Optional[Dict[str, Any]] = None,
) -> Command:
    """Create a Command to END with error information."""
    updates = {
        "supervisor_chat_messages": updated_messages,
        "script": None,
        "final_answer": error_message.content,
        "execution_complete": True,
        "error": error_message.content,
        "step_count": step_count + 1,
    }
    if additional_updates:
        updates.update(additional_updates)

    return Command(goto=END, update=updates)


def create_cuga_supervisor_graph(
    supervisor_model: BaseChatModel,
    agents: Dict[str, Union[CugaAgent, Dict[str, Any]]],
) -> StateGraph:
    """
    Create supervisor subgraph that orchestrates multiple CugaAgent instances.

    Args:
        supervisor_model: The language model for the supervisor
        agents: Dict mapping agent names to CugaAgent instances (internal) or A2A config (external)

    Returns:
        StateGraph implementing the CugaSupervisor architecture
    """
    return _create_supervisor_conversational_graph(supervisor_model, agents)


def _create_supervisor_conversational_graph(
    supervisor_model: BaseChatModel,
    agents: Dict[str, Union[CugaAgent, Dict[str, Any]]],
) -> StateGraph:
    """
    Create supervisor conversational mode graph - supervisor acts as a single agent with delegation tools.

    Similar to cuga_lite but uses agent delegation tools instead of regular tools.
    The supervisor can call agents via Python code, similar to how cuga_lite calls tools.

    Args:
        supervisor_model: The language model for the supervisor
        agents: Dict mapping agent names to CugaAgent instances (internal) or A2A config (external)

    Returns:
        StateGraph implementing the Supervisor Conversational architecture
    """
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
        A2AProtocol,
        HAS_A2A_SDK,
        _agent_card_description,
        delegate_task_via_a2a_sdk,
        fetch_agent_card,
        format_agent_card_for_prompt,
    )

    # Load prompt template as string (for Jinja2 rendering)
    prompt_filename = "supervisor_lite_prompt.jinja2"  # Keep filename for now (backward compatibility)
    if settings.advanced_features.enable_todos:
        # TODO: Create supervisor_conversational_prompt_todos.jinja2 if needed
        prompt_filename = "supervisor_lite_prompt.jinja2"
    prompt_path = Path(__file__).parent / "prompts" / prompt_filename
    # Read template file directly as string for Jinja2
    with open(prompt_path, 'r', encoding='utf-8') as f:
        prompt_template_str = f.read()
    instructions = get_all_instructions_formatted()

    # Create mutable agent delegation tools context
    agent_tools_context = {}
    pass_variables_a2a = getattr(settings.supervisor, "pass_variables_a2a", False)

    def create_agent_delegation_func(
        agent_name: str,
        agent_or_config: Union[CugaAgent, Dict[str, Any]],
        agent_card: Any = None,
    ):
        """Create a delegation function for a specific agent. agent_card is set for external A2A (http) when using a2a-sdk."""

        async def delegate_to_agent(task: str, variables: Optional[List[str]] = None) -> Any:
            logger.info(f"Delegating to {agent_name}: {task[:100]}...")

            if isinstance(agent_or_config, CugaAgent):
                vars_to_pass = {}
                if variables is not None:
                    vars_to_pass = _resolve_names_from_caller_frame(variables)
                result = await agent_or_config.invoke(
                    task,
                    thread_id=f"supervisor_conversational_{agent_name}",
                    variables=vars_to_pass if vars_to_pass else None,
                )
                return result.answer if hasattr(result, "answer") else str(result)

            if isinstance(agent_or_config, dict) and agent_or_config.get("type") == "external":
                a2a_config = agent_or_config.get("config", {}).get("a2a_protocol", {})
                endpoint = a2a_config.get("endpoint")
                transport = a2a_config.get("transport", "http")

                if agent_card is not None and HAS_A2A_SDK and transport == "http":
                    vars_to_pass = {}
                    if pass_variables_a2a and variables is not None:
                        vars_to_pass = _resolve_names_from_caller_frame(variables)
                    result = await delegate_task_via_a2a_sdk(
                        agent_card,
                        task,
                        auth=a2a_config.get("auth"),
                        timeout=float(a2a_config.get("timeout", 30)),
                        variables=vars_to_pass if vars_to_pass else None,
                    )
                    return result.get("result", "")
                else:
                    a2a_protocol = A2AProtocol(endpoint=endpoint, transport=transport)
                    await a2a_protocol.connect()
                    try:
                        vars_to_pass = {}
                        if variables is not None:
                            vars_to_pass = _resolve_names_from_caller_frame(variables)
                        result = await a2a_protocol.delegate_task(
                            target_agent=agent_name,
                            task=task,
                            context={"thread_id": None},
                            variables=vars_to_pass,
                        )
                        return result.get("result", "")
                    finally:
                        await a2a_protocol.disconnect()

            return f"Error: Unknown agent type for {agent_name}"

        return delegate_to_agent

    # Factory function to create prepare_agents_and_prompt node
    def create_prepare_agents_and_prompt_node(base_agents, base_prompt_template_str, base_instructions):
        """Factory to create prepare node with closure over agents and prompt template."""

        async def prepare_agents_and_prompt(
            state: CugaSupervisorState, config: Optional[RunnableConfig] = None
        ) -> Command:
            """Prepare agents, create delegation tools, and generate prompt."""
            logger.info("Preparing agents and prompt for supervisor conversational mode")

            # Build agent info for prompt
            agent_list = []
            agent_tools_for_prompt = []

            for agent_name, agent_or_config in base_agents.items():
                agent_card = None
                if isinstance(agent_or_config, CugaAgent):
                    agent_type = "internal"
                    description = getattr(agent_or_config, 'description', f"Internal agent: {agent_name}")
                elif isinstance(agent_or_config, dict):
                    agent_type = agent_or_config.get("type", "external")
                    a2a_cfg = agent_or_config.get("config", {}).get("a2a_protocol", {})
                    if agent_type == "external" and HAS_A2A_SDK and a2a_cfg.get("transport") == "http":
                        endpoint = a2a_cfg.get("endpoint")
                        if endpoint:
                            try:
                                agent_card = await fetch_agent_card(
                                    endpoint,
                                    auth=a2a_cfg.get("auth"),
                                    timeout=float(a2a_cfg.get("timeout", 30)),
                                )
                                description = _agent_card_description(agent_card)
                            except Exception as e:
                                logger.warning(f"Failed to fetch A2A agent card for {agent_name}: {e}")
                                description = agent_or_config.get(
                                    "description", f"External agent: {agent_name}"
                                )
                        else:
                            description = agent_or_config.get("description", f"External agent: {agent_name}")
                    else:
                        description = agent_or_config.get("description", f"{agent_type} agent: {agent_name}")
                else:
                    agent_type = "unknown"
                    description = f"Agent: {agent_name}"

                agent_entry = {
                    "name": agent_name,
                    "type": agent_type,
                    "description": description,
                }
                if agent_card is not None:
                    agent_entry["agent_card"] = format_agent_card_for_prompt(agent_card)
                agent_list.append(agent_entry)

                tool_name = f"delegate_to_{agent_name}"
                tool_func = create_agent_delegation_func(agent_name, agent_or_config, agent_card=agent_card)
                agent_tools_context[tool_name] = tool_func

                is_a2a_agent = agent_card is not None
                if is_a2a_agent and pass_variables_a2a:
                    tool_info = {
                        "name": tool_name,
                        "description": f"Delegate a task to the {agent_name} agent. {description} Variables are passed in request metadata.",
                        "params_str": "task: str, variables: Optional[List[str]] = None",
                        "params_doc": f"- task (str): The task description to send to {agent_name}\n- variables (Optional[List[str]]): Variable names to pass in A2A metadata (e.g. ['customer_id', 'order_data'])",
                        "response_doc": f"Returns the result from {agent_name}.",
                    }
                elif is_a2a_agent:
                    tool_info = {
                        "name": tool_name,
                        "description": f"Delegate a task to {agent_name}. {description}",
                        "params_str": "task: str",
                        "params_doc": f"- task (str): The task description to send to {agent_name}.",
                        "response_doc": f"Returns the result from {agent_name}.",
                    }
                else:
                    tool_info = {
                        "name": tool_name,
                        "description": f"Delegate a task to the {agent_name} agent. This agent specializes in: {description}",
                        "params_str": "task: str, variables: Optional[List[str]] = None",
                        "params_doc": f"- task (str): The task description to delegate to {agent_name}\n- variables (Optional[List[str]]): List of variable names to pass to the agent (e.g., ['customer_id', 'order_data'])",
                        "response_doc": f"Returns the result from {agent_name} agent execution.",
                    }
                agent_tools_for_prompt.append(tool_info)

            # Always enable todos tool for supervisor conversational mode
            from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import create_update_todos_tool

            todos_tool = await create_update_todos_tool()
            agent_tools_context['create_update_todos'] = make_tool_awaitable(todos_tool.func)
            agent_tools_for_prompt.append(
                {
                    "name": "create_update_todos",
                    "description": todos_tool.description,
                    "params_str": "todos: List[Dict[str, str]]",
                    "params_doc": "todos: List of todo items, each with 'text' and 'status' ('pending' or 'completed')",
                    "response_doc": "Returns the current list of todos with their status.",
                }
            )

            # Create prompt using template (similar to create_mcp_prompt)
            is_autonomous_subtask = state.sub_task is not None and state.sub_task.strip() != ""

            # Use Jinja2 template rendering
            from jinja2 import Template

            template = Template(base_prompt_template_str)
            dynamic_prompt = template.render(
                base_prompt=None,
                agents=agent_list,
                tools=agent_tools_for_prompt,
                is_autonomous_subtask=is_autonomous_subtask,
                instructions=base_instructions,
                enable_todos=True,  # Always enable todos for supervisor conversational mode
                special_instructions=None,
            )

            return Command(
                goto="call_model",
                update={
                    "tools_prepared": True,
                    "prepared_prompt": dynamic_prompt,
                    "step_count": 0,
                    "available_agents": {
                        name: AgentInfo(
                            name=name, type=info["type"], description=info["description"]
                        ).model_dump()
                        for name, info in zip([a["name"] for a in agent_list], agent_list)
                    },
                },
            )

        return prepare_agents_and_prompt

    # Factory function to create call_model node
    def create_call_model_node(base_model):
        """Factory to create call_model node with closure over model."""

        async def call_model(state: CugaSupervisorState, config: Optional[RunnableConfig] = None) -> Command:
            """Call the LLM to generate code or text response."""
            # ============================================================================
            # CONTEXT SUMMARIZATION - Manage context before LLM invocation
            # ============================================================================
            effective_chat_messages = await apply_context_summarization(
                state.supervisor_chat_messages or [],
                base_model,
                system_prompt=state.prepared_prompt,
                tools=None,  # Supervisor doesn't use traditional tools
                tracker=None,  # Supervisor doesn't have a tracker
                variables_storage=state.supervisor_variables,
                variable_counter_state=state.variable_counter_state,
                variable_creation_order=state.variable_creation_order,
                message_list_name="supervisor_chat_messages",  # Use supervisor message list
            )
            # ============================================================================
            # END CONTEXT SUMMARIZATION BLOCK
            # ============================================================================

            logger.info("Supervisor conversational: calling model")

            # Get prompt from state
            dynamic_prompt = state.prepared_prompt

            # Convert supervisor_chat_messages to messages for model
            messages_for_model = [{"role": "system", "content": dynamic_prompt}]

            # Add chat history from supervisor_chat_messages
            # Also add variables summary if available
            var_manager = state.supervisor_variables_manager
            existing_variable_names = var_manager.get_variable_names()
            variables_summary_text = None

            if existing_variable_names:
                variables_summary_text = var_manager.get_variables_summary(
                    variable_names=existing_variable_names
                )
                variables_addendum = f"\n\n## Available Variables\n\n{variables_summary_text}\n\nYou can use these variables directly by their names."

            logger.info(
                f"Processing {len(effective_chat_messages)} supervisor_chat_messages for model invocation"
            )

            # Create a copy of the messages list to avoid mutating the original until we return
            modified_chat_messages = list(effective_chat_messages)

            for i, msg in enumerate(modified_chat_messages):
                msg_type = type(msg).__name__
                msg_role = getattr(msg, 'type', None)
                logger.debug(
                    f"Message {i}: type={msg_type}, role={msg_role}, isinstance(HumanMessage)={isinstance(msg, HumanMessage)}, isinstance(AIMessage)={isinstance(msg, AIMessage)}"
                )

                if isinstance(msg, HumanMessage):
                    content = msg.content
                    content_modified = False

                    # Add variables summary to the LAST user message only
                    if variables_summary_text and i == len(modified_chat_messages) - 1:
                        content = content + variables_addendum
                        content_modified = True
                        logger.debug("Added variables summary to last user message")

                    # Update the local copy if content was modified
                    if content_modified:
                        modified_chat_messages[i] = HumanMessage(content=content)
                        logger.debug(f"Updated modified_chat_messages[{i}] with modified content (variables)")

                    messages_for_model.append({"role": "user", "content": content})
                elif isinstance(msg, AIMessage):
                    messages_for_model.append({"role": "assistant", "content": msg.content})
                else:
                    # Handle generic BaseMessage by checking the 'type' attribute
                    if (
                        msg_role == 'human'
                        or msg_role == 'user'
                        or (isinstance(msg, dict) and msg.get("type") == "human")
                    ):
                        content = msg.content if hasattr(msg, 'content') else msg.get("content", "")
                        content_modified = False

                        # Add variables summary to the LAST user message only
                        if variables_summary_text and i == len(modified_chat_messages) - 1:
                            content = content + variables_addendum
                            content_modified = True
                            logger.debug("Added variables summary to last user message")

                        # Update the local copy if content was modified
                        if content_modified:
                            modified_chat_messages[i] = HumanMessage(content=content)
                            logger.debug(
                                f"Updated modified_chat_messages[{i}] with modified content (variables)"
                            )

                        messages_for_model.append({"role": "user", "content": content})
                        logger.debug(f"Added BaseMessage as user message (role={msg_role})")
                    elif (
                        msg_role == 'ai'
                        or msg_role == 'assistant'
                        or (isinstance(msg, dict) and msg.get("type") == "ai")
                    ):
                        content = msg.content if hasattr(msg, 'content') else msg.get("content", "")
                        messages_for_model.append({"role": "assistant", "content": content})
                        logger.debug(f"Added BaseMessage as assistant message (role={msg_role})")
                    else:
                        logger.warning(
                            f"Skipping message {i} with unknown type: {msg_type}, role: {msg_role}"
                        )

            logger.debug(f"Total messages for model (including system): {len(messages_for_model)}")

            response = await base_model.ainvoke(messages_for_model, config=config or {})
            content = response.content
            reasoning_content = response.additional_kwargs.get('reasoning_content')

            # Extract code
            code = extract_and_combine_codeblocks(content) if content else ""
            if not code and reasoning_content:
                code = extract_and_combine_codeblocks(reasoning_content)

            if code:
                logger.info(f"Supervisor conversational: extracted code block ({len(code)} chars)")
                # Append AI response to our local modified_chat_messages
                final_messages = modified_chat_messages + [AIMessage(content=content)]

                # Check step limit
                max_steps = getattr(settings.advanced_features, 'cuga_lite_max_steps', 50)
                new_step_count = state.step_count + 1

                if new_step_count > max_steps:
                    error_msg = (
                        f"Maximum step limit ({max_steps}) reached. "
                        f"The task has exceeded the allowed number of execution cycles. "
                        f"Please simplify your request or break it into smaller tasks."
                    )
                    logger.warning(f"Step limit reached: {new_step_count} > {max_steps}")
                    error_ai_message = AIMessage(content=error_msg)
                    final_messages = final_messages + [error_ai_message]
                    return create_error_command(final_messages, error_ai_message, state.step_count)

                return Command(
                    goto="execute_agent_tool",
                    update={
                        "supervisor_chat_messages": final_messages,
                        "script": code,
                        "step_count": new_step_count,
                    },
                )
            else:
                # No code - final text answer
                logger.info("Supervisor conversational: final text answer (no code)")
                # Append AI response to our local modified_chat_messages
                final_messages = modified_chat_messages + [AIMessage(content=content)]

                # Check step limit
                max_steps = getattr(settings.advanced_features, 'cuga_lite_max_steps', 50)
                new_step_count = state.step_count + 1

                if new_step_count > max_steps:
                    error_msg = (
                        f"Maximum step limit ({max_steps}) reached. "
                        f"The task has exceeded the allowed number of execution cycles. "
                        f"Please simplify your request or break it into smaller tasks."
                    )
                    logger.warning(f"Step limit reached: {new_step_count} > {max_steps}")
                    error_ai_message = AIMessage(content=error_msg)
                    final_messages = final_messages + [error_ai_message]
                    return create_error_command(final_messages, error_ai_message, state.step_count)

                return Command(
                    goto=END,
                    update={
                        "supervisor_chat_messages": final_messages,
                        "script": None,
                        "final_answer": content,
                        "execution_complete": True,
                        "step_count": new_step_count,
                    },
                )

        return call_model

    # Factory function to create execute_agent_tool node
    def create_execute_agent_tool_node(base_agent_tools_context):
        """Factory to create execute_agent_tool node with closure over agent tools."""

        async def execute_agent_tool(state: CugaSupervisorState, config: Optional[RunnableConfig] = None):
            """Execute code with agent delegation tools available."""
            logger.info("Supervisor conversational: executing agent delegation code")

            # Get existing variables
            existing_vars = {}
            var_manager = state.supervisor_variables_manager
            for var_name in var_manager.get_variable_names():
                existing_vars[var_name] = var_manager.get_variable(var_name)

            # Add agent tools to context
            context = {**existing_vars, **base_agent_tools_context}

            try:
                # Execute code using CodeExecutor (reuse from cuga_lite)
                output, new_vars = await CodeExecutor.eval_with_tools_async(
                    code=state.script,
                    _locals=context,
                    state=state,  # Pass state for variables_manager
                    thread_id=state.thread_id,
                    apps_list=None,  # Not needed for agent delegation
                )

                logger.debug(f"Execution output: {output.strip()[:500]}...")

                # Update variables
                for name, value in new_vars.items():
                    state.supervisor_variables_manager.add_variable(
                        value, name=name, description="Created during agent delegation execution"
                    )

                # Create execution message
                execution_message_content = f"Execution output:\n{output}"
                new_message = HumanMessage(content=execution_message_content)
                updated_messages, error_message = append_chat_messages_with_step_limit(state, [new_message])

                if error_message:
                    return create_error_command(
                        updated_messages,
                        error_message,
                        state.step_count,
                        additional_updates={
                            "supervisor_variables": state.supervisor_variables,
                        },
                    )

                return {
                    "supervisor_chat_messages": updated_messages,
                    "supervisor_variables": state.supervisor_variables,
                    "step_count": state.step_count + 1,
                }
            except Exception as e:
                error_msg = f"Error during execution: {str(e)}"
                logger.error(error_msg, exc_info=True)
                new_message = HumanMessage(content=error_msg)
                updated_messages, limit_error_message = append_chat_messages_with_step_limit(
                    state, [new_message]
                )

                if limit_error_message:
                    return create_error_command(updated_messages, limit_error_message, state.step_count)

                return {
                    "supervisor_chat_messages": updated_messages,
                    "error": error_msg,
                    "execution_complete": True,
                    "step_count": state.step_count + 1,
                }

        return execute_agent_tool

    # Create node instances
    prepare_node = create_prepare_agents_and_prompt_node(agents, prompt_template_str, instructions)
    call_model_node = create_call_model_node(supervisor_model)
    execute_agent_tool_node = create_execute_agent_tool_node(agent_tools_context)

    # Build the graph
    graph = StateGraph(CugaSupervisorState)
    graph.add_node("prepare_agents_and_prompt", prepare_node)
    graph.add_node("call_model", call_model_node)
    graph.add_node("execute_agent_tool", execute_agent_tool_node)

    graph.add_edge(START, "prepare_agents_and_prompt")
    graph.add_edge("prepare_agents_and_prompt", "call_model")
    graph.add_edge("execute_agent_tool", "call_model")  # Loop back to call_model after execution

    return graph
