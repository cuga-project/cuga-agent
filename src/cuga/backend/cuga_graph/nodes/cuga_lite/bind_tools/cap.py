"""Provider-safe cap and LLM shortlisting for ``bind_tools`` candidate lists.

Strict providers (Groq, OpenAI) reject ``bind_tools`` calls with more than ~128
tools per request. This module reads the cap from settings, and — when the
candidate list exceeds it — defers to the same LLM shortlister that runtime
tool-discovery uses (:meth:`PromptUtils.shortlist_tool_names`) to pick the
top-K most relevant tools for the user query.

Note: when the cap is exceeded, applying it incurs a single shortlister LLM
round-trip per ``call_model`` invocation. This is intentional — silent
truncation would corrupt benchmark results comparing native tool-calling vs
text-mode. Permissive backends (WatsonX, Anthropic via LiteLLM) can disable
the cap with ``cuga_lite_bind_tools_max_count=0``.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
from cuga.config import settings


__all__ = [
    "apply_bind_tools_cap_and_merge",
    "bind_tools_max_count_from_settings",
    "bind_tools_pad_to_cap_from_settings",
]


def bind_tools_max_count_from_settings() -> int:
    """Provider-safe cap on the number of tools passed to ``LLM.bind_tools``.

    Default 128 matches the strictest common provider limit (Groq, OpenAI). Set
    ``DYNACONF_ADVANCED_FEATURES__CUGA_LITE_BIND_TOOLS_MAX_COUNT=0`` (or negative)
    to disable the cap entirely — useful for permissive backends like WatsonX or
    LiteLLM routing to Anthropic.
    """
    try:
        raw = getattr(settings.advanced_features, "cuga_lite_bind_tools_max_count", 128)
    except Exception:
        return 128
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 128


def bind_tools_pad_to_cap_from_settings() -> bool:
    """Whether to pad the shortlister output with the remaining tools to fill the cap.

    Default ``False`` — bind only the tools the shortlister deemed relevant (often 1-4
    on the existing system prompt). cuga_lite is a code-execution agent and exhibits
    measurable regressions in code-emission when many tools are bound natively (the
    model tends to switch to native ``tool_calls`` mode, which the code-mode flow
    doesn't fully exercise).

    Set ``True`` for research scenarios where the user explicitly wants ``mode=all``
    to bind as many tools as the provider will accept.
    """
    try:
        raw = getattr(settings.advanced_features, "cuga_lite_bind_tools_pad_to_cap", False)
    except Exception:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return bool(raw)


def _resolve_find_tools_overlay(
    bound: List[StructuredTool],
    *,
    include_find_tools: bool,
    tools_context_ref: Optional[Dict[str, Any]],
) -> Tuple[Optional[StructuredTool], str, bool, List[StructuredTool]]:
    """Resolve the ``find_tools`` overlay candidate and reconcile against ``bound``.

    Returns ``(find_tools_tool, find_tools_name, find_tools_already_in_bound, bound)``.

    The overlay path (``_indexed_tools_for_native_bind``) can inject ``find_tools``
    into ``bound`` independently of ``include_find_tools``, so we detect it either
    way to honor an explicit opt-out. If the user disabled it but the overlay
    injected it anyway, strip it from ``bound`` so it can't consume a capped slot
    or sneak into the shortlister's input. (Coderabbit on #203.)
    """
    find_tools_tool: Optional[StructuredTool] = None
    if tools_context_ref:
        candidate = tools_context_ref.get("_lc_bind_tools_find_tools")
        if candidate is not None:
            find_tools_tool = candidate

    find_tools_name = getattr(find_tools_tool, "name", "") or ""
    find_tools_already_in_bound = bool(find_tools_name) and any(
        getattr(t, "name", "") == find_tools_name for t in bound
    )
    if not include_find_tools and find_tools_already_in_bound:
        bound = [t for t in bound if getattr(t, "name", "") != find_tools_name]
        find_tools_already_in_bound = False
    return find_tools_tool, find_tools_name, find_tools_already_in_bound, bound


def _build_ranking_pool(
    bound: List[StructuredTool],
    *,
    keep_find_tools: bool,
    find_tools_name: str,
    find_tools_already_in_bound: bool,
) -> List[StructuredTool]:
    """Strip ``find_tools`` from the ranking pool when we must guarantee it survives.

    When ``include_find_tools=True`` the LLM ranker is free to drop any tool from
    the ranking pool — pulling find_tools out and appending it back is the only
    safe way to guarantee it.
    """
    if keep_find_tools and find_tools_already_in_bound:
        return [t for t in bound if getattr(t, "name", "") != find_tools_name]
    return bound


async def _run_shortlister(
    query_text: str,
    *,
    ranking_pool: List[StructuredTool],
    tool_provider: Optional[ToolProviderInterface],
    llm: Optional[BaseChatModel],
    top_k: int,
    mode: str,
    max_count: int,
    run_config: Any = None,
) -> List[str]:
    """Run :meth:`PromptUtils.shortlist_tool_names` and validate the result.

    Raises ``RuntimeError`` on shortlister failure or empty ranking — silent
    truncation would corrupt benchmark results comparing native vs text mode.
    """
    all_apps: List[Any] = []
    if tool_provider is not None:
        try:
            all_apps = await tool_provider.get_apps()
        except Exception as e:
            logger.warning("bind_tools cap: tool_provider.get_apps() failed: {}", e)

    logger.info(
        "bind_tools cap exceeded: mode={} candidates={} cap={} → LLM shortlister to top {}",
        mode,
        len(ranking_pool),
        max_count,
        top_k,
    )
    try:
        ranked_names = await PromptUtils.shortlist_tool_names(
            query=query_text,
            all_tools=ranking_pool,
            all_apps=all_apps,
            llm=llm,
            top_k=top_k,
            run_config=run_config,
        )
    except Exception as e:
        raise RuntimeError(
            f"cuga_lite_bind_tools shortlister failed reducing {len(ranking_pool)} tools to "
            f"top {top_k} (cap={max_count}): {e!r}. Raise the cap or fix the shortlister LLM."
        ) from e

    if not ranked_names:
        raise RuntimeError(
            f"cuga_lite_bind_tools shortlister returned 0 tools for {len(ranking_pool)} "
            f"candidates (cap={max_count}, query={query_text!r}). Cannot proceed safely; "
            f"raise the cap or refine the query."
        )
    return ranked_names


def _materialize_shortlist(
    ranked_names: List[str],
    *,
    ranking_pool: List[StructuredTool],
    target_k: int,
    query_text: str,
    max_count: int,
) -> Tuple[List[StructuredTool], Set[str]]:
    """Map ranker output back to ``StructuredTool`` objects, clamped to ``target_k``.

    Defense-in-depth clamp: enforce ``target_k`` at the call site too, in case the
    shortlister returns more names than ``top_k`` (custom shortlister, future
    refactor, or a mocked path). Without this clamp the bound list could exceed
    ``max_count`` and re-trigger the provider 400 the cap exists to prevent.
    Raises ``RuntimeError`` if the ranker hallucinated names that don't match any
    candidate. (Coderabbit on #203.)
    """
    by_name = {getattr(t, "name", ""): t for t in ranking_pool}
    shortlisted: List[StructuredTool] = []
    seen_short: Set[str] = set()
    for n in ranked_names:
        t = by_name.get(n)
        if t is not None and n not in seen_short:
            seen_short.add(n)
            shortlisted.append(t)
            if len(shortlisted) >= target_k:
                break

    if not shortlisted:
        raise RuntimeError(
            f"cuga_lite_bind_tools shortlister returned {len(ranked_names)} names but none "
            f"matched the {len(ranking_pool)} candidates (cap={max_count}, "
            f"query={query_text!r}, sample_ranked={ranked_names[:5]}). Shortlister LLM "
            f"hallucinated tool names — raise the cap, fix the shortlister prompt, or "
            f"refine the query."
        )
    return shortlisted, seen_short


def _maybe_pad_to_cap(
    shortlisted: List[StructuredTool],
    *,
    ranking_pool: List[StructuredTool],
    seen_short: Set[str],
    target_k: int,
) -> int:
    """Opt-in padding (off by default) — measured regressions on m3 hockey otherwise.

    Padding pushes the model toward native ``tool_calls`` mode, which the code-mode
    flow doesn't fully exercise (measured: 0 tool calls vs 5-7 without padding).
    Users explicitly chasing "true mode=all" can opt in.
    """
    if not bind_tools_pad_to_cap_from_settings() or len(shortlisted) >= target_k:
        return 0
    padded_count = 0
    for t in ranking_pool:
        name = getattr(t, "name", "") or ""
        if not name or name in seen_short:
            continue
        seen_short.add(name)
        shortlisted.append(t)
        padded_count += 1
        if len(shortlisted) >= target_k:
            break
    return padded_count


async def apply_bind_tools_cap_and_merge(
    bound: List[StructuredTool],
    *,
    query: Optional[str],
    tool_provider: Optional[ToolProviderInterface],
    llm: Optional[BaseChatModel],
    max_count: int,
    include_find_tools: bool,
    tools_context_ref: Optional[Dict[str, Any]],
    mode: str,
    run_config: Any = None,
) -> List[StructuredTool]:
    """Enforce the provider-safe ``max_count`` and optionally merge ``find_tools``.

    Under cap → merge ``find_tools`` (when ``include_find_tools``) and return. Over cap →
    run the existing LLM shortlister (see :meth:`PromptUtils.shortlist_tool_names`) against
    ``query``, take top-K (reserving 1 slot for ``find_tools`` when applicable), and return
    the ranked subset.

    Raises ``RuntimeError`` with an actionable message when the cap is exceeded but
    shortlisting is impossible — no user query, shortlister failure, or empty ranking.
    Failing loudly is intentional: silent truncation would corrupt research/benchmark
    results that compare native tool-calling against text-mode.
    """
    bound_in_len = len(bound)
    (
        find_tools_tool,
        find_tools_name,
        find_tools_already_in_bound,
        bound,
    ) = _resolve_find_tools_overlay(
        bound,
        include_find_tools=include_find_tools,
        tools_context_ref=tools_context_ref,
    )

    keep_find_tools = include_find_tools and find_tools_tool is not None
    ranking_pool = _build_ranking_pool(
        bound,
        keep_find_tools=keep_find_tools,
        find_tools_name=find_tools_name,
        find_tools_already_in_bound=find_tools_already_in_bound,
    )

    def _append_find_tools(tools: List[StructuredTool]) -> List[StructuredTool]:
        if not keep_find_tools or find_tools_tool is None:
            return tools
        if find_tools_name in {getattr(t, "name", "") for t in tools}:
            return tools
        return [*tools, find_tools_tool]

    cap_disabled = max_count <= 0
    effective_count = len(ranking_pool) + (1 if keep_find_tools else 0)
    if cap_disabled or effective_count <= max_count:
        return _append_find_tools(ranking_pool)

    query_text = (query or "").strip()
    if not query_text:
        raise RuntimeError(
            f"cuga_lite_bind_tools_mode={mode!r} produced {bound_in_len} tools but the "
            f"provider-safe cap (cuga_lite_bind_tools_max_count) is {max_count}. "
            f"Shortlisting requires a non-empty user query, but none was provided. Options: "
            f"(a) ensure the first user message is non-empty so the shortlister can run, "
            f"(b) raise the cap via DYNACONF_ADVANCED_FEATURES__CUGA_LITE_BIND_TOOLS_MAX_COUNT "
            f"for permissive backends (WatsonX, Anthropic via LiteLLM), or "
            f"(c) set the cap to 0 to disable (Groq/OpenAI will reject)."
        )

    reserve = 1 if keep_find_tools else 0
    target_k = max_count - reserve
    if target_k <= 0:
        return _append_find_tools([])

    ranked_names = await _run_shortlister(
        query_text,
        ranking_pool=ranking_pool,
        tool_provider=tool_provider,
        llm=llm,
        top_k=target_k,
        mode=mode,
        max_count=max_count,
        run_config=run_config,
    )
    shortlisted, seen_short = _materialize_shortlist(
        ranked_names,
        ranking_pool=ranking_pool,
        target_k=target_k,
        query_text=query_text,
        max_count=max_count,
    )
    padded_count = _maybe_pad_to_cap(
        shortlisted,
        ranking_pool=ranking_pool,
        seen_short=seen_short,
        target_k=target_k,
    )
    shortlisted = _append_find_tools(shortlisted)
    logger.info(
        "bind_tools cap: shortlisted to {} tools (mode={}, cap={}, ranked={}, padded={}, "
        "include_find_tools={}, top_ranked={})",
        len(shortlisted),
        mode,
        max_count,
        len(ranked_names),
        padded_count,
        find_tools_tool is not None,
        ranked_names[:5],
    )
    return shortlisted
