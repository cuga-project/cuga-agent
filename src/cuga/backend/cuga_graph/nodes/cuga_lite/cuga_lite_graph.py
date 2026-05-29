"""CugaLite LangGraph — thin wiring module.

State class, loop adapter, and the ``create_cuga_lite_graph`` factory.
All node logic lives in ``cuga_lite.adapter`` (prepare/sandbox nodes + hook overrides).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import StateGraph
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    CoreGraphAdapter,
    append_chat_messages_with_step_limit as _core_append_with_step_limit,
    create_error_command as _core_create_error_command,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_graph import build_agent_graph
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import (
    create_call_model_node as _create_shared_call_model_node,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.agent_graph_adapter import AgentGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_one_prompt
from cuga.config import settings
from cuga.configurations.instructions_manager import get_all_instructions_formatted

tracker = ActivityTracker()
llm_manager = LLMManager()


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
    - user_id: Optional[str] (caller user ID for Evolve attribution; None when unset)

    Subgraph-only keys:
    - script, execution_complete, error, metrics
    - tools_prepared: bool (flag indicating tools have been prepared)
    - prepared_prompt: str (dynamically generated prompt)
    - reflection_apps: list of dicts (name, type, description) for reflection prompt
    - reflection_enable_find_tools: whether find_tools shortlisting was enabled
    - reflection_skills_enabled: whether skills were available to the executor
    - reflection_skills_prompt_section: rendered skills registry block for reflection
    - mcp_few_shot_messages: normalized role/content few-shot messages injected before live chat
    - task_todos: latest todo list from create_update_todos (injected as Current Plan on the system prompt)
    """

    # Shared keys (compatible with AgentState)
    chat_messages: Optional[List[BaseMessage]] = Field(default_factory=list)
    final_answer: Optional[str] = ""
    user_id: Optional[str] = None  # Shared with AgentState; None means unset (no user context sent to Evolve)
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
    reflection_apps: List[Dict[str, Any]] = Field(default_factory=list)
    reflection_enable_find_tools: bool = False
    reflection_skills_enabled: bool = False
    reflection_skills_prompt_section: str = ""
    mcp_few_shot_messages: List[Dict[str, str]] = Field(default_factory=list)
    task_todos: Optional[List[Dict[str, Any]]] = Field(default=None)
    script: Optional[str] = None
    execution_complete: bool = False
    error: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    step_count: int = 0  # Counter for number of steps (call_model + sandbox cycles)
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list
    )  # List of tracked tool calls (when track_tool_calls is enabled)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def variables_manager(self):
        """Get a state-specific variables manager that stores data in this CugaLiteState.

        Uses the same StateVariablesManager as AgentState for consistent interface.
        """
        from cuga.backend.cuga_graph.state.agent_state import StateVariablesManager

        return StateVariablesManager(self)


class _CugaLiteLoopAdapter(CoreGraphAdapter):
    """Lite seam: messages live on ``chat_messages``; step limit from
    ``configurable`` override else ``settings.advanced_features``."""

    messages_key = "chat_messages"

    def get_messages(self, state: CugaLiteState) -> List[BaseMessage]:
        return state.chat_messages

    def resolve_max_steps(self, state: CugaLiteState, override: Optional[int]) -> int:
        return override if override is not None else settings.advanced_features.cuga_lite_max_steps


_LITE_LOOP_ADAPTER = _CugaLiteLoopAdapter()


def append_chat_messages_with_step_limit(
    state: CugaLiteState,
    new_messages: List[BaseMessage],
    max_steps: Optional[int] = None,
) -> Tuple[List[BaseMessage], Optional[AIMessage]]:
    """Append messages to ``chat_messages`` with step counting + limit check."""
    return _core_append_with_step_limit(_LITE_LOOP_ADAPTER, state, new_messages, max_steps)


def create_error_command(
    updated_messages: List[BaseMessage],
    error_message: AIMessage,
    step_count: int,
    additional_updates: Optional[Dict[str, Any]] = None,
) -> Command:
    """Create a Command to END with error information."""
    return _core_create_error_command(
        _LITE_LOOP_ADAPTER, updated_messages, error_message, step_count, additional_updates
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
    model_settings: Optional[Dict[str, Any]] = None,
) -> StateGraph:
    """
    Create a unified CugaLite subgraph combining CodeAct and CugaAgent functionality.

    enable_todos and reflection_enabled are read from config["configurable"] at runtime.
    Fallback to settings.advanced_features when not provided.
    """
    prompts_dir = Path(__file__).parent / "prompts"
    prompt_template = load_one_prompt(str(prompts_dir / "mcp_prompt.jinja2"), relative_to_caller=False)
    instructions = get_all_instructions_formatted()

    # Mutable list shared by prepare (create_update_todos) and call_model (system prompt section).
    task_todos_ref: List[Dict[str, str]] = []

    # Execution context: callable tools for the sandbox. Populated by prepare_node.
    tools_context: Dict[str, Any] = {}
    # LangChain bind-tools metadata: _lc_bind_tools_* entries for resolve_model_with_bind_tools.
    # Kept separate so it never leaks into context_locals used by the code executor.
    lc_bind_tools_meta: Dict[str, Any] = {}

    adapter = AgentGraphAdapter(
        tracker=tracker,
        base_callbacks=callbacks or [],
        task_todos_ref=task_todos_ref,
        tools_context_ref=lc_bind_tools_meta,
        base_tool_provider=tool_provider,
        model=model,
        prompt_template=prompt_template,
        instructions=instructions,
        special_instructions=special_instructions,
        tools_context=tools_context,
        static_prompt=prompt,
        thread_id=thread_id,
    )

    prepare_node = adapter.build_prepare_node(lc_bind_tools_meta)
    sandbox_node = adapter.build_sandbox_node(thread_id, apps_list)
    call_model_node = _create_shared_call_model_node(adapter, model, settings)

    return build_agent_graph(
        adapter=adapter,
        state_class=CugaLiteState,
        prepare_node=prepare_node,
        call_model_node=call_model_node,
        execute_node=sandbox_node,
    )
