"""
CugaLite LangGraph - Unified subgraph combining CugaAgent and CodeAct

TODO: Multi-user, multi-model, multi-tools dependency injection refactoring
----------------------------------------------------------------------------
CURRENT STATE: Supports multi-user but with same configuration (shared model, tools, memory backend)
GOAL: Enable per-user configuration with isolated models, tools, and memory

This class needs architectural changes to support multi-user, multi-model, and multi-tools scenarios:

1. Multi-Tools Client Dependency Injection:
   - Replace direct tool_provider parameter with injectable tools_client per user_id
   - Use LangGraph's configurable pattern to inject tools_client at runtime
   - Store tools_client in graph config/state rather than closure
   - Enable per-user tool access control and isolation
   - Support dynamic tool loading/unloading per user session
   - Allow different tool sets per user based on permissions/subscriptions

2. Multi-Model Support:
   - Replace hardcoded model parameter with injectable model per user/session
   - Support per-user model selection (different LLMs for different users)
   - Enable model switching mid-conversation via configurable
   - Inject llm_manager or model_client rather than direct model instance
   - Support model-specific configurations (temperature, max_tokens, etc.) per user

3. Multi-User Memory/Storage:
   - Implement user_id-scoped memory storage (variables_storage, chat_messages)
   - Use injectable memory backend (e.g., per-user checkpointer)
   - Migrate from global state to user-scoped state partitioning
   - Consider LangGraph's built-in checkpointing with user_id as partition key
   - Isolate conversation history and variables per user

4. LangGraph Configurable Dependencies:
   - Leverage config["configurable"] for runtime dependency injection
   - Pass user_id, tools_client, model_client, memory_backend via configurable
   - Remove hardcoded global instances (tracker, llm_manager)
   - Make all external dependencies (LLM, tools, memory) injectable
   - Support per-request configuration overrides

5. State Isolation:
   - Ensure CugaLiteState is scoped per user session
   - Add user_id field to state for tracking and isolation
   - Implement proper state cleanup between user sessions
   - Consider multi-tenancy patterns for shared resources
   - Track model_id and tools_version in state for debugging

6. Thread Safety:
   - Ensure thread-safe access to user-scoped resources
   - Avoid shared mutable state between users
   - Use proper async/await patterns for concurrent user requests
   - Handle concurrent model/tool requests from different users
"""

import re
import json
import asyncio
import inspect
from typing import Any, Optional, Sequence, Dict, List, Tuple
from loguru import logger
from pydantic import BaseModel, Field


from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task import TaskAnalyzer
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.errors import extract_code_from_tool_use_failed
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt, PromptUtils
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
from cuga.config import settings
from cuga.configurations.instructions_manager import get_all_instructions_formatted
from cuga.backend.llm.utils.helpers import load_one_prompt
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.reflection import reflection_task
from pathlib import Path

try:
    from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
except ImportError:
    try:
        from langfuse.callback.langchain import LangchainCallbackHandler as LangfuseCallbackHandler
    except ImportError:
        LangfuseCallbackHandler = None


tracker = ActivityTracker()
llm_manager = LLMManager()

BACKTICK_PATTERN = r'```python(.*?)```'


def make_tool_awaitable(func):
    """Wrap a sync function to make it awaitable (since agent always uses await).

    Also automatically converts Pydantic model return values to dicts using .model_dump().

    If the function is already async, wrap it to handle Pydantic models.
    If it's sync, wrap it to be awaitable using asyncio.run_in_executor and handle Pydantic models.

    Args:
        func: The tool function (sync or async)

    Returns:
        An awaitable function (coroutine function) that returns dicts for Pydantic models
    """
    from pydantic import BaseModel

    async def wrapper_with_pydantic(*args, **kwargs):
        """Inner wrapper that handles Pydantic model conversion."""
        result = await func(*args, **kwargs) if inspect.iscoroutinefunction(func) else func(*args, **kwargs)

        # Convert Pydantic models to dicts
        if isinstance(result, BaseModel):
            return result.model_dump()

        return result

    if inspect.iscoroutinefunction(func):
        # Function is already async, just add Pydantic handling
        return wrapper_with_pydantic

    # Function is sync, make it awaitable and add Pydantic handling
    async def async_wrapper(*args, **kwargs):
        # For sync functions, run in executor to make them awaitable
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: func(*args, **kwargs))

        # Convert Pydantic models to dicts
        from pydantic import BaseModel

        if isinstance(result, BaseModel):
            return result.model_dump()

        return result

    return async_wrapper


