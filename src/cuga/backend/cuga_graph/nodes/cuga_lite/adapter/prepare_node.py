"""Prepare node for the CugaLite agent graph."""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.arg_warning import make_arg_warning_callable
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import create_update_todos_tool
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import (
    ExecutionRouter,
    split_execution_note,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.nodes.cuga_agent_core.tools.runtime_tools import (
    build_runtime_tools,
    resolve_runtime_backends,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import (
    _ensure_web_app,
    _first_user_message_text,
    _load_default_find_tools_few_shot_examples,
    _web_search_enabled,
    create_find_tools_tool,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.knowledge import (
    _get_knowledge_tool_scope_context,
    _knowledge_scope_instruction,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import resolved_runtime_model_name
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    create_mcp_prompt,
    format_apps_for_prompt,
    normalize_mcp_few_shot_examples,
    resolve_cuga_lite_few_shots_enabled,
)
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task import TaskAnalyzer
from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
from cuga.backend.skills import (
    SkillRegistry,
    create_skill_tools,
    discover_skills,
    format_available_skills_block,
)
from cuga.config import settings


def create_prepare_tools_and_apps_node(adapter: Any, lc_bind_tools_meta: dict) -> Callable:
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
        # Reset cross-task state at the start of a fresh conversation.
        # adapter._task_todos_ref is closure-scoped per compiled graph and
        # outlives a single .invoke(); state.task_todos can also persist via a
        # thread-keyed checkpointer. We detect "fresh conversation" by message
        # count (<=1 means just the user's initial message).
        # The closure ref is cleared in place; state.task_todos is propagated
        # via every Command.update returned below because in-place state
        # mutation does not persist through LangGraph.
        # Note: this does not cover "new task as a follow-up turn on the same
        # thread" (chat_messages > 1 with a topic change) — tracked separately.
        is_fresh_conversation = len(state.chat_messages or []) <= 1
        if is_fresh_conversation:
            adapter._task_todos_ref.clear()
            state.task_todos = None

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
            graph_default_model=adapter._model,
        )
        few_shots_enabled = resolve_cuga_lite_few_shots_enabled(
            configurable,
            model_name=_runtime_model_name,
        )
        logger.debug(
            f"[APPROVAL DEBUG] prepare_tools_and_apps received cuga_lite_metadata: {state.cuga_lite_metadata}"
        )

        # Skip policy checking if policies are disabled or if we're returning from approval
        if settings.policy.enabled and not ToolApprovalHandler.should_skip_policy_check(adapter, state):
            # Check for policies and enact if matched
            # Include IntentGuard, Playbook, and ToolGuide for intent checks
            from cuga.backend.cuga_graph.policy.models import PolicyType

            command, metadata = await PolicyEnactment.check_and_enact(
                state,
                config,
                policy_types=[PolicyType.INTENT_GUARD, PolicyType.PLAYBOOK, PolicyType.TOOL_GUIDE],
                adapter=adapter,
            )

            # If policy returned a command (e.g., BLOCK_INTENT), execute it immediately
            if command:
                # Propagate the task_todos reset through the policy-returned
                # Command so LangGraph persists it; the in-place state mutation
                # above does not survive the node return.
                if is_fresh_conversation and isinstance(getattr(command, "update", None), dict):
                    command.update.setdefault("task_todos", None)
                return command

            # If policy returned metadata (e.g., playbook guidance), store it
            if metadata:
                adapter.set_metadata(state, metadata)
        elif not settings.policy.enabled:
            logger.debug("Policy system disabled - skipping policy checks")
        else:
            logger.info("[APPROVAL DEBUG] Skipping policy check - user has already approved")

        if not adapter._base_tool_provider:
            raise ValueError("tool_provider is required")

        # Get total tool count across ALL apps (for shortlisting threshold - not per app)
        all_tools_total = await adapter._base_tool_provider.get_all_tools()
        total_tool_count = len(all_tools_total) if all_tools_total else 0

        # Get tools from provider
        apps_for_prompt = None
        app_to_tools_map = {}

        # Get apps from state and filter tools if specific app is selected
        if state.sub_task_app:
            # Specific app selected - filter tools to only this app
            all_apps = await adapter._base_tool_provider.get_apps()
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
                current_tools_for_execution = await adapter._base_tool_provider.get_tools(app.name)
                app_to_tools_map[app.name] = current_tools_for_execution
                tools_for_execution.extend(current_tools_for_execution)

            logger.info(
                f"Filtered to {len(tools_for_execution)} tools for {len(apps_for_prompt)} identified apps"
            )
        elif state.api_intent_relevant_apps:
            # Filter to API apps
            all_apps = await adapter._base_tool_provider.get_apps()
            apps_for_prompt = [
                app for app in state.api_intent_relevant_apps if hasattr(app, 'type') and app.type == 'api'
            ]
            apps_for_prompt = _ensure_web_app(apps_for_prompt, all_apps)
            # Get tools only for the identified apps
            tools_for_execution = []
            for app in apps_for_prompt:
                app_tools = await adapter._base_tool_provider.get_tools(app.name)
                app_to_tools_map[app.name] = app_tools
                tools_for_execution.extend(app_tools)
            logger.info(
                f"Filtered to {len(tools_for_execution)} tools for {len(apps_for_prompt)} identified apps"
            )
        else:
            # Get all tools and apps
            all_apps = await adapter._base_tool_provider.get_apps()
            apps_for_prompt = all_apps
            tools_for_execution = all_tools_total or []
            # Build mapping for all apps
            for app in apps_for_prompt:
                app_tools = await adapter._base_tool_provider.get_tools(app.name)
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
            adapter._tools_context['find_tools'] = make_tool_awaitable(find_tool_func)
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
                agent_state=state, todos_store_ref=adapter._task_todos_ref
            )
            tools_for_prompt.append(todos_tool)
            # Add to tools context for sandbox execution
            # Prefer coroutine over func to avoid run_in_executor issues
            todos_tool_func = (
                todos_tool.coroutine
                if hasattr(todos_tool, 'coroutine') and todos_tool.coroutine
                else todos_tool.func
            )
            adapter._tools_context['create_update_todos'] = make_tool_awaitable(todos_tool_func)

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
        _cfg = config.get("configurable", {}) if config else {}
        configurable_special = _cfg.get("special_instructions")
        effective_special = "\n\n".join(
            part for part in (adapter._special_instructions, configurable_special) if part
        )
        _cfg_skills = _cfg.get("skills_enabled")
        skills_cfg_on = _cfg_skills if _cfg_skills is not None else getattr(settings.skills, "enabled", False)
        cuga_folder_for_skills = _cfg.get("skills_folder") or os.getenv(
            "CUGA_FOLDER", settings.policy.cuga_folder
        )
        if skills_cfg_on:
            skill_entries = discover_skills(cuga_folder_for_skills)
            if skill_entries:
                skill_registry = SkillRegistry(skill_entries)
                skill_tools = create_skill_tools(skill_registry)
                tools_for_prompt.extend(skill_tools)
                skills_prompt_section = format_available_skills_block(skill_registry)
                skills_enabled = True

        # Resolve thread_id early for per-thread workspace selection.
        _runtime_thread_id_for_fs = _cfg.get("thread_id") or state.thread_id or adapter._thread_id

        # Update tools context with all execution tools.
        # Wrap to make awaitable (agent always uses await). Filesystem path
        # rewriting is no longer needed here — filesystem tools come from
        # the consolidated runtime class below, not from MCP.
        # Pre-flight arg WARNING: the sandbox calls tool.coroutine directly,
        # bypassing the StructuredTool's args_schema, so nothing flags malformed
        # kwargs before the registry. This detector logs (never mutates) suspect
        # shapes — the dict-as-string bug and friends. See arg_warning.py.
        _af = getattr(settings, "advanced_features", None)
        _warn_args = bool(getattr(_af, "cuga_lite_warn_suspect_args", True))

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
                tool_func = make_arg_warning_callable(
                    tool_func,
                    getattr(tool, "args_schema", None),
                    enable=_warn_args,
                )
                adapter._tools_context[tool.name] = make_tool_awaitable(tool_func)
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
                adapter._tools_context[tool.name] = make_tool_awaitable(tool_func)
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
                cfg["thread_id"] if "thread_id" in cfg else (state.thread_id or adapter._thread_id)
            )
        else:
            runtime_thread_id = None

        _runtime_bundle = build_runtime_tools(thread_id=runtime_thread_id, backends=_runtime_backends)
        adapter._tools_context.update(_runtime_bundle.execution_callables)
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
            tool.name for tool in tools_for_execution if getattr(tool, "name", "").startswith("knowledge_")
        }

        if knowledge_tool_names and not allowed_knowledge_scopes:
            tools_for_execution = [
                tool for tool in tools_for_execution if getattr(tool, "name", "") not in knowledge_tool_names
            ]
            tools_for_prompt = [
                tool for tool in tools_for_prompt if getattr(tool, "name", "") not in knowledge_tool_names
            ]
            apps_for_prompt = [
                app for app in (apps_for_prompt or []) if getattr(app, "name", "") != "knowledge"
            ]
            for tool_name in knowledge_tool_names:
                adapter._tools_context.pop(tool_name, None)
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
                original_fn = adapter._tools_context.get(tool_name)
                if original_fn:
                    adapter._tools_context[tool_name] = _wrap_knowledge_tool(
                        original_fn,
                        _thread_id,
                        allowed_knowledge_scopes,
                        default_knowledge_scope,
                    )

            # Note: scope rules are injected once via effective_instructions.
            # No per-tool decoration needed — avoids repeated text in prompt.

        # Inject knowledge base awareness if knowledge tools are available
        effective_instructions = adapter._instructions
        upload_context = _cfg.get("upload_context")
        if upload_context:
            effective_instructions = (
                f"{upload_context}\n\n{effective_instructions}" if effective_instructions else upload_context
            )
        # Detect knowledge tools — works for both registry (app named
        # "knowledge") and SDK mode (tools under "runtime_tools")
        has_knowledge_tools = any(getattr(app, "name", "") == "knowledge" for app in (apps_for_prompt or []))
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
                    assemble_system_prompt_section,
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

                # Use draft knowledge config for search-time params when
                # running in draft mode (Try-It-Out). Published agent always
                # uses engine config. Prefer the per-agent
                # ``draft_knowledge_configs`` dict (multi-tenant safe), fall
                # back to the legacy singular attribute.
                _search_cfg = None
                if engine is not None:
                    _search_cfg = engine._config
                    if agent_id and agent_id.endswith("--draft"):
                        try:
                            from cuga.backend.server.main import app as _app

                            _das = getattr(_app.state, "draft_app_state", None)
                            _draft_kc = None
                            if _das is not None:
                                _base_aid = agent_id[: -len("--draft")]
                                _cfgs = getattr(_das, "draft_knowledge_configs", None)
                                if isinstance(_cfgs, dict):
                                    _draft_kc = _cfgs.get(_base_aid)
                                if _draft_kc is None:
                                    _draft_kc = getattr(_das, "draft_knowledge_config", None)
                            if _draft_kc:
                                _search_cfg = _draft_kc
                        except Exception:
                            pass

                # Single seam — assemble_system_prompt_section subsumes the
                # previous format_knowledge_context + get_knowledge_summary
                # + knowledge_instructions.md compose dance. Returns a
                # dataclass with composed text + audit prompt_hash.
                # Closes review comment 24 ("cuga_lite is the lasting path").
                assembled = await assemble_system_prompt_section(
                    engine,
                    agent_id,
                    awareness_thread_id,
                    base_instructions=effective_instructions,
                    agent_config_hash=knowledge_config_hash,
                    search_config=_search_cfg,
                )
                logger.info(
                    "Knowledge awareness: agent_id=%s, thread_id=%s, "
                    "has_knowledge=%s, knowledge_block=%d chars, contract=%d chars, "
                    "prompt_hash=%s",
                    agent_id,
                    awareness_thread_id,
                    assembled.has_knowledge,
                    assembled.knowledge_block_chars,
                    assembled.contract_chars,
                    assembled.prompt_hash,
                )
                if assembled.has_knowledge:
                    effective_instructions = assembled.text
            except Exception as e:
                logger.debug(f"Knowledge awareness injection skipped: {e}")
        if lc_bind_tools_meta is not None:
            lc_bind_tools_meta["_lc_bind_tools_overlay_structured_tools"] = [
                t for t in (tools_for_prompt or []) if getattr(t, "name", None)
            ]

        # Create prompt dynamically
        dynamic_prompt = adapter._static_prompt

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
                prompt_template=adapter._prompt_template,
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

        update_payload: dict[str, Any] = {
            "tools_prepared": True,
            "prepared_prompt": dynamic_prompt,
            "step_count": 0,
            "cuga_lite_metadata": state.cuga_lite_metadata,
            "reflection_apps": reflection_apps_snapshot,
            "reflection_enable_find_tools": enable_find_tools,
            "reflection_skills_enabled": skills_enabled,
            "reflection_skills_prompt_section": skills_prompt_section,
            "mcp_few_shot_messages": few_shot_examples,
        }
        if is_fresh_conversation:
            # Carry the task_todos reset through LangGraph state so the
            # state.task_todos fallback path in prepare_system_content sees an
            # empty value on the next turn.
            update_payload["task_todos"] = None

        return Command(goto="call_model", update=update_payload)

    return prepare_tools_and_apps
