"""Prepare node for the supervisor conversational graph."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import create_update_todos_tool
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import (
    ExecutionRouter,
    split_execution_note,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.nodes.cuga_agent_core.tools.runtime_tools import (
    build_runtime_tools,
    prompt_tool_dicts,
    resolve_runtime_backends,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import AgentInfo, CugaSupervisorState
from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import create_agent_delegation_func
from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    resolve_supervisor_execution_context,
)
from cuga.config import settings
from cuga.configurations.instructions_manager import get_all_instructions_formatted


def create_prepare_agents_and_prompt_node(adapter: Any) -> Callable:
    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "supervisor_lite_prompt.jinja2"
    with open(prompt_path, "r", encoding="utf-8") as prompt_file:
        prompt_template_str = prompt_file.read()
    instructions = get_all_instructions_formatted()

    def _store_todos_on_run_state(serialized_todos: List[Dict[str, str]]) -> None:
        """Persist todos onto the active run's state, not a shared adapter list.

        The create_update_todos tool runs inside ``execute_agent_tool``; the per-run
        execution context resolved here points at that run's CugaSupervisorState, so
        concurrent conversations never share a todo list.
        """
        exec_ctx = resolve_supervisor_execution_context()
        if exec_ctx is not None and exec_ctx.state is not None:
            exec_ctx.state.task_todos = serialized_todos

    async def prepare_agents_and_prompt(
        state: CugaSupervisorState, config: Optional[RunnableConfig] = None
    ) -> Command:
        logger.info("Preparing agents and prompt for supervisor conversational mode")

        is_fresh_conversation = len(state.supervisor_chat_messages or []) <= 1
        if is_fresh_conversation:
            state.task_todos = None

        cfg = config.get("configurable", {}) if config else {}
        enable_todos = (
            cfg.get("enable_todos") if "enable_todos" in cfg else settings.advanced_features.enable_todos
        )

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
                if is_fresh_conversation and isinstance(getattr(policy_command, "update", None), dict):
                    policy_command.update.setdefault("task_todos", None)
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
                            description = agent_or_config.get("description", f"External agent: {agent_name}")
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
            tool_func = create_agent_delegation_func(
                adapter, agent_name, agent_or_config, agent_card=agent_card
            )
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
                        f"Delegate a task to the {agent_name} agent. This agent specializes in: {description}"
                    ),
                    "params_str": "task: str, variables: Optional[List[str]] = None",
                    "params_doc": (
                        f"- task (str): The task description to delegate to {agent_name}\n"
                        f"- variables (Optional[List[str]]): List of variable names to pass"
                    ),
                    "response_doc": f"Returns the result from {agent_name} agent execution.",
                }
            agent_tools_for_prompt.append(tool_info)

        todos_tool = await create_update_todos_tool(write_todos=_store_todos_on_run_state)
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

        runtime_thread_id = cfg.get("thread_id") or state.thread_id
        runtime_backends = resolve_runtime_backends(settings, cfg)
        runtime_bundle = build_runtime_tools(thread_id=runtime_thread_id, backends=runtime_backends)
        adapter._agent_tools_context.update(runtime_bundle.execution_callables)
        agent_tools_for_prompt.extend(prompt_tool_dicts(runtime_bundle.prompt_tools))

        skills_section = ""
        if getattr(settings.skills, "enabled", False):
            from cuga.backend.skills import (
                SkillRegistry,
                create_skill_tools,
                discover_skills,
                format_available_skills_block,
            )

            cuga_folder = os.getenv("CUGA_FOLDER", settings.policy.cuga_folder)
            skill_entries = discover_skills(cuga_folder)
            if skill_entries:
                skill_registry = SkillRegistry(skill_entries)
                skill_tools = create_skill_tools(skill_registry)
                for skill_tool in skill_tools:
                    tool_func = (
                        skill_tool.coroutine
                        if getattr(skill_tool, "coroutine", None)
                        else getattr(skill_tool, "func", None)
                    )
                    if tool_func:
                        adapter._agent_tools_context[skill_tool.name] = make_tool_awaitable(tool_func)
                agent_tools_for_prompt.extend(prompt_tool_dicts(skill_tools))
                skills_section = format_available_skills_block(skill_registry)
                logger.info(f"Supervisor: loaded {len(skill_entries)} skill(s)")

        if adapter._tool_provider is not None:
            try:
                provider_tools = await adapter._tool_provider.get_all_tools()
                for provider_tool in provider_tools:
                    provider_func = (
                        provider_tool.coroutine
                        if getattr(provider_tool, "coroutine", None)
                        else getattr(provider_tool, "func", None)
                    )
                    if provider_func:
                        adapter._agent_tools_context[provider_tool.name] = make_tool_awaitable(provider_func)
                agent_tools_for_prompt.extend(prompt_tool_dicts(provider_tools))
                logger.info(f"Supervisor: loaded {len(provider_tools)} tool(s) from tool_provider")
            except Exception as exc:
                logger.warning(f"Supervisor: failed to load tools from tool_provider: {exc}")

        split_note = split_execution_note(ExecutionRouter.resolve(settings))
        effective_special_instructions = (
            "\n\n".join(filter(None, [adapter._special_instructions, skills_section, split_note])) or None
        )

        is_autonomous_subtask = state.sub_task is not None and state.sub_task.strip() != ""

        from jinja2 import Template

        template = Template(prompt_template_str)
        dynamic_prompt = template.render(
            base_prompt=adapter._static_prompt,
            agents=agent_list,
            tools=agent_tools_for_prompt,
            is_autonomous_subtask=is_autonomous_subtask,
            instructions=instructions,
            enable_todos=enable_todos,
            special_instructions=effective_special_instructions,
        )

        update_payload = {
            "tools_prepared": True,
            "prepared_prompt": dynamic_prompt,
            "step_count": 0,
            "available_agents": {
                agent["name"]: AgentInfo(
                    name=agent["name"], type=agent["type"], description=agent["description"]
                ).model_dump()
                for agent in agent_list
            },
        }
        if adapter.get_metadata(state):
            update_payload[adapter.metadata_key] = adapter.get_metadata(state)
        if is_fresh_conversation:
            update_payload["task_todos"] = None

        return Command(
            goto="call_model",
            update=update_payload,
        )

    return prepare_agents_and_prompt