class CugaLiteState(BaseModel):
    """State for CugaLite subgraph.

    Shared keys with AgentState:
    - chat_messages: List[BaseMessage] (primary message history)
    - final_answer: str (compatible with parent)
    - pi: str (personal information/user context injected with first message)
    - variables_storage: Dict[str, Dict[str, Any]] (shared variables)
    - variable_counter_state: int (shared variable counter)
    - variable_creation_order: List[str] (shared variable order)
    - cuga_lite_metadata: Dict[str, Any] (metadata for tracking execution context)
    - sub_task: str (current subtask being executed)
    - sub_task_app: str (app name for subtask)
    - api_intent_relevant_apps: List[Any] (relevant apps for the task)
    - hitl_action: Optional[FollowUpAction] (human-in-the-loop action request)
    - hitl_response: Optional[ActionResponse] (human-in-the-loop response)
    - sender: str (node that sent the current state)
    - service_scope: Dict[str, str] (tenant_id, instance_id for multi-tenant/prod scoping)

    Subgraph-only keys:
    - script, execution_complete, error, metrics
    - tools_prepared: bool (flag indicating tools have been prepared)
    - prepared_prompt: str (dynamically generated prompt)
    """

    # Shared keys (compatible with AgentState)
    chat_messages: Optional[List[BaseMessage]] = Field(default_factory=list)
    final_answer: Optional[str] = ""
    thread_id: Optional[str] = None
    service_scope: Optional[Dict[str, str]] = Field(
        default_factory=lambda: {"tenant_id": "", "instance_id": ""}
    )
    pi: Optional[str] = ""
    variables_storage: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    variable_counter_state: int = 0
    variable_creation_order: List[str] = Field(default_factory=list)
    cuga_lite_metadata: Optional[Dict[str, Any]] = None
    sub_task: Optional[str] = None
    sub_task_app: Optional[str] = None
    api_intent_relevant_apps: Optional[List[Any]] = None
    hitl_action: Optional[Any] = None  # FollowUpAction from followup_model
    hitl_response: Optional[Any] = None  # ActionResponse from followup_model
    sender: Optional[str] = None

    # Subgraph-only keys
    tools_prepared: bool = False
    prepared_prompt: Optional[str] = None
    script: Optional[str] = None
    execution_complete: bool = False
    error: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    step_count: int = 0  # Counter for number of steps (call_model + sandbox cycles)
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list
    )  # List of tracked tool calls (when track_tool_calls is enabled)

    class Config:
        arbitrary_types_allowed = True

    @property
    def variables_manager(self):
        """Get a state-specific variables manager that stores data in this CugaLiteState.

        Uses the same StateVariablesManager as AgentState for consistent interface.
        """
        from cuga.backend.cuga_graph.state.agent_state import StateVariablesManager

        return StateVariablesManager(self)


def extract_and_combine_codeblocks(text: str) -> str:
    """Extract all codeblocks from a text string and combine them."""
    code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)

    if code_blocks:
        processed_blocks = []
        for block in code_blocks:
            block = block.strip()
            processed_blocks.append(block)

        combined_code = "\n\n".join(processed_blocks)

        return combined_code

    stripped_text = text.strip()

    if "print(" not in stripped_text:
        return ""

    try:
        compile(stripped_text.replace('await ', ''), '<string>', 'exec')
        return stripped_text
    except SyntaxError:
        return ""


def append_chat_messages_with_step_limit(
    state: CugaLiteState,
    new_messages: List[BaseMessage],
    max_steps: Optional[int] = None,
) -> Tuple[List[BaseMessage], Optional[AIMessage]]:
    """Append new messages to chat_messages with step counting and limit checking.

    Args:
        state: Current CugaLiteState
        new_messages: List of new messages to append
        max_steps: Override from configurable; when None, use settings

    Returns:
        Tuple of (updated_chat_messages, error_message)
        - updated_chat_messages: Updated list of chat messages
        - error_message: AIMessage with error if limit reached, None otherwise
    """
    limit = max_steps if max_steps is not None else settings.advanced_features.cuga_lite_max_steps
    new_step_count = state.step_count + 1

    if new_step_count > limit:
        error_msg = (
            f"Maximum step limit ({limit}) reached. "
            f"The task has exceeded the allowed number of execution cycles. "
            f"Please simplify your request or break it into smaller tasks."
        )
        logger.warning(f"Step limit reached: {new_step_count} > {limit}")
        error_ai_message = AIMessage(content=error_msg)
        return state.chat_messages + new_messages + [error_ai_message], error_ai_message

    logger.debug(f"Step count: {new_step_count}/{limit}")
    return state.chat_messages + new_messages, None


def create_error_command(
    updated_messages: List[BaseMessage],
    error_message: AIMessage,
    step_count: int,
    additional_updates: Optional[Dict[str, Any]] = None,
) -> Command:
    """Create a Command to END with error information.

    Args:
        updated_messages: Updated chat messages
        error_message: Error message to return
        step_count: Current step count
        additional_updates: Optional additional state updates

    Returns:
        Command routing to END with error state
    """
    updates = {
        "chat_messages": updated_messages,
        "script": None,
        "final_answer": error_message.content,
        "execution_complete": True,
        "error": error_message.content,
        "step_count": step_count + 1,
    }
    if additional_updates:
        updates.update(additional_updates)

    return Command(goto=END, update=updates)


class Todo(BaseModel):
    """A single todo item with text and status."""

    text: str = Field(..., description="The task description")
    status: str = Field(
        default="pending",
        description="Status of the todo: 'pending', 'in_progress', or 'completed'",
    )


class TodosInput(BaseModel):
    """Input schema for create_update_todos function."""

    todos: List[Todo] = Field(..., description="List of todos, each with 'text' and 'status' fields")


class TodosOutput(BaseModel):
    """Output schema for create_update_todos function."""

    todos: List[Todo] = Field(..., description="List of todos with their current status")


