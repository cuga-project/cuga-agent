"""Deferred tool binding for function-calling mode — Claude Code's loading model.

Every model call binds ``core ∪ pinned ∪ loaded``:

- **core**: ``tool_search`` and in-graph tools (todos, skills, agents, shell…);
- **pinned**: ``cuga_lite_bind_tools_apps`` / ``cuga_lite_bind_tools_tool_names``;
- **loaded**: deferred (registry) tools the model discovered — via ``tool_search``
  results, or by successfully calling one directly — derived from the message
  history like free-code's ``extractDiscoveredToolNames``, plus the
  ``discovered_tools`` state carry that survives summarization.

``loaded`` is bounded by a minified-schema **token budget** and a count
backstop; over either, the least-recently-used loaded tools are unbound
(core and pinned never are). Nothing here calls a model: the per-turn
shortlister round-trip of the legacy cap path is gone.

See ``docs/design/fc-deferred-tool-search.md`` §3.1-3.6.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from loguru import logger

TOOL_SEARCH_MODE_OFF = "off"
TOOL_SEARCH_MODE_AUTO = "auto"
TOOL_SEARCH_MODE_ON = "on"
_TOOL_SEARCH_MODES = (TOOL_SEARCH_MODE_OFF, TOOL_SEARCH_MODE_AUTO, TOOL_SEARCH_MODE_ON)

#: ``lc_bind_tools_meta`` key holding the :class:`DeferredToolSurface` built by prepare.
META_KEY = "_lc_tool_search_surface"

DEFAULT_LOADED_TOKEN_BUDGET = 6000
DEFAULT_LOADED_MAX = 20


def normalize_tool_search_mode(raw: Any) -> str:
    key = str(raw if raw is not None else "").strip().lower()
    if key in _TOOL_SEARCH_MODES:
        return key
    if key in ("true", "1", "yes"):
        return TOOL_SEARCH_MODE_ON
    if key in ("false", "0", "no", "none"):
        return TOOL_SEARCH_MODE_OFF
    if key:
        logger.warning("Unknown cuga_lite_tool_search {!r}; using 'auto'", raw)
    return TOOL_SEARCH_MODE_AUTO


def resolve_tool_search_mode(
    configurable: Optional[Dict[str, Any]], profile: Optional[Dict[str, Any]] = None
) -> str:
    """``configurable`` → model profile → settings, like the other FC knobs."""
    from cuga.config import settings

    for source in (configurable or {}, profile or {}):
        if source.get("cuga_lite_tool_search") is not None:
            return normalize_tool_search_mode(source["cuga_lite_tool_search"])
    return normalize_tool_search_mode(getattr(settings.advanced_features, "cuga_lite_tool_search", "auto"))


def tool_search_active(
    mode: str,
    deferrable_count: int,
    threshold: int,
    *,
    schema_tokens: int = 0,
    context_tokens: int = 0,
    context_pct: float = 0,
) -> bool:
    """``on``: any deferrable tool. ``auto``: more than ``threshold`` tools, **or** their
    minified schemas would take ``context_pct`` % of the context window — Claude Code's
    ``tst-auto`` rule (10% by default), so a few enormous schemas defer too."""
    if mode == TOOL_SEARCH_MODE_ON:
        return deferrable_count > 0
    if mode == TOOL_SEARCH_MODE_AUTO:
        if deferrable_count > threshold:
            return True
        if deferrable_count and context_pct > 0 and context_tokens > 0:
            return schema_tokens >= context_tokens * context_pct / 100
    return False


def schema_tokens_for(tools: Iterable[Any]) -> int:
    """Estimated tokens of the minified wire schemas ``tools`` would bind."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.minify import minified_tools

    total = 0
    for tool in tools:
        try:
            total += estimate_tokens(minified_tools([tool])[0])
        except Exception:
            continue
    return total


