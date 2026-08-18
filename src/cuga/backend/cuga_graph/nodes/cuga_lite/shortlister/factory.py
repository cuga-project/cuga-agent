"""Build a strategy from a resolved :class:`ShortlisterPlan`.

Name resolution follows the repo's existing idiom: a bare name selects a
built-in, anything containing a dot is loaded as a class path via
``cuga.config.get_class`` — the same mechanism as
``page_understanding.transformer_path``.

Instances are cached per plan signature so the embedding model's weights load
once per process rather than once per call.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import (
    ShortlistRequest,
    ShortlistResult,
    ShortlisterStrategy,
    ShortlisterUnavailableError,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding import EmbeddingShortlister
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.hybrid import HybridShortlister
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm import LLMShortlister
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.plan import (
    BUILTIN_STRATEGIES,
    ShortlisterPlan,
    ShortlisterRouter,
)

_INSTANCES: Dict[str, ShortlisterStrategy] = {}


def clear_instance_cache() -> None:
    """Drop cached strategy instances. Tests only."""
    _INSTANCES.clear()


def _build_llm(plan: ShortlisterPlan) -> LLMShortlister:
    return LLMShortlister()


def _build_embedding(plan: ShortlisterPlan) -> EmbeddingShortlister:
    return EmbeddingShortlister(
        model_name=plan.embedding_model,
        provider=plan.embedding_provider,
        query_weight=plan.query_weight,
        min_score=plan.min_score,
    )


def _build_hybrid(plan: ShortlisterPlan) -> HybridShortlister:
    return HybridShortlister(embedding=_build_embedding(plan), llm=_build_llm(plan))


_BUILDERS: Dict[str, Callable[[ShortlisterPlan], Any]] = {
    "llm": _build_llm,
    "embedding": _build_embedding,
    "hybrid": _build_hybrid,
}


def _build_from_dotted_path(path: str, plan: ShortlisterPlan) -> Any:
    """Instantiate a user-supplied strategy class.

    Passes ``plan=`` when the constructor accepts it, so custom strategies can
    read configuration. The signature is *inspected* rather than probed with
    ``try/except TypeError``: a ``TypeError`` raised inside a constructor that
    does accept ``plan`` would otherwise be misread as "does not accept plan",
    silently building an unconfigured strategy or masking the original error.
    """
    import inspect

    from cuga.config import get_class

    cls = get_class(path)
    try:
        parameters = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        # Un-introspectable callable (C extension, unusual __init__); assume no plan.
        return cls()
    accepts_plan = "plan" in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )
    return cls(plan=plan) if accepts_plan else cls()


def resolve_shortlister(plan: ShortlisterPlan) -> ShortlisterStrategy:
    """Return the strategy described by ``plan``, cached where possible."""
    if plan.instance is not None:
        return plan.instance

    key = plan.cache_key()
    cached = _INSTANCES.get(key)
    if cached is not None:
        return cached

    strategy_name = plan.strategy
    if strategy_name in _BUILDERS:
        strategy = _BUILDERS[strategy_name](plan)
    elif "." in strategy_name:
        try:
            strategy = _build_from_dotted_path(strategy_name, plan)
        except Exception as e:
            logger.error(
                "Could not load custom shortlister {!r}: {}. Falling back to {!r}.",
                strategy_name,
                e,
                plan.fallback_strategy,
            )
            strategy = _BUILDERS.get(plan.fallback_strategy, _build_llm)(plan)
    else:
        # ShortlisterRouter normally rewrites unknown names; this covers a plan
        # constructed directly in code.
        raise ValueError(
            f"Unknown shortlister strategy {strategy_name!r}. Expected one of "
            f"{', '.join(BUILTIN_STRATEGIES)} or a dotted class path."
        )

    _INSTANCES[key] = strategy
    return strategy


async def run_shortlister(plan: ShortlisterPlan, request: ShortlistRequest) -> ShortlistResult:
    """Run the planned strategy, degrading to the fallback if it cannot run.

    Only :class:`ShortlisterUnavailableError` triggers the fallback — a missing
    model or dependency. Genuine ranking failures propagate so each call site
    keeps its own error contract (``find_tools`` turns them into a message for
    the agent; the bind-time cap raises).
    """
    strategy = resolve_shortlister(plan)
    try:
        return await strategy.shortlist(request)
    except ShortlisterUnavailableError as e:
        fallback_name = plan.fallback_strategy
        if fallback_name == plan.strategy or fallback_name not in _BUILDERS:
            raise
        logger.warning(
            "Shortlister {!r} unavailable ({}); using {!r} for this call.",
            plan.strategy,
            e,
            fallback_name,
        )
        return await _BUILDERS[fallback_name](plan).shortlist(request)


async def warm_tool_vectors(tools: Any, *, configurable: Optional[Dict[str, Any]] = None) -> int:
    """Embed ``tools`` into the shortlister cache ahead of the first query.

    A no-op unless a cosine-backed strategy is configured, so the default LLM
    deployment pays nothing. Intended for server mode — at startup and whenever
    the tool catalogue changes — where falling back to the LLM on the first
    ``find_tools`` after boot would be a visible regression. The SDK stays lazy.

    Never raises: warming is an optimization, and a server must start even when
    the embedding model cannot load.
    """
    from cuga.config import settings

    try:
        plan = ShortlisterRouter.resolve(settings, seam="discovery", configurable=configurable)
        if plan.is_llm_only:
            return 0
        strategy = resolve_shortlister(plan)
        warm = getattr(strategy, "warm", None)
        if warm is None:
            return 0
        embedded = await warm(list(tools or []))
        if embedded:
            logger.info("Shortlister: embedded {} tool document(s) (strategy={})", embedded, plan.strategy)
        return embedded
    except Exception as e:
        logger.warning("Shortlister warm-up skipped: {}", e)
        return 0
