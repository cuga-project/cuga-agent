"""
CugaSupervisor LangGraph - Supervisor subgraph for orchestrating multiple CugaAgent instances

This subgraph coordinates multiple CugaAgent instances, delegating tasks and aggregating results.
Similar structure to cuga_lite_graph.py but focused on multi-agent orchestration.

Uses conversational mode: Supervisor acts as a single agent with delegation tools (similar to cuga_lite).
"""

from typing import Any, Dict, List, Optional, Union, Tuple

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage

from langgraph.graph import StateGraph

from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
    CugaSupervisorState,
)
from cuga.sdk import CugaAgent
from cuga.config import settings
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    append_chat_messages_with_step_limit as _core_append_with_step_limit,
    create_error_command as _core_create_error_command,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import (
    create_call_model_node as _create_shared_call_model_node,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_graph import build_agent_graph
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import (
    SupervisorGraphAdapter,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface


# Default loop adapter for step-limit helpers (empty agent registry).
_SUPERVISOR_LOOP_ADAPTER = SupervisorGraphAdapter(agents={})


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
):
    """Create a Command to END with error information."""
    return _core_create_error_command(
        _SUPERVISOR_LOOP_ADAPTER, updated_messages, error_message, step_count, additional_updates
    )


def create_cuga_supervisor_graph(
    supervisor_model: BaseChatModel,
    agents: Dict[str, Union[CugaAgent, Dict[str, Any]]],
    special_instructions: Optional[str] = None,
    tool_provider: Optional[ToolProviderInterface] = None,
    prompt: Optional[str] = None,
    callbacks: Optional[List[BaseCallbackHandler]] = None,
) -> StateGraph:
    """
    Create supervisor subgraph that orchestrates multiple CugaAgent instances.

    Args:
        supervisor_model: The language model for the supervisor
        agents: Dict mapping agent names to CugaAgent instances (internal) or A2A config (external)
        special_instructions: Optional workflow instructions injected into the supervisor's system prompt
        tool_provider: Optional provider for MCP/external tools available to the supervisor directly
        prompt: Optional static base prompt prepended to the supervisor template
        callbacks: Optional LangChain callback handlers for supervisor model calls

    Returns:
        StateGraph implementing the CugaSupervisor architecture
    """
    sup_adapter = SupervisorGraphAdapter(
        agents=agents,
        special_instructions=special_instructions,
        tool_provider=tool_provider,
        base_callbacks=callbacks or [],
        static_prompt=prompt,
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
