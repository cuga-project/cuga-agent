"""Execute node for the supervisor conversational graph."""

from __future__ import annotations

from typing import Any, Callable, Optional

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import extract_task_todos_from_new_vars
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    append_chat_messages_with_step_limit as core_append,
    create_error_command as core_create_error,
    execution_output_text,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionRouter
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SUPERVISOR_EXEC_KEY,
    SupervisorExecutionContext,
)
from cuga.config import settings


def _delegation_state_update(state: CugaSupervisorState) -> dict:
    return {
        "selected_agents": list(state.selected_agents),
        "agent_results": dict(state.agent_results),
        "agent_variables": dict(state.agent_variables),
        "agent_chat_messages": dict(state.agent_chat_messages),
        "metrics": dict(state.metrics or {}),
    }


def _resolve_thread_id(state: CugaSupervisorState, config: Optional[RunnableConfig]) -> Optional[str]:
    cfg = config.get("configurable", {}) if config else {}
    return cfg.get("thread_id") or state.thread_id


def create_execute_agent_tool_node(adapter: Any) -> Callable:
    def append(state, new_msgs):
        return core_append(adapter, state, new_msgs)

    def create_error(updated_messages, error_message, step_count, additional_updates=None):
        return core_create_error(adapter, updated_messages, error_message, step_count, additional_updates)

    async def execute_agent_tool(state: CugaSupervisorState, config: Optional[RunnableConfig] = None):
        logger.info("Supervisor conversational: executing agent delegation code")

        if settings.policy.enabled:
            denial_command = ToolApprovalHandler.handle_denial(adapter, state)
            if denial_command:
                return denial_command

        existing_vars = {}
        var_manager = adapter.get_variable_manager(state)
        if var_manager is not None:
            for var_name in var_manager.get_variable_names():
                existing_vars[var_name] = var_manager.get_variable(var_name)

        exec_ctx = SupervisorExecutionContext(state=state, variable_manager=var_manager)
        context = {
            **existing_vars,
            **adapter._agent_tools_context,
            SUPERVISOR_EXEC_KEY: exec_ctx,
        }

        try:
            exec_plan = ExecutionRouter.resolve(settings)
            if exec_plan.split_execution_active:
                logger.info(
                    "Supervisor split execution: python=%s shell=%s fs=%s",
                    exec_plan.python_backend,
                    exec_plan.shell_backend,
                    exec_plan.filesystem_backend,
                )
            output, new_vars = await CodeExecutor.eval_with_tools_async(
                code=state.script,
                _locals=context,
                state=state,
                thread_id=_resolve_thread_id(state, config),
                apps_list=None,
                variable_manager=var_manager,
                plan=exec_plan,
            )

            logger.debug(f"Execution output: {output.strip()[:500]}...")

            if var_manager is not None:
                for name, value in new_vars.items():
                    var_manager.add_variable(
                        value, name=name, description="Created during agent delegation execution"
                    )

            execution_message_content = execution_output_text(output)
            new_message = HumanMessage(content=execution_message_content)
            updated_messages, error_message = append(state, [new_message])

            delegation_updates = _delegation_state_update(state)
            if error_message:
                return create_error(
                    updated_messages,
                    error_message,
                    state.step_count,
                    additional_updates={
                        "supervisor_variables": state.supervisor_variables,
                        **delegation_updates,
                    },
                )

            base_update = {
                "supervisor_chat_messages": updated_messages,
                "supervisor_variables": state.supervisor_variables,
                "step_count": state.step_count + 1,
                **delegation_updates,
            }
            # The create_update_todos tool writes onto the run-local state via the execution
            # context, so prefer that; fall back to scanning execution outputs for older flows.
            if state.task_todos is not None:
                base_update["task_todos"] = state.task_todos
            else:
                todo_state_update = extract_task_todos_from_new_vars(new_vars)
                if todo_state_update is not None:
                    base_update["task_todos"] = todo_state_update
            return base_update
        except Exception as exc:
            error_msg = f"Error during execution: {str(exc)}"
            logger.error(error_msg, exc_info=True)
            new_message = HumanMessage(content=error_msg)
            updated_messages, limit_error_message = append(state, [new_message])

            if limit_error_message:
                return create_error(updated_messages, limit_error_message, state.step_count)

            return {
                "supervisor_chat_messages": updated_messages,
                "error": error_msg,
                "execution_complete": True,
                "step_count": state.step_count + 1,
                **_delegation_state_update(state),
            }

    return execute_agent_tool
