"""
CugaLite LangGraph - Unified subgraph combining CugaAgent and CodeAct

TODO: Multi-user, multi-model, multi-tools dependency injection refactoring
----------------------------------------------------------------------------
CURRENT STATE: Supports multi-user but with same configuration (shared model, tools, memory backend)
GOAL: Enable per-user configuration with isolated models, tools, and memory

This class needs architectural changes to support multi-user, multi-model, and multi-tools scenarios:

1. Multi-Tools Client Dependency Injection:
   - Replace direct tool_provider parameter with injectable tools_client per user_id
   - Use LangGraph's configurable pattern to inject tools_client at runtime
   - Store tools_client in graph config/state rather than closure
   - Enable per-user tool access control and isolation
   - Support dynamic tool loading/unloading per user session
   - Allow different tool sets per user based on permissions/subscriptions

2. Multi-Model Support:
   - Replace hardcoded model parameter with injectable model per user/session
   - Support per-user model selection (different LLMs for different users)
   - Enable model switching mid-conversation via configurable
   - Inject llm_manager or model_client rather than direct model instance
   - Support model-specific configurations (temperature, max_tokens, etc.) per user

3. Multi-User Memory/Storage:
   - Implement user_id-scoped memory storage (variables_storage, chat_messages)
   - Use injectable memory backend (e.g., per-user checkpointer)
   - Migrate from global state to user-scoped state partitioning
   - Consider LangGraph's built-in checkpointing with user_id as partition key
   - Isolate conversation history and variables per user

4. LangGraph Configurable Dependencies:
   - Leverage config["configurable"] for runtime dependency injection
   - Pass user_id, tools_client, model_client, memory_backend via configurable
   - Remove hardcoded global instances (tracker, llm_manager)
   - Make all external dependencies (LLM, tools, memory) injectable
   - Support per-request configuration overrides

5. State Isolation:
   - Ensure CugaLiteState is scoped per user session
   - Add user_id field to state for tracking and isolation
   - Implement proper state cleanup between user sessions
   - Consider multi-tenancy patterns for shared resources
   - Track model_id and tools_version in state for debugging

6. Thread Safety:
   - Ensure thread-safe access to user-scoped resources
   - Avoid shared mutable state between users
   - Use proper async/await patterns for concurrent user requests
   - Handle concurrent model/tool requests from different users
"""

import os
import re
import json
import asyncio
import inspect
from typing import Any, Optional, Sequence, Dict, List, Tuple, Set
from loguru import logger
from pydantic import BaseModel, Field