async def create_find_tools_tool(
    all_tools: Sequence[StructuredTool],
    all_apps: List[Any],
    app_to_tools_map: Optional[Dict[str, List[StructuredTool]]] = None,
) -> StructuredTool:
    """Create a find_tools StructuredTool for tool discovery.

    Args:
        all_tools: All available tools to search through
        all_apps: All available app definitions
        app_to_tools_map: Optional mapping of app_name -> list of tools. If provided, used for filtering by app_name.

    Returns:
        StructuredTool configured for finding relevant tools
    """

    async def find_tools_func(query: str, app_name: str):
        """Search for relevant tools from the connected applications based on a natural language query.

        Args:
            query: Natural language query describing what tools are needed to accomplish the task can include also which parameters are needed or the output expected
            app_name: Name of a specific app to filter tools from. Only searches tools from that app.

        Returns:
            Top 4 matching tools with their details
        """
        if app_to_tools_map and app_name in app_to_tools_map:
            filtered_tools = app_to_tools_map[app_name]
        else:
            logger.warning(
                f"App '{app_name}' not found in app_to_tools_map. Available apps: {list(app_to_tools_map.keys()) if app_to_tools_map else 'N/A'}"
            )
            filtered_tools = []

        filtered_apps = [app for app in all_apps if hasattr(app, 'name') and app.name == app_name]

        if not filtered_apps:
            logger.warning(
                f"App '{app_name}' not found in available apps. Available apps: {[app.name if hasattr(app, 'name') else str(app) for app in all_apps]}"
            )

        return await PromptUtils.find_tools(query=query, all_tools=filtered_tools, all_apps=filtered_apps)

    return StructuredTool.from_function(
        func=find_tools_func,
        name="find_tools",
        description="Search for relevant tools from a specific connected application based on a natural language query. Use this when you need to discover what tools are available for a specific task within a specific application.",
    )


async def create_update_todos_tool(agent_state: Optional['AgentState'] = None) -> StructuredTool:
    """Create a create_update_todos StructuredTool for managing task todos.

    Args:
        agent_state: Optional AgentState to store todos for prompt updates

    Returns:
        StructuredTool configured for creating and updating todos
    """

    async def create_update_todos_func(input_data) -> str:
        """Create or update a list of todos for complex multi-step tasks.

        Use this tool when you have a complex task that requires multiple steps.
        This helps you track progress and organize your work.

        Args:
            input_data: Can be:
                       - A TodosInput Pydantic model
                       - A dict with 'todos' key: {"todos": [...]}
                       - A list directly: [...] (will be wrapped in {"todos": [...]})

        Returns:
            Simple confirmation message
        """
        # Handle different input types
        if isinstance(input_data, TodosInput):
            todos_list = input_data.todos
        elif isinstance(input_data, dict):
            # If it's a dict, check if it has 'todos' key
            if 'todos' in input_data:
                todos_list = input_data['todos']
            else:
                # If no 'todos' key, treat the whole dict as a single todo or wrap it
                todos_list = [input_data]
            # Convert dict items to Todo models
            todos_list = [Todo(**todo) if isinstance(todo, dict) else todo for todo in todos_list]
        elif isinstance(input_data, list):
            # If it's a list directly, convert each item to Todo
            todos_list = [Todo(**todo) if isinstance(todo, dict) else todo for todo in input_data]
        else:
            # Fallback: try to create TodosInput
            try:
                if isinstance(input_data, dict):
                    input_data = TodosInput(**input_data)
                else:
                    input_data = TodosInput(todos=input_data)
                todos_list = input_data.todos
            except Exception:
                # Last resort: wrap in a list
                todos_list = [Todo(**input_data) if isinstance(input_data, dict) else input_data]

        # Store todos in agent_state if available
        logger.info(
            f"🔍 DEBUG create_update_todos: agent_state type = {type(agent_state).__name__ if agent_state else 'None'}"
        )
        logger.info(f"🔍 DEBUG create_update_todos: agent_state exists = {agent_state is not None}")

        return "Todos have been updated"

    return StructuredTool.from_function(
        func=create_update_todos_func,
        name="create_update_todos",
        description="Create or update a list of todos for complex multi-step tasks. Use this when you have a task that requires more than one step. You can pass either: (1) A list directly: create_update_todos([{'text': '...', 'status': 'pending'}, ...]) or (2) A dict with 'todos' key: create_update_todos({'todos': [{'text': '...', 'status': 'pending'}, ...]}). Each todo dict should have 'text' (task description) and 'status' ('pending', 'in_progress', or 'completed'). Returns a simple confirmation message.",
        args_schema=TodosInput,
        return_direct=False,
    )


