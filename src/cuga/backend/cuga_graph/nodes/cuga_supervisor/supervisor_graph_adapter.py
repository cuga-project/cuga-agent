"""SupervisorGraphAdapter — CoreGraphAdapter implementation for CugaSupervisor.

The adapter defines how the shared agent graph uses supervisor state: message and
metadata keys, variable manager seams, and node factory wiring. Prompt, tool, and
execution logic live in ``cuga_supervisor/nodes/`` and ``delegation.py``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import BaseMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import (
    format_task_todos_system_block,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import resolve_names_from_caller_frame
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
    create_execute_agent_tool_node,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
    create_prepare_agents_and_prompt_node,
)
from cuga.backend.cuga_graph.utils.token_counter import clamp_watsonx_completion_for_messages
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
        base_callbacks: Optional[List[Any]] = None,
        static_prompt: Optional[str] = None,
    ) -> None:
        self._agents = agents
        self._special_instructions = special_instructions
        self._tool_provider = tool_provider
        self._base_callbacks = base_callbacks or []
        self._static_prompt = static_prompt
        self._agent_tools_context: Dict[str, Any] = {}

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

    def prepare_system_content(self, state: Any, configurable: dict, base_prompt: str) -> str:
        # Todos are run-local: the create_update_todos tool writes them onto the per-run state via
        # the supervisor execution context, so concurrent runs never share a todo list.
        task_todos = getattr(state, "task_todos", None)
        if task_todos:
            return base_prompt + format_task_todos_system_block(task_todos)
        return base_prompt

    def get_invoke_config(self, configurable: dict) -> dict:
        callbacks = configurable.get("callbacks", self._base_callbacks)
        return {"callbacks": callbacks}

    async def ainvoke_model(self, bound: Any, messages: list, invoke_config: dict) -> Any:
        clamp_watsonx_completion_for_messages(bound, messages)
        return await bound.ainvoke(messages, config=invoke_config)

    def build_metadata_update(self, state: Any, *, playbook_fired: bool) -> dict:
        meta = dict(self.get_metadata(state))
        if playbook_fired:
            return {**meta, "playbook_guidance_added": True}
        return meta

    def record_delegation(
        self,
        state: Any,
        agent_name: str,
        *,
        result: Any,
        answer: Any,
        variables: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist sub-agent delegation outcomes on the supervisor state."""
        if state is None:
            return

        if agent_name not in state.selected_agents:
            state.selected_agents.append(agent_name)

        state.agent_results[agent_name] = answer

        if variables:
            state.agent_variables[agent_name] = dict(variables)

        chat_messages = getattr(result, "chat_messages", None) if result is not None else None
        if chat_messages:
            state.agent_chat_messages[agent_name] = list(chat_messages)

        metrics = dict(getattr(state, "metrics", None) or {})
        delegation_count = int(metrics.get("delegation_count", 0)) + 1
        metrics["delegation_count"] = delegation_count
        metrics["last_delegated_agent"] = agent_name
        state.metrics = metrics

    def build_prepare_node(self) -> Callable:
        return create_prepare_agents_and_prompt_node(self)

    def build_execute_node(self) -> Callable:
        return create_execute_agent_tool_node(self)
