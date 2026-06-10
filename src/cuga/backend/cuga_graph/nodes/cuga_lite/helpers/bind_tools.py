"""Model and tool binding helper functions for CugaLite."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.config import settings
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import (
    resolve_bind_tools_fields,
    resolved_runtime_model_name,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools import (
    apply_bind_tools_cap_and_merge,
    bind_tools_max_count_from_settings,
)


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
        logger.warning("bind_tools: get_all_tools failed: {}", e)
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
    query: Optional[str] = None,
    run_config: Any = None,
) -> BaseChatModel:
    """Optionally wrap ``active_model`` with ``bind_tools`` for native tool-calling tests.

    LangGraph ``config['configurable']`` overrides per-model runtime profile overrides TOML:

    - ``cuga_lite_bind_tools_mode``: ``none`` | ``find_tools`` | ``all`` | ``apps`` | ``tools`` | ``apps_and_tools``
    - ``cuga_lite_bind_tools_apps``: list of app names (``mode=apps`` or ``apps_and_tools``)
    - ``cuga_lite_bind_tools_tool_names``: StructuredTool ``name`` values (``mode=tools`` or ``apps_and_tools``)
    - ``cuga_lite_bind_tools_include_find_tools``: merge ``find_tools`` into ``all`` / ``apps`` / ``tools`` / ``apps_and_tools``
    - ``cuga_lite_bind_tools_max_count``: provider-safe cap on the number of tools sent to
      ``bind_tools``. Default 128 (matches Groq/OpenAI). Set 0 to disable. When the
      candidate list exceeds the cap, the LLM shortlister picks the top-K most relevant
      tools for ``query`` (typically the first user message).
    - ``cuga_lite_bind_tools_pad_to_cap``: opt-in padding (default ``False``). When the
      shortlister returns fewer than the cap allows, pad with remaining candidates to fill
      the cap. Off by default because padding pushes the model toward native ``tool_calls``
      mode, which the code-mode flow doesn't fully exercise (measured: 0 tool calls vs 5-7
      without padding on the m3 hockey benchmark).

    Operational cost: when the cap is exceeded, applying it incurs **one extra LLM
    round-trip** (the shortlister) per ``call_model`` invocation. Permissive backends
    (WatsonX, Anthropic via LiteLLM) can avoid this round-trip entirely by setting
    ``cuga_lite_bind_tools_max_count=0``. Silent truncation is **not** an option — when
    shortlisting cannot run safely (no user query, shortlister failure, or hallucinated
    names that don't match any candidate), a ``RuntimeError`` is raised so research/
    benchmark runs comparing native tool-calling vs text-mode don't silently degrade.

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
    max_count = bind_tools_max_count_from_settings()

    async def _cap_merge_bound(bound: List[StructuredTool]) -> List[StructuredTool]:
        # Closes over query, tool_provider, llm, max_count, include_find_tools,
        # tools_context_ref, mode — the four mode branches all pass the same kwargs.
        return await apply_bind_tools_cap_and_merge(
            bound,
            query=query,
            tool_provider=tool_provider,
            llm=active_model,
            max_count=max_count,
            include_find_tools=include_find_tools,
            tools_context_ref=tools_context_ref,
            mode=mode,
            run_config=run_config,
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
            bound = await _cap_merge_bound(list(by_name.values()))
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
                    logger.warning("bind_tools apps_and_tools: get_tools({}) failed: {}", app_name, e)

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

            bound = await _cap_merge_bound(bound)
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
                    logger.warning("bind_tools apps: get_tools({}) failed: {}", app_name, e)
            bound = await _cap_merge_bound(bound)
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
            bound = await _cap_merge_bound(bound)
            if not bound:
                return active_model
            return active_model.bind_tools(bound)

        logger.warning(
            "Unknown cuga_lite_bind_tools_mode: %s (use none|find_tools|all|apps|tools|apps_and_tools)",
            mode,
        )
    except RuntimeError:
        # Actionable cap/shortlist errors from apply_bind_tools_cap_and_merge are intentional —
        # surfacing them is required so research/benchmark runs don't silently degrade.
        raise
    except Exception as e:
        logger.warning("resolve_model_with_bind_tools failed: {}", e)
    return active_model