def create_cuga_lite_graph(
    model: BaseChatModel,
    prompt: Optional[str] = None,
    tool_provider: ToolProviderInterface = None,
    apps_list: Optional[List[str]] = None,
    agent_state: Optional[AgentState] = None,
    thread_id: Optional[str] = None,
    callbacks: Optional[List[BaseCallbackHandler]] = None,
    special_instructions: Optional[str] = None,
) -> StateGraph:
    """
    Create a unified CugaLite subgraph combining CodeAct and CugaAgent functionality.

    enable_todos and reflection_enabled are read from config["configurable"] at runtime.
    Fallback to settings.advanced_features when not provided.

    Args:
        model: The language model to use
        prompt: Optional static prompt (if None, will be created dynamically from state)
        tool_provider: Tool provider interface for accessing tools
        apps_list: List of app names for tool context
        agent_state: Optional AgentState for variables management
        thread_id: Thread ID for E2B sandbox caching
        callbacks: Optional list of callback handlers
        special_instructions: Optional special instructions to add to the prompt

    Returns:
        StateGraph implementing the CugaLite architecture
    """
    prompts_dir = Path(__file__).parent / "prompts"
    prompt_template = load_one_prompt(str(prompts_dir / "mcp_prompt.jinja2"), relative_to_caller=False)
    prompt_template_todos = load_one_prompt(
        str(prompts_dir / "mcp_prompt_todos.jinja2"), relative_to_caller=False
    )
    instructions = get_all_instructions_formatted()

    def create_prepare_node(
        base_tool_provider,
        base_prompt_template,
        base_prompt_template_todos,
        base_instructions,
        tools_context_dict,
        base_special_instructions,
    ):
        """Factory to create prepare node with closure over tool provider and config."""

        async def prepare_tools_and_apps(
            state: CugaLiteState, config: Optional[RunnableConfig] = None
        ) -> Command:
            """Prepare tools, apps, and prompt once at the start of the graph.

            This node gets tools from tool_provider, filters based on state configuration,
            determines if find_tools should be enabled, and prepares the prompt.
            Tools are available via closure (per graph instance), prompt is stored in state.

            enable_todos is read from config["configurable"] at runtime.
            """
            configurable = config.get("configurable", {}) if config else {}
            enable_todos = (
                configurable.get("enable_todos")
                if "enable_todos" in configurable
                else settings.advanced_features.enable_todos
            )
            shortlisting_threshold = (
                configurable.get("shortlisting_tool_threshold")
                if "shortlisting_tool_threshold" in configurable
                else settings.advanced_features.shortlisting_tool_threshold
            )
            selected_prompt_template = base_prompt_template_todos if enable_todos else base_prompt_template

            logger.debug(
                f"[APPROVAL DEBUG] prepare_tools_and_apps received cuga_lite_metadata: {state.cuga_lite_metadata}"
            )

            # Skip policy checking if policies are disabled or if we're returning from approval
            if settings.policy.enabled and not ToolApprovalHandler.should_skip_policy_check(state):
                # Check for policies and enact if matched
                # Include IntentGuard, Playbook, and ToolGuide for intent checks
                from cuga.backend.cuga_graph.policy.models import PolicyType

                command, metadata = await PolicyEnactment.check_and_enact(
                    state,
                    config,
                    policy_types=[PolicyType.INTENT_GUARD, PolicyType.PLAYBOOK, PolicyType.TOOL_GUIDE],
                )

                # If policy returned a command (e.g., BLOCK_INTENT), execute it immediately
                if command:
                    return command

                # If policy returned metadata (e.g., playbook guidance), store it
                if metadata:
                    state.cuga_lite_metadata = metadata
            elif not settings.policy.enabled:
                logger.debug("Policy system disabled - skipping policy checks")
            else:
                logger.info("[APPROVAL DEBUG] Skipping policy check - user has already approved")

            if not base_tool_provider:
                raise ValueError("tool_provider is required")

            # Get total tool count across ALL apps (for shortlisting threshold - not per app)
            all_tools_total = await base_tool_provider.get_all_tools()
            total_tool_count = len(all_tools_total) if all_tools_total else 0

            # Get tools from provider
            apps_for_prompt = None
            app_to_tools_map = {}

            # Get apps from state and filter tools if specific app is selected
            if state.sub_task_app:
                # Specific app selected - filter tools to only this app
                all_apps = await base_tool_provider.get_apps()
                # add here the implementation of force_
                force_lite_apps = getattr(settings.advanced_features, 'force_lite_mode_apps', [])
                if force_lite_apps:
                    allowed_apps_names = list(set([state.sub_task_app] + force_lite_apps))
                    # call authenticate_apps for the allowed apps
                    if settings.advanced_features.benchmark == "appworld":
                        await TaskAnalyzer.call_authenticate_apps(force_lite_apps)
                    apps_for_prompt = [app for app in all_apps if app.name in allowed_apps_names]
                else:
                    apps_for_prompt = [app for app in all_apps if app.name == state.sub_task_app]
                # Get only tools for this specific app
                tools_for_execution = []
                for app in apps_for_prompt:
                    current_tools_for_execution = await base_tool_provider.get_tools(app.name)
                    app_to_tools_map[app.name] = current_tools_for_execution
                    tools_for_execution.extend(current_tools_for_execution)

                logger.info(
                    f"Filtered to {len(tools_for_execution)} tools for {len(apps_for_prompt)} identified apps"
                )
            elif state.api_intent_relevant_apps:
                # Filter to API apps
                all_apps = await base_tool_provider.get_apps()
                apps_for_prompt = [
                    app
                    for app in state.api_intent_relevant_apps
                    if hasattr(app, 'type') and app.type == 'api'
                ]
                # Get tools only for the identified apps
                tools_for_execution = []
                for app in apps_for_prompt:
                    app_tools = await base_tool_provider.get_tools(app.name)
                    app_to_tools_map[app.name] = app_tools
                    tools_for_execution.extend(app_tools)
                logger.info(
                    f"Filtered to {len(tools_for_execution)} tools for {len(apps_for_prompt)} identified apps"
                )
            else:
                # Get all tools and apps
                all_apps = await base_tool_provider.get_apps()
                apps_for_prompt = all_apps
                tools_for_execution = all_tools_total or []
                # Build mapping for all apps
                for app in apps_for_prompt:
                    app_tools = await base_tool_provider.get_tools(app.name)
                    app_to_tools_map[app.name] = app_tools

            enable_find_tools = total_tool_count > shortlisting_threshold

            if enable_find_tools:
                logger.info(
                    f"Auto-enabling find_tools: total {total_tool_count} tools (across all apps) exceeds threshold of {shortlisting_threshold}"
                )

            # Prepare prompt
            is_autonomous_subtask = state.sub_task is not None and state.sub_task.strip() != ""

            # TODO: Add task loaded from file support this happens when we load file as playboook
            task_loaded_from_file = False  # Not used in current flow

            # Prepare tools for prompt - if find_tools enabled, only expose find_tools
            tools_for_prompt = tools_for_execution
            if enable_find_tools:
                find_tool = await create_find_tools_tool(
                    all_tools=tools_for_execution, all_apps=apps_for_prompt, app_to_tools_map=app_to_tools_map
                )
                tools_for_prompt = [find_tool]
                # Add find_tools to tools context for sandbox execution
                # Wrap to make awaitable (agent always uses await)
                # Prefer coroutine over func to avoid run_in_executor issues
                find_tool_func = (
                    find_tool.coroutine
                    if hasattr(find_tool, 'coroutine') and find_tool.coroutine
                    else find_tool.func
                )
                tools_context_dict['find_tools'] = make_tool_awaitable(find_tool_func)
                logger.info(
                    "Exposing only find_tools in prompt (all tools + find_tools available in execution context)"
                )

            # Add create_update_todos tool for complex task management if enabled
            if enable_todos:
                # Pass the CugaLiteState so todos updates are reflected in the graph state
                todos_tool = await create_update_todos_tool(agent_state=state)
                tools_for_prompt.append(todos_tool)
                # Add to tools context for sandbox execution
                # Prefer coroutine over func to avoid run_in_executor issues
                todos_tool_func = (
                    todos_tool.coroutine
                    if hasattr(todos_tool, 'coroutine') and todos_tool.coroutine
                    else todos_tool.func
                )
                tools_context_dict['create_update_todos'] = make_tool_awaitable(todos_tool_func)

            # Apply tool guide if guides exist in metadata and haven't been applied yet
            # Guides should apply regardless of whether a playbook matched
            if settings.policy.enabled and state.cuga_lite_metadata:
                # Check if guides exist (either as separate guides list or legacy format)
                has_guides = (
                    state.cuga_lite_metadata.get("guides")
                    or state.cuga_lite_metadata.get("guide_content")
                    or state.cuga_lite_metadata.get("policy_type") == "tool_guide"
                    or state.cuga_lite_metadata.get("has_guides", False)
                )

                if has_guides:
                    tools_for_execution = PolicyEnactment.apply_tool_guide(
                        tools_for_execution, state.cuga_lite_metadata
                    )
                    tools_for_prompt = PolicyEnactment.apply_tool_guide(
                        tools_for_prompt, state.cuga_lite_metadata
                    )
                    # Mark guides as applied to prevent re-application
                    state.cuga_lite_metadata["guides_applied"] = True
                    logger.info("Applied tool guide from policy")
                else:
                    logger.debug("No tool guides found in metadata")

            # Update tools context with all execution tools
            # Wrap to make awaitable (agent always uses await)
            for tool in tools_for_execution:
                # Extract tool function - StructuredTool may use .func, .coroutine, or ._run
                # IMPORTANT: Prefer coroutine over func to avoid run_in_executor issues
                # with tools that have async implementations (like MCP tools)
                tool_func = None
                if hasattr(tool, 'coroutine') and tool.coroutine:
                    # Prefer async coroutine - avoids run_in_executor timeout issues
                    tool_func = tool.coroutine
                elif hasattr(tool, 'func') and tool.func:
                    tool_func = tool.func
                else:
                    tool_func = getattr(tool, '_run', None)

                if tool_func:
                    tools_context_dict[tool.name] = make_tool_awaitable(tool_func)
                else:
                    logger.warning(f"Tool '{tool.name}' has no callable function, skipping")

            # Create prompt dynamically
            dynamic_prompt = prompt

            if not dynamic_prompt:
                dynamic_prompt = create_mcp_prompt(
                    tools_for_prompt,
                    allow_user_clarification=True,
                    return_to_user_cases=None,
                    instructions=base_instructions,
                    apps=apps_for_prompt,
                    task_loaded_from_file=task_loaded_from_file,
                    is_autonomous_subtask=settings.advanced_features.force_autonomous_mode
                    or is_autonomous_subtask,
                    prompt_template=selected_prompt_template,
                    enable_find_tools=enable_find_tools,
                    special_instructions=base_special_instructions,
                )

            return Command(
                goto="call_model",
                update={
                    "tools_prepared": True,
                    "prepared_prompt": dynamic_prompt,
                    "step_count": 0,
                    "cuga_lite_metadata": state.cuga_lite_metadata,
                },
            )

        return prepare_tools_and_apps

    # Factory function to create call_model node with access to model
    def create_call_model_node(base_model, base_callbacks):
        """Factory to create call_model node with closure over model."""

        async def call_model(state: CugaLiteState, config: Optional[RunnableConfig] = None) -> Command:
            """Call the LLM to generate code or text response."""
            configurable = config.get("configurable", {}) if config else {}
            max_steps = (
                configurable.get("cuga_lite_max_steps") if "cuga_lite_max_steps" in configurable else None
            )

            logger.debug(
                f"[APPROVAL DEBUG] call_model received cuga_lite_metadata: {state.cuga_lite_metadata}"
            )

            # Check if we're returning from tool approval - if so, skip code generation and go to sandbox
            # Only check if policies are enabled
            if settings.policy.enabled and ToolApprovalHandler.is_returning_from_approval(state):
                return ToolApprovalHandler.handle_approval_resumption(state)

            # Get prompt from state (tools are available via sandbox context, not needed here)
            dynamic_prompt = state.prepared_prompt

            # Convert BaseMessage objects to dict format for model invocation
            messages_for_model = [{"role": "system", "content": dynamic_prompt}]

            # Check if we have variables and this is a new question (not a follow-up with existing AI responses)
            # If this is a new question (1 user msg, 0 AI msgs) or follow-up, add variables to the last user message
            var_manager = state.variables_manager
            existing_variable_names = var_manager.get_variable_names()
            variables_summary_text = None

            if existing_variable_names and state.sub_task_app:
                variables_summary_text = var_manager.get_variables_summary(
                    variable_names=existing_variable_names
                )
                variables_addendum = f"\n\n## Available Variables\n\n{variables_summary_text}\n\nYou can use these variables directly by their names."
                logger.info(
                    f"Will add variables summary for {len(existing_variable_names)} variables to user message"
                )

            logger.info(f"Processing {len(state.chat_messages)} chat messages for model invocation")

            # Track if we've added personal information (pi)
            pi_added = False

            # Get playbook guidance if available (only on first detection)
            # TODO: In the future, we could refine the playbook guidance on each message
            # based on conversation progress and completed steps
            playbook_guidance = None
            playbook_already_added = False

            # Check if playbook guidance was already added in previous messages
            if state.cuga_lite_metadata and state.cuga_lite_metadata.get('playbook_guidance_added'):
                playbook_already_added = True

            if (
                state.cuga_lite_metadata
                and state.cuga_lite_metadata.get('policy_matched')
                and not playbook_already_added
            ):
                if state.cuga_lite_metadata.get('policy_type') == 'playbook':
                    playbook_guidance = state.cuga_lite_metadata.get('playbook_guidance')
                    if playbook_guidance:
                        logger.info(
                            "Will inject playbook guidance into current user message (first time only)"
                        )

            for i, msg in enumerate(state.chat_messages):
                msg_type = type(msg).__name__
                msg_role = getattr(msg, 'type', None)
                logger.debug(
                    f"Message {i}: type={msg_type}, role={msg_role}, isinstance(HumanMessage)={isinstance(msg, HumanMessage)}, isinstance(AIMessage)={isinstance(msg, AIMessage)}"
                )

                if isinstance(msg, HumanMessage):
                    content = msg.content
                    content_modified = False

                    # Add personal information (pi) to the FIRST user message only
                    if (
                        state.pi
                        and not pi_added
                        and "## User Context" not in content
                        and len(state.chat_messages) == 1
                    ):
                        content = f"{content}\n\n## User Context\n{state.pi}"
                        pi_added = True
                        content_modified = True
                        logger.debug("Added personal information (pi) to first user message")

                    # Add playbook guidance to the LAST user message only
                    if playbook_guidance and i == len(state.chat_messages) - 1:
                        content = f"{content}\n\n## Task Guidance\n{playbook_guidance}"
                        content_modified = True
                        logger.debug("Added playbook guidance to last user message")

                    # Add variables summary to the LAST user message only
                    if variables_summary_text and i == len(state.chat_messages) - 1:
                        content = content + variables_addendum
                        content_modified = True
                        logger.debug("Added variables summary to last user message")

                    # Update state.chat_messages directly if content was modified (so it persists across turns)
                    if content_modified:
                        state.chat_messages[i] = HumanMessage(content=content)
                        logger.debug(
                            f"Updated state.chat_messages[{i}] with modified content (playbook/pi/variables)"
                        )

                    messages_for_model.append({"role": "user", "content": content})
                elif isinstance(msg, AIMessage):
                    messages_for_model.append({"role": "assistant", "content": msg.content})
                else:
                    # Handle generic BaseMessage by checking the 'type' attribute
                    if msg_role == 'human' or msg_role == 'user':
                        content = msg.content
                        content_modified = False

                        # Add personal information (pi) to the FIRST user message only
                        if state.pi and not pi_added:
                            content = f"{content}\n\n## User Context\n{state.pi}"
                            pi_added = True
                            content_modified = True
                            logger.debug("Added personal information (pi) to first user message")

                        # Add playbook guidance to the LAST user message only
                        if playbook_guidance and i == len(state.chat_messages) - 1:
                            content = f"{content}\n\n## Task Guidance\n{playbook_guidance}"
                            content_modified = True
                            logger.debug("Added playbook guidance to last user message")

                        if variables_summary_text and i == len(state.chat_messages) - 1:
                            content = content + variables_addendum
                            content_modified = True

                        # Update state.chat_messages directly if content was modified (so it persists across turns)
                        if content_modified:
                            state.chat_messages[i] = HumanMessage(content=content)
                            logger.debug(
                                f"Updated state.chat_messages[{i}] with modified content (playbook/pi/variables)"
                            )

                        messages_for_model.append({"role": "user", "content": content})
                        logger.debug(f"Added BaseMessage as user message (role={msg_role})")
                    elif msg_role == 'ai' or msg_role == 'assistant':
                        messages_for_model.append({"role": "assistant", "content": msg.content})
                        logger.debug(f"Added BaseMessage as assistant message (role={msg_role})")
                    else:
                        logger.warning(
                            f"Skipping message {i} with unknown type: {msg_type}, role: {msg_role}"
                        )

            logger.debug(f"Total messages for model (including system): {len(messages_for_model)}")

            # Get configurable values from config
            configurable = config.get("configurable", {}) if config else {}
            current_callbacks = configurable.get("callbacks", base_callbacks or [])

            try:
                response = await base_model.ainvoke(
                    messages_for_model, config={"callbacks": current_callbacks}
                )
            except Exception as e:
                code = extract_code_from_tool_use_failed(str(e))
                if code:
                    logger.warning(
                        "Model attempted tool call without tools bound (tool_use_failed). "
                        "Using generated code in sandbox"
                    )
                    response = type(
                        "_FakeResponse", (), {"content": f"```python\n{code}\n```", "additional_kwargs": {}}
                    )()
                else:
                    raise e

            content = response.content
            reasoning_content = response.additional_kwargs.get('reasoning_content')

            tracker.collect_step(step=Step(name="Raw_Assistant_Response", data=content))

            # Try to extract code from content first, then reasoning_content if content has no code
            code = extract_and_combine_codeblocks(content) if content else ""

            if not code and reasoning_content:
                code = extract_and_combine_codeblocks(reasoning_content)

            if code:
                tracker.collect_step(step=Step(name="Assistant_code", data=content))
                logger.debug(
                    f"\n{'=' * 50} ASSISTANT CODE {'=' * 50}\n{code}\n{'=' * 50} END ASSISTANT CODE {'=' * 50}"
                )

                # Check if code requires approval and create interrupt if needed
                # Only check if policies are enabled
                if settings.policy.enabled:
                    approval_command = await ToolApprovalHandler.check_and_create_approval_interrupt(
                        state, code, content, config
                    )
                    if approval_command:
                        return approval_command

                updated_messages, error_message = append_chat_messages_with_step_limit(
                    state, [AIMessage(content=content)], max_steps=max_steps
                )
                if error_message:
                    return create_error_command(updated_messages, error_message, state.step_count)

                # Update metadata to mark playbook guidance as added
                updated_metadata = state.cuga_lite_metadata or {}
                if playbook_guidance:
                    updated_metadata = {**updated_metadata, "playbook_guidance_added": True}

                return Command(
                    goto="sandbox",
                    update={
                        "chat_messages": updated_messages,
                        "script": code,
                        "step_count": state.step_count + 1,
                        "cuga_lite_metadata": updated_metadata,
                    },
                )
            else:
                tracker.collect_step(step=Step(name="Assistant_nl", data=content))
                planning_response = response.content

                updated_messages, error_message = append_chat_messages_with_step_limit(
                    state, [AIMessage(content=planning_response)], max_steps=max_steps
                )
                if error_message:
                    return create_error_command(updated_messages, error_message, state.step_count)

                # Update metadata to mark playbook guidance as added
                updated_metadata = state.cuga_lite_metadata or {}
                if playbook_guidance:
                    updated_metadata = {**updated_metadata, "playbook_guidance_added": True}

                return Command(
                    goto=END,
                    update={
                        "chat_messages": updated_messages,
                        "script": None,
                        "final_answer": planning_response,
                        "execution_complete": True,
                        "step_count": state.step_count + 1,
                        "cuga_lite_metadata": updated_metadata,
                    },
                )

        return call_model

    # Factory function to create sandbox node with access to tools context
    def create_sandbox_node(base_tools_context, base_thread_id, base_apps_list):
        """Factory to create sandbox node with closure over tools context and config."""

        async def sandbox(state: CugaLiteState, config: Optional[RunnableConfig] = None):
            """Execute code in sandbox and return results."""
            from cuga.backend.cuga_graph.nodes.cuga_lite.tool_call_tracker import ToolCallTracker

            # Check if user denied approval (only if policies are enabled)
            if settings.policy.enabled:
                denial_command = ToolApprovalHandler.handle_denial(state)
                if denial_command:
                    return denial_command

            configurable = config.get("configurable", {}) if config else {}
            max_steps = (
                configurable.get("cuga_lite_max_steps") if "cuga_lite_max_steps" in configurable else None
            )
            current_thread_id = configurable.get("thread_id", base_thread_id)
            current_apps_list = configurable.get("apps_list", base_apps_list)
            track_tool_calls = configurable.get("track_tool_calls", False)
            reflection_enabled = (
                configurable.get("reflection_enabled")
                if "reflection_enabled" in configurable
                else settings.advanced_features.reflection_enabled
            )

            # Get existing variables using CugaLiteState's own variables_manager
            existing_vars = {}
            for var_name in state.variables_manager.get_variable_names():
                existing_vars[var_name] = state.variables_manager.get_variable(var_name)

            # Add tools to context
            context = {**existing_vars, **base_tools_context}

            # Start tool call tracking (only if enabled via invoke parameter)
            ToolCallTracker.start_tracking(enabled=track_tool_calls)

            try:
                # Execute the script - pass the CugaLiteState itself since it has variables_manager
                output, new_vars = await CodeExecutor.eval_with_tools_async(
                    code=state.script,
                    _locals=context,
                    state=state,  # Pass CugaLiteState - it has variables_manager property
                    thread_id=current_thread_id,
                    apps_list=current_apps_list,
                )

                tracker.collect_step(step=Step(name="User_output", data=output))
                tracker.collect_step(step=Step(name="User_output_variables", data=json.dumps(new_vars)))

                # Output is already formatted and trimmed by code_executor
                logger.debug(f"\n\n------\n\n📝 Execution output:\n\n{output}\n\n------\n\n")

                # Update variables using CugaLiteState's variables_manager
                # This automatically updates state.variables_storage
                for name, value in new_vars.items():
                    state.variables_manager.add_variable(
                        value, name=name, description="Created during code execution"
                    )

                reflection_output = ""
                if reflection_enabled:
                    try:
                        reflection_agent = reflection_task(
                            llm=llm_manager.get_model(settings.agent.planner.model)
                        )
                        # Format chat messages as history string
                        agent_history_parts = []
                        for msg in state.chat_messages:
                            if isinstance(msg, HumanMessage):
                                agent_history_parts.append(f"User: {msg.content}")
                            elif isinstance(msg, AIMessage):
                                agent_history_parts.append(f"Assistant: {msg.content}")
                            else:
                                agent_history_parts.append(
                                    f"{type(msg).__name__}: {getattr(msg, 'content', str(msg))}"
                                )
                        agent_history = (
                            "\n".join(agent_history_parts)
                            if agent_history_parts
                            else "No previous conversation history"
                        )

                        reflection_result = await reflection_agent.ainvoke(
                            {
                                "instructions": "",
                                "current_task": state.sub_task,
                                "agent_history": agent_history,
                                "shortlister_agent_output": "",
                                "coder_agent_output": output,
                            }
                        )
                        reflection_output = reflection_result.content
                        logger.debug(f"Reflection output:\n{reflection_output}")
                    except Exception as e:
                        logger.warning(f"Reflection failed: {e}")
                        reflection_output = ""

                # Output is already formatted by code_executor
                execution_message_content = f"Execution output:\n{output}"
                if reflection_output:
                    execution_message_content = (
                        f"{execution_message_content}\n\n---\n\nSummary:\n{reflection_output}"
                    )

                tracker.collect_step(
                    step=Step(
                        name="User_return",
                        data=execution_message_content,
                    )
                )

                new_message = HumanMessage(content=execution_message_content)
                updated_messages, error_message = append_chat_messages_with_step_limit(
                    state, [new_message], max_steps=max_steps
                )

                # Collect tool calls from this execution
                execution_tool_calls = ToolCallTracker.stop_tracking()
                accumulated_tool_calls = (state.tool_calls or []) + execution_tool_calls

                if error_message:
                    return create_error_command(
                        updated_messages,
                        error_message,
                        state.step_count,
                        additional_updates={
                            "variables_storage": state.variables_storage,
                            "variable_counter_state": state.variable_counter_state,
                            "variable_creation_order": state.variable_creation_order,
                            "tool_calls": accumulated_tool_calls,
                        },
                    )

                return {
                    "chat_messages": updated_messages,
                    "variables_storage": state.variables_storage,
                    "variable_counter_state": state.variable_counter_state,
                    "variable_creation_order": state.variable_creation_order,
                    "step_count": state.step_count + 1,
                    "tool_calls": accumulated_tool_calls,
                }
            except Exception as e:
                # Collect tool calls even on error
                execution_tool_calls = ToolCallTracker.stop_tracking()
                accumulated_tool_calls = (state.tool_calls or []) + execution_tool_calls

                error_msg = f"Error during execution: {str(e)}"
                logger.error(error_msg)
                new_message = HumanMessage(content=error_msg)
                updated_messages, limit_error_message = append_chat_messages_with_step_limit(
                    state, [new_message], max_steps=max_steps
                )

                if limit_error_message:
                    return create_error_command(updated_messages, limit_error_message, state.step_count)

                return {
                    "chat_messages": updated_messages,
                    "error": error_msg,
                    "execution_complete": True,
                    "step_count": state.step_count + 1,
                    "tool_calls": accumulated_tool_calls,
                }

        return sandbox

    # Create mutable tools context that will be populated by prepare_node
    tools_context = {}

    # Create node instances using factories
    prepare_node = create_prepare_node(
        tool_provider,
        prompt_template,
        prompt_template_todos,
        instructions,
        tools_context,
        special_instructions,
    )
    call_model_node = create_call_model_node(model, callbacks)
    sandbox_node = create_sandbox_node(tools_context, thread_id, apps_list)

    # Build the graph
    graph = StateGraph(CugaLiteState)
    graph.add_node("prepare_tools_and_apps", prepare_node)
    graph.add_node("call_model", call_model_node)
    graph.add_node("sandbox", sandbox_node)

    graph.add_edge(START, "prepare_tools_and_apps")
    graph.add_edge("sandbox", "call_model")

    return graph
