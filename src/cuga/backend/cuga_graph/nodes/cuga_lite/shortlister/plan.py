"""Resolve shortlister settings into an explicit :class:`ShortlisterPlan`.

Mirrors ``cuga_agent_core/policy/execution_policy.py`` (``ExecutionRouter`` →
``ExecutionPlan``): settings in, one typed, log-visible object out.

Precedence, highest first:
  1. ``override``                       — a live object / dataclass from the SDK
  2. ``configurable["shortlister_*"]``  — per-invoke
  3. ``[shortlister.<seam>]``           — per-seam TOML
  4. ``[shortlister]``                  — global TOML
  5. module defaults below
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Seam = Literal["discovery", "bind_cap"]

BUILTIN_STRATEGIES = ("llm", "embedding", "hybrid")

# Defaults live here, not in settings.toml, so a missing section can never break
# resolution. settings.toml restates them for discoverability.
DEFAULT_STRATEGY = "llm"
DEFAULT_FALLBACK_STRATEGY = "llm"
DEFAULT_THRESHOLD = 128
DEFAULT_TOP_K = 128
DEFAULT_MAX_RESULTS = 10
DEFAULT_MIN_SCORE = 0.15
DEFAULT_QUERY_WEIGHT = 0.7
DEFAULT_EMBEDDING_PROVIDER = "local"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

_INT_FIELDS = ("threshold", "top_k", "max_results")
_FLOAT_FIELDS = ("min_score", "query_weight")
_STR_FIELDS = (
    "strategy",
    "fallback_strategy",
    "embedding_provider",
    "embedding_model",
)
ALL_FIELDS = _INT_FIELDS + _FLOAT_FIELDS + _STR_FIELDS

#: ``configurable`` keys, e.g. ``shortlister_strategy``.
CONFIGURABLE_PREFIX = "shortlister_"


class ShortlisterPlan(BaseModel):
    """Explicit description of how shortlisting will run for one seam."""

    seam: Seam = "discovery"
    strategy: str = DEFAULT_STRATEGY
    fallback_strategy: str = DEFAULT_FALLBACK_STRATEGY
    threshold: int = DEFAULT_THRESHOLD
    top_k: int = DEFAULT_TOP_K
    max_results: int = DEFAULT_MAX_RESULTS
    min_score: float = DEFAULT_MIN_SCORE
    query_weight: float = DEFAULT_QUERY_WEIGHT
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    #: Live strategy object injected via the SDK; bypasses ``strategy``.
    instance: Optional[Any] = Field(default=None, exclude=True)
    notes: List[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_llm_only(self) -> bool:
        """True when no cosine stage can run — i.e. today's behavior exactly."""
        return self.instance is None and self.strategy == "llm"

    def cache_key(self) -> str:
        """Identity for the strategy-instance cache.

        Must include every field passed to a strategy *constructor*, or two
        plans differing only in that field share one cached instance and the
        second silently keeps the first one's value. ``top_k`` and
        ``max_results`` are deliberately absent: they travel per request, not
        per instance.
        """
        return "|".join(
            [
                self.strategy,
                self.fallback_strategy,
                self.embedding_provider,
                self.embedding_model,
                f"qw={self.query_weight}",
                f"ms={self.min_score}",
            ]
        )


def _coerce(field_name: str, raw: Any) -> Any:
    """Coerce a settings/configurable value, returning ``None`` when unusable.

    Values arrive from TOML, env (always strings) and Python, so every field is
    coerced rather than trusted. A bad value is dropped in favour of the lower
    precedence source instead of raising — a typo in one key must not take the
    agent down.
    """
    if raw is None:
        return None
    try:
        if field_name in _INT_FIELDS:
            return int(raw)
        if field_name in _FLOAT_FIELDS:
            return float(raw)
        text = str(raw).strip()
        return text or None
    except (TypeError, ValueError):
        return None


def _layer(target: Dict[str, Any], source: Any, *, keys: Any = ALL_FIELDS, prefix: str = "") -> None:
    """Copy recognised, non-``None`` values from ``source`` onto ``target``."""
    if source is None:
        return
    for field_name in keys:
        lookup = f"{prefix}{field_name}"
        if isinstance(source, dict):
            if lookup not in source:
                continue
            raw = source.get(lookup)
        else:
            raw = getattr(source, lookup, None)
        value = _coerce(field_name, raw)
        if value is not None:
            target[field_name] = value


class ShortlisterRouter:
    """Resolves settings + overrides into a :class:`ShortlisterPlan`."""

    @staticmethod
    def resolve(
        settings: Any,
        *,
        seam: Seam = "discovery",
        configurable: Optional[Dict[str, Any]] = None,
        override: Optional[Any] = None,
    ) -> ShortlisterPlan:
        values: Dict[str, Any] = {}
        notes: List[str] = []

        section = getattr(settings, "shortlister", None)
        _layer(values, section)
        _layer(values, getattr(section, seam, None) if section is not None else None)
        _layer(values, configurable, prefix=CONFIGURABLE_PREFIX)

        instance = None
        if configurable:
            instance = configurable.get("shortlister_instance") or _instance_of(
                configurable.get("shortlister")
            )
            _layer(values, _as_mapping(configurable.get("shortlister")))

        if override is not None:
            instance = _instance_of(override) or instance
            _layer(values, _as_mapping(override))

        plan = ShortlisterPlan(seam=seam, instance=instance, notes=notes, **values)

        # Append to ``plan.notes``, not the local list — pydantic copies it on
        # validation, so mutating ``notes`` here would silently do nothing.
        if plan.instance is None and plan.strategy not in BUILTIN_STRATEGIES and "." not in plan.strategy:
            plan.notes.append(
                f"unknown shortlister strategy {plan.strategy!r}; falling back to "
                f"{DEFAULT_STRATEGY!r} (expected one of {', '.join(BUILTIN_STRATEGIES)} "
                f"or a dotted class path)"
            )
            plan.strategy = DEFAULT_STRATEGY
        if plan.top_k < 0:
            plan.top_k = 0
        if plan.max_results <= 0:
            plan.max_results = DEFAULT_MAX_RESULTS
        # Both are documented as bounded; a value outside the range silently
        # distorts ranking rather than failing, so clamp instead of trusting.
        plan.query_weight = min(max(plan.query_weight, 0.0), 1.0)
        plan.min_score = min(max(plan.min_score, -1.0), 1.0)
        return plan


def _instance_of(candidate: Any) -> Optional[Any]:
    """Extract a live strategy object from an SDK config or raw instance."""
    if candidate is None:
        return None
    inner = getattr(candidate, "instance", None)
    if inner is not None:
        return inner
    if hasattr(candidate, "shortlist"):
        return candidate
    return None


def _as_mapping(candidate: Any) -> Optional[Any]:
    """Return ``candidate`` if it can carry plan fields, else ``None``."""
    if candidate is None or hasattr(candidate, "shortlist"):
        return None
    return candidate