def context_tokens_for(model_name: str) -> int:
    from cuga.backend.cuga_graph.utils.token_counter import DEFAULT_CONTEXT_SIZE, lookup_model_context_size

    return lookup_model_context_size(model_name) or DEFAULT_CONTEXT_SIZE


def estimate_tokens(spec: Dict[str, Any]) -> int:
    """chars/4 — Claude Code's fallback estimate when no token counter is available."""
    return max(1, len(json.dumps(spec, separators=(",", ":"), ensure_ascii=False)) // 4)


@dataclass
class DeferredToolSurface:
    """Built once by prepare; read by call_model (bind) and tool_exec (hints, promotion)."""

    core: List[str]
    pinned: List[str]
    deferred: List[str]
    tools_by_name: Dict[str, Any]
    token_budget: int = DEFAULT_LOADED_TOKEN_BUDGET
    max_loaded: int = DEFAULT_LOADED_MAX
    seed: List[str] = field(default_factory=list)
    _spec_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict, repr=False)
    _deferred_set: frozenset = field(init=False, repr=False)
    _wire_to_real: Dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import wire_name

        self._deferred_set = frozenset(self.deferred)
        self._wire_to_real = {wire_name(n): n for n in self.tools_by_name}

    def to_real(self, name: str) -> str:
        """Registered name for a name the model used (wire names are ≤64 chars)."""
        return name if name in self.tools_by_name else self._wire_to_real.get(name, name)

    @staticmethod
    def to_wire(name: str) -> str:
        from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import wire_name

        return wire_name(name)

    def is_deferred(self, name: str) -> bool:
        return name in self._deferred_set

    def spec(self, name: str) -> Optional[Dict[str, Any]]:
        """Minified OpenAI-format spec, memoized per tool."""
        if name not in self._spec_cache:
            tool = self.tools_by_name.get(name)
            if tool is None:
                return None
            from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.minify import minified_tools

            try:
                self._spec_cache[name] = minified_tools([tool])[0]
            except Exception as exc:
                logger.warning("Could not build a wire schema for {}: {}", name, exc)
                return None
        return self._spec_cache[name]


# ── discovery from history ──────────────────────────────────────────────────


def _role(message: Any) -> str:
    return str(
        getattr(message, "type", None) or (message.get("type") if isinstance(message, dict) else "") or ""
    )


def _field(message: Any, name: str) -> Any:
    return getattr(message, name, None) if not isinstance(message, dict) else message.get(name)


def discovered_in_history(messages: Iterable[Any], deferred: Optional[Iterable[str]] = None) -> List[str]:
    """Deferred tools the model has loaded, in first-discovery order.

    A tool counts as loaded when a ``tool_search`` result listed it (the
    ``ToolMessage.artifact["discovered"]``), or when the model called it
    directly and the call succeeded — an undiscovered call only runs once its
    arguments validate, so success implies the model already knows its shape.
    """
    deferred_set = set(deferred) if deferred is not None else None
    seen: Dict[str, None] = {}
    for message in messages or []:
        if _role(message) != "tool":
            continue
        artifact = _field(message, "artifact")
        if isinstance(artifact, dict) and artifact.get("tool_search"):
            for name in artifact.get("discovered") or []:
                seen.setdefault(str(name), None)
            continue
        name = _field(message, "name")
        if name and _field(message, "status") != "error" and (deferred_set is None or name in deferred_set):
            seen.setdefault(str(name), None)
    return list(seen)


def last_used_positions(messages: Sequence[Any]) -> Dict[str, int]:
    """Tool name → index of the last assistant turn that called it (for LRU)."""
    positions: Dict[str, int] = {}
    for i, message in enumerate(messages or []):
        for call in _field(message, "tool_calls") or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if name:
                positions[str(name)] = i
    return positions


def merge_ordered(*groups: Iterable[str]) -> List[str]:
    out: Dict[str, None] = {}
    for group in groups:
        for name in group or []:
            out.setdefault(str(name), None)
    return list(out)


# ── the bind set ────────────────────────────────────────────────────────────


