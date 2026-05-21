"""SupervisorGraphAdapter — CoreGraphAdapter implementation for CugaSupervisor.

Provides all hook overrides that the shared ``create_call_model_node`` factory
delegates to for Supervisor-specific behaviour:

- messages_key, metadata_key, execute_node_name, sender_name attributes
- get_messages: reads state.supervisor_chat_messages
- resolve_max_steps: state.cuga_lite_max_steps → settings default
- get_variable_manager: state.supervisor_variables_manager (Phase-9 coupling fix)
- get_variables_storage: state.supervisor_variables
- build_prepare_node(): returns the prepare_agents_and_prompt async node
- build_execute_node(): returns the execute_agent_tool async node

``_resolve_names_from_caller_frame`` is a module-level helper moved here from
``cuga_supervisor_graph.py`` so delegation functions can resolve variable names
from the delegating code's caller frame.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import BaseMessage, HumanMessage
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.config import settings


# ── Module-level helper (moved from cuga_supervisor_graph.py) ──────────────


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


# ── SupervisorGraphAdapter ─────────────────────────────────────────────────


class SupervisorGraphAdapter(CoreGraphAdapter):
    """CoreGraphAdapter implementation for the CugaSupervisor multi-agent graph.

    Overrides the hook methods that differ from the no-op defaults and provides
    ``build_prepare_node`` / ``build_execute_node`` factories that produce the
    graph nodes parameterised by the agents and tool configuration captured at
    construction time.
    """

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
        # Mutable state shared between the prepare and execute node closures
        self._agent_tools_context: Dict[str, Any] = {}
        self._shared_vm_ref: List[Any] = [None]

    # ── Abstract method implementations ───────────────────────────────────

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

    # ── Hook overrides ─────────────────────────────────────────────────────

    def get_variable_manager(self, state: Any) -> Any:
        return getattr(state, "supervisor_variables_manager", None)

    def get_variables_storage(self, state: Any) -> Optional[Any]:
        return getattr(state, "supervisor_variables", None)

    # ── Node factories ─────────────────────────────────────────────────────

    def build_prepare_node(self) -> Callable:
        """Return the ``prepare_agents_and_prompt`` async node function."""
        from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
            AgentInfo,
            CugaSupervisorState,
        )
        from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import ToolApprovalHandler
        from cuga.backend.cuga_graph.nodes.cuga_agent_core.tools.runtime_tools import (
            build_runtime_tools,
            prompt_tool_dicts,
            resolve_runtime_backends,
        )
        from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import (
            ExecutionRouter,
            split_execution_note,
        )
        from cuga.configurations.instructions_manager import get_all_instructions_formatted
        from langchain_core.runnables import RunnableConfig
        from langgraph.types import Command

        adapter = self

        prompt_filename = "supervisor_lite_prompt.jinja2"
        prompt_path = Path(__file__).parent / "prompts" / prompt_filename
        with open(prompt_path, "r", encoding="utf-8") as _f:
            _prompt_template_str = _f.read()
        _instructions = get_all_instructions_formatted()

        def _create_agent_delegation_func(
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
                        vars_to_pass = _resolve_names_from_caller_frame(variables)
                    result = await agent_or_config.invoke(
                        task,
                        thread_id=f"supervisor_conversational_{agent_name}",
                        variables=vars_to_pass if vars_to_pass else None,
                    )
                    if (
                        hasattr(result, "variables")
                        and result.variables
                        and adapter._shared_vm_ref[0] is not None
                    ):
                        from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import (
                            VariableBridge,
                        )

                        bridged = VariableBridge.bridge(
                            result.variables,
                            adapter._shared_vm_ref[0],
                            description_prefix=f"from {agent_name}",
                        )
                        if bridged:
                            logger.info(
                                "Bridged %d variable(s) from %s: %s", len(bridged), agent_name, bridged
                            )
                    return result.answer if hasattr(result, "answer") else str(result)

                if isinstance(agent_or_config, dict) and agent_or_config.get("type") == "external":
                    a2a_config = agent_or_config.get("config", {}).get("a2a_protocol", {})
                    endpoint = a2a_config.get("endpoint")
                    transport = a2a_config.get("transport", "http")

                    if agent_card is not None and HAS_A2A_SDK and transport == "http":
                        vars_to_pass = {}
                        if pass_variables_a2a and variables is not None:
                            vars_to_pass = _resolve_names_from_caller_frame(variables)
                        result = await delegate_task_via_a2a_sdk(
                            agent_card,
                            task,
                            auth=a2a_config.get("auth"),
                            timeout=float(a2a_config.get("timeout", 30)),
                            variables=vars_to_pass if vars_to_pass else None,
                        )
                        return result.get("result", "")
                    else:
                        a2a_protocol = A2AProtocol(endpoint=endpoint, transport=transport)
                        await a2a_protocol.connect()
                        try:
                            vars_to_pass = {}
                            if variables is not None:
                                vars_to_pass = _resolve_names_from_caller_frame(variables)
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

        async def prepare_agents_and_prompt(
            state: CugaSupervisorState, config: Optional[RunnableConfig] = None
        ) -> Command:
            logger.info("Preparing agents and prompt for supervisor conversational mode")

            if settings.policy.enabled and not ToolApprovalHandler.should_skip_policy_check(adapter, state):
                from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
                from cuga.backend.cuga_graph.policy.models import PolicyType

                policy_command, policy_metadata = await PolicyEnactment.check_and_enact(
                    state,
                    config,
                    policy_types=[
                        PolicyType.INTENT_GUARD,
                        PolicyType.PLAYBOOK,
                        PolicyType.TOOL_GUIDE,
                    ],
                    adapter=adapter,
                )
                if policy_command:
                    return policy_command
                if policy_metadata:
                    adapter.set_metadata(state, policy_metadata)

            from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
                HAS_A2A_SDK,
                _agent_card_description,
                fetch_agent_card,
                format_agent_card_for_prompt,
            )
            from cuga.sdk import CugaAgent

            agent_list = []
            agent_tools_for_prompt = []
            pass_variables_a2a = getattr(settings.supervisor, "pass_variables_a2a", False)

            for agent_name, agent_or_config in adapter._agents.items():
                agent_card = None
                if isinstance(agent_or_config, CugaAgent):
                    agent_type = "internal"
                    description = getattr(agent_or_config, "description", f"Internal agent: {agent_name}")
                elif isinstance(agent_or_config, dict):
                    agent_type = agent_or_config.get("type", "external")
                    a2a_cfg = agent_or_config.get("config", {}).get("a2a_protocol", {})
                    if agent_type == "external" and HAS_A2A_SDK and a2a_cfg.get("transport") == "http":
                        endpoint = a2a_cfg.get("endpoint")
                        if endpoint:
                            try:
                                agent_card = await fetch_agent_card(
                                    endpoint,
                                    auth=a2a_cfg.get("auth"),
                                    timeout=float(a2a_cfg.get("timeout", 30)),
                                )
                                description = _agent_card_description(agent_card)
                            except Exception as e:
                                logger.warning(f"Failed to fetch A2A agent card for {agent_name}: {e}")
                                description = agent_or_config.get(
                                    "description", f"External agent: {agent_name}"
                                )
                        else:
                            description = agent_or_config.get("description", f"External agent: {agent_name}")
                    else:
                        description = agent_or_config.get("description", f"{agent_type} agent: {agent_name}")
                else:
                    agent_type = "unknown"
                    description = f"Agent: {agent_name}"

                agent_entry = {"name": agent_name, "type": agent_type, "description": description}
                if agent_card is not None:
                    agent_entry["agent_card"] = format_agent_card_for_prompt(agent_card)
                agent_list.append(agent_entry)

                tool_name = f"delegate_to_{agent_name}"
                tool_func = _create_agent_delegation_func(agent_name, agent_or_config, agent_card=agent_card)
                adapter._agent_tools_context[tool_name] = tool_func

                is_a2a_agent = agent_card is not None
                if is_a2a_agent and pass_variables_a2a:
                    tool_info = {
                        "name": tool_name,
                        "description": (
                            f"Delegate a task to the {agent_name} agent. {description} "
                            "Variables are passed in request metadata."
                        ),
                        "params_str": "task: str, variables: Optional[List[str]] = None",
                        "params_doc": (
                            f"- task (str): The task description to send to {agent_name}\n"
                            f"- variables (Optional[List[str]]): Variable names to pass in A2A metadata"
                        ),
                        "response_doc": f"Returns the result from {agent_name}.",
                    }
                elif is_a2a_agent:
                    tool_info = {
                        "name": tool_name,
                        "description": f"Delegate a task to {agent_name}. {description}",
                        "params_str": "task: str",
                        "params_doc": f"- task (str): The task description to send to {agent_name}.",
                        "response_doc": f"Returns the result from {agent_name}.",
                    }
                else:
                    tool_info = {
                        "name": tool_name,
                        "description": (
                            f"Delegate a task to the {agent_name} agent. "
                            f"This agent specializes in: {description}"
                        ),
                        "params_str": "task: str, variables: Optional[List[str]] = None",
                        "params_doc": (
                            f"- task (str): The task description to delegate to {agent_name}\n"
                            f"- variables (Optional[List[str]]): List of variable names to pass"
                        ),
                        "response_doc": f"Returns the result from {agent_name} agent execution.",
                    }
                agent_tools_for_prompt.append(tool_info)

            from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import create_update_todos_tool
            from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
                make_tool_awaitable,
            )

            todos_tool = await create_update_todos_tool()
            adapter._agent_tools_context["create_update_todos"] = make_tool_awaitable(todos_tool.func)
            agent_tools_for_prompt.append(
                {
                    "name": "create_update_todos",
                    "description": todos_tool.description,
                    "params_str": "todos: List[Dict[str, str]]",
                    "params_doc": (
                        "todos: List of todo items, each with 'text' and 'status' ('pending' or 'completed')"
                    ),
                    "response_doc": "Returns the current list of todos with their status.",
                }
            )

            _cfg = config.get("configurable", {}) if config else {}
            _runtime_thread_id = _cfg.get("thread_id") or state.thread_id
            _runtime_backends = resolve_runtime_backends(settings, _cfg)
            _runtime_bundle = build_runtime_tools(thread_id=_runtime_thread_id, backends=_runtime_backends)
            adapter._agent_tools_context.update(_runtime_bundle.execution_callables)
            agent_tools_for_prompt.extend(prompt_tool_dicts(_runtime_bundle.prompt_tools))

            _skills_section = ""
            if getattr(settings.skills, "enabled", False):
                from cuga.backend.skills import (
                    SkillRegistry,
                    create_skill_tools,
                    discover_skills,
                    format_available_skills_block,
                )

                _cuga_folder = os.getenv("CUGA_FOLDER", settings.policy.cuga_folder)
                _skill_entries = discover_skills(_cuga_folder)
                if _skill_entries:
                    _skill_registry = SkillRegistry(_skill_entries)
                    _skill_tools = create_skill_tools(_skill_registry)
                    for _st in _skill_tools:
                        _tool_func = (
                            _st.coroutine if getattr(_st, "coroutine", None) else getattr(_st, "func", None)
                        )
                        if _tool_func:
                            adapter._agent_tools_context[_st.name] = make_tool_awaitable(_tool_func)
                    agent_tools_for_prompt.extend(prompt_tool_dicts(_skill_tools))
                    _skills_section = format_available_skills_block(_skill_registry)
                    logger.info(f"Supervisor: loaded {len(_skill_entries)} skill(s)")

            if adapter._tool_provider is not None:
                try:
                    _provider_tools = await adapter._tool_provider.get_all_tools()
                    for _pt in _provider_tools:
                        _pt_func = (
                            _pt.coroutine if getattr(_pt, "coroutine", None) else getattr(_pt, "func", None)
                        )
                        if _pt_func:
                            adapter._agent_tools_context[_pt.name] = make_tool_awaitable(_pt_func)
                    agent_tools_for_prompt.extend(prompt_tool_dicts(_provider_tools))
                    logger.info(f"Supervisor: loaded {len(_provider_tools)} tool(s) from tool_provider")
                except Exception as _e:
                    logger.warning(f"Supervisor: failed to load tools from tool_provider: {_e}")

            _split_note = split_execution_note(ExecutionRouter.resolve(settings))
            _effective_special_instructions = (
                "\n\n".join(filter(None, [adapter._special_instructions, _skills_section, _split_note]))
                or None
            )

            is_autonomous_subtask = state.sub_task is not None and state.sub_task.strip() != ""

            from jinja2 import Template

            template = Template(_prompt_template_str)
            dynamic_prompt = template.render(
                base_prompt=None,
                agents=agent_list,
                tools=agent_tools_for_prompt,
                is_autonomous_subtask=is_autonomous_subtask,
                instructions=_instructions,
                enable_todos=True,
                special_instructions=_effective_special_instructions,
            )

            return Command(
                goto="call_model",
                update={
                    "tools_prepared": True,
                    "prepared_prompt": dynamic_prompt,
                    "step_count": 0,
                    "available_agents": {
                        name: AgentInfo(
                            name=name, type=info["type"], description=info["description"]
                        ).model_dump()
                        for name, info in zip([a["name"] for a in agent_list], agent_list)
                    },
                },
            )

        return prepare_agents_and_prompt

    def build_execute_node(self) -> Callable:
        """Return the ``execute_agent_tool`` async node function."""
        from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
            CugaSupervisorState,
        )
        from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import ToolApprovalHandler
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
        from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
            append_chat_messages_with_step_limit as _core_append,
            create_error_command as _core_create_error,
            execution_output_text,
        )
        from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionRouter
        from langchain_core.runnables import RunnableConfig

        adapter = self

        def _append(state, new_msgs):
            return _core_append(adapter, state, new_msgs)

        def _create_error(updated_messages, error_message, step_count, additional_updates=None):
            return _core_create_error(
                adapter, updated_messages, error_message, step_count, additional_updates
            )

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
                _exec_plan = ExecutionRouter.resolve(settings)
                if _exec_plan.split_execution_active:
                    logger.info(
                        "Supervisor split execution: python=%s shell=%s fs=%s",
                        _exec_plan.python_backend,
                        _exec_plan.shell_backend,
                        _exec_plan.filesystem_backend,
                    )
                output, new_vars = await CodeExecutor.eval_with_tools_async(
                    code=state.script,
                    _locals=context,
                    state=state,
                    thread_id=state.thread_id,
                    apps_list=None,
                    variable_manager=adapter.get_variable_manager(state),
                    plan=_exec_plan,
                )

                logger.debug(f"Execution output: {output.strip()[:500]}...")

                if var_manager is not None:
                    for name, value in new_vars.items():
                        var_manager.add_variable(
                            value, name=name, description="Created during agent delegation execution"
                        )

                execution_message_content = execution_output_text(output)
                new_message = HumanMessage(content=execution_message_content)
                updated_messages, error_message = _append(state, [new_message])

                if error_message:
                    return _create_error(
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
            except Exception as e:
                error_msg = f"Error during execution: {str(e)}"
                logger.error(error_msg, exc_info=True)
                new_message = HumanMessage(content=error_msg)
                updated_messages, limit_error_message = _append(state, [new_message])

                if limit_error_message:
                    return _create_error(updated_messages, limit_error_message, state.step_count)

                return {
                    "supervisor_chat_messages": updated_messages,
                    "error": error_msg,
                    "execution_complete": True,
                    "step_count": state.step_count + 1,
                }

        return execute_agent_tool
