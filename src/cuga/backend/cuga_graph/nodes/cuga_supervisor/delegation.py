"""Agent delegation helpers for supervisor conversational mode."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SUPERVISOR_EXEC_KEY,
    resolve_supervisor_execution_context,
)
from cuga.config import settings


def _frame_has_supervisor_exec(frame: Any) -> bool:
    return SUPERVISOR_EXEC_KEY in frame.f_locals or SUPERVISOR_EXEC_KEY in frame.f_globals


def _variables_from_supervisor_vm() -> Dict[str, Any]:
    """Copy the active supervisor variable manager into a name→value dict."""
    exec_ctx = resolve_supervisor_execution_context()
    if exec_ctx is None or exec_ctx.variable_manager is None:
        return {}
    vm = exec_ctx.variable_manager
    return {name: vm.get_variable(name) for name in vm.get_variable_names()}


def resolve_names_from_caller_frame(variable_names: List[str]) -> Dict[str, Any]:
    """Resolve names from the generated supervisor script frame.

    Walk to the frame holding ``SUPERVISOR_EXEC_KEY`` (``_async_main``), not the
    immediate ``delegate_to_agent`` wrapper. Fill names missing from that frame
    from the supervisor VM (prior-turn values are not locals yet).
    """
    resolved: Dict[str, Any] = {}
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame is not None else None
        first_caller = current
        exec_frame = None
        while current is not None:
            if _frame_has_supervisor_exec(current):
                exec_frame = current
                break
            current = current.f_back
        target = exec_frame if exec_frame is not None else first_caller
        if target is None:
            return resolved
        for name in variable_names:
            if name in target.f_locals:
                resolved[name] = target.f_locals[name]
            elif name in target.f_globals:
                resolved[name] = target.f_globals[name]
        if any(name not in resolved for name in variable_names):
            vm_vars = _variables_from_supervisor_vm()
            for name in variable_names:
                if name not in resolved and name in vm_vars:
                    resolved[name] = vm_vars[name]
    finally:
        del frame
    return resolved


def _record_delegation(
    adapter: Any,
    agent_name: str,
    *,
    result: Any = None,
    answer: Any,
    variables: Optional[Dict[str, Any]] = None,
) -> None:
    exec_ctx = resolve_supervisor_execution_context()
    if exec_ctx is None or exec_ctx.state is None:
        return

    record = getattr(adapter, "record_delegation", None)
    if callable(record):
        record(
            exec_ctx.state,
            agent_name,
            result=result,
            answer=answer,
            variables=variables,
        )


def create_agent_delegation_func(
    adapter: Any,
    agent_name: str,
    agent_or_config: Any,
    agent_card: Any = None,
    permission_handler: Any = None,
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
            if variables is not None:
                vars_to_pass = resolve_names_from_caller_frame(variables)
            else:
                vars_to_pass = _variables_from_supervisor_vm()
            result = await agent_or_config.invoke(
                task,
                thread_id=f"supervisor_conversational_{agent_name}",
                variables=vars_to_pass if vars_to_pass else None,
            )

            exec_ctx = resolve_supervisor_execution_context()
            if (
                hasattr(result, "variables")
                and result.variables
                and exec_ctx is not None
                and exec_ctx.variable_manager is not None
            ):
                from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import (
                    VariableBridge,
                )

                bridged = VariableBridge.bridge(
                    result.variables,
                    exec_ctx.variable_manager,
                    description_prefix=f"from {agent_name}",
                )
                if bridged:
                    logger.info(f"Bridged {len(bridged)} variable(s) from {agent_name}: {bridged}")

            answer = result.answer if hasattr(result, "answer") else str(result)
            result_vars = getattr(result, "variables", None) or None
            _record_delegation(
                adapter,
                agent_name,
                result=result,
                answer=answer,
                variables=result_vars,
            )
            return answer

        if isinstance(agent_or_config, dict) and agent_or_config.get("type") == "external":
            external_config = agent_or_config.get("config", {})
            from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import (
                validate_external_protocol_config,
            )

            try:
                acp_mapping, a2a_mapping = validate_external_protocol_config(
                    external_config, require_enabled=True
                )
            except ValueError:
                result = {
                    "result": "ACP agent configuration is invalid.",
                    "status": "failed",
                    "variables": {},
                }
                answer = result["result"]
                _record_delegation(adapter, agent_name, result=result, answer=answer, variables={})
                return answer
            if acp_mapping is not None and acp_mapping["enabled"]:
                from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.config import (
                    acp_process_config_from_mapping,
                )
                from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import (
                    delegate_task_via_acp,
                )

                permission_bridge = None
                try:
                    acp_config = acp_process_config_from_mapping(
                        acp_mapping,
                        name=external_config.get("name", agent_name),
                        description=external_config.get("description"),
                    )
                except ValueError:
                    result = {
                        "result": "ACP agent configuration is invalid.",
                        "status": "failed",
                        "variables": {},
                    }
                else:
                    exec_ctx = resolve_supervisor_execution_context()
                    if (
                        exec_ctx is not None
                        and exec_ctx.pending_acp_registry is not None
                        and isinstance(exec_ctx.thread_id, str)
                        and exec_ctx.thread_id.strip()
                    ):
                        from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import (
                            ACPPermissionRuntimeBridge,
                        )

                        def finalize_pending(outcome: str) -> None:
                            result = {
                                "result": f"ACP pending delegation ended without a valid resume ({outcome}).",
                                "status": "failed",
                                "variables": {},
                            }
                            adapter.record_delegation(
                                exec_ctx.state,
                                agent_name,
                                result=result,
                                answer=result["result"],
                                variables={},
                            )

                        permission_bridge = ACPPermissionRuntimeBridge(
                            registry=exec_ctx.pending_acp_registry,
                            thread_id=exec_ctx.thread_id,
                            agent_name=agent_name,
                            interactive=exec_ctx.interactive,
                            finalizer=finalize_pending,
                        )
                    result = await delegate_task_via_acp(
                        config=acp_config,
                        task=task,
                        permission_handler=permission_handler,
                        permission_bridge=permission_bridge,
                    )
                answer = result.get("result", "")
                result_vars = result.get("variables") or {}
                if permission_bridge is None or not permission_bridge.was_parked:
                    _record_delegation(
                        adapter,
                        agent_name,
                        result=result,
                        answer=answer,
                        variables=result_vars,
                    )
                return answer

            a2a_config = a2a_mapping
            if a2a_config is None:
                error_answer = f"Error: Unknown agent type for {agent_name}"
                _record_delegation(adapter, agent_name, answer=error_answer)
                return error_answer
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
                answer = result.get("result", "")
                _record_delegation(adapter, agent_name, answer=answer)
                return answer

            a2a_protocol = A2AProtocol(endpoint=endpoint, transport=transport)
            await a2a_protocol.connect()
            try:
                vars_to_pass = {}
                if pass_variables_a2a and variables is not None:
                    vars_to_pass = resolve_names_from_caller_frame(variables)
                result = await a2a_protocol.delegate_task(
                    target_agent=agent_name,
                    task=task,
                    context={"thread_id": None},
                    variables=vars_to_pass,
                )
                answer = result.get("result", "")
                _record_delegation(adapter, agent_name, answer=answer)
                return answer
            finally:
                await a2a_protocol.disconnect()

        error_answer = f"Error: Unknown agent type for {agent_name}"
        _record_delegation(adapter, agent_name, answer=error_answer)
        return error_answer

    return delegate_to_agent
