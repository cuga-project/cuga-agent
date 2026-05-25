"""Execute node for the supervisor conversational graph."""

from __future__ import annotations

from typing import Any, Callable, Optional

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    append_chat_messages_with_step_limit as core_append,
    create_error_command as core_create_error,
    execution_output_text,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionRouter
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.config import settings


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
            adapter._shared_vm_ref[0] = var_manager

        context = {**existing_vars, **adapter._agent_tools_context}

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
                thread_id=state.thread_id,
                apps_list=None,
                variable_manager=adapter.get_variable_manager(state),
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

            if error_message:
                return create_error(
                    updated_messages,
                    error_message,
                    state.step_count,
                    additional_updates={"supervisor_variables": state.supervisor_variables},
                )

            return {
                "supervisor_chat_messages": updated_messages,
                "supervisor_variables": state.supervisor_variables,
                "step_count": state.step_count + 1,
            }
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
            }

    return execute_agent_tool