def loaded_within_budget(
    surface: DeferredToolSurface,
    discovered: Sequence[str],
    messages: Sequence[Any],
) -> Tuple[List[str], List[str]]:
    """``(loaded, evicted)``: discovered deferred tools that fit the budget.

    Least-recently-used go first: a tool's recency is its last call, or its
    discovery position when it was never called. Result keeps discovery order,
    so the bound list only ever grows at the tail (prompt-cache friendly).
    """
    candidates = [n for n in discovered if surface.is_deferred(n) and n in surface.tools_by_name]
    if not candidates:
        return [], []
    used = {surface.to_real(n): i for n, i in last_used_positions(messages).items()}
    # Rank by recency: last call position, else discovery order (older = smaller).
    recency = {n: (used.get(n, -1), i) for i, n in enumerate(candidates)}
    by_recency = sorted(candidates, key=lambda n: recency[n], reverse=True)

    keep: List[str] = []
    spent = 0
    for name in by_recency:
        spec = surface.spec(name)
        cost = estimate_tokens(spec) if spec else 0
        if len(keep) >= surface.max_loaded or (keep and spent + cost > surface.token_budget):
            continue
        keep.append(name)
        spent += cost
    kept = set(keep)
    loaded = [n for n in candidates if n in kept]
    evicted = [n for n in candidates if n not in kept]
    return loaded, evicted


def bind_names(
    surface: DeferredToolSurface,
    messages: Sequence[Any],
    state_discovered: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[str]]:
    """``(names to bind, evicted)`` for this model call, deterministic order."""
    discovered = merge_ordered(
        surface.seed,
        state_discovered or [],
        discovered_in_history(messages, surface.deferred),
    )
    loaded, evicted = loaded_within_budget(surface, discovered, messages)
    names = merge_ordered(surface.core, surface.pinned, loaded)
    return [n for n in names if n in surface.tools_by_name], evicted


def bind_deferred(model: Any, surface: DeferredToolSurface, names: Sequence[str]) -> Any:
    """Bind the minified specs for ``names`` (falls back to the unbound model like ``_safe_bind``)."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import _safe_bind

    specs = [s for s in (surface.spec(n) for n in names) if s]
    if not specs:
        return model
    return _safe_bind(model, specs)


def surface_from_meta(meta: Optional[Dict[str, Any]]) -> Optional[DeferredToolSurface]:
    surface = (meta or {}).get(META_KEY)
    return surface if isinstance(surface, DeferredToolSurface) else None


# ── "searched but never called a tool" (one-shot correction) ─────────────────

SEARCH_ONLY_CORRECTION_KEY = "fc_search_only_retry"

SEARCH_ONLY_CORRECTION = (
    "You searched for tools but have not called any of them, so your answer is not based on data. "
    "Loaded tools you can call now: {loaded}. Before concluding the data is unavailable, call the "
    "loaded tool most likely to answer — descriptions often name the underlying data field rather "
    "than your wording — or search again with different terms (synonyms, the field or statistic "
    "involved). Answer without data only if no tool can apply."
)


def search_only_turn(messages: Sequence[Any], turn_start: int) -> Optional[List[str]]:
    """Tools loaded this turn when the model searched but called nothing else, else ``None``.

    ``turn_start`` is the index of the user message that opened the turn. Only a
    turn that ran at least one ``tool_search`` which loaded something, and no
    other tool, qualifies — a model that answers plain chat without searching,
    or that called a tool and got an error, is never second-guessed here.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import TOOL_SEARCH_NAME

    loaded: Dict[str, None] = {}
    for message in list(messages)[turn_start:]:
        if _role(message) != "tool":
            continue
        if _field(message, "name") != TOOL_SEARCH_NAME:
            return None  # a domain tool was called this turn
        artifact = _field(message, "artifact")
        if isinstance(artifact, dict):
            for name in artifact.get("discovered") or []:
                loaded.setdefault(str(name), None)
    return list(loaded) or None
