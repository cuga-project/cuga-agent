"""Execute node for the supervisor conversational graph."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional, Set

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command
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
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SUPERVISOR_EXEC_KEY,
    SupervisorExecutionContext,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
    delegate_tool_names,
)
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import create_agent_approval_action
from cuga.config import settings

_DELEGATE_CALL_RE = re.compile(r"delegate_to_(\w+)\s*\(([^)]*)\)")
_TASK_KWARG_RE = re.compile(r"task\s*=\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")


def _extract_pending_delegations(script: Optional[str], known_agents: Set[str]) -> Dict[str, str]:
    """Best-effort scan of generated delegation code for ``delegate_to_<agent>(task=...)`` calls.

    The supervisor is code-generating (like cuga_lite), so "the plan" is Python source, not a
    structured tool-call list — this mirrors the same regex-based extraction shape without
    needing a full AST walk for a two-line approval message.

    Matches against ``delegate_tool_names`` rather than raw agent ids: agent ids from the
    manage UI are slugified with hyphens (e.g. ``crm-agent``), but the delegate tool is actually
    named ``delegate_to_crm_h_agent`` — matching hyphenated ids directly would never recognize the
    call, silently disabling the approval gate for any UI-created agent.
    """
    if not script:
        return {}
    tool_name_to_agent = {tool: name for name, tool in delegate_tool_names(known_agents).items()}
    tasks: Dict[str, str] = {}
    for match in _DELEGATE_CALL_RE.finditer(script):
        full_call_name = f"delegate_to_{match.group(1)}"
        call_args = match.group(2)
        agent_name = tool_name_to_agent.get(full_call_name)
        if agent_name is None or agent_name in tasks:
            continue
        task_match = _TASK_KWARG_RE.search(call_args)
        tasks[agent_name] = task_match.group(1)[1:-1] if task_match else "(see generated plan)"
    return tasks


def _delegation_state_update(state: CugaSupervisorState) -> dict:
    return {
        "selected_agents": list(state.selected_agents),
        "agent_results": dict(state.agent_results),
        "agent_variables": dict(state.agent_variables),
        "agent_chat_messages": dict(state.agent_chat_messages),
        "supervisor_metadata": dict(state.supervisor_metadata or {}),
        "metrics": dict(state.metrics or {}),
    }


def _resolve_thread_id(state: CugaSupervisorState, config: Optional[RunnableConfig]) -> Optional[str]:
    cfg = config.get("configurable", {}) if config else {}
    return cfg.get("thread_id") or state.thread_id


def _budget_updates() -> dict:
    """Tool-call budget fields every exit from the execute node must carry.

    Each path runs *after* the delegation code, so each can be leaving spent
    budget behind. Omitting them leaves the keys absent from the update and the
    checkpoint keeps its pre-execution values, under-counting the ceiling.
    """
    return {
        "tool_calls_used_run": ToolCallTracker.get_run_budget_used(),
        "tool_calls_used_thread": ToolCallTracker.get_thread_budget_used(),
        "tool_budget_exhausted": ToolCallTracker.budget_exhausted(),
    }


def create_execute_agent_tool_node(adapter: Any) -> Callable:
    def append(state, new_msgs):
        return core_append(adapter, state, new_msgs)

    def create_error(updated_messages, error_message, step_count, additional_updates=None):
        return core_create_error(adapter, updated_messages, error_message, step_count, additional_updates)

    def _maybe_create_plan_approval_command(state: CugaSupervisorState) -> Optional[Command]:
        """Gate delegation behind human approval when the supervisor's planApproval is on.

        Mirrors ToolApprovalHandler._create_approval_interrupt's Command shape (goto=END,
        hitl_action + sender set) so it flows through the same, already-wired
        SuggestHumanActions -> WaitForResponse -> interrupt() path and the existing
        AGENT_APPROVAL handling in CugaSupervisorNode.callback_node.
        """
        if not getattr(adapter, "_plan_approval", False):
            return None
        metadata = adapter.get_metadata(state) or {}
        if metadata.get("plan_approved"):
            return None
        pending = _extract_pending_delegations(state.script, set(adapter._agents.keys()))
        if not pending:
            return None

        hitl_action = create_agent_approval_action(
            agent_names=list(pending.keys()),
            tasks=pending,
        )
        approval_metadata = {**metadata, "approval_required": True, "plan_approved": False}
        updated_messages, error_message = append(state, [AIMessage(content=hitl_action.description)])
        if error_message:
            return create_error(updated_messages, error_message, state.step_count)

        return Command(
            goto=END,
            update={
                adapter.messages_key: updated_messages,
                "final_answer": hitl_action.description,
                adapter.metadata_key: approval_metadata,
                "hitl_action": hitl_action,
                "sender": adapter.sender_name,
                "step_count": state.step_count + 1,
            },
        )

    async def execute_agent_tool(state: CugaSupervisorState, config: Optional[RunnableConfig] = None):
        logger.info("Supervisor conversational: executing agent delegation code")

        if settings.policy.enabled:
            denial_command = ToolApprovalHandler.handle_denial(adapter, state)
            if denial_command:
                return denial_command

        approval_command = _maybe_create_plan_approval_command(state)
        if approval_command:
            return approval_command
        # One-shot: a prior turn's approval clears itself here so the *next* delegation
        # (next turn) is gated again rather than staying approved for the whole thread.
        plan_approval_consumed = False
        if getattr(adapter, "_plan_approval", False):
            meta = adapter.get_metadata(state)
            if meta.get("plan_approved"):
                adapter.set_metadata(state, {**meta, "plan_approved": False})
                plan_approval_consumed = True

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

        # Tool-call budgets: carry the turn count from earlier steps and the
        # conversation count from earlier turns. Without this the supervisor's
        # delegation tools escape the caps entirely.
        ToolCallTracker.seed_call_budget(
            getattr(state, "tool_calls_used_run", 0),
            getattr(state, "tool_calls_used_thread", 0),
        )

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
                        **_budget_updates(),
                        **delegation_updates,
                    },
                )

            base_update = {
                "supervisor_chat_messages": updated_messages,
                "supervisor_variables": state.supervisor_variables,
                "step_count": state.step_count + 1,
                **_budget_updates(),
                **delegation_updates,
            }
            if plan_approval_consumed:
                base_update[adapter.metadata_key] = adapter.get_metadata(state)
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
                return create_error(
                    updated_messages,
                    limit_error_message,
                    state.step_count,
                    additional_updates=_budget_updates(),
                )

            return {
                "supervisor_chat_messages": updated_messages,
                "error": error_msg,
                "execution_complete": True,
                "step_count": state.step_count + 1,
                **_budget_updates(),
                **_delegation_state_update(state),
            }

    return execute_agent_tool
