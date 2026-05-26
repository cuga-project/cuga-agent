"""Agent delegation helpers for supervisor conversational mode."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from cuga.config import settings


def resolve_names_from_caller_frame(variable_names: List[str]) -> Dict[str, Any]:
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


def create_agent_delegation_func(
    adapter: Any,
    agent_name: str,
    agent_or_config: Any,
    agent_card: Any = None,
) -> Callable:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
        A2AProtocol,
        HAS_A2A_SDK,
        delegate_task_via_a2a_sdk,
    )
    from cuga.sdk import CugaAgent

    pass_variables_a2a = getattr(settings.supervisor, "pass_variables_a2a", False)

    async def delegate_to_agent(task: str, variables: Optional[List[str]] = None) -> Any:
        logger.info(f"Delegating to {agent_name}: {task[:100]}...")

        if isinstance(agent_or_config, CugaAgent):
            vars_to_pass = {}
            if variables is not None:
                vars_to_pass = resolve_names_from_caller_frame(variables)
            result = await agent_or_config.invoke(
                task,
                thread_id=f"supervisor_conversational_{agent_name}",
                variables=vars_to_pass if vars_to_pass else None,
            )
            if hasattr(result, "variables") and result.variables and adapter._shared_vm_ref[0] is not None:
                from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import (
                    VariableBridge,
                )

                bridged = VariableBridge.bridge(
                    result.variables,
                    adapter._shared_vm_ref[0],
                    description_prefix=f"from {agent_name}",
                )
                if bridged:
                    logger.info("Bridged %d variable(s) from %s: %s", len(bridged), agent_name, bridged)
            return result.answer if hasattr(result, "answer") else str(result)

        if isinstance(agent_or_config, dict) and agent_or_config.get("type") == "external":
            a2a_config = agent_or_config.get("config", {}).get("a2a_protocol", {})
            endpoint = a2a_config.get("endpoint")
            transport = a2a_config.get("transport", "http")

            if agent_card is not None and HAS_A2A_SDK and transport == "http":
                vars_to_pass = {}
                if pass_variables_a2a and variables is not None:
                    vars_to_pass = resolve_names_from_caller_frame(variables)
                result = await delegate_task_via_a2a_sdk(
                    agent_card,
                    task,
                    auth=a2a_config.get("auth"),
                    timeout=float(a2a_config.get("timeout", 30)),
                    variables=vars_to_pass if vars_to_pass else None,
                )
                return result.get("result", "")

            a2a_protocol = A2AProtocol(endpoint=endpoint, transport=transport)
            await a2a_protocol.connect()
            try:
                vars_to_pass = {}
                if variables is not None:
                    vars_to_pass = resolve_names_from_caller_frame(variables)
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
