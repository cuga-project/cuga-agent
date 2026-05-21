"""AgentGraphAdapter — CoreGraphAdapter implementation for CugaLite (single-agent).

Provides all hook overrides that the shared ``create_call_model_node`` factory
delegates to for Lite-specific behaviour:

- Few-shot messages, PI injection, todos system block
- normalize_response: normalize_assistant_text + tool-call code recovery
- Tracker side-effects, Langfuse callbacks
- Metadata cleanup (_clean_empty_response_retry_meta)
- NL auto-continue via classify_nl_auto_continue

Also houses the format_task_todos_system_block / format_current_plan_section
helpers that were previously defined in cuga_lite_graph.py; cuga_lite_graph.py
imports them from here in Phase 6.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from loguru import logger

from cuga.backend.activity_tracker.tracker import Step
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    CoreGraphAdapter,
    append_chat_messages_with_step_limit as _core_append_with_step_limit,
    create_error_command as _core_create_error_command,
    execution_output_text,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import (
    ExecutionRouter,
    split_execution_note,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.tools.runtime_tools import (
    build_runtime_tools,
    resolve_runtime_backends,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import (
    CodeExecutor,
    is_find_tools_listing_markdown,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import (
    resolved_runtime_model_name,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    classify_nl_auto_continue,
    normalize_assistant_text,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    create_mcp_prompt,
    format_apps_for_prompt,
    normalize_mcp_few_shot_examples,
    resolve_cuga_lite_few_shots_enabled,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.reflection import reflection_task
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task import TaskAnalyzer
from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
from cuga.backend.llm.errors import extract_code_from_tool_use_failed
from cuga.backend.llm.models import LLMManager
from cuga.backend.skills import (
    SkillRegistry,
    create_skill_tools,
    discover_skills,
    format_available_skills_block,
)
from cuga.config import settings

# ── Helpers (imported from cuga_lite/helpers/) ─────────────────────────────

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import (
    resolve_model_with_bind_tools,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import (
    _first_user_message_text,
    create_find_tools_tool,
    _load_default_find_tools_few_shot_examples,
    _ensure_web_app,
    _web_search_enabled,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.knowledge import (
    _get_knowledge_tool_scope_context,
    _knowledge_scope_instruction,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import (
    create_update_todos_tool,
    extract_task_todos_from_new_vars,
    format_current_plan_section,
    format_task_todos_system_block,
)

_llm_manager = LLMManager()


def _clean_empty_response_retry_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    m = {**(meta or {})}
    m.pop("_empty_response_correction", None)
    return m


def _reflection_current_task(state: Any) -> str:
    """Prefer ``sub_task``; else last user message that is not sandbox ``Execution output`` feedback."""
    if (state.sub_task or "").strip():
        return state.sub_task.strip()
    if state.chat_messages:
        execution_prefix = "Execution output:"
        for msg in reversed(state.chat_messages):
            if isinstance(msg, HumanMessage):
                c = (msg.content or "").strip()
                if c and not c.startswith(execution_prefix):
                    return c
    return ""


def _tool_call_kwarg_literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def _extract_code_from_response_tool_calls(response: Any) -> Optional[str]:
    """Recover fenced Python from AIMessage.tool_calls when content is empty."""
    tool_calls = getattr(response, "tool_calls", None) or (
        getattr(response, "additional_kwargs", None) or {}
    ).get("tool_calls")
    if not tool_calls:
        return None

    tc = tool_calls[0]
    if not isinstance(tc, dict):
        return None

    name = tc.get("name") or (tc.get("function") or {}).get("name")
    args = tc.get("args") or (tc.get("function") or {}).get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    if not name:
        return None

    args_str = ", ".join(
        f"{k}={_tool_call_kwarg_literal(v)}" for k, v in (args if isinstance(args, dict) else {}).items()
    )
    return f"```python\nresult = await {name}({args_str})\nprint(result)\n```"


# ── AgentGraphAdapter ──────────────────────────────────────────────────────


class AgentGraphAdapter(CoreGraphAdapter):
    """CoreGraphAdapter implementation for the CugaLite single-agent graph.

    Overrides all call_model hooks that differ from the no-op defaults:
    few-shot messages, PI, todos, normalize, tracker, callbacks, metadata
    cleanup, and NL auto-continue.
    """

    messages_key: str = "chat_messages"
    execute_node_name: str = "sandbox"
    metadata_key: str = "cuga_lite_metadata"
    sender_name: str = "CugaLite"

    def __init__(
        self,
        *,
        tracker: Any,
        base_callbacks: Optional[List[Any]],
        task_todos_ref: List[Dict[str, str]],
        tools_context_ref: Optional[Dict[str, Any]],
        base_tool_provider: Any,
        model: Any = None,
        prompt_template: Any = None,
        instructions: Any = None,
        special_instructions: Any = None,
        tools_context: Optional[Dict[str, Any]] = None,
        static_prompt: Any = None,
        thread_id: Any = None,
    ) -> None:
        self._tracker = tracker
        self._base_callbacks = base_callbacks or []
        self._task_todos_ref = task_todos_ref
        self._tools_context_ref = tools_context_ref
        self._base_tool_provider = base_tool_provider
        self._model = model
        self._prompt_template = prompt_template
        self._instructions = instructions
        self._special_instructions = special_instructions
        self._tools_context = tools_context if tools_context is not None else {}
        self._static_prompt = static_prompt
        self._thread_id = thread_id

    # ── Abstract method implementations ───────────────────────────────────

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        if override is not None:
            return override
        return (
            state.cuga_lite_max_steps
            if getattr(state, "cuga_lite_max_steps", None) is not None
            else getattr(settings.advanced_features, "cuga_lite_max_steps", 50)
        )

    # ── Pre-invocation hook overrides ─────────────────────────────────────

    def get_few_shot_messages(self, state: Any) -> List[Any]:
        return list(state.mcp_few_shot_messages or [])

    def get_pi(self, state: Any) -> Optional[str]:
        return getattr(state, "pi", None)

    def prepare_system_content(self, state: Any, configurable: dict, base_prompt: str) -> str:
        if self._task_todos_ref:
            return base_prompt + format_task_todos_system_block(self._task_todos_ref)
        task_todos = getattr(state, "task_todos", None)
        if task_todos:
            return base_prompt + format_current_plan_section(task_todos)
        return base_prompt

    def get_tracker(self) -> Any:
        return self._tracker

    def get_invoke_config(self, configurable: dict) -> dict:
        callbacks = configurable.get("callbacks", self._base_callbacks)
        return {"callbacks": callbacks}

    async def ainvoke_model(self, bound: Any, messages: list, invoke_config: dict) -> Any:
        try:
            return await bound.ainvoke(messages, config=invoke_config)
        except Exception as exc:
            code = extract_code_from_tool_use_failed(exc)
            if code:
                logger.warning(
                    "Model attempted tool call without tools bound (tool_use_failed). "
                    "Using generated code in sandbox"
                )

                class _FakeResponse:
                    content = f"```python\n{code}\n```"
                    additional_kwargs: dict = {}

                return _FakeResponse()
            raise

    async def resolve_bind_tools(self, state: Any, active_model: Any, configurable: dict) -> Any:
        try:
            return await resolve_model_with_bind_tools(
                active_model,
                configurable=configurable,
                tools_context_ref=self._tools_context_ref,
                tool_provider=self._base_tool_provider,
            )
        except Exception as exc:
            logger.warning("AgentGraphAdapter.resolve_bind_tools failed: %s", exc)
        return None

    # ── Post-invocation hook overrides ────────────────────────────────────

    def normalize_response(self, response: Any) -> Tuple[str, Optional[str]]:
        content = normalize_assistant_text(response.content)
        if not content:
            tool_code = _extract_code_from_response_tool_calls(response)
            if tool_code:
                logger.warning("Empty content with tool_calls detected; recovering tool call as Python code")
                content = tool_code
        reasoning = normalize_assistant_text(
            (getattr(response, "additional_kwargs", None) or {}).get("reasoning_content")
        )
        return content, reasoning

    def on_response_processed(self, state: Any, code: Optional[str], content: str) -> None:
        try:
            self._tracker.collect_step(step=Step(name="Raw_Assistant_Response", data=content))
            if code:
                self._tracker.collect_step(step=Step(name="Assistant_code", data=content))
            else:
                self._tracker.collect_step(step=Step(name="Assistant_nl", data=content))
        except Exception as exc:
            logger.debug("AgentGraphAdapter.on_response_processed tracker error: %s", exc)

    def build_metadata_update(self, state: Any, *, playbook_fired: bool) -> dict:
        meta = _clean_empty_response_retry_meta(self.get_metadata(state))
        if playbook_fired:
            return {**meta, "playbook_guidance_added": True}
        return meta

    async def classify_auto_continue(
        self, state: Any, model: Any, content: str, reasoning: Optional[str]
    ) -> bool:
        return await classify_nl_auto_continue(model, content, reasoning)

    # ── Node factory methods (Tasks 4c and 5b) ────────────────────────────

    def build_prepare_node(self, lc_bind_tools_meta: dict):
        """Return the prepare_tools_and_apps async node."""

        async def prepare_tools_and_apps(state: Any, config: Optional[RunnableConfig] = None) -> Command:
            """Prepare tools, apps, and prompt once at the start of the graph.

            This node gets tools from tool_provider, filters based on state configuration,
            determines if find_tools should be enabled, and prepares the prompt.
            Tools are available via closure (per graph instance), prompt is stored in state.

            enable_todos is read from config["configurable"] at runtime.

            Optional configurable key ``mcp_few_shot_examples``: overrides few-shots—a JSON string or
            list of dicts with ``role`` and ``content``. If absent (or explicitly ``None``) and
            ``find_tools`` is enabled, ``prompts/find_tools_few_shot_examples.json`` (bundled next to the
            MCP template) is loaded, with optional fallback to repo ``samples/cuga_lite/mcp_few_shot_examples.json``.
            Bundled few-shots only apply when ``find_tools`` shortlisting is active
            (``total_tool_count > shortlisting_tool_threshold``, see settings configurable).

            Disable few-shots entirely via ``advanced_features.cuga_lite_enable_few_shots`` in settings.toml
            or ``cuga_lite_enable_few_shots`` in configurable (skips prefix chat few-shots).
            """
            configurable = config.get("configurable", {}) if config else {}
            enable_todos = (
                configurable.get("enable_todos")
                if "enable_todos" in configurable
                else settings.advanced_features.enable_todos
            )
            shortlisting_threshold = (
                configurable.get("shortlisting_tool_threshold")
                if "shortlisting_tool_threshold" in configurable
                else settings.advanced_features.shortlisting_tool_threshold
            )
            _runtime_model_name = resolved_runtime_model_name(
                configurable_llm=configurable.get("llm"),
                graph_default_model=self._model,
            )
            few_shots_enabled = resolve_cuga_lite_few_shots_enabled(
                configurable,
                model_name=_runtime_model_name,
            )
            logger.debug(
                f"[APPROVAL DEBUG] prepare_tools_and_apps received cuga_lite_metadata: {state.cuga_lite_metadata}"
            )

            # Skip policy checking if policies are disabled or if we're returning from approval
            if settings.policy.enabled and not ToolApprovalHandler.should_skip_policy_check(self, state):
                # Check for policies and enact if matched
                # Include IntentGuard, Playbook, and ToolGuide for intent checks
                from cuga.backend.cuga_graph.policy.models import PolicyType

                command, metadata = await PolicyEnactment.check_and_enact(
                    state,
                    config,
                    policy_types=[PolicyType.INTENT_GUARD, PolicyType.PLAYBOOK, PolicyType.TOOL_GUIDE],
                    adapter=self,
                )

                # If policy returned a command (e.g., BLOCK_INTENT), execute it immediately
                if command:
                    return command

                # If policy returned metadata (e.g., playbook guidance), store it
                if metadata:
                    self.set_metadata(state, metadata)
            elif not settings.policy.enabled:
                logger.debug("Policy system disabled - skipping policy checks")
            else:
                logger.info("[APPROVAL DEBUG] Skipping policy check - user has already approved")

            if not self._base_tool_provider:
                raise ValueError("tool_provider is required")

            # Get total tool count across ALL apps (for shortlisting threshold - not per app)
            all_tools_total = await self._base_tool_provider.get_all_tools()
            total_tool_count = len(all_tools_total) if all_tools_total else 0

            # Get tools from provider
            apps_for_prompt = None
            app_to_tools_map = {}

            # Get apps from state and filter tools if specific app is selected
            if state.sub_task_app:
                # Specific app selected - filter tools to only this app
                all_apps = await self._base_tool_provider.get_apps()
                # add here the implementation of force_
                force_lite_apps = getattr(settings.advanced_features, 'force_lite_mode_apps', [])
                if force_lite_apps:
                    allowed_apps_names = list(set([state.sub_task_app] + force_lite_apps))
                    if _web_search_enabled():
                        allowed_apps_names.append("web")
                    # call authenticate_apps for the allowed apps
                    if settings.advanced_features.benchmark == "appworld":
                        await TaskAnalyzer.call_authenticate_apps(force_lite_apps)
                    apps_for_prompt = [app for app in all_apps if app.name in allowed_apps_names]
                else:
                    apps_for_prompt = [app for app in all_apps if app.name == state.sub_task_app]
                    apps_for_prompt = _ensure_web_app(apps_for_prompt, all_apps)
                # Get only tools for this specific app
                tools_for_execution = []
                for app in apps_for_prompt:
                    current_tools_for_execution = await self._base_tool_provider.get_tools(app.name)
                    app_to_tools_map[app.name] = current_tools_for_execution
                    tools_for_execution.extend(current_tools_for_execution)

                logger.info(
                    f"Filtered to {len(tools_for_execution)} tools for {len(apps_for_prompt)} identified apps"
                )
            elif state.api_intent_relevant_apps:
                # Filter to API apps
                all_apps = await self._base_tool_provider.get_apps()
                apps_for_prompt = [
                    app
                    for app in state.api_intent_relevant_apps
                    if hasattr(app, 'type') and app.type == 'api'
                ]
                apps_for_prompt = _ensure_web_app(apps_for_prompt, all_apps)
                # Get tools only for the identified apps
                tools_for_execution = []
                for app in apps_for_prompt:
                    app_tools = await self._base_tool_provider.get_tools(app.name)
                    app_to_tools_map[app.name] = app_tools
                    tools_for_execution.extend(app_tools)
                logger.info(
                    f"Filtered to {len(tools_for_execution)} tools for {len(apps_for_prompt)} identified apps"
                )
            else:
                # Get all tools and apps
                all_apps = await self._base_tool_provider.get_apps()
                apps_for_prompt = all_apps
                tools_for_execution = all_tools_total or []
                # Build mapping for all apps
                for app in apps_for_prompt:
                    app_tools = await self._base_tool_provider.get_tools(app.name)
                    app_to_tools_map[app.name] = app_tools

            enable_find_tools = total_tool_count > shortlisting_threshold or _web_search_enabled()

            if enable_find_tools:
                logger.info(
                    f"Auto-enabling find_tools: total {total_tool_count} tools (across all apps) exceeds threshold of {shortlisting_threshold}"
                )

            # Prepare prompt
            is_autonomous_subtask = state.sub_task is not None and state.sub_task.strip() != ""

            # TODO: Add task loaded from file support this happens when we load file as playboook
            task_loaded_from_file = False  # Not used in current flow

            # Prepare tools for prompt - if find_tools enabled, only expose find_tools
            tools_for_prompt = tools_for_execution
            if enable_find_tools:
                active_model = configurable.get("llm")
                find_tool = await create_find_tools_tool(
                    all_tools=tools_for_execution,
                    all_apps=apps_for_prompt,
                    app_to_tools_map=app_to_tools_map,
                    llm=active_model,
                    initial_user_message=_first_user_message_text(state.chat_messages),
                )
                tools_for_prompt = [find_tool]
                # Add find_tools to tools context for sandbox execution
                # Wrap to make awaitable (agent always uses await)
                # Prefer coroutine over func to avoid run_in_executor issues
                find_tool_func = (
                    find_tool.coroutine
                    if hasattr(find_tool, 'coroutine') and find_tool.coroutine
                    else find_tool.func
                )
                self._tools_context['find_tools'] = make_tool_awaitable(find_tool_func)
                if lc_bind_tools_meta is not None:
                    lc_bind_tools_meta["_lc_bind_tools_find_tools"] = find_tool
                logger.info(
                    "Exposing only find_tools in prompt (all tools + find_tools available in execution context)"
                )

            if few_shots_enabled:
                if "mcp_few_shot_examples" in configurable:
                    raw_fs = configurable["mcp_few_shot_examples"]
                    if raw_fs is not None:
                        few_shot_examples = normalize_mcp_few_shot_examples(raw_fs)
                    elif enable_find_tools:
                        few_shot_examples = _load_default_find_tools_few_shot_examples()
                    else:
                        few_shot_examples = []
                elif enable_find_tools:
                    few_shot_examples = _load_default_find_tools_few_shot_examples()
                else:
                    few_shot_examples = []
                    logger.debug(
                        "Bundled MCP few-shots (prompts/find_tools_few_shot_examples.json) not loaded: find_tools "
                        "is off "
                        f"(total_tool_count={total_tool_count} <= shortlisting_tool_threshold="
                        f"{shortlisting_threshold}). Lower the threshold via configurable or add apps/tools."
                    )
            else:
                few_shot_examples = []
                logger.debug("MCP few-shots disabled (cuga_lite_enable_few_shots=false)")
            if few_shot_examples:
                logger.debug(f"MCP few-shot examples: {len(few_shot_examples)} turns")

            # Add create_update_todos tool for complex task management if enabled
            if enable_todos:
                todos_tool = await create_update_todos_tool(
                    agent_state=state, todos_store_ref=self._task_todos_ref
                )
                tools_for_prompt.append(todos_tool)
                # Add to tools context for sandbox execution
                # Prefer coroutine over func to avoid run_in_executor issues
                todos_tool_func = (
                    todos_tool.coroutine
                    if hasattr(todos_tool, 'coroutine') and todos_tool.coroutine
                    else todos_tool.func
                )
                self._tools_context['create_update_todos'] = make_tool_awaitable(todos_tool_func)

            # Apply tool guide if guides exist in metadata and haven't been applied yet
            # Guides should apply regardless of whether a playbook matched
            if settings.policy.enabled and state.cuga_lite_metadata:
                # Check if guides exist (either as separate guides list or legacy format)
                has_guides = (
                    state.cuga_lite_metadata.get("guides")
                    or state.cuga_lite_metadata.get("guide_content")
                    or state.cuga_lite_metadata.get("policy_type") == "tool_guide"
                    or state.cuga_lite_metadata.get("has_guides", False)
                )

                if has_guides:
                    tools_for_execution = PolicyEnactment.apply_tool_guide(
                        tools_for_execution, state.cuga_lite_metadata
                    )
                    tools_for_prompt = PolicyEnactment.apply_tool_guide(
                        tools_for_prompt, state.cuga_lite_metadata
                    )
                    # Mark guides as applied to prevent re-application
                    state.cuga_lite_metadata["guides_applied"] = True
                    logger.info("Applied tool guide from policy")
                else:
                    logger.debug("No tool guides found in metadata")

            skill_tools = []
            skills_prompt_section = ""
            skills_enabled = False
            configurable_special = (
                (config or {}).get("configurable", {}).get("special_instructions") if config else None
            )
            effective_special = self._special_instructions or configurable_special or ""
            skills_cfg_on = getattr(settings.skills, "enabled", False)
            cuga_folder_for_skills = os.getenv("CUGA_FOLDER", settings.policy.cuga_folder)
            if skills_cfg_on:
                skill_entries = discover_skills(cuga_folder_for_skills)
                if skill_entries:
                    skill_registry = SkillRegistry(skill_entries)
                    skill_tools = create_skill_tools(skill_registry)
                    tools_for_prompt.extend(skill_tools)
                    skills_prompt_section = format_available_skills_block(skill_registry)
                    skills_enabled = True
                    logger.info(
                        f"Loaded {len(skill_entries)} agent skill(s) from .agents/skills and "
                        f"~/.config/agents/skills with legacy {cuga_folder_for_skills}/skills and "
                        "~/.config/cuga/skills fallbacks"
                    )

            # Resolve thread_id early for per-thread workspace selection.
            _cfg_for_thread = config.get("configurable", {}) if config else {}
            _runtime_thread_id_for_fs = _cfg_for_thread.get("thread_id") or state.thread_id or self._thread_id

            # Update tools context with all execution tools.
            # Wrap to make awaitable (agent always uses await). Filesystem path
            # rewriting is no longer needed here — filesystem tools come from
            # the consolidated runtime class below, not from MCP.
            for tool in tools_for_execution:
                # Extract tool function - StructuredTool may use .func, .coroutine, or ._run
                # IMPORTANT: Prefer coroutine over func to avoid run_in_executor issues
                # with tools that have async implementations (like MCP tools)
                tool_func = None
                if hasattr(tool, 'coroutine') and tool.coroutine:
                    # Prefer async coroutine - avoids run_in_executor timeout issues
                    tool_func = tool.coroutine
                elif hasattr(tool, 'func') and tool.func:
                    tool_func = tool.func
                else:
                    tool_func = getattr(tool, '_run', None)

                if tool_func:
                    self._tools_context[tool.name] = make_tool_awaitable(tool_func)
                else:
                    logger.warning(f"Tool '{tool.name}' has no callable function, skipping")

            for tool in skill_tools:
                tool_func = None
                if hasattr(tool, "coroutine") and tool.coroutine:
                    tool_func = tool.coroutine
                elif hasattr(tool, "func") and tool.func:
                    tool_func = tool.func
                else:
                    tool_func = getattr(tool, "_run", None)
                if tool_func:
                    self._tools_context[tool.name] = make_tool_awaitable(tool_func)
                else:
                    logger.warning(f"Skill tool '{tool.name}' has no callable, skipping")

            # Inject the consolidated filesystem tools + run_command via the
            # shared runtime_tools orchestrator. Backend selection and gating
            # live in cuga_agent_core (behavior-identical to the previous
            # inline block); filesystem and run_command remain independently
            # gated by enable_filesystem_tools / enable_shell_tool.
            _runtime_backends = resolve_runtime_backends(settings, configurable)

            if _runtime_backends.filesystem != "none" or _runtime_backends.shell != "none":
                cfg = config.get("configurable", {}) if config else {}
                runtime_thread_id = (
                    cfg["thread_id"] if "thread_id" in cfg else (state.thread_id or self._thread_id)
                )
            else:
                runtime_thread_id = None

            _runtime_bundle = build_runtime_tools(thread_id=runtime_thread_id, backends=_runtime_backends)
            self._tools_context.update(_runtime_bundle.execution_callables)
            tools_for_prompt.extend(_runtime_bundle.prompt_tools)
            if _runtime_bundle.app_definitions and apps_for_prompt is not None:
                apps_for_prompt = list(apps_for_prompt) + _runtime_bundle.app_definitions

            from cuga.backend.evolve.memory import build_evolve_special_instructions_extension

            special_instructions_final = effective_special or ""
            _split_note = split_execution_note(ExecutionRouter.resolve(settings))
            if _split_note:
                special_instructions_final = (special_instructions_final + "\n\n" + _split_note).strip()
            evolve_extension = await build_evolve_special_instructions_extension(
                state=state,
                configurable=configurable,
                timeout=settings.evolve.timeout,
            )
            if evolve_extension:
                special_instructions_final = (special_instructions_final or "") + evolve_extension

            cfg = config.get("configurable", {}) if config else {}
            _thread_id = cfg.get("thread_id") or ""
            _knowledge_engine = cfg.get("knowledge_engine")
            if _knowledge_engine is None:
                try:
                    from cuga.backend.server.main import app as _app

                    _app_state = getattr(_app.state, "app_state", None)
                    _knowledge_engine = getattr(_app_state, "knowledge_engine", None) if _app_state else None
                except Exception:
                    _knowledge_engine = None

            allowed_knowledge_scopes, default_knowledge_scope = _get_knowledge_tool_scope_context(
                _knowledge_engine,
                _thread_id or None,
            )

            knowledge_tool_names = {
                tool.name
                for tool in tools_for_execution
                if getattr(tool, "name", "").startswith("knowledge_")
            }

            if knowledge_tool_names and not allowed_knowledge_scopes:
                tools_for_execution = [
                    tool
                    for tool in tools_for_execution
                    if getattr(tool, "name", "") not in knowledge_tool_names
                ]
                tools_for_prompt = [
                    tool for tool in tools_for_prompt if getattr(tool, "name", "") not in knowledge_tool_names
                ]
                apps_for_prompt = [
                    app for app in (apps_for_prompt or []) if getattr(app, "name", "") != "knowledge"
                ]
                for tool_name in knowledge_tool_names:
                    self._tools_context.pop(tool_name, None)
            elif knowledge_tool_names:
                if _thread_id:
                    logger.debug("Knowledge tools: thread context available for session scope injection")

                def _wrap_knowledge_tool(fn, tid, allowed_scopes, default_scope):
                    async def _wrapped(*args, **kwargs):
                        scope = kwargs.get("scope")
                        if scope is None and default_scope:
                            kwargs["scope"] = default_scope
                            scope = default_scope
                        if scope is not None and scope not in allowed_scopes:
                            allowed_text = ", ".join(allowed_scopes)
                            return {
                                "error": (
                                    f"Knowledge scope '{scope}' is unavailable in this context. "
                                    f"Allowed scopes: {allowed_text}"
                                )
                            }
                        if tid and "session" in allowed_scopes:
                            kwargs.setdefault("thread_id", tid)
                        return await fn(*args, **kwargs)

                    _wrapped.__doc__ = getattr(fn, "__doc__", None)
                    _wrapped._knowledge_allowed_scopes = allowed_scopes
                    _wrapped._knowledge_default_scope = default_scope
                    _wrapped._knowledge_thread_id = tid
                    return _wrapped

                for tool_name in knowledge_tool_names:
                    original_fn = self._tools_context.get(tool_name)
                    if original_fn:
                        self._tools_context[tool_name] = _wrap_knowledge_tool(
                            original_fn,
                            _thread_id,
                            allowed_knowledge_scopes,
                            default_knowledge_scope,
                        )

                # Note: scope rules are injected once via effective_instructions.
                # No per-tool decoration needed — avoids repeated text in prompt.

            # Inject knowledge base awareness if knowledge tools are available
            effective_instructions = self._instructions
            # Detect knowledge tools — works for both registry (app named
            # "knowledge") and SDK mode (tools under "runtime_tools")
            has_knowledge_tools = any(
                getattr(app, "name", "") == "knowledge" for app in (apps_for_prompt or [])
            )
            if not has_knowledge_tools and tools_for_execution:
                has_knowledge_tools = any(
                    getattr(t, "name", "").startswith("knowledge_") for t in tools_for_execution
                )
            knowledge_scope_instruction = _knowledge_scope_instruction(
                allowed_knowledge_scopes,
                _thread_id or None,
            )
            if knowledge_tool_names:
                effective_instructions = (
                    f"{knowledge_scope_instruction}\n\n{effective_instructions}"
                    if effective_instructions
                    else knowledge_scope_instruction
                )
            if has_knowledge_tools:
                try:
                    from cuga.backend.knowledge.awareness import (
                        get_knowledge_summary,
                        format_knowledge_context,
                        get_engine_from_app_state,
                    )

                    cfg = config.get("configurable", {})
                    engine = cfg.get("knowledge_engine") or get_engine_from_app_state()
                    # Get agent_id: configurable > app_state > fallback
                    agent_id = cfg.get("agent_id")
                    knowledge_config_hash = cfg.get("knowledge_config_hash")
                    if not agent_id:
                        try:
                            from cuga.backend.server.main import app as _app

                            _as = getattr(_app.state, "app_state", None)
                            agent_id = getattr(_as, "agent_id", None) if _as else None
                            if knowledge_config_hash is None:
                                knowledge_config_hash = (
                                    getattr(_as, "knowledge_config_hash", None) if _as else None
                                )
                        except Exception:
                            pass
                    if not agent_id:
                        agent_id = "cuga-default"
                    awareness_thread_id = cfg.get("thread_id")
                    kb_ctx = format_knowledge_context(
                        agent_id,
                        awareness_thread_id,
                        engine=engine,
                        agent_config_hash=knowledge_config_hash,
                    )
                    logger.info(
                        f"Knowledge awareness: agent_id={agent_id}, thread_id={awareness_thread_id}, "
                        f"agent_collection={kb_ctx.get('agent_collection')}, "
                        f"session_collection={kb_ctx.get('session_collection')}"
                    )

                    if not engine:
                        logger.warning("Knowledge awareness skipped: engine not available")
                    else:
                        # Use draft knowledge config for search-time params when running
                        # in draft mode (Try-It-Out). Published agent always uses engine config.
                        _search_cfg = engine._config
                        _is_draft = agent_id and agent_id.endswith("--draft")
                        if _is_draft:
                            try:
                                from cuga.backend.server.main import app as _app

                                _das = getattr(_app.state, "draft_app_state", None)
                                _draft_kc = getattr(_das, "draft_knowledge_config", None) if _das else None
                                if _draft_kc:
                                    _search_cfg = _draft_kc
                            except Exception:
                                pass
                        knowledge_block = await get_knowledge_summary(
                            engine,
                            agent_collection=kb_ctx.get("agent_collection"),
                            session_collection=kb_ctx.get("session_collection"),
                            max_search_attempts=getattr(_search_cfg, "max_search_attempts", None)
                            or getattr(engine._config, "max_search_attempts", None),
                            default_limit=getattr(_search_cfg, "default_limit", None)
                            or getattr(engine._config, "default_limit", None),
                            rag_profile=getattr(_search_cfg, "rag_profile", None)
                            or getattr(engine._config, "rag_profile", "standard"),
                        )
                        if knowledge_block:
                            # Load knowledge search instructions from dedicated file
                            knowledge_instructions_text = ""
                            try:
                                kb_instructions_path = (
                                    Path(__file__).parents[4]
                                    / "configurations"
                                    / "knowledge"
                                    / "knowledge_instructions.md"
                                )
                                if kb_instructions_path.exists():
                                    knowledge_instructions_text = kb_instructions_path.read_text(
                                        encoding="utf-8"
                                    ).strip()
                            except Exception as ki_err:
                                logger.debug(f"Failed to load knowledge instructions: {ki_err}")

                            # Prepend knowledge block BEFORE other instructions
                            # so the LLM sees it early and acts on it
                            effective_instructions = (
                                f"{knowledge_block}\n\n{knowledge_instructions_text}\n\n{effective_instructions}"
                                if effective_instructions
                                else f"{knowledge_block}\n\n{knowledge_instructions_text}"
                            )
                            logger.info(f"Knowledge awareness injected: {len(knowledge_block)} chars")
                except Exception as e:
                    logger.debug(f"Knowledge awareness injection skipped: {e}")
            if lc_bind_tools_meta is not None:
                lc_bind_tools_meta["_lc_bind_tools_overlay_structured_tools"] = [
                    t for t in (tools_for_prompt or []) if getattr(t, "name", None)
                ]

            # Create prompt dynamically
            dynamic_prompt = self._static_prompt

            if not dynamic_prompt:
                dynamic_prompt = create_mcp_prompt(
                    tools_for_prompt,
                    allow_user_clarification=True,
                    return_to_user_cases=None,
                    instructions=effective_instructions,
                    apps=apps_for_prompt,
                    task_loaded_from_file=task_loaded_from_file,
                    is_autonomous_subtask=settings.advanced_features.force_autonomous_mode
                    or is_autonomous_subtask,
                    prompt_template=self._prompt_template,
                    enable_find_tools=enable_find_tools,
                    enable_todos=enable_todos,
                    special_instructions=special_instructions_final,
                    skills_enabled=skills_enabled,
                    skills_prompt_section=skills_prompt_section,
                    enable_shell_tool=getattr(settings.advanced_features, "enable_shell_tool", False),
                    has_knowledge=has_knowledge_tools,
                    few_shot_examples=few_shot_examples,
                    few_shots_enabled=few_shots_enabled,
                )
                logger.info(
                    "Prepared CugaLite prompt: enable_find_tools={} few_shot_message_turns={} "
                    "few_shots_as_messages={} prompt_chars={}",
                    enable_find_tools,
                    len(few_shot_examples),
                    bool(few_shot_examples),
                    len(dynamic_prompt),
                )
            else:
                logger.info(
                    "Using static CugaLite prompt; dynamic few-shot injection skipped "
                    "(enable_find_tools={} few_shot_turns={})",
                    enable_find_tools,
                    len(few_shot_examples),
                )

            reflection_apps_snapshot = format_apps_for_prompt(apps_for_prompt or [])

            return Command(
                goto="call_model",
                update={
                    "tools_prepared": True,
                    "prepared_prompt": dynamic_prompt,
                    "step_count": 0,
                    "cuga_lite_metadata": state.cuga_lite_metadata,
                    "reflection_apps": reflection_apps_snapshot,
                    "reflection_enable_find_tools": enable_find_tools,
                    "reflection_skills_enabled": skills_enabled,
                    "reflection_skills_prompt_section": skills_prompt_section,
                    "mcp_few_shot_messages": few_shot_examples,
                },
            )

        return prepare_tools_and_apps

    def build_sandbox_node(self, base_thread_id: Any, base_apps_list: Any):
        """Return the sandbox async node."""

        async def sandbox(state: Any, config: Optional[RunnableConfig] = None):
            """Execute code in sandbox and return results."""
            from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

            # Check if user denied approval (only if policies are enabled)
            if settings.policy.enabled:
                denial_command = ToolApprovalHandler.handle_denial(self, state)
                if denial_command:
                    return denial_command

            configurable = config.get("configurable", {}) if config else {}
            max_steps = (
                configurable.get("cuga_lite_max_steps") if "cuga_lite_max_steps" in configurable else None
            )
            if "thread_id" in configurable:
                current_thread_id = configurable["thread_id"]
            else:
                current_thread_id = state.thread_id or base_thread_id
            current_apps_list = configurable.get("apps_list", base_apps_list)
            track_tool_calls = configurable.get("track_tool_calls", False)
            reflection_enabled = (
                configurable.get("reflection_enabled")
                if "reflection_enabled" in configurable
                else settings.advanced_features.reflection_enabled
            )

            # Get existing variables using CugaLiteState's own variables_manager
            existing_vars = {}
            for var_name in list(state.variables_manager.get_variable_names()):
                var_value = state.variables_manager.get_variable(var_name)
                if is_find_tools_listing_markdown(var_value):
                    state.variables_manager.remove_variable(var_name)
                    continue
                existing_vars[var_name] = var_value

            # Add tools to context
            context = {**existing_vars, **self._tools_context}

            # Start tool call tracking (only if enabled via invoke parameter)
            ToolCallTracker.start_tracking(enabled=track_tool_calls)

            try:
                # Execute the script - pass the CugaLiteState itself since it has variables_manager
                _exec_plan = ExecutionRouter.resolve(settings)
                if _exec_plan.split_execution_active:
                    logger.info(
                        "Split execution: python=%s shell=%s fs=%s",
                        _exec_plan.python_backend,
                        _exec_plan.shell_backend,
                        _exec_plan.filesystem_backend,
                    )
                output, new_vars = await CodeExecutor.eval_with_tools_async(
                    code=state.script,
                    _locals=context,
                    state=state,  # Pass CugaLiteState - it has variables_manager property
                    thread_id=current_thread_id,
                    apps_list=current_apps_list,
                    plan=_exec_plan,
                )

                self._tracker.collect_step(step=Step(name="User_output", data=output))
                self._tracker.collect_step(
                    step=Step(
                        name="User_output_variables",
                        data=json.dumps(
                            new_vars,
                            default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o),
                        ),
                    )
                )

                # Output is already formatted and trimmed by code_executor
                logger.debug(f"\n\n------\n\n📝 Execution output:\n\n{output}\n\n------\n\n")

                # Update variables using CugaLiteState's variables_manager
                # This automatically updates state.variables_storage
                for name, value in new_vars.items():
                    if is_find_tools_listing_markdown(value):
                        continue
                    state.variables_manager.add_variable(
                        value, name=name, description="Created during code execution"
                    )

                reflection_output = ""
                if reflection_enabled:
                    try:
                        active_model = configurable.get("llm") or _llm_manager.get_model(
                            settings.agent.planner.model
                        )
                        reflection_agent = reflection_task(llm=active_model)
                        # Format chat messages as history string
                        agent_history_parts = []
                        for msg in state.chat_messages:
                            if isinstance(msg, HumanMessage):
                                agent_history_parts.append(f"User: {msg.content}")
                            elif isinstance(msg, AIMessage):
                                agent_history_parts.append(f"Assistant: {msg.content}")
                            else:
                                agent_history_parts.append(
                                    f"{type(msg).__name__}: {getattr(msg, 'content', str(msg))}"
                                )
                        agent_history = (
                            "\n".join(agent_history_parts)
                            if agent_history_parts
                            else "No previous conversation history"
                        )
                        reflection_result = await reflection_agent.ainvoke(
                            {
                                "instructions": "",
                                "current_task": _reflection_current_task(state) or "(no task text)",
                                "agent_history": agent_history,
                                "coder_agent_output": output,
                                "apps": state.reflection_apps or [],
                                "enable_find_tools": state.reflection_enable_find_tools,
                                "skills_enabled": state.reflection_skills_enabled,
                                "skills_prompt_section": state.reflection_skills_prompt_section,
                                "force_autonomous_mode": settings.advanced_features.force_autonomous_mode,
                            }
                        )
                        reflection_output = reflection_result.content
                        logger.debug(f"Reflection output:\n{reflection_output}")
                    except Exception as e:
                        logger.warning(f"Reflection failed: {e}")
                        reflection_output = ""

                # Output is already formatted by code_executor
                execution_message_content = execution_output_text(output)
                if reflection_output:
                    execution_message_content = (
                        f"{execution_message_content}\n\n---\n\nSummary:\n{reflection_output}"
                    )

                self._tracker.collect_step(
                    step=Step(
                        name="User_return",
                        data=execution_message_content,
                    )
                )

                new_message = HumanMessage(content=execution_message_content)
                updated_messages, error_message = _core_append_with_step_limit(
                    self, state, [new_message], max_steps
                )

                # Collect tool calls from this execution
                execution_tool_calls = ToolCallTracker.stop_tracking()
                accumulated_tool_calls = (state.tool_calls or []) + execution_tool_calls

                if error_message:
                    return _core_create_error_command(
                        self,
                        updated_messages,
                        error_message,
                        state.step_count,
                        additional_updates={
                            "variables_storage": state.variables_storage,
                            "variable_counter_state": state.variable_counter_state,
                            "variable_creation_order": state.variable_creation_order,
                            "tool_calls": accumulated_tool_calls,
                        },
                    )

                todo_state_update = extract_task_todos_from_new_vars(new_vars)
                base_update = {
                    "chat_messages": updated_messages,
                    "variables_storage": state.variables_storage,
                    "variable_counter_state": state.variable_counter_state,
                    "variable_creation_order": state.variable_creation_order,
                    "step_count": state.step_count + 1,
                    "tool_calls": accumulated_tool_calls,
                }
                if todo_state_update is not None:
                    base_update["task_todos"] = todo_state_update
                return base_update
            except Exception as e:
                # Collect tool calls even on error
                execution_tool_calls = ToolCallTracker.stop_tracking()
                accumulated_tool_calls = (state.tool_calls or []) + execution_tool_calls

                error_msg = f"Error during execution: {str(e)}"
                logger.error(error_msg)
                new_message = HumanMessage(content=error_msg)
                updated_messages, limit_error_message = _core_append_with_step_limit(
                    self, state, [new_message], max_steps
                )

                if limit_error_message:
                    return _core_create_error_command(
                        self, updated_messages, limit_error_message, state.step_count
                    )

                return {
                    "chat_messages": updated_messages,
                    "error": error_msg,
                    "execution_complete": True,
                    "step_count": state.step_count + 1,
                    "tool_calls": accumulated_tool_calls,
                }

        return sandbox
