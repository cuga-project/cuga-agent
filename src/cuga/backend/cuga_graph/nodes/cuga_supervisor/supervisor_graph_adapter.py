"""SupervisorGraphAdapter — CoreGraphAdapter implementation for CugaSupervisor.

The adapter defines how the shared agent graph uses supervisor state: message and
metadata keys, variable manager seams, and node factory wiring. Prompt, tool, and
execution logic live in ``cuga_supervisor/nodes/`` and ``delegation.py``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import BaseMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import resolve_names_from_caller_frame
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
    create_execute_agent_tool_node,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
    create_prepare_agents_and_prompt_node,
)
from cuga.config import settings

# Backward-compatible alias for tests and callers that imported the private helper.
_resolve_names_from_caller_frame = resolve_names_from_caller_frame


class SupervisorGraphAdapter(CoreGraphAdapter):
    """CoreGraphAdapter implementation for the CugaSupervisor multi-agent graph."""

    messages_key: str = "supervisor_chat_messages"
    execute_node_name: str = "execute_agent_tool"
    metadata_key: str = "supervisor_metadata"
    sender_name: str = "CugaSupervisor"

    def __init__(
        self,
        *,
        agents: Dict[str, Any],
        special_instructions: Optional[str] = None,
        tool_provider: Optional[Any] = None,
    ) -> None:
        self._agents = agents
        self._special_instructions = special_instructions
        self._tool_provider = tool_provider
        self._agent_tools_context: Dict[str, Any] = {}
        self._shared_vm_ref: List[Any] = [None]

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return state.supervisor_chat_messages or []

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        if override is not None:
            return override
        return (
            state.cuga_lite_max_steps
            if getattr(state, "cuga_lite_max_steps", None) is not None
            else getattr(settings.advanced_features, "cuga_lite_max_steps", 50)
        )

    def get_variable_manager(self, state: Any) -> Any:
        return getattr(state, "supervisor_variables_manager", None)

    def get_variables_storage(self, state: Any) -> Optional[Any]:
        return getattr(state, "supervisor_variables", None)

    def build_prepare_node(self) -> Callable:
        return create_prepare_agents_and_prompt_node(self)

    def build_execute_node(self) -> Callable:
        return create_execute_agent_tool_node(self)
