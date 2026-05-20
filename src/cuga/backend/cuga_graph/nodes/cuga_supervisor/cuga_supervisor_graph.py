"""
CugaSupervisor LangGraph - Supervisor subgraph for orchestrating multiple CugaAgent instances

This subgraph coordinates multiple CugaAgent instances, delegating tasks and aggregating results.
Similar structure to cuga_lite_graph.py but focused on multi-agent orchestration.

Uses conversational mode: Supervisor acts as a single agent with delegation tools (similar to cuga_lite).
"""

import inspect
from typing import Any, Dict, List, Optional, Union, Tuple

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage

from langgraph.graph import StateGraph
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
    CugaSupervisorState,
)
from cuga.sdk import CugaAgent
from cuga.config import settings
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph_nodes import (
    CoreGraphAdapter,
    append_chat_messages_with_step_limit as _core_append_with_step_limit,
    create_error_command as _core_create_error_command,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.shared_nodes import (
    create_call_model_node as _create_shared_call_model_node,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.shared_graph import build_agent_graph
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import (
    SupervisorGraphAdapter,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface


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


class _CugaSupervisorLoopAdapter(CoreGraphAdapter):
    """Supervisor seam: messages live on ``supervisor_chat_messages``
    (None-safe); step limit from ``state.cuga_lite_max_steps`` else
    ``settings.advanced_features`` (default 50)."""

    messages_key = "supervisor_chat_messages"
    # Approval seams (override the Lite defaults on CoreGraphAdapter).
    metadata_key = "supervisor_metadata"
    execute_node_name = "execute_agent_tool"
    sender_name = "CugaSupervisor"

    def get_messages(self, state: CugaSupervisorState) -> List[BaseMessage]:
        return state.supervisor_chat_messages or []

    def resolve_max_steps(self, state: CugaSupervisorState, override: Optional[int]) -> int:
        if override is not None:
            return override
        return (
            state.cuga_lite_max_steps
            if state.cuga_lite_max_steps is not None
            else getattr(settings.advanced_features, 'cuga_lite_max_steps', 50)
        )

    def get_variable_manager(self, state: CugaSupervisorState):
        # Supervisor stores execution vars on its own manager, not the
        # root state.variables_manager (the phase-9 coupling fix builds here).
        return state.supervisor_variables_manager


_SUPERVISOR_LOOP_ADAPTER = _CugaSupervisorLoopAdapter()


def append_chat_messages_with_step_limit(
    state: CugaSupervisorState, new_messages: List[BaseMessage]
) -> Tuple[List[BaseMessage], Optional[AIMessage]]:
    """Append messages to ``supervisor_chat_messages`` with step limit check."""
    return _core_append_with_step_limit(_SUPERVISOR_LOOP_ADAPTER, state, new_messages)


def create_error_command(
    updated_messages: List[BaseMessage],
    error_message: AIMessage,
    step_count: int,
    additional_updates: Optional[Dict[str, Any]] = None,
) -> Command:
    """Create a Command to END with error information."""
    return _core_create_error_command(
        _SUPERVISOR_LOOP_ADAPTER, updated_messages, error_message, step_count, additional_updates
    )


def create_cuga_supervisor_graph(
    supervisor_model: BaseChatModel,
    agents: Dict[str, Union[CugaAgent, Dict[str, Any]]],
    special_instructions: Optional[str] = None,
    tool_provider: Optional[ToolProviderInterface] = None,
) -> StateGraph:
    """
    Create supervisor subgraph that orchestrates multiple CugaAgent instances.

    Args:
        supervisor_model: The language model for the supervisor
        agents: Dict mapping agent names to CugaAgent instances (internal) or A2A config (external)
        special_instructions: Optional workflow instructions injected into the supervisor's system prompt
        tool_provider: Optional provider for MCP/external tools available to the supervisor directly

    Returns:
        StateGraph implementing the CugaSupervisor architecture
    """
    return _create_supervisor_conversational_graph(
        supervisor_model, agents, special_instructions, tool_provider=tool_provider
    )


def _create_supervisor_conversational_graph(
    supervisor_model: BaseChatModel,
    agents: Dict[str, Union[CugaAgent, Dict[str, Any]]],
    special_instructions: Optional[str] = None,
    tool_provider: Optional[ToolProviderInterface] = None,
) -> StateGraph:
    """Create supervisor conversational mode graph."""
    sup_adapter = SupervisorGraphAdapter(
        agents=agents,
        special_instructions=special_instructions,
        tool_provider=tool_provider,
    )
    prepare_node = sup_adapter.build_prepare_node()
    execute_node = sup_adapter.build_execute_node()
    call_model_node = _create_shared_call_model_node(sup_adapter, supervisor_model, settings)

    return build_agent_graph(
        adapter=sup_adapter,
        state_class=CugaSupervisorState,
        prepare_node=prepare_node,
        call_model_node=call_model_node,
        execute_node=execute_node,
    )