from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task import TaskAnalyzer
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.errors import extract_code_from_tool_use_failed
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    create_mcp_prompt,
    format_apps_for_prompt,
    normalize_mcp_few_shot_examples,
    PromptUtils,
    resolve_cuga_lite_few_shots_enabled,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import (
    CodeExecutor,
    is_find_tools_listing_markdown,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import (
    resolved_runtime_model_name,
    resolve_bind_tools_fields,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    classify_nl_auto_continue,
    normalize_assistant_text,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
from cuga.backend.cuga_graph.utils.context_management_utils import apply_context_summarization
from cuga.config import settings
from cuga.configurations.instructions_manager import get_all_instructions_formatted
from cuga.backend.llm.utils.helpers import load_one_prompt
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.reflection import reflection_task
from pathlib import Path

from cuga.backend.skills import (
    SkillRegistry,
    create_skill_tools,
    discover_skills,
    format_available_skills_block,
)


try:
    from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
except ImportError:
    try:
        from langfuse.callback.langchain import LangchainCallbackHandler as LangfuseCallbackHandler
    except ImportError:
        LangfuseCallbackHandler = None


tracker = ActivityTracker()
llm_manager = LLMManager()

BACKTICK_PATTERN = r'```python(.*?)```'


def _tool_call_kwarg_literal(value: Any) -> str:
    """Python expression for values reconstructed from JSON tool-call arguments."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def _extract_code_from_response_tool_calls(response: object) -> str | None:
    """Recover fenced Python from AIMessage.tool_calls when content is empty (proxy/native FC)."""
    tool_calls: list | None = getattr(response, "tool_calls", None)
    if not tool_calls:
        tool_calls = (getattr(response, "additional_kwargs", {}) or {}).get("tool_calls")
    if not tool_calls:
        return None

    tc = tool_calls[0]
    if not isinstance(tc, dict):
        return None

    name: str | None = tc.get("name") or (tc.get("function") or {}).get("name")
    args: dict | str = tc.get("args") or (tc.get("function") or {}).get("arguments") or {}
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
    logger.debug("Recovered tool call '%s' from tool_calls field", name)
    return f"```python\nresult = await {name}({args_str})\nprint(result)\n```"


def _bind_tools_mode_from_settings() -> str:
    try:
        m = getattr(settings.advanced_features, "cuga_lite_bind_tools_mode", None)
        if m is not None and str(m).strip():
            return str(m).strip().lower()
    except Exception:
        pass
    return "none"


def _bind_tools_apps_from_settings():
    try:
        raw = getattr(settings.advanced_features, "cuga_lite_bind_tools_apps", None)
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw.strip()] if raw.strip() else []
        if isinstance(raw, (list, tuple)):
            return [str(x).strip() for x in raw if str(x).strip()]
    except Exception:
        pass
    return []


def _bind_tools_tool_names_from_settings():
    try:
        raw = getattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", None)
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw.strip()] if raw.strip() else []
        if isinstance(raw, (list, tuple)):
            return [str(x).strip() for x in raw if str(x).strip()]
    except Exception:
        pass
    return []


def _bind_include_find_tools_from_config(cfg: Dict[str, Any]) -> bool:
    v = cfg.get("cuga_lite_bind_tools_include_find_tools")
    if v is None:
        try:
            v = getattr(settings.advanced_features, "cuga_lite_bind_tools_include_find_tools", False)
        except Exception:
            v = False
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def _merge_find_tools_into_bound(
    bound: List[StructuredTool],
    seen: Set[str],
    *,
    include_find_tools: bool,
    tools_context_ref: Optional[Dict[str, Any]],
) -> None:
    if not include_find_tools:
        return
    ft = (tools_context_ref or {}).get("_lc_bind_tools_find_tools")
    if not ft:
        return
    name = getattr(ft, "name", None) or ""
    if name and name not in seen:
        seen.add(name)
        bound.append(ft)


async def _indexed_provider_tools_first_wins(
    tool_provider: ToolProviderInterface,
) -> Dict[str, StructuredTool]:
    """Map tool name → StructuredTool using provider.get_all_tools (first occurrence wins)."""
    try:
        all_tools = await tool_provider.get_all_tools()
    except Exception as e:
        logger.warning("bind_tools: get_all_tools failed: %s", e)
        return {}
    by_name: Dict[str, StructuredTool] = {}
    duplicates: Set[str] = set()
    for t in all_tools or []:
        n = getattr(t, "name", None) or ""
        if not n:
            continue
        if n in by_name:
            duplicates.add(n)
            continue
        by_name[n] = t
    if duplicates:
        logger.debug(
            "bind_tools: duplicate tool names from provider (using first): %s",
            sorted(duplicates),
        )
    return by_name


async def _indexed_tools_for_native_bind(
    tool_provider: ToolProviderInterface,
    tools_context_ref: Optional[Dict[str, Any]],
) -> Dict[str, StructuredTool]:
    """Registry MCP tools plus in-graph overlays (skills, OpenSandbox shell, todos, find_tools).

    ``run_command`` / ``write_file`` / etc. are not registered with ToolRegistryProvider; prepare
    copies them onto ``tools_for_prompt`` only. Overlay must merge so ``cuga_lite_bind_tools_tool_names``
    can bind them by name.
    """
    by_name = await _indexed_provider_tools_first_wins(tool_provider)
    overlay = (tools_context_ref or {}).get("_lc_bind_tools_overlay_structured_tools") or []
    if not overlay:
        return by_name
    for t in overlay:
        n = getattr(t, "name", None) or ""
        if not n:
            continue
        by_name[n] = t
    return by_name


async def resolve_model_with_bind_tools(
    active_model: BaseChatModel,
    *,
    configurable: Optional[Dict[str, Any]],
    tools_context_ref: Optional[Dict[str, Any]],
    tool_provider: Optional[ToolProviderInterface],
    model_name: Optional[str] = None,
) -> BaseChatModel:
    """Optionally wrap ``active_model`` with ``bind_tools`` for native tool-calling tests.

    LangGraph ``config['configurable']`` overrides per-model runtime profile overrides TOML:

    - ``cuga_lite_bind_tools_mode``: ``none`` | ``find_tools`` | ``all`` | ``apps`` | ``tools`` | ``apps_and_tools``
    - ``cuga_lite_bind_tools_apps``: list of app names (``mode=apps`` or ``apps_and_tools``)
    - ``cuga_lite_bind_tools_tool_names``: StructuredTool ``name`` values (``mode=tools`` or ``apps_and_tools``)
    - ``cuga_lite_bind_tools_include_find_tools``: merge ``find_tools`` into ``all`` / ``apps`` / ``tools`` / ``apps_and_tools``

    Profile ``gpt-oss-20b``: see ``model_runtime_profile.GPT_OSS_20B_RUNTIME_DEFAULTS``.
    """
    cfg = configurable or {}
    mn = (model_name or "").strip()
    if not mn:
        mn = resolved_runtime_model_name(
            configurable_llm=cfg.get("llm"),
            graph_default_model=active_model,
        )
    mode, app_names, tool_names, include_find_tools = resolve_bind_tools_fields(
        configurable,
        mn,
        settings_mode_fn=_bind_tools_mode_from_settings,
        settings_apps_fn=_bind_tools_apps_from_settings,
        settings_tool_names_fn=_bind_tools_tool_names_from_settings,
        settings_include_fn=lambda: _bind_include_find_tools_from_config({}),
    )

    if mode in ("", "none", "false", "0", "off"):
        if include_find_tools:
            ft_only = (tools_context_ref or {}).get("_lc_bind_tools_find_tools")
            if ft_only:
                return active_model.bind_tools([ft_only])
        return active_model

    try:
        if mode == "find_tools":
            ft = (tools_context_ref or {}).get("_lc_bind_tools_find_tools")
            if ft:
                return active_model.bind_tools([ft])
            logger.debug(
                "cuga_lite_bind_tools_mode=find_tools but find_tools StructuredTool is missing "
                "(shortlisting may be off)"
            )
            return active_model

        if mode == "all":
            if not tool_provider:
                logger.warning("cuga_lite_bind_tools_mode=all but tool_provider is missing")
                return active_model
            by_name = await _indexed_tools_for_native_bind(tool_provider, tools_context_ref)
            bound = list(by_name.values())
            seen: Set[str] = {n for n in by_name}
            _merge_find_tools_into_bound(
                bound, seen, include_find_tools=include_find_tools, tools_context_ref=tools_context_ref
            )
            if not bound:
                return active_model
            return active_model.bind_tools(bound)

        if mode == "apps_and_tools":
            if not tool_provider:
                logger.warning("cuga_lite_bind_tools_mode=apps_and_tools but tool_provider is missing")
                return active_model
            if not app_names and not tool_names:
                if include_find_tools:
                    ft = (tools_context_ref or {}).get("_lc_bind_tools_find_tools")
                    if ft:
                        return active_model.bind_tools([ft])
                logger.warning(
                    "cuga_lite_bind_tools_mode=apps_and_tools but cuga_lite_bind_tools_apps and "
                    "cuga_lite_bind_tools_tool_names are both empty "
                    "(set include_find_tools to bind find_tools only)"
                )
                return active_model

            bound: List[StructuredTool] = []
            seen_names: Set[str] = set()
            for app_name in app_names:
                try:
                    for t in await tool_provider.get_tools(app_name):
                        name = getattr(t, "name", None) or ""
                        if name and name not in seen_names:
                            seen_names.add(name)
                            bound.append(t)
                except Exception as e:
                    logger.warning("bind_tools apps_and_tools: get_tools(%s) failed: %s", app_name, e)

            by_name_lookup: Dict[str, StructuredTool] = {}
            if tool_names:
                by_name_lookup = await _indexed_tools_for_native_bind(tool_provider, tools_context_ref)

            missing: List[str] = []
            if tool_names:
                for tn in tool_names:
                    if tn in seen_names:
                        continue
                    t = by_name_lookup.get(tn)
                    if t is None:
                        missing.append(tn)
                        continue
                    seen_names.add(tn)
                    bound.append(t)
                if missing:
                    logger.warning(
                        "cuga_lite_bind_tools_tool_names not found among provider tools (skipped): %s",
                        missing,
                    )

            _merge_find_tools_into_bound(
                bound, seen_names, include_find_tools=include_find_tools, tools_context_ref=tools_context_ref
            )
            if not bound:
                return active_model
            return active_model.bind_tools(bound)

        if mode == "apps":
            if not app_names:
                if include_find_tools:
                    ft = (tools_context_ref or {}).get("_lc_bind_tools_find_tools")
                    if ft:
                        return active_model.bind_tools([ft])
                logger.warning(
                    "cuga_lite_bind_tools_mode=apps but cuga_lite_bind_tools_apps is empty "
                    "(set include_find_tools to bind find_tools only)"
                )
                return active_model
            if not tool_provider:
                logger.warning("cuga_lite_bind_tools_mode=apps but tool_provider is missing")
                return active_model

            bound = []
            seen: Set[str] = set()
            for app_name in app_names:
                try:
                    for t in await tool_provider.get_tools(app_name):
                        name = getattr(t, "name", None) or ""
                        if name and name not in seen:
                            seen.add(name)
                            bound.append(t)
                except Exception as e:
                    logger.warning("bind_tools apps: get_tools(%s) failed: %s", app_name, e)
            _merge_find_tools_into_bound(
                bound, seen, include_find_tools=include_find_tools, tools_context_ref=tools_context_ref
            )
            if not bound:
                return active_model
            return active_model.bind_tools(bound)

        if mode == "tools":
            if not tool_names:
                if include_find_tools:
                    ft = (tools_context_ref or {}).get("_lc_bind_tools_find_tools")
                    if ft:
                        return active_model.bind_tools([ft])
                logger.warning(
                    "cuga_lite_bind_tools_mode=tools but cuga_lite_bind_tools_tool_names is empty "
                    "(set include_find_tools to bind find_tools only)"
                )
                return active_model
            if not tool_provider:
                logger.warning("cuga_lite_bind_tools_mode=tools but tool_provider is missing")
                return active_model
            by_name = await _indexed_tools_for_native_bind(tool_provider, tools_context_ref)
            if not by_name:
                return active_model
            bound = []
            seen: Set[str] = set()
            missing: List[str] = []
            for tn in tool_names:
                t = by_name.get(tn)
                if t is None:
                    missing.append(tn)
                    continue
                if tn not in seen:
                    seen.add(tn)
                    bound.append(t)
            if missing:
                logger.warning(
                    "cuga_lite_bind_tools_tool_names not found among provider tools (skipped): %s",
                    missing,
                )
            _merge_find_tools_into_bound(
                bound, seen, include_find_tools=include_find_tools, tools_context_ref=tools_context_ref
            )
            if not bound:
                return active_model
            return active_model.bind_tools(bound)

        logger.warning(
            "Unknown cuga_lite_bind_tools_mode: %s (use none|find_tools|all|apps|tools|apps_and_tools)",
            mode,
        )
    except Exception as e:
        logger.warning("resolve_model_with_bind_tools failed: %s", e)
    return active_model


def _clean_empty_response_retry_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    m = {**(meta or {})}
    m.pop("_empty_response_correction", None)
    return m


def _get_knowledge_tool_scope_context(
    engine: Any | None,
    thread_id: str | None,
) -> tuple[tuple[str, ...], str | None]:
    config = getattr(engine, "_config", None) if engine else None
    if not config or not getattr(config, "enabled", False):
        return (), None

    scopes: list[str] = []
    if getattr(config, "agent_level_enabled", True):
        scopes.append("agent")
    if getattr(config, "session_level_enabled", True) and thread_id:
        scopes.append("session")

    default_scope = "agent" if "agent" in scopes else scopes[0] if scopes else None
    return tuple(scopes), default_scope


def _knowledge_scope_instruction(allowed_scopes: tuple[str, ...], thread_id: str | None) -> str:
    if allowed_scopes == ("agent",):
        return (
            "Knowledge scope rules for this run: only agent-level knowledge is available. "
            "Never call `knowledge_*` tools with `scope=\"session\"`."
        )
    if allowed_scopes == ("session",):
        return (
            "Knowledge scope rules for this run: only session-level knowledge is available. "
            "Never call `knowledge_*` tools with `scope=\"agent\"`. The conversation thread context is injected automatically."
        )
    if allowed_scopes == ("agent", "session"):
        return (
            "Knowledge scope rules for this run: both knowledge scopes are available. "
            "Use `scope=\"agent\"` for permanent agent documents and `scope=\"session\"` for this conversation's documents."
        )
    if thread_id:
        return "Knowledge tools are unavailable in this run. Do not call any `knowledge_*` tool."
    return (
        "Knowledge tools are unavailable in this run. "
        "Session scope cannot be used here because there is no conversation thread context."
    )


def _decorate_knowledge_tool(tool: Any, allowed_scopes: tuple[str, ...], thread_id: str | None) -> None:
    """Add a brief scope hint to the tool description.

    The full scope rules are already in the system instructions, so we only
    add a short reminder here to avoid bloating the prompt with repeated text.
    """
    base_description = getattr(tool, "description", "") or "Knowledge tool"
    scopes_str = ", ".join(f'"{s}"' for s in allowed_scopes)
    hint = f"Allowed scopes: {scopes_str}. See knowledge scope rules in instructions above."
    tool.description = f"{base_description}\n\n{hint}".strip()


def make_tool_awaitable(func):
    """Wrap a sync function to make it awaitable (since agent always uses await).

    Also automatically converts Pydantic model return values to dicts using .model_dump().

    If the function is already async, wrap it to handle Pydantic models.
    If it's sync, wrap it to be awaitable using asyncio.run_in_executor and handle Pydantic models.

    Args:
        func: The tool function (sync or async)

    Returns:
        An awaitable function (coroutine function) that returns dicts for Pydantic models
    """
    from pydantic import BaseModel

    async def wrapper_with_pydantic(*args, **kwargs):
        """Inner wrapper that handles Pydantic model conversion."""
        result = await func(*args, **kwargs) if inspect.iscoroutinefunction(func) else func(*args, **kwargs)

        # Convert Pydantic models to dicts
        if isinstance(result, BaseModel):
            return result.model_dump()

        return result

    if inspect.iscoroutinefunction(func):
        # Function is already async, just add Pydantic handling
        return wrapper_with_pydantic

    # Function is sync, make it awaitable and add Pydantic handling
    async def async_wrapper(*args, **kwargs):
        # For sync functions, run in executor to make them awaitable
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: func(*args, **kwargs))

        # Convert Pydantic models to dicts
        from pydantic import BaseModel

        if isinstance(result, BaseModel):
            return result.model_dump()

        return result

    return async_wrapper


class CugaLiteState(BaseModel):
    """State for CugaLite subgraph.

    Shared keys with AgentState:
    - chat_messages: List[BaseMessage] (primary message history)
    - final_answer: str (compatible with parent)
    - pi: str (personal information/user context injected with first message)
    - variables_storage: Dict[str, Dict[str, Any]] (shared variables)
    - variable_counter_state: int (shared variable counter)
    - variable_creation_order: List[str] (shared variable order)
    - cuga_lite_metadata: Dict[str, Any] (metadata for tracking execution context)
    - sub_task: str (current subtask being executed)
    - sub_task_app: str (app name for subtask)
    - api_intent_relevant_apps: List[Any] (relevant apps for the task)
    - hitl_action: Optional[FollowUpAction] (human-in-the-loop action request)
    - hitl_response: Optional[ActionResponse] (human-in-the-loop response)
    - sender: str (node that sent the current state)
    - service_scope: Dict[str, str] (tenant_id, instance_id for multi-tenant/prod scoping)

    Subgraph-only keys:
    - script, execution_complete, error, metrics
    - tools_prepared: bool (flag indicating tools have been prepared)
    - prepared_prompt: str (dynamically generated prompt)
    - reflection_apps: list of dicts (name, type, description) for reflection prompt
    - reflection_enable_find_tools: whether find_tools shortlisting was enabled
    - reflection_skills_enabled: whether skills were available to the executor
    - reflection_skills_prompt_section: rendered skills registry block for reflection
    - mcp_few_shot_messages: normalized role/content few-shot messages injected before live chat
    - task_todos: latest todo list from create_update_todos (injected as Current Plan on the system prompt)
    """

    # Shared keys (compatible with AgentState)
    chat_messages: Optional[List[BaseMessage]] = Field(default_factory=list)
    final_answer: Optional[str] = ""
    thread_id: Optional[str] = None
    service_scope: Optional[Dict[str, str]] = Field(
        default_factory=lambda: {"tenant_id": "", "instance_id": ""}
    )
    pi: Optional[str] = ""
    variables_storage: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    variable_counter_state: int = 0
    variable_creation_order: List[str] = Field(default_factory=list)
    cuga_lite_metadata: Optional[Dict[str, Any]] = None
    sub_task: Optional[str] = None
    sub_task_app: Optional[str] = None
    api_intent_relevant_apps: Optional[List[Any]] = None
    hitl_action: Optional[Any] = None  # FollowUpAction from followup_model
    hitl_response: Optional[Any] = None  # ActionResponse from followup_model
    sender: Optional[str] = None

    # Subgraph-only keys
    tools_prepared: bool = False
    prepared_prompt: Optional[str] = None
    reflection_apps: List[Dict[str, Any]] = Field(default_factory=list)
    reflection_enable_find_tools: bool = False
    reflection_skills_enabled: bool = False
    reflection_skills_prompt_section: str = ""
    mcp_few_shot_messages: List[Dict[str, str]] = Field(default_factory=list)
    task_todos: Optional[List[Dict[str, Any]]] = Field(default=None)
    script: Optional[str] = None
    execution_complete: bool = False
    error: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    step_count: int = 0  # Counter for number of steps (call_model + sandbox cycles)
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list
    )  # List of tracked tool calls (when track_tool_calls is enabled)

    class Config:
        arbitrary_types_allowed = True

    @property
    def variables_manager(self):
        """Get a state-specific variables manager that stores data in this CugaLiteState.

        Uses the same StateVariablesManager as AgentState for consistent interface.
        """
        from cuga.backend.cuga_graph.state.agent_state import StateVariablesManager

        return StateVariablesManager(self)


def format_task_todos_system_block(todos: List[Dict[str, str]]) -> str:
    """Append to system prompt so the model always sees the live todo list (not execution variables)."""
    if not todos:
        return ""
    lines = [
        "",
        "---",
        "",
        "## Current task todos",
        "",
        "Execution only prints **Todos updated** after each change; use this list as the source of truth.",
        "",
    ]
    for i, item in enumerate(todos, start=1):
        status = item.get("status", "pending")
        text = item.get("text", "")
        lines.append(f"{i}. **[{status}]** {text}")
    lines.append("")
    return "\n".join(lines)


def _reflection_current_task(state: CugaLiteState) -> str:
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


def extract_and_combine_codeblocks(text: str) -> str:
    """Extract all python codeblocks from text and combine them."""
    code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)

    if code_blocks:
        return "\n\n".join(block.strip() for block in code_blocks)

    stripped_text = text.strip()

    if "print(" not in stripped_text:
        return ""

    try:
        compile(stripped_text.replace('await ', ''), '<string>', 'exec')
        return stripped_text
    except SyntaxError:
        return ""


def append_chat_messages_with_step_limit(
    state: CugaLiteState,
    new_messages: List[BaseMessage],
    max_steps: Optional[int] = None,
) -> Tuple[List[BaseMessage], Optional[AIMessage]]:
    """Append new messages to chat_messages with step counting and limit checking.

    Args:
        state: Current CugaLiteState
        new_messages: List of new messages to append
        max_steps: Override from configurable; when None, use settings

    Returns:
        Tuple of (updated_chat_messages, error_message)
        - updated_chat_messages: Updated list of chat messages
        - error_message: AIMessage with error if limit reached, None otherwise
    """
    limit = max_steps if max_steps is not None else settings.advanced_features.cuga_lite_max_steps
    new_step_count = state.step_count + 1

    if new_step_count > limit:
        error_msg = (
            f"Maximum step limit ({limit}) reached. "
            f"The task has exceeded the allowed number of execution cycles. "
            f"Please simplify your request or break it into smaller tasks."
        )
        logger.warning(f"Step limit reached: {new_step_count} > {limit}")
        error_ai_message = AIMessage(content=error_msg)
        return state.chat_messages + new_messages + [error_ai_message], error_ai_message

    logger.debug(f"Step count: {new_step_count}/{limit}")
    return state.chat_messages + new_messages, None


def create_error_command(
    updated_messages: List[BaseMessage],
    error_message: AIMessage,
    step_count: int,
    additional_updates: Optional[Dict[str, Any]] = None,
) -> Command:
    """Create a Command to END with error information.

    Args:
        updated_messages: Updated chat messages
        error_message: Error message to return
        step_count: Current step count
        additional_updates: Optional additional state updates

    Returns:
        Command routing to END with error state
    """
    updates = {
        "chat_messages": updated_messages,
        "script": None,
        "final_answer": error_message.content,
        "execution_complete": True,
        "error": error_message.content,
        "step_count": step_count + 1,
    }
    if additional_updates:
        updates.update(additional_updates)

    return Command(goto=END, update=updates)


class Todo(BaseModel):
    """A single todo item with text and status."""

    text: str = Field(..., description="The task description")
    status: str = Field(
        default="pending",
        description="Status of the todo: 'pending', 'in_progress', or 'completed'",
    )


class TodosInput(BaseModel):
    """Input schema for create_update_todos function."""

    todos: List[Todo] = Field(..., description="List of todos, each with 'text' and 'status' fields")


class TodosOutput(BaseModel):
    """Output schema for create_update_todos function."""

    todos: List[Todo] = Field(..., description="List of todos with their current status")


def _try_parse_todos_payload(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, dict) or "todos" not in value:
        return None
    raw = value["todos"]
    if not isinstance(raw, list):
        return None
    if not raw:
        return []
    if not all(isinstance(x, dict) and "text" in x and "status" in x for x in raw):
        return None
    return raw


def extract_task_todos_from_new_vars(new_vars: dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    for val in new_vars.values():
        parsed = _try_parse_todos_payload(val)
        if parsed is not None:
            return parsed
    return None


def format_current_plan_section(task_todos: List[Dict[str, Any]]) -> str:
    lines = ["## Current Plan", ""]
    for item in task_todos:
        text = str(item.get("text", "")).strip()
        status = str(item.get("status", "pending")).strip()
        lines.append(f"- **[{status}]** {text}")
    return "\n".join(lines) + "\n"


def _first_user_message_text(chat_messages: Optional[List[BaseMessage]]) -> Optional[str]:
    if not chat_messages:
        return None
    for msg in chat_messages:
        if isinstance(msg, HumanMessage):
            raw = msg.content
            text = raw.strip() if isinstance(raw, str) else str(raw).strip()
            return text or None
    return None


def _compose_find_tools_shortlister_query(query: str, initial_user_message: Optional[str]) -> str:
    q = query.strip()
    init = (initial_user_message or "").strip()
    if not init:
        return q
    return f"Query: {q}\nTask context (initial user message): {init}"


def _web_search_enabled() -> bool:
    return bool(getattr(settings.advanced_features, "enable_web_search", False))


def _ensure_web_app(apps: List[Any], all_apps: List[Any]) -> List[Any]:
    if not _web_search_enabled() or any(getattr(app, "name", None) == "web" for app in apps):
        return apps
    web_app = next((app for app in all_apps if getattr(app, "name", None) == "web"), None)
    if web_app:
        return [*apps, web_app]
    return apps


async def create_find_tools_tool(
    all_tools: Sequence[StructuredTool],
    all_apps: List[Any],
    app_to_tools_map: Optional[Dict[str, List[StructuredTool]]] = None,
    llm: Optional[Any] = None,
    initial_user_message: Optional[str] = None,
) -> StructuredTool:
    """Create a find_tools StructuredTool for tool discovery.

    Args:
        all_tools: All available tools to search through
        all_apps: All available app definitions
        app_to_tools_map: Optional mapping of app_name -> list of tools. If provided, used for filtering by app_name.
        initial_user_message: First human message in the session; combined with the tool `query` for shortlisting.

    Returns:
        StructuredTool configured for finding relevant tools
    """

    async def find_tools_func(query: str, app_name: str):
        """Search for relevant tools from the connected applications based on a natural language query.

        Args:
            query: Natural language query describing what tools are needed to accomplish the task can include also which parameters are needed or the output expected
            app_name: Name of a specific app to filter tools from. Only searches tools from that app.

        Returns:
            Top 4 matching tools with their details
        """
        if app_to_tools_map and app_name in app_to_tools_map:
            filtered_tools = app_to_tools_map[app_name]
        else:
            logger.warning(
                f"App '{app_name}' not found in app_to_tools_map. Available apps: {list(app_to_tools_map.keys()) if app_to_tools_map else 'N/A'}"
            )
            filtered_tools = []

        filtered_apps = [app for app in all_apps if hasattr(app, 'name') and app.name == app_name]

        if not filtered_apps:
            logger.warning(
                f"App '{app_name}' not found in available apps. Available apps: {[app.name if hasattr(app, 'name') else str(app) for app in all_apps]}"
            )

        from langchain_core.exceptions import OutputParserException

        shortlister_query = _compose_find_tools_shortlister_query(query, initial_user_message)

        try:
            return await PromptUtils.find_tools(
                query=shortlister_query, all_tools=filtered_tools, all_apps=filtered_apps, llm=llm
            )
        except OutputParserException as e:
            logger.bind(
                query_len=len(shortlister_query),
                error_type=type(e).__name__,
            ).opt(exception=True).warning(
                "Tool shortlisting failed due to parser error; returning error to agent"
            )
            return (
                f"Tool shortlisting failed due to malformed response: {e}. "
                "Please retry with a different query."
            )
        except Exception as e:
            logger.bind(
                query_len=len(shortlister_query),
                error_type=type(e).__name__,
            ).opt(exception=True).warning("Tool shortlisting failed unexpectedly; returning error to agent")
            return (
                f"Tool shortlisting failed due to an internal error: {e}. "
                "Please retry with a different query."
            )

    return StructuredTool.from_function(
        func=find_tools_func,
        name="find_tools",
        description="Search for relevant tools from a specific connected application based on a natural language query. Use this when you need to discover what tools are available for a specific task within a specific application.",
    )


def _serialize_todos_for_store(todos_list: List[Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for t in todos_list:
        if isinstance(t, Todo):
            out.append({"text": t.text, "status": t.status})
        elif hasattr(t, "model_dump"):
            d = t.model_dump()
            out.append({"text": str(d.get("text", "")), "status": str(d.get("status", "pending"))})
        elif isinstance(t, dict):
            out.append({"text": str(t.get("text", "")), "status": str(t.get("status", "pending"))})
        else:
            out.append({"text": str(t), "status": "pending"})
    return out


async def create_update_todos_tool(
    agent_state: Optional['AgentState'] = None,
    todos_store_ref: Optional[List[Dict[str, str]]] = None,
) -> StructuredTool:
    """Create a create_update_todos StructuredTool for managing task todos.

    Args:
        agent_state: Optional AgentState (reserved for future use)
        todos_store_ref: Mutable list shared with the graph; latest todos are written here for the system prompt.

    Returns:
        StructuredTool configured for creating and updating todos
    """

    async def create_update_todos_func(todos: Any) -> TodosOutput:
        """Create or update a list of todos for complex multi-step tasks.

        Use this tool when you have a complex task that requires multiple steps.
        This helps you track progress and organize your work.

        Args:
            todos: List of todo dicts/models (matches ``TodosInput.todos`` / tool schema).

        Returns:
            Short confirmation only (full list is shown in the system prompt via todos_store_ref).
        """
        input_data = todos
        # Handle different input types
        if isinstance(input_data, TodosInput):
            todos_list = input_data.todos
        elif isinstance(input_data, dict):
            # If it's a dict, check if it has 'todos' key
            if 'todos' in input_data:
                todos_list = input_data['todos']
            else:
                # If no 'todos' key, treat the whole dict as a single todo or wrap it
                todos_list = [input_data]
            # Convert dict items to Todo models
            todos_list = [Todo(**todo) if isinstance(todo, dict) else todo for todo in todos_list]
        elif isinstance(input_data, list):
            # If it's a list directly, convert each item to Todo
            todos_list = [Todo(**todo) if isinstance(todo, dict) else todo for todo in input_data]
        else:
            # Fallback: try to create TodosInput
            try:
                if isinstance(input_data, dict):
                    input_data = TodosInput(**input_data)
                else:
                    input_data = TodosInput(todos=input_data)
                todos_list = input_data.todos
            except Exception:
                # Last resort: wrap in a list
                todos_list = [Todo(**input_data) if isinstance(input_data, dict) else input_data]

        if todos_store_ref is not None:
            serialized = _serialize_todos_for_store(todos_list)
            todos_store_ref.clear()
            todos_store_ref.extend(serialized)

        normalized = [t if isinstance(t, Todo) else Todo(**t) for t in todos_list]
        return TodosOutput(todos=normalized)

    return StructuredTool.from_function(
        func=create_update_todos_func,
        name="create_update_todos",
        description="Create or update a list of todos for complex multi-step tasks. Pass `todos` as a list of objects with 'text' and 'status' ('pending', 'in_progress', or 'completed'). Returns a todos payload; the full list is shown in the system prompt under 'Current task todos' (Current Plan).",
        args_schema=TodosInput,
        return_direct=False,
    )


_BUNDLED_FIND_TOOLS_FEW_SHOT_JSON = (
    Path(__file__).resolve().parent / "prompts" / "find_tools_few_shot_examples.json"
)


def _resolve_find_tools_few_shot_json_path() -> Optional[Path]:
    if _BUNDLED_FIND_TOOLS_FEW_SHOT_JSON.is_file():
        return _BUNDLED_FIND_TOOLS_FEW_SHOT_JSON
    return None


def _load_default_find_tools_few_shot_examples() -> List[Dict[str, str]]:
    path = _resolve_find_tools_few_shot_json_path()
    if path is None:
        logger.debug(
            "Find-tools few-shot JSON not found (expected packaged %s or repo samples copy); skipping",
            _BUNDLED_FIND_TOOLS_FEW_SHOT_JSON,
        )
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        normalized = normalize_mcp_few_shot_examples(raw)
        if normalized:
            logger.info(f"Loaded {len(normalized)} find_tools MCP few-shot turn(s) from {path}")
        return normalized
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load find_tools few-shot JSON from {path}: {e}")
        return []


def create_cuga_lite_graph(
    model: BaseChatModel,
    prompt: Optional[str] = None,
    tool_provider: ToolProviderInterface = None,
    apps_list: Optional[List[str]] = None,
    agent_state: Optional[AgentState] = None,
    thread_id: Optional[str] = None,
    callbacks: Optional[List[BaseCallbackHandler]] = None,
    special_instructions: Optional[str] = None,
    model_settings: Optional[Dict[str, Any]] = None,
) -> StateGraph:
    """
    Create a unified CugaLite subgraph combining CodeAct and CugaAgent functionality.

    enable_todos and reflection_enabled are read from config["configurable"] at runtime.
    Fallback to settings.advanced_features when not provided.

    Args:
        model: The language model to use
        prompt: Optional static prompt (if None, will be created dynamically from state)
        tool_provider: Tool provider interface for accessing tools
        apps_list: List of app names for tool context
        agent_state: Optional AgentState for variables management
        thread_id: Thread ID for E2B sandbox caching
        callbacks: Optional list of callback handlers
        special_instructions: Optional special instructions to add to the prompt

    Returns:
        StateGraph implementing the CugaLite architecture
    """
    prompts_dir = Path(__file__).parent / "prompts"
    prompt_template = load_one_prompt(str(prompts_dir / "mcp_prompt.jinja2"), relative_to_caller=False)
    instructions = get_all_instructions_formatted()

    def create_prepare_node(
        base_tool_provider,
        base_prompt_template,
        base_instructions,
        tools_context_dict,
        base_special_instructions,
        task_todos_ref: List[Dict[str, str]],
        lc_bind_tools_meta: Dict[str, Any] = None,
    ):
        """Factory to create prepare node with closure over tool provider and config."""

        async def prepare_tools_and_apps(
            state: CugaLiteState, config: Optional[RunnableConfig] = None
        ) -> Command:
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
                graph_default_model=model,
            )
            few_shots_enabled = resolve_cuga_lite_few_shots_enabled(
                configurable,
                model_name=_runtime_model_name,
            )
            logger.debug(
                f"[APPROVAL DEBUG] prepare_tools_and_apps received cuga_lite_metadata: {state.cuga_lite_metadata}"
            )

            # Skip policy checking if policies are disabled or if we're returning from approval
            if settings.policy.enabled and not ToolApprovalHandler.should_skip_policy_check(state):
                # Check for policies and enact if matched
                # Include IntentGuard, Playbook, and ToolGuide for intent checks
                from cuga.backend.cuga_graph.policy.models import PolicyType

                command, metadata = await PolicyEnactment.check_and_enact(
                    state,
                    config,
                    policy_types=[PolicyType.INTENT_GUARD, PolicyType.PLAYBOOK, PolicyType.TOOL_GUIDE],
                )

                # If policy returned a command (e.g., BLOCK_INTENT), execute it immediately
                if command:
                    return command

                # If policy returned metadata (e.g., playbook guidance), store it
                if metadata:
                    state.cuga_lite_metadata = metadata
            elif not settings.policy.enabled:
                logger.debug("Policy system disabled - skipping policy checks")
            else:
                logger.info("[APPROVAL DEBUG] Skipping policy check - user has already approved")

            if not base_tool_provider:
                raise ValueError("tool_provider is required")

            # Get total tool count across ALL apps (for shortlisting threshold - not per app)
            all_tools_total = await base_tool_provider.get_all_tools()
            total_tool_count = len(all_tools_total) if all_tools_total else 0

            # Get tools from provider
            apps_for_prompt = None
            app_to_tools_map = {}

            # Get apps from state and filter tools if specific app is selected
            if state.sub_task_app:
                # Specific app selected - filter tools to only this app
                all_apps = await base_tool_provider.get_apps()
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
                    current_tools_for_execution = await base_tool_provider.get_tools(app.name)
                    app_to_tools_map[app.name] = current_tools_for_execution
                    tools_for_execution.extend(current_tools_for_execution)

                logger.info(
                    f"Filtered to {len(tools_for_execution)} tools for {len(apps_for_prompt)} identified apps"
                )
            elif state.api_intent_relevant_apps:
                # Filter to API apps
                all_apps = await base_tool_provider.get_apps()
                apps_for_prompt = [
                    app
                    for app in state.api_intent_relevant_apps
                    if hasattr(app, 'type') and app.type == 'api'
                ]
                apps_for_prompt = _ensure_web_app(apps_for_prompt, all_apps)
                # Get tools only for the identified apps
                tools_for_execution = []
                for app in apps_for_prompt:
                    app_tools = await base_tool_provider.get_tools(app.name)
                    app_to_tools_map[app.name] = app_tools
                    tools_for_execution.extend(app_tools)
                logger.info(
                    f"Filtered to {len(tools_for_execution)} tools for {len(apps_for_prompt)} identified apps"
                )
            else:
                # Get all tools and apps
                all_apps = await base_tool_provider.get_apps()
                apps_for_prompt = all_apps
                tools_for_execution = all_tools_total or []
                # Build mapping for all apps
                for app in apps_for_prompt:
                    app_tools = await base_tool_provider.get_tools(app.name)
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
                tools_context_dict['find_tools'] = make_tool_awaitable(find_tool_func)
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
                todos_tool = await create_update_todos_tool(agent_state=state, todos_store_ref=task_todos_ref)
                tools_for_prompt.append(todos_tool)
                # Add to tools context for sandbox execution
                # Prefer coroutine over func to avoid run_in_executor issues
                todos_tool_func = (
                    todos_tool.coroutine
                    if hasattr(todos_tool, 'coroutine') and todos_tool.coroutine
                    else todos_tool.func
                )
                tools_context_dict['create_update_todos'] = make_tool_awaitable(todos_tool_func)

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
            effective_special = base_special_instructions or configurable_special or ""
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

            # Update tools context with all execution tools
            # Wrap to make awaitable (agent always uses await)
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
                    tools_context_dict[tool.name] = make_tool_awaitable(tool_func)
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
                    tools_context_dict[tool.name] = make_tool_awaitable(tool_func)
                else:
                    logger.warning(f"Skill tool '{tool.name}' has no callable, skipping")

            # Inject sandbox tools when shell tools are enabled
            _sandbox_mode = getattr(settings.advanced_features, "sandbox_mode", "opensandbox")
            _shell_tool_on = getattr(settings.advanced_features, "enable_shell_tool", False)
            _opensandbox_on = getattr(settings.advanced_features, "opensandbox_sandbox", False)
            _use_sandbox = _shell_tool_on and (
                (_sandbox_mode == "native")
                or (_sandbox_mode == "opensandbox" and _opensandbox_on)
                or (_sandbox_mode == "local")
            )
            if _use_sandbox:
                from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor

                cfg = config.get("configurable", {}) if config else {}
                if "thread_id" in cfg:
                    runtime_thread_id = cfg["thread_id"]
                else:
                    runtime_thread_id = state.thread_id or thread_id

                if _sandbox_mode == "native":
                    sandbox_executor = CodeExecutor._get_native_executor()
                    sandbox_label = "NativeSandbox"
                elif _sandbox_mode == "local":
                    sandbox_executor = CodeExecutor._get_local_sandbox_executor()
                    sandbox_label = "LocalSandbox"
                else:
                    sandbox_executor = CodeExecutor._get_opensandbox_executor()
                    sandbox_label = "OpenSandbox"

                sandbox_tools = sandbox_executor.create_sandbox_tools(thread_id=runtime_thread_id)
                for st in sandbox_tools:
                    fn = st.coroutine or st.func
                    if fn:
                        tools_context_dict[st.name] = fn
                tools_for_prompt.extend(sandbox_tools)
                logger.info(
                    f"[{sandbox_label}] Injected sandbox tools (thread_id={runtime_thread_id!r}) into execution context and prompt: {[t.name for t in sandbox_tools]}"
                )

            from cuga.backend.evolve.integration import EvolveIntegration

            special_instructions_final = effective_special or ""
            if EvolveIntegration.is_enabled():
                task_description = ""
                if state.sub_task:
                    task_description = state.sub_task
                elif state.chat_messages:
                    for msg in state.chat_messages:
                        if isinstance(msg, HumanMessage):
                            task_description = msg.content
                            break
                if task_description:
                    evolve_guidelines = await EvolveIntegration.get_guidelines(task_description)
                    if evolve_guidelines:
                        evolve_section = f"\n\n## Evolve Guidelines\n{evolve_guidelines}"
                        special_instructions_final = (special_instructions_final or "") + evolve_section
                        logger.info("Evolve: Injected guidelines into system prompt")
                        logger.debug(
                            f"Evolve: Full special_instructions with guidelines:\n{special_instructions_final}"
                        )

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
                    tools_context_dict.pop(tool_name, None)
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
                    original_fn = tools_context_dict.get(tool_name)
                    if original_fn:
                        tools_context_dict[tool_name] = _wrap_knowledge_tool(
                            original_fn,
                            _thread_id,
                            allowed_knowledge_scopes,
                            default_knowledge_scope,
                        )

                # Note: scope rules are injected once via effective_instructions.
                # No per-tool decoration needed — avoids repeated text in prompt.

            # Inject knowledge base awareness if knowledge tools are available
            effective_instructions = base_instructions
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
            dynamic_prompt = prompt

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
                    prompt_template=base_prompt_template,
                    enable_find_tools=enable_find_tools,
                    enable_todos=enable_todos,
                    special_instructions=special_instructions_final,
                    skills_enabled=skills_enabled,
                    skills_prompt_section=skills_prompt_section,
                    enable_shell_tool=getattr(settings.advanced_features, "enable_shell_tool", False),
                    sandbox_workspace="." if _sandbox_mode in ("native", "local") else "/workspace",
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

    # Factory function to create call_model node with access to model
    def create_call_model_node(
        base_model,
        base_callbacks,
        task_todos_ref: List[Dict[str, str]],
        model_settings=None,
        tools_context_ref=None,
        base_tool_provider=None,
    ):
        """Factory to create call_model node. Model is taken from config['configurable']['llm']
        when set (injected at invocation), otherwise uses base_model from graph build.
        """

        async def call_model(state: CugaLiteState, config: Optional[RunnableConfig] = None) -> Command:
            """Call the LLM to generate code or text response."""
            configurable = config.get("configurable", {}) if config else {}
            max_steps = (
                configurable.get("cuga_lite_max_steps") if "cuga_lite_max_steps" in configurable else None
            )

            logger.debug(
                f"[APPROVAL DEBUG] call_model received cuga_lite_metadata: {state.cuga_lite_metadata}"
            )

            # Check if we're returning from tool approval - if so, skip code generation and go to sandbox
            # Only check if policies are enabled
            if settings.policy.enabled and ToolApprovalHandler.is_returning_from_approval(state):
                return ToolApprovalHandler.handle_approval_resumption(state)

            # Get prompt from state (tools are available via sandbox context, not needed here)
            dynamic_prompt = state.prepared_prompt or ""

            _cfg = config.get("configurable", {}) if config else {}
            _enable_todos = (
                _cfg.get("enable_todos")
                if "enable_todos" in _cfg
                else settings.advanced_features.enable_todos
            )

            system_content = dynamic_prompt
            if _enable_todos:
                if task_todos_ref:
                    system_content = dynamic_prompt + format_task_todos_system_block(task_todos_ref)
                elif state.task_todos:
                    system_content = dynamic_prompt + format_current_plan_section(state.task_todos)

            # Convert BaseMessage objects to dict format for model invocation
            messages_for_model = [{"role": "system", "content": system_content}]
            few_shot_messages = state.mcp_few_shot_messages or []
            for example in few_shot_messages:
                role = (example.get("role") or "").strip().lower()
                content = example.get("content") or ""
                if role in {"user", "assistant"} and content:
                    messages_for_model.append({"role": role, "content": content})
            if few_shot_messages:
                logger.info(
                    "Injected {} MCP few-shot turn(s) as chat messages before live conversation",
                    len(few_shot_messages),
                )

            # Check if we have variables and this is a new question (not a follow-up with existing AI responses)
            # If this is a new question (1 user msg, 0 AI msgs) or follow-up, add variables to the last user message
            var_manager = state.variables_manager
            for _vn in list(var_manager.get_variable_names()):
                if is_find_tools_listing_markdown(var_manager.get_variable(_vn)):
                    var_manager.remove_variable(_vn)
            existing_variable_names = var_manager.get_variable_names()
            variables_summary_text = None

            if existing_variable_names and state.sub_task_app:
                variables_summary_text = var_manager.get_variables_summary(
                    variable_names=existing_variable_names
                )
                variables_addendum = f"\n\n## Available Variables\n\n{variables_summary_text}\n\nYou can use these variables directly by their names."
                logger.info(
                    f"Will add variables summary for {len(existing_variable_names)} variables to user message"
                )

            logger.info(f"Processing {len(state.chat_messages)} chat messages for model invocation")

            # Track if we've added personal information (pi)
            pi_added = False

            # Get playbook guidance if available (only on first detection)
            # TODO: In the future, we could refine the playbook guidance on each message
            # based on conversation progress and completed steps
            playbook_guidance = None
            playbook_already_added = False

            # Check if playbook guidance was already added in previous messages
            if state.cuga_lite_metadata and state.cuga_lite_metadata.get('playbook_guidance_added'):
                playbook_already_added = True

            if (
                state.cuga_lite_metadata
                and state.cuga_lite_metadata.get('policy_matched')
                and not playbook_already_added
            ):
                if state.cuga_lite_metadata.get('policy_type') == 'playbook':
                    playbook_guidance = state.cuga_lite_metadata.get('playbook_guidance')
                    if playbook_guidance:
                        logger.info(
                            "Will inject playbook guidance into current user message (first time only)"
                        )

            # Get configurable values from config
            configurable = config.get("configurable", {}) if config else {}
            current_callbacks = configurable.get("callbacks", base_callbacks or [])
            active_model = configurable.get("llm") or base_model
            _runtime_model_name = resolved_runtime_model_name(
                configurable_llm=configurable.get("llm"),
                graph_default_model=base_model,
            )

            # ── Context management BEFORE building messages_for_model ────────────
            effective_chat_messages = await apply_context_summarization(
                state.chat_messages or [],
                active_model,
                system_prompt=dynamic_prompt,
                tools=None,
                tracker=tracker,
                variables_storage=state.variables_storage,
                variable_counter_state=state.variable_counter_state,
                variable_creation_order=state.variable_creation_order,
            )
            # effective_chat_messages may contain summarized messages if context limit exceeded
            # ─────────────────────────────────────────────────────────────────────

            # Build messages_for_model from effective_chat_messages (post-summarization)
            # Also build modified_chat_messages with playbook/pi/variables injected
            modified_chat_messages = []
            for i, msg in enumerate(effective_chat_messages):
                msg_type = type(msg).__name__
                msg_role = getattr(msg, 'type', None)

                if isinstance(msg, HumanMessage):
                    content = msg.content
                    content_modified = False

                    # Add personal information (pi) to the FIRST user message only
                    if (
                        state.pi
                        and not pi_added
                        and "## User Context" not in content
                        and len(effective_chat_messages) == 1
                    ):
                        content = f"{content}\n\n## User Context\n{state.pi}"
                        pi_added = True
                        content_modified = True
                        logger.debug("Added personal information (pi) to first user message")

                    # Add playbook guidance to the LAST user message only
                    if playbook_guidance and i == len(effective_chat_messages) - 1:
                        content = f"{content}\n\n## Task Guidance\n{playbook_guidance}"
                        content_modified = True
                        logger.debug("Added playbook guidance to last user message")

                    # Add variables summary to the LAST user message only
                    if variables_summary_text and i == len(effective_chat_messages) - 1:
                        content = content + variables_addendum
                        content_modified = True
                        logger.debug("Added variables summary to last user message")

                    # Build new message if modified, otherwise keep original
                    if content_modified:
                        modified_chat_messages.append(HumanMessage(content=content))
                        logger.debug(f"Created modified message at index {i} with playbook/pi/variables")
                    else:
                        modified_chat_messages.append(msg)

                    messages_for_model.append({"role": "user", "content": content})
                elif isinstance(msg, AIMessage):
                    modified_chat_messages.append(msg)
                    messages_for_model.append({"role": "assistant", "content": msg.content})
                else:
                    # Handle generic BaseMessage by checking the 'type' attribute
                    if msg_role == 'human' or msg_role == 'user':
                        content = msg.content
                        content_modified = False

                        # Add personal information (pi) to the FIRST user message only
                        if state.pi and not pi_added:
                            content = f"{content}\n\n## User Context\n{state.pi}"
                            pi_added = True
                            content_modified = True
                            logger.debug("Added personal information (pi) to first user message")

                        # Add playbook guidance to the LAST user message only
                        if playbook_guidance and i == len(effective_chat_messages) - 1:
                            content = f"{content}\n\n## Task Guidance\n{playbook_guidance}"
                            content_modified = True
                            logger.debug("Added playbook guidance to last user message")

                        if variables_summary_text and i == len(effective_chat_messages) - 1:
                            content = content + variables_addendum
                            content_modified = True

                        # Build new message if modified, otherwise keep original
                        if content_modified:
                            modified_chat_messages.append(HumanMessage(content=content))
                            logger.debug(f"Created modified message at index {i} with playbook/pi/variables")
                        else:
                            modified_chat_messages.append(msg)

                        messages_for_model.append({"role": "user", "content": content})
                        logger.debug(f"Added BaseMessage as user message (role={msg_role})")
                    elif msg_role == 'ai' or msg_role == 'assistant':
                        modified_chat_messages.append(msg)
                        messages_for_model.append({"role": "assistant", "content": msg.content})
                        logger.debug(f"Added BaseMessage as assistant message (role={msg_role})")
                    else:
                        modified_chat_messages.append(msg)
                        logger.warning(
                            f"Skipping message {i} with unknown type: {msg_type}, role: {msg_role}"
                        )

            try:
                invoke_model = await resolve_model_with_bind_tools(
                    active_model,
                    configurable=configurable,
                    tools_context_ref=tools_context_ref,
                    tool_provider=base_tool_provider,
                    model_name=_runtime_model_name,
                )

                response = await invoke_model.ainvoke(
                    messages_for_model, config={"callbacks": current_callbacks}
                )
                logger.debug(f"Response: {response}")
            except Exception as e:
                code = extract_code_from_tool_use_failed(e)
                if code:
                    logger.warning(
                        "Model attempted tool call without tools bound (tool_use_failed). "
                        "Using generated code in sandbox"
                    )
                    response = type(
                        "_FakeResponse", (), {"content": f"```python\n{code}\n```", "additional_kwargs": {}}
                    )()
                else:
                    raise e

            _resp_tool_calls = getattr(response, "tool_calls", None) or []
            _resp_ak_keys = list((getattr(response, "additional_kwargs", None) or {}).keys())
            _resp_finish = (getattr(response, "response_metadata", None) or {}).get(
                "finish_reason", "unknown"
            )
            logger.debug(
                f"LLM response — type: {type(response).__name__} | "
                f"content_len: {len(response.content or '')} | "
                f"finish_reason: {_resp_finish} | "
                f"tool_calls: {_resp_tool_calls} | "
                f"additional_kwargs_keys: {_resp_ak_keys}"
            )

            raw_content = normalize_assistant_text(response.content)
            if not raw_content:
                tool_code = _extract_code_from_response_tool_calls(response)
                if tool_code:
                    logger.warning(
                        "Empty content with tool_calls detected (proxy conversion); "
                        "recovering tool call as Python code"
                    )
                    raw_content = tool_code
                elif _resp_finish not in ("stop", "unknown"):
                    logger.warning(
                        f"LLM returned empty content with finish_reason='{_resp_finish}'; "
                        "likely a safety filter or terminal stop."
                    )

            content = raw_content

            reasoning_str = normalize_assistant_text(response.additional_kwargs.get('reasoning_content'))

            tracker.collect_step(step=Step(name="Raw_Assistant_Response", data=content))

            # Try to extract code from content first, then reasoning if content has no code
            code = extract_and_combine_codeblocks(content) if content else ""

            if not code and reasoning_str:
                code = extract_and_combine_codeblocks(reasoning_str)

            if code:
                tracker.collect_step(step=Step(name="Assistant_code", data=content))
                logger.debug(
                    f"\n{'=' * 50} ASSISTANT CODE {'=' * 50}\n{code}\n{'=' * 50} END ASSISTANT CODE {'=' * 50}"
                )

                # Check if code requires approval and create interrupt if needed
                # Only check if policies are enabled
                if settings.policy.enabled:
                    approval_command = await ToolApprovalHandler.check_and_create_approval_interrupt(
                        state, code, content, config
                    )
                    if approval_command:
                        return approval_command

                # Build updated messages from modified_chat_messages + new AI response
                updated_messages = modified_chat_messages + [AIMessage(content=content)]
                new_step_count = state.step_count + 1

                # Check step limit
                limit = max_steps if max_steps is not None else settings.advanced_features.cuga_lite_max_steps
                if new_step_count > limit:
                    error_msg = (
                        f"Maximum step limit ({limit}) reached. "
                        f"The task has exceeded the allowed number of execution cycles. "
                        f"Please simplify your request or break it into smaller tasks."
                    )
                    logger.warning(f"Step limit reached: {new_step_count} > {limit}")
                    error_ai_message = AIMessage(content=error_msg)
                    return create_error_command(
                        updated_messages + [error_ai_message], error_ai_message, state.step_count
                    )

                logger.debug(f"Step count: {new_step_count}/{limit}")

                # Update metadata to mark playbook guidance as added
                updated_metadata = _clean_empty_response_retry_meta(state.cuga_lite_metadata or {})
                if playbook_guidance:
                    updated_metadata = {**updated_metadata, "playbook_guidance_added": True}

                return Command(
                    goto="sandbox",
                    update={
                        "chat_messages": updated_messages,
                        "script": code,
                        "step_count": new_step_count,
                        "cuga_lite_metadata": updated_metadata,
                    },
                )
            else:
                tracker.collect_step(step=Step(name="Assistant_nl", data=content))
                planning_response = content or ""

                # Build updated messages from modified_chat_messages + new AI response
                updated_messages = modified_chat_messages + [AIMessage(content=planning_response)]
                new_step_count = state.step_count + 1

                # Check step limit
                limit = max_steps if max_steps is not None else settings.advanced_features.cuga_lite_max_steps
                if new_step_count > limit:
                    error_msg = (
                        f"Maximum step limit ({limit}) reached. "
                        f"The task has exceeded the allowed number of execution cycles. "
                        f"Please simplify your request or break it into smaller tasks."
                    )
                    logger.warning(f"Step limit reached: {new_step_count} > {limit}")
                    error_ai_message = AIMessage(content=error_msg)
                    return create_error_command(
                        updated_messages + [error_ai_message], error_ai_message, state.step_count
                    )

                logger.debug(f"Step count: {new_step_count}/{limit}")

                # Update metadata to mark playbook guidance as added
                updated_metadata = _clean_empty_response_retry_meta(state.cuga_lite_metadata or {})
                if playbook_guidance:
                    updated_metadata = {**updated_metadata, "playbook_guidance_added": True}

                should_auto_continue = await classify_nl_auto_continue(
                    active_model,
                    planning_response,
                    reasoning_str or None,
                )
                tracker.collect_step(
                    step=Step(
                        name="NL_Auto_Continue_Classifier",
                        data=json.dumps({"auto_continue": should_auto_continue}),
                    )
                )
                if should_auto_continue:
                    logger.info(
                        "CugaLite: NL-only response classified as interim; simulating user 'continue'"
                    )
                    return Command(
                        goto="call_model",
                        update={
                            "chat_messages": updated_messages + [HumanMessage(content="continue")],
                            "script": None,
                            "final_answer": "",
                            "execution_complete": False,
                            "step_count": new_step_count,
                            "cuga_lite_metadata": updated_metadata,
                        },
                    )

                return Command(
                    goto=END,
                    update={
                        "chat_messages": updated_messages,
                        "script": None,
                        "final_answer": planning_response,
                        "execution_complete": True,
                        "step_count": new_step_count,
                        "cuga_lite_metadata": updated_metadata,
                    },
                )

        return call_model

    # Factory function to create sandbox node with access to tools context
    def create_sandbox_node(base_tools_context, base_thread_id, base_apps_list):
        """Factory to create sandbox node with closure over tools context and config."""

        async def sandbox(state: CugaLiteState, config: Optional[RunnableConfig] = None):
            """Execute code in sandbox and return results."""
            from cuga.backend.cuga_graph.nodes.cuga_lite.tool_call_tracker import ToolCallTracker

            # Check if user denied approval (only if policies are enabled)
            if settings.policy.enabled:
                denial_command = ToolApprovalHandler.handle_denial(state)
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
            context = {**existing_vars, **base_tools_context}

            # Start tool call tracking (only if enabled via invoke parameter)
            ToolCallTracker.start_tracking(enabled=track_tool_calls)

            try:
                # Execute the script - pass the CugaLiteState itself since it has variables_manager
                output, new_vars = await CodeExecutor.eval_with_tools_async(
                    code=state.script,
                    _locals=context,
                    state=state,  # Pass CugaLiteState - it has variables_manager property
                    thread_id=current_thread_id,
                    apps_list=current_apps_list,
                )

                tracker.collect_step(step=Step(name="User_output", data=output))
                tracker.collect_step(
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
                        active_model = configurable.get("llm") or llm_manager.get_model(
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
                execution_message_content = f"Execution output:\n{output}"
                if reflection_output:
                    execution_message_content = (
                        f"{execution_message_content}\n\n---\n\nSummary:\n{reflection_output}"
                    )

                tracker.collect_step(
                    step=Step(
                        name="User_return",
                        data=execution_message_content,
                    )
                )

                new_message = HumanMessage(content=execution_message_content)
                updated_messages, error_message = append_chat_messages_with_step_limit(
                    state, [new_message], max_steps=max_steps
                )

                # Collect tool calls from this execution
                execution_tool_calls = ToolCallTracker.stop_tracking()
                accumulated_tool_calls = (state.tool_calls or []) + execution_tool_calls

                if error_message:
                    return create_error_command(
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
                updated_messages, limit_error_message = append_chat_messages_with_step_limit(
                    state, [new_message], max_steps=max_steps
                )

                if limit_error_message:
                    return create_error_command(updated_messages, limit_error_message, state.step_count)

                return {
                    "chat_messages": updated_messages,
                    "error": error_msg,
                    "execution_complete": True,
                    "step_count": state.step_count + 1,
                    "tool_calls": accumulated_tool_calls,
                }

        return sandbox

    # Mutable list shared by prepare (create_update_todos) and call_model (system prompt section).
    task_todos_ref: List[Dict[str, str]] = []

    # Execution context: callable tools for the sandbox. Populated by prepare_node.
    tools_context = {}
    # LangChain bind-tools metadata: _lc_bind_tools_* entries for resolve_model_with_bind_tools.
    # Kept separate so it never leaks into context_locals used by the code executor.
    lc_bind_tools_meta: Dict[str, Any] = {}

    # Create node instances using factories
    prepare_node = create_prepare_node(
        tool_provider,
        prompt_template,
        instructions,
        tools_context,
        special_instructions,
        task_todos_ref,
        lc_bind_tools_meta=lc_bind_tools_meta,
    )
    call_model_node = create_call_model_node(
        model,
        callbacks,
        task_todos_ref,
        model_settings=model_settings,
        tools_context_ref=lc_bind_tools_meta,
        base_tool_provider=tool_provider,
    )
    sandbox_node = create_sandbox_node(tools_context, thread_id, apps_list)

    # Build the graph
    graph = StateGraph(CugaLiteState)
    graph.add_node("prepare_tools_and_apps", prepare_node)
    graph.add_node("call_model", call_model_node)
    graph.add_node("sandbox", sandbox_node)

    graph.add_edge(START, "prepare_tools_and_apps")
    graph.add_edge("sandbox", "call_model")

    return graph
