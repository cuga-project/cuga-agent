"""``tool_search`` — load deferred tools on demand, the way Claude Code's ``ToolSearch`` does.

In function-calling mode with tool search active, registry tools are *deferred*:
their schemas are not bound until the model asks for them. ``tool_search``
returns at most ``max_results`` (default 5) tools as compact signatures; the
next ``call_model`` binds exactly those schemas (``bind_tools/deferred.py``),
and they stay bound for the rest of the conversation.

Resolution is a cascade, cheapest first; the first confident stage wins:

1. ``select:a,b``   exact names (unknown names get close matches, never the full list)
2. exact tool name  the whole query is a tool name
3. app narrowing    ``app_name`` given, or the query names an app → that app's tools only
4. keyword          Claude Code's deterministic scorer (``shortlister/keyword.py``)
5. embedding        local cosine ranking, fused with the keyword ranks (skipped if unavailable)
6. LLM              the configured discovery shortlister — only when keyword is not
                    confident and embedding is unavailable or disagrees with it, per
                    ``cuga_lite_tool_search_llm_fallback``

See ``docs/design/fc-deferred-tool-search.md`` §3.3.
"""

from __future__ import annotations

import asyncio
import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import wire_name
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.signature_render import (
    DEFAULT_SIGNATURE_MAX_CHARS,
    render_signature,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import keyword

TOOL_SEARCH_NAME = "tool_search"


LLM_FALLBACK_NEVER = "never"
LLM_FALLBACK_LOW_CONFIDENCE = "low_confidence"
LLM_FALLBACK_ALWAYS = "always"
_LLM_FALLBACK_MODES = (LLM_FALLBACK_NEVER, LLM_FALLBACK_LOW_CONFIDENCE, LLM_FALLBACK_ALWAYS)

# Apps whose aggregate keyword score leads are searched first when no app is named;
# the pool handed to the embedding / LLM stages is capped so a single ranking
# prompt can never hold the whole catalogue (the ~473-tool AppWorld overflow).
MAX_POOL_APPS = 3
MAX_LLM_POOL = 60
_RRF_K = 60

TOOL_SEARCH_DESCRIPTION = (
    "Load tools so you can call them. Most tools are not loaded yet: search for what you need, "
    "and the matching tools (at most max_results) become directly callable on your next step and "
    "stay loaded. Query forms: \"select:<name>[,<name>...]\" loads exact tools by name; otherwise "
    "describe the action in a few keywords (prefix a word with + to require it), and pass app_name "
    "when you know which app has the tool."
)


class ToolSearchInput(BaseModel):
    query: str = Field(
        ...,
        description='"select:<name>[,<name>...]" for exact tools, or keywords describing the action needed',
    )
    app_name: Optional[str] = Field(default=None, description="Restrict the search to this app")
    max_results: Optional[int] = Field(default=None, ge=1, le=10, description="Tools to load (default 5)")


@dataclass
class ToolSearchConfig:
    max_results: int = 5
    llm_fallback: str = LLM_FALLBACK_LOW_CONFIDENCE
    min_score: int = 10
    margin_ratio: float = 0.2
    #: Wall-clock budget for one search. Must stay under tool_exec's per-call timeout
    #: (``sandbox_execution_timeout``, 30s): when the embedding / LLM stages run out of
    #: time the search degrades to the cheaper stages' ranking instead of failing.
    time_budget_s: float = 20.0
    signature_max_chars: int = DEFAULT_SIGNATURE_MAX_CHARS

    @classmethod
    def from_settings(cls, configurable: Optional[Dict[str, Any]] = None) -> "ToolSearchConfig":
        from cuga.config import settings

        af = settings.advanced_features
        cfg = configurable or {}

        def pick(key: str, default: Any) -> Any:
            return cfg[key] if key in cfg else getattr(af, key, default)

        fallback = (
            str(pick("cuga_lite_tool_search_llm_fallback", LLM_FALLBACK_LOW_CONFIDENCE)).strip().lower()
        )
        if fallback not in _LLM_FALLBACK_MODES:
            logger.warning("Unknown cuga_lite_tool_search_llm_fallback {!r}; using low_confidence", fallback)
            fallback = LLM_FALLBACK_LOW_CONFIDENCE
        return cls(
            max_results=int(pick("cuga_lite_tool_search_max_results", 5) or 5),
            llm_fallback=fallback,
            min_score=int(pick("cuga_lite_tool_search_min_score", 10)),
            margin_ratio=float(pick("cuga_lite_tool_search_margin_ratio", 0.2)),
            time_budget_s=float(pick("cuga_lite_tool_search_time_budget_s", 20.0)),
            signature_max_chars=int(pick("cuga_lite_signature_max_chars", DEFAULT_SIGNATURE_MAX_CHARS)),
        )


@dataclass
class ToolSearchResult:
    """What ``tool_search`` returns; ``tool_exec`` turns it into a ``ToolMessage``
    whose ``artifact`` carries ``discovered`` for the next bind."""

    text: str
    discovered: List[str] = field(default_factory=list)
    stage: str = ""
    scores: Dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.text

    def artifact(self) -> Dict[str, Any]:
        return {
            "tool_search": True,
            "discovered": list(self.discovered),
            "stage": self.stage,
            "scores": self.scores,
        }


def _app_of(tool: Any) -> str:
    return keyword._tool_app(tool)


class ToolSearchIndex:
    """The searchable (deferred) catalogue: tools by name, grouped by app, keyword-indexed once."""

    def __init__(self, tools: Iterable[StructuredTool], apps: Optional[Sequence[Any]] = None):
        self.by_name: Dict[str, StructuredTool] = {}
        for tool in tools:
            name = getattr(tool, "name", None)
            if name and name not in self.by_name:
                self.by_name[name] = tool
        self.by_app: Dict[str, List[StructuredTool]] = {}
        for tool in self.by_name.values():
            self.by_app.setdefault(_app_of(tool), []).append(tool)
        self.apps = list(apps or [])
        # Model-facing names are wire names (provider limit); accept both forms back.
        self.by_wire: Dict[str, str] = {wire_name(n): n for n in self.by_name}
        self.keyword_index = {name: keyword.index_tool(t) for name, t in self.by_name.items()}

    def resolve(self, name: str) -> Optional[str]:
        """Registered name for a registered or wire name, else ``None``."""
        return name if name in self.by_name else self.by_wire.get(name)

    @property
    def tools(self) -> List[StructuredTool]:
        return list(self.by_name.values())

    def app_names(self) -> List[str]:
        return [a for a in self.by_app if a]

    def close_names(self, name: str, n: int = 3) -> List[str]:
        return difflib.get_close_matches(name, list(self.by_name), n=n, cutoff=0.5)

    def app_mentioned(self, query: str) -> Optional[str]:
        """An app named verbatim in the query (``hockey coaches`` → ``hockey``)."""
        words = set(keyword.split_name(query))
        for app in sorted(self.app_names(), key=len, reverse=True):
            parts = keyword.split_name(app)
            if parts and all(p in words for p in parts):
                return app
        return None


def _is_confident(ranked: Sequence[Tuple[str, float]], limit: int, config: ToolSearchConfig) -> bool:
    """Top score clears ``min_score`` and leads the first result that was cut by ``margin_ratio``
    of itself. Relative, not absolute: long queries give large raw scores, so an absolute
    gap does not separate hits from misses (AUC 0.83 vs 0.85 relative on the M3 REST
    catalogue; see scripts/tool_search_calibration.py)."""
    if not ranked:
        return False
    top = ranked[0][1]
    first_cut = ranked[limit][1] if len(ranked) > limit else 0
    return top >= config.min_score and top > 0 and (top - first_cut) / top >= config.margin_ratio


def _pool_for(
    index: ToolSearchIndex, query: str, app_name: Optional[str]
) -> Tuple[List[StructuredTool], str]:
    """Stage 3: the tools a query is ranked over, and a note for the model."""
    if app_name:
        if app_name in index.by_app:
            return list(index.by_app[app_name]), ""
        close = difflib.get_close_matches(app_name, index.app_names(), n=3, cutoff=0.4)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        return index.tools, f"Unknown app {app_name!r}; searched all apps.{hint}"
    mentioned = index.app_mentioned(query)
    if mentioned:
        return list(index.by_app[mentioned]), ""
    return index.tools, ""


def _top_apps_pool(index: ToolSearchIndex, ranked_all: Sequence[Tuple[str, float]]) -> List[StructuredTool]:
    """No app named: the apps whose tools score best, CugaLite's two-stage (app → tool) shape."""
    app_score: Dict[str, float] = {}
    for name, score in ranked_all:
        app = _app_of(index.by_name[name])
        app_score[app] = app_score.get(app, 0) + score
    top_apps = sorted(app_score, key=lambda a: -app_score[a])[:MAX_POOL_APPS]
    pool: List[StructuredTool] = []
    for app in top_apps:
        pool.extend(index.by_app.get(app, []))
    return pool


def _fuse(*rankings: Sequence[str]) -> List[str]:
    """Reciprocal-rank fusion: robust to incomparable score scales."""
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for pos, name in enumerate(ranking):
            scores[name] = scores.get(name, 0.0) + 1.0 / (_RRF_K + pos + 1)
    return sorted(scores, key=lambda n: -scores[n])


async def _run_strategy(
    strategy: str,
    query: str,
    pool: List[StructuredTool],
    apps: Sequence[Any],
    *,
    limit: int,
    llm: Any,
    run_config: Any,
    task_context: Optional[str],
    configurable: Optional[Dict[str, Any]],
) -> Optional[List[str]]:
    """Ranked names from a shortlister strategy, or ``None`` if it cannot run.

    ``fallback_strategy`` is pinned to the same strategy so an unavailable
    embedding model is *skipped* here — never silently replaced by an LLM call.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
        ShortlistRequest,
        ShortlisterRouter,
        run_shortlister,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import ShortlisterUnavailableError
    from cuga.config import settings

    if strategy == "llm" and llm is None:
        return None
    plan = ShortlisterRouter.resolve(settings, seam="discovery", configurable=configurable)
    plan = plan.model_copy(update={"strategy": strategy, "fallback_strategy": strategy, "instance": None})
    try:
        result = await run_shortlister(
            plan,
            ShortlistRequest(
                query=query,
                tools=pool,
                apps=list(apps),
                task_context=task_context,
                top_k=limit,
                max_results=limit,
                llm=llm,
                run_config=run_config,
            ),
        )
    except ShortlisterUnavailableError as exc:
        logger.info("tool_search: {} stage unavailable ({})", strategy, exc)
        return None
    except Exception as exc:
        logger.warning("tool_search: {} stage failed: {}", strategy, exc)
        return None
    pool_names = {t.name for t in pool}
    return [c.name for c in result.candidates if c.name in pool_names]


async def search(
    index: ToolSearchIndex,
    query: str,
    *,
    app_name: Optional[str] = None,
    max_results: Optional[int] = None,
    config: Optional[ToolSearchConfig] = None,
    llm: Any = None,
    run_config: Any = None,
    task_context: Optional[str] = None,
    configurable: Optional[Dict[str, Any]] = None,
) -> ToolSearchResult:
    """Run the cascade and render the result the model reads."""
    config = config or ToolSearchConfig()
    limit = max(1, min(int(max_results or config.max_results), 10))
    q = (query or "").strip()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(config.time_budget_s, 1.0)

    async def within_budget(stage_name: str, coro) -> Optional[List[str]]:
        remaining = deadline - loop.time()
        if remaining <= 0.5:
            coro.close()
            logger.info("tool_search: skipping {} stage, time budget spent", stage_name)
            return None
        try:
            return await asyncio.wait_for(coro, timeout=remaining)
        except asyncio.TimeoutError:
            logger.warning("tool_search: {} stage exceeded the time budget; using cheaper stages", stage_name)
            return None

    # 0. listing: ``select:*`` / bare ``select`` / ``*`` / empty — the model asking what exists
    # (M3/AppWorld: above the prompt's name-listing limit it only sees apps and counts).
    if _is_listing_query(q):
        return _render_listing(index, app_name or index.app_mentioned(q))

    # 1. select:
    if q.lower().startswith("select:"):
        requested = [s.strip() for s in q[len("select:") :].split(",") if s.strip()]
        found = list(dict.fromkeys(r for r in (index.resolve(n) for n in requested) if r))
        missing = [n for n in requested if not index.resolve(n)]
        if not missing:
            return _render(index, found, "select", {}, config, query=q)
        # Models guess names (``select:team_stats``). Instead of a dead end, search
        # the words of the guessed names and fill the remaining slots with the best
        # matches — the guess usually names the right concept, just not the tool.
        pool, _ = _pool_for(index, " ".join(missing), app_name)
        guess_terms = " ".join(" ".join(keyword.split_name(m)) for m in missing)
        near = [
            n
            for n, _ in keyword.rank(guess_terms, pool, max_results=limit, index=index.keyword_index)
            if n not in found
        ]
        loaded = (found + near)[:limit]
        notes = [
            f"Not found: {', '.join(missing)}." + (" Loaded the closest matches instead." if near else "")
        ]
        return _render(
            index, loaded, "select+keyword" if near else "select", {}, config, notes=notes, query=q
        )

    # 2. exact name
    if index.resolve(q):
        return _render(index, [index.resolve(q)], "exact", {}, config, query=q)

    # 3. app narrowing
    pool, pool_note = _pool_for(index, q, app_name)
    notes = [pool_note] if pool_note else []

    # 4. keyword
    ranked = keyword.rank(q, pool, max_results=None, index=index.keyword_index)
    ordered = [n for n, _ in ranked]
    scores = {n: float(s) for n, s in ranked}
    if _is_confident(ranked, limit, config) and config.llm_fallback != LLM_FALLBACK_ALWAYS:
        return _render(index, ordered, "keyword", scores, config, notes=notes, query=q, limit=limit)

    # No app named and nothing narrowed it: rank within the best-scoring apps only.
    if len(pool) == len(index.by_name) and ranked:
        pool = _top_apps_pool(index, ranked)

    # 5. embedding, fused with keyword ranks
    embedded = await within_budget("embedding", _run_strategy(
        "embedding", q, pool, index.apps, limit=limit, llm=llm, run_config=run_config,
        task_context=task_context, configurable=configurable,
    ))  # fmt: skip
    stage, fused = "keyword", ordered
    if embedded:
        stage, fused = "embedding", _fuse(ordered, embedded)
        scores = {}

    # 6. LLM — when the cheaper stages are not confident (or always, if configured).
    # Cosine scores are not comparable across embedding models, so the embedding stage
    # is judged by agreement instead of a threshold: it is trusted when the keyword
    # ranker's best pick is also in its top results, or when keyword found nothing to
    # disagree with. A disagreement is exactly the case worth one LLM call.
    embedding_agrees = bool(embedded) and (not ranked or ranked[0][0] in embedded[:limit])
    use_llm = config.llm_fallback == LLM_FALLBACK_ALWAYS or (
        config.llm_fallback == LLM_FALLBACK_LOW_CONFIDENCE and not embedding_agrees
    )
    if use_llm and llm is not None:
        llm_pool = pool
        if len(llm_pool) > MAX_LLM_POOL:
            keep = set(_fuse(ordered, embedded or [])[:MAX_LLM_POOL])
            llm_pool = [t for t in pool if t.name in keep] or pool[:MAX_LLM_POOL]
        ranked_llm = await within_budget("llm", _run_strategy(
            "llm", q, llm_pool, index.apps, limit=limit, llm=llm, run_config=run_config,
            task_context=task_context, configurable=configurable,
        ))  # fmt: skip
        if ranked_llm:
            # The LLM's picks lead, but never replace the list: it often returns one or
            # two tools, and a plausible-but-wrong single pick (M3 hockey #10) must not
            # hide the siblings the cheaper stages ranked. Fill the slots from them.
            merged = list(dict.fromkeys([*ranked_llm, *fused]))
            return _render(index, merged, "llm", {}, config, notes=notes, query=q, limit=limit)

    return _render(index, fused, stage, scores, config, notes=notes, query=q, limit=limit)


_LISTING_QUERIES = {
    "",
    "*",
    "select",
    "select:",
    "select:*",
    "list",
    "all",
    "list all",
    "list tools",
    "tools",
}

#: Names per app in a listing reply; large apps are split so one reply stays readable.
LISTING_MAX_NAMES = 150


def _is_listing_query(q: str) -> bool:
    return q.strip().lower() in _LISTING_QUERIES


def _render_listing(index: ToolSearchIndex, app_name: Optional[str]) -> ToolSearchResult:
    """Every tool name in one app (or the apps, when none is named). Loads nothing."""
    if not app_name or app_name not in index.by_app:
        apps = ", ".join(f"{a} ({len(t)})" for a, t in sorted(index.by_app.items()) if a)
        note = f"Unknown app {app_name!r}. " if app_name else ""
        return ToolSearchResult(
            text=f"{note}Apps (tool count): {apps}. Call tool_search with app_name and query \"select:*\" "
            "to list an app's tools, then select:<name> the ones you need.",
            stage="listing",
        )
    names = [wire_name(t.name) for t in index.by_app[app_name]]
    shown = names[:LISTING_MAX_NAMES]
    more = (
        f" (+{len(names) - len(shown)} more — search with keywords to narrow)"
        if len(names) > len(shown)
        else ""
    )
    return ToolSearchResult(
        text=f"{app_name} has {len(names)} tools{more}. Load the ones you need with select:<name>[,<name>...]:\n"
        + "\n".join(shown),
        stage="listing",
    )


def _render(
    index: ToolSearchIndex,
    names: Sequence[str],
    stage: str,
    scores: Dict[str, float],
    config: ToolSearchConfig,
    *,
    notes: Sequence[str] = (),
    query: str = "",
    limit: Optional[int] = None,
) -> ToolSearchResult:
    """``names`` is the result, or — with ``limit`` — a full ranking truncated to it."""
    names = [n for n in names if n in index.by_name]
    if limit is not None:
        names = names[:limit]
        scores = {n: scores[n] for n in names if n in scores}
    lines: List[str] = []
    if names:
        lines.append(
            f"Loaded {len(names)} tool{'s' if len(names) != 1 else ''} (callable on your next step):"
        )
        for name in names:
            lines.append(
                render_signature(
                    index.by_name[name], max_chars=config.signature_max_chars, display_name=wire_name(name)
                )
            )
    else:
        apps = ", ".join(index.app_names()[:20])
        lines.append(
            f"No matching tools for {query!r}. Try different keywords, pass app_name "
            f"(apps: {apps}), or use select:<name>."
        )
    lines.extend(n for n in notes if n)
    return ToolSearchResult(text="\n".join(lines), discovered=list(names), stage=stage, scores=scores)


def create_tool_search_tool(
    index: ToolSearchIndex,
    *,
    llm: Any = None,
    task_context: Optional[str] = None,
    configurable: Optional[Dict[str, Any]] = None,
) -> StructuredTool:
    """The native ``tool_search`` tool over ``index``."""
    config = ToolSearchConfig.from_settings(configurable)

    async def tool_search(query: str, app_name: Optional[str] = None, max_results: Optional[int] = None):
        from cuga.backend.cuga_graph.utils.langfuse_tracing import nested_langgraph_invoke_config

        return await search(
            index,
            query,
            app_name=app_name,
            max_results=max_results,
            config=config,
            llm=llm,
            run_config=nested_langgraph_invoke_config(),
            task_context=task_context,
            configurable=configurable,
        )

    return StructuredTool.from_function(
        coroutine=tool_search,
        name=TOOL_SEARCH_NAME,
        description=TOOL_SEARCH_DESCRIPTION,
        args_schema=ToolSearchInput,
    )


# ── prepare-time wiring ─────────────────────────────────────────────────────


def _setting(configurable: Optional[Dict[str, Any]], key: str, default: Any) -> Any:
    from cuga.config import settings

    cfg = configurable or {}
    if key in cfg and cfg[key] is not None:
        return cfg[key]
    return getattr(settings.advanced_features, key, default)


def pinned_tool_names(
    tools: Sequence[StructuredTool], configurable: Optional[Dict[str, Any]], model_name: str
) -> List[str]:
    """``cuga_lite_bind_tools_apps`` / ``_tool_names`` reinterpreted as "always load"."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import (
        _bind_include_find_tools_from_config,
        _bind_tools_apps_from_settings,
        _bind_tools_mode_from_settings,
        _bind_tools_tool_names_from_settings,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import resolve_bind_tools_fields

    _, apps, names, _ = resolve_bind_tools_fields(
        configurable,
        model_name,
        settings_mode_fn=_bind_tools_mode_from_settings,
        settings_apps_fn=_bind_tools_apps_from_settings,
        settings_tool_names_fn=_bind_tools_tool_names_from_settings,
        settings_include_fn=lambda: _bind_include_find_tools_from_config({}),
    )
    apps_set, names_set = set(apps), set(names)
    return [t.name for t in tools if t.name in names_set or _app_of(t) in apps_set]


def prepare_tool_search_surface(
    *,
    tools_for_execution: Sequence[StructuredTool],
    tools_for_prompt: Sequence[StructuredTool],
    tools_context: Dict[str, Any],
    apps: Sequence[Any],
    configurable: Optional[Dict[str, Any]],
    model_name: str,
    llm: Any,
    task_context: Optional[str],
    threshold: int,
):
    """Build the deferred surface + ``tool_search`` when tool search is active, else ``None``.

    Registers ``tool_search`` in ``tools_context`` so ``tool_exec`` can run it.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import (
        DEFAULT_LOADED_MAX,
        DEFAULT_LOADED_TOKEN_BUDGET,
        TOOL_SEARCH_MODE_AUTO,
        DeferredToolSurface,
        context_tokens_for,
        resolve_tool_search_mode,
        schema_tokens_for,
        tool_search_active,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import runtime_defaults_for_model

    execution = [t for t in tools_for_execution if getattr(t, "name", None) and t.name in tools_context]
    execution = list({t.name: t for t in execution}.values())
    exec_names = {t.name for t in execution}
    pinned = pinned_tool_names(execution, configurable, model_name)
    deferred = [t.name for t in execution if t.name not in set(pinned)]

    mode = resolve_tool_search_mode(configurable, runtime_defaults_for_model(model_name))
    schema_tokens = context_tokens = 0
    context_pct = float(_setting(configurable, "cuga_lite_tool_search_context_pct", 10) or 0)
    if mode == TOOL_SEARCH_MODE_AUTO and len(deferred) <= threshold and context_pct > 0:
        # Only measured when the count alone does not decide: large catalogues skip this.
        deferred_set = set(deferred)
        schema_tokens = schema_tokens_for(t for t in execution if t.name in deferred_set)
        context_tokens = context_tokens_for(model_name)
    if not tool_search_active(
        mode,
        len(deferred),
        threshold,
        schema_tokens=schema_tokens,
        context_tokens=context_tokens,
        context_pct=context_pct,
    ):
        logger.info(
            "tool_search inactive: mode={} deferrable={} threshold={} schema_tokens={} context={}",
            mode,
            len(deferred),
            threshold,
            schema_tokens,
            context_tokens,
        )
        return None

    index = ToolSearchIndex([t for t in execution if t.name in set(deferred)], apps)
    search_tool = create_tool_search_tool(
        index, llm=llm, task_context=task_context, configurable=configurable
    )
    tools_context[TOOL_SEARCH_NAME] = search_tool.coroutine

    # In-graph tools (todos, skills, agents, shell…) stay bound; find_tools is superseded.
    overlays = [
        t
        for t in tools_for_prompt
        if getattr(t, "name", None)
        and t.name not in exec_names
        and t.name not in ("find_tools", TOOL_SEARCH_NAME)
        and t.name in tools_context
    ]
    core = [TOOL_SEARCH_NAME, *dict.fromkeys(t.name for t in overlays)]
    tools_by_name: Dict[str, Any] = {t.name: t for t in execution}
    tools_by_name.update({t.name: t for t in overlays})
    tools_by_name[TOOL_SEARCH_NAME] = search_tool

    seed: List[str] = []
    seed_k = int(_setting(configurable, "cuga_lite_tool_search_seed_k", 0) or 0)
    if seed_k > 0 and task_context:
        # Deterministic, no model call: the keyword stage over the first user message.
        seed = [
            n
            for n, _ in keyword.rank(
                task_context, index.tools, max_results=min(seed_k, 10), index=index.keyword_index
            )
        ]

    surface = DeferredToolSurface(
        core=core,
        pinned=pinned,
        deferred=deferred,
        tools_by_name=tools_by_name,
        token_budget=int(
            _setting(configurable, "cuga_lite_tool_search_loaded_token_budget", DEFAULT_LOADED_TOKEN_BUDGET)
        ),
        max_loaded=int(_setting(configurable, "cuga_lite_tool_search_loaded_max", DEFAULT_LOADED_MAX)),
        seed=seed,
    )
    logger.info(
        "tool_search active: core={} pinned={} deferred={} seed={}",
        len(core),
        len(pinned),
        len(deferred),
        seed,
    )
    return surface, index


def render_tool_search_section(
    index: ToolSearchIndex,
    *,
    loaded: Iterable[str] = (),
    list_names_max: int = 150,
) -> str:
    """The FC prompt's ``FINDING TOOLS`` block: how to search, and what exists per app."""
    loaded_set = set(loaded)
    lines = [
        "# FINDING TOOLS",
        f"Most tools are not loaded. Call `{TOOL_SEARCH_NAME}` to load the ones you need "
        "(top 5 per search); loaded tools are callable on your next step and stay loaded.",
        "- select:<name>[,<name>...]   load exact tools by name",
        "- <keywords> [+required]      keyword search; pass app_name when you know the app",
        "- select:* with app_name      list every tool name in that app",
        "A capability is almost never missing: if a search does not show the tool you need, search again "
        "with the resource's name (e.g. orders, wish list, files, contacts) or list the app's tools — "
        "do not tell the user a tool does not exist after one or two searches.",
        "",
        "Apps (loaded/total tools):",
    ]
    for app, tools in sorted(index.by_app.items()):
        count_loaded = sum(1 for t in tools if t.name in loaded_set)
        lines.append(f"- {app or '(no app)'} ({count_loaded}/{len(tools)})")
    if len(index.by_name) <= list_names_max:
        lines.append("")
        lines.append("Tool names by app (use select:<name> to load):")
        for app, tools in sorted(index.by_app.items()):
            lines.append(f"- {app or '(no app)'}: {', '.join(wire_name(t.name) for t in tools)}")
    return "\n".join(lines)
