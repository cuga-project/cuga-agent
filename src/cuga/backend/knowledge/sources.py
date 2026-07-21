# src/cuga/backend/knowledge/sources.py
"""Thread-scoped source ledger + citation resolution for the knowledge engine.

Deliberately imports nothing from ``engine.py`` — retrieval results are
duck-typed (``text/filename/page/scope/score/section_path`` attributes) so
this module stays cheap to import in unit tests and in the graph layer.

The ledger is the memory that makes citations survive multi-hop retrieval
and multi-turn conversations: every retrieved chunk is registered under a
content-identity hash and receives a thread-stable ``cite_id`` (``s1``,
``s2``, …). Re-retrieving the same chunk in any later hop or turn returns
the SAME id, which is what lets the LLM cite a source found three turns
ago. Display numbers ([1], [2]) are a per-message concern — assigned by
``resolve_citations`` at final-answer time, never stored here.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cuga.backend.knowledge.config import KnowledgeConfig

logger = logging.getLogger(__name__)


@dataclass
class SourceRecord:
    cite_id: str
    key: str
    scope: str
    filename: str
    page: int | None
    section_path: str
    text: str
    score: float
    query: str
    cited: bool = False

    def to_snapshot(self, n: int) -> dict[str, Any]:
        """Self-contained per-message source entry. Must render without the
        ledger, the collection, or the document still existing."""
        snap: dict[str, Any] = {
            "n": n,
            "cite_id": self.cite_id,
            "filename": self.filename,
            "page": self.page,
            "scope": self.scope,
            "snippet": self.text,
            "query": self.query,
        }
        if self.section_path:
            snap["section_path"] = self.section_path
        if self.score:
            snap["score"] = round(float(self.score), 4)
        return snap


def _content_key(scope: str, filename: str, page: int | None, text: str) -> str:
    text_h = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    raw = f"{scope}|{filename}|{page if page is not None else ''}|{text_h}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


class SourceLedger:
    """Per-thread registry of retrieved chunks.

    Thread-safe: retrieval runs on worker threads while resolution runs on
    the event loop.  cited-marking is lock-protected via ``mark_cited``; use
    that method from external code rather than setting ``record.cited``
    directly.
    """

    def __init__(self, max_records: int = 500):
        self._by_key: OrderedDict[str, SourceRecord] = OrderedDict()
        self._by_cite_id: dict[str, SourceRecord] = {}
        self._counter = 0
        self._max = max_records
        self._lock = threading.Lock()
        # cite_ids retrieved during the CURRENT turn. A marker only resolves to
        # a source retrieved this turn; ids from earlier turns (or rehydrated
        # from history) are stripped like a miss. Cleared by begin_turn().
        self._turn_ids: set[str] = set()

    def __len__(self) -> int:
        return len(self._by_key)

    def register(self, result: Any, *, query: str) -> str:
        """Record one retrieved chunk; return its stable cite_id."""
        text = getattr(result, "text", "") or ""
        filename = getattr(result, "filename", "") or ""
        page = getattr(result, "page", None)
        scope = getattr(result, "scope", "") or ""
        key = _content_key(scope, filename, page, text)
        with self._lock:
            existing = self._by_key.get(key)
            if existing is not None:
                # Refresh recency so a re-retrieved (still-uncited) chunk is not
                # the oldest and evicted before newer one-off chunks.
                self._by_key.move_to_end(key)
                # Re-retrieving this turn makes it current-turn evidence again.
                self._turn_ids.add(existing.cite_id)
                return existing.cite_id
            self._counter += 1
            record = SourceRecord(
                cite_id=f"s{self._counter}",
                key=key,
                scope=scope,
                filename=filename,
                page=page,
                section_path=getattr(result, "section_path", "") or "",
                text=text,
                score=float(getattr(result, "score", 0.0) or 0.0),
                query=query,
            )
            self._by_key[key] = record
            self._by_cite_id[record.cite_id] = record
            self._turn_ids.add(record.cite_id)
            self._evict_if_needed()
            return record.cite_id

    def begin_turn(self) -> None:
        """Start a new turn: forget which ids were retrieved last turn.

        Rehydration (restore) runs before this and deliberately does NOT add to
        the turn set, so a rehydrated prior-turn id stays out of scope until it
        is actually re-retrieved this turn.
        """
        with self._lock:
            self._turn_ids.clear()

    def retrieved_this_turn(self, cite_id: str) -> bool:
        with self._lock:
            return cite_id.lower() in self._turn_ids

    def get(self, cite_id: str) -> SourceRecord | None:
        return self._by_cite_id.get(cite_id.lower())

    def mark_cited(self, cite_id: str) -> None:
        """Set cited=True for cite_id under the ledger lock."""
        with self._lock:
            record = self._by_cite_id.get(cite_id.lower())
            if record is not None:
                record.cited = True

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Re-insert a persisted snapshot (restart rehydration). Keeps the
        original cite_id and bumps the counter past it so future ids never
        collide with ids already present in the on-disk conversation."""
        cite_id = str(snapshot.get("cite_id", "")).lower()
        m = re.fullmatch(r"s(\d+)", cite_id)
        if not m:
            return
        key = _content_key(
            snapshot.get("scope", "") or "",
            snapshot.get("filename", "") or "",
            snapshot.get("page"),
            snapshot.get("snippet", "") or "",
        )
        with self._lock:
            # Bump the counter regardless of duplicate status so future
            # register() calls never collide with ids already in the persisted
            # conversation.
            self._counter = max(self._counter, int(m.group(1)))
            if key in self._by_key or cite_id in self._by_cite_id:
                return
            record = SourceRecord(
                cite_id=cite_id,
                key=key,
                scope=snapshot.get("scope", "") or "",
                filename=snapshot.get("filename", "") or "",
                page=snapshot.get("page"),
                section_path=snapshot.get("section_path", "") or "",
                text=snapshot.get("snippet", "") or "",
                score=float(snapshot.get("score", 0.0) or 0.0),
                query=snapshot.get("query", "") or "",
                cited=True,
            )
            self._by_key[key] = record
            self._by_cite_id[cite_id] = record
            self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        # Called under self._lock. Evict oldest uncited first; cited records
        # are referenced by persisted messages' history semantics — keep them
        # as long as possible so re-citing stays possible.
        while len(self._by_key) > self._max:
            victim_key = None
            for k, rec in self._by_key.items():
                if not rec.cited:
                    victim_key = k
                    break
            if victim_key is None:  # everything cited — evict absolute oldest
                victim_key = next(iter(self._by_key))
            victim = self._by_key.pop(victim_key)
            self._by_cite_id.pop(victim.cite_id, None)


# --- module-level thread registry -------------------------------------------

_ledgers: OrderedDict[str, SourceLedger] = OrderedDict()
_registry_lock = threading.Lock()
_MAX_THREADS = 300


def get_ledger(thread_id: str, create: bool = True) -> SourceLedger | None:
    if not thread_id:
        return None
    thread_id = thread_id.strip()
    if not thread_id:
        return None
    with _registry_lock:
        ledger = _ledgers.get(thread_id)
        if ledger is not None:
            _ledgers.move_to_end(thread_id)
            return ledger
        if not create:
            return None
        ledger = SourceLedger()
        # Publish to registry before rehydration completes. Rehydration happens
        # at turn start in the server (_rehydrate_citation_ledger in main.py).
        _ledgers[thread_id] = ledger
        while len(_ledgers) > _MAX_THREADS:
            _ledgers.popitem(last=False)
    return ledger


def begin_ledger_turn(thread_id: str) -> None:
    """Reset the current-turn cite-id scope at the start of a NEW user turn.

    Call at every turn boundary that is NOT a HITL resume (server event_stream,
    SDK invoke). No-op when no ledger exists yet — the turn's first search
    creates one with an empty scope. create=False so plain chit-chat turns with
    citations disabled never allocate a ledger.
    """
    if not thread_id:
        return
    ledger = get_ledger(thread_id.strip(), create=False)
    if ledger is not None:
        ledger.begin_turn()


def drop_ledger(thread_id: str) -> None:
    if not thread_id:
        return
    thread_id = thread_id.strip()
    with _registry_lock:
        _ledgers.pop(thread_id, None)


def _reset_all_ledgers_for_tests() -> None:
    with _registry_lock:
        _ledgers.clear()


# --- citation resolution ------------------------------------------------------

# [s1] or [s1, s4] or [s1 s4] — case-insensitive. Requires the s-prefix so
# plain bracketed numbers/text ("[1]", "[note]") never match.
# Accepts the whole SQUARE-BRACKET FAMILY, because models drift from the ASCII
# [sN] contract: ASCII [ ], fullwidth ［ ］, lenticular 【 】, tortoise-shell 〔 〕.
# Resolution always rewrites the match to ASCII [n], so the frontend chip
# injector (which matches [n]) works regardless of which brackets the model used.
_MARKER_RE = re.compile(r"[\[［【〔]\s*([sS]\d+(?:[\s,]+[sS]\d+)*)\s*[\]］】〕]")
# Segments the answer so markers inside code are never rewritten.
# Handles: fenced blocks (``` or ~~~, terminated or unterminated/truncated),
# double-backtick inline spans, and single-backtick inline spans.
_CODE_RE = re.compile(
    r"(```[\s\S]*?```|~~~[\s\S]*?~~~|```[\s\S]*$|~~~[\s\S]*$|``[^`\n](?:[^`\n]|`[^`\n])*``|`[^`\n]*`)"
)

# Canary: a cite_id (sN) wrapped in a bracket style resolution does NOT rewrite.
# Logged (not resolved) so silent model-format drift stays VISIBLE in monitoring
# instead of producing a broken, unclickable citation — the exact failure the
# 【sN】 case caused. Excludes the square-bracket family (those ARE resolved), and
# the check runs on non-code segments only, since parens / angle / brace commonly
# appear in code (e.g. a ``foo(s1)`` call is not a citation).
_UNSUPPORTED_MARKER_RE = re.compile(r"[(<{〖｢«⟦]\s*[sS]\d+\s*[)>}〗｣»⟧]")


def has_citation_markers(text: str) -> bool:
    return bool(text) and _MARKER_RE.search(text) is not None


def _warn_unsupported_markers(text: str) -> None:
    """Log once if a cite_id appears in a bracket style we don't resolve."""
    for i, part in enumerate(_CODE_RE.split(text)):
        if i % 2:  # code segment — left byte-identical, never a citation
            continue
        m = _UNSUPPORTED_MARKER_RE.search(part)
        if m:
            logger.warning(
                "citation id in an unsupported bracket style, not rendered as a "
                "chip: %r — the model deviated from the [sN] contract; add the "
                "bracket to _MARKER_RE if this recurs.",
                m.group(0),
            )
            return


def resolve_citations(text: str, ledger: SourceLedger | None) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite ``[sN]`` markers into per-message display numbers ``[k]``.

    - Display numbers are assigned in order of first appearance.
    - Ids missing from the ledger (hallucinated, or evicted) are stripped.
    - Code fences (``` or ~~~, including unterminated/truncated ones),
      double-backtick inline spans, and single-backtick inline spans are
      left byte-identical.
    - Unknown markers are warned once per ``resolve_citations`` call.
    - Whitespace-separated id lists (``[s1 s3]``) are treated identically
      to comma-separated ones.
    Returns ``(display_text, sources_snapshots)``.
    """
    if not text:
        return text, []
    # Surface unrecognized-bracket drift even when NO supported marker exists.
    _warn_unsupported_markers(text)
    if not has_citation_markers(text):
        return text, []

    numbers: dict[str, int] = {}  # cite_id -> display n
    ordered: list[SourceRecord] = []
    warned: set[str] = set()

    def _sub(match: re.Match[str]) -> str:
        out = []
        for raw_id in re.split(r"[\s,]+", match.group(1)):
            cite_id = raw_id.strip().lower()
            if not cite_id:
                continue
            record = ledger.get(cite_id) if ledger is not None else None
            # A marker resolves only when its source was retrieved THIS turn.
            # Absent (hallucinated/evicted) OR from an earlier turn both strip:
            # citing text whose provenance we can't tie to this answer is worse
            # than no citation. Strip-mode (ledger is None) removes silently by
            # design. Split the two live-ledger strip reasons in the log so
            # monitoring can tell model hallucination from stale-id reuse.
            in_ledger = record is not None
            this_turn = in_ledger and ledger is not None and ledger.retrieved_this_turn(cite_id)
            if not this_turn:
                if ledger is not None and cite_id not in warned:
                    reason = "not from this turn's retrieval" if in_ledger else "not in ledger"
                    logger.warning("citation marker [%s] %s — stripped", cite_id, reason)
                    warned.add(cite_id)
                continue
            if cite_id not in numbers:
                numbers[cite_id] = len(numbers) + 1
                ledger.mark_cited(cite_id)
                ordered.append(record)
            out.append(f"[{numbers[cite_id]}]")
        return "".join(out)

    parts = _CODE_RE.split(text)
    resolved = "".join(part if i % 2 else _MARKER_RE.sub(_sub, part) for i, part in enumerate(parts))
    sources = [rec.to_snapshot(n=i + 1) for i, rec in enumerate(ordered)]
    return resolved, sources


# --- envelope stamping --------------------------------------------------------

# Rides on retrieval.reading_directive so the LLM sees it at composition time
# (the system-prompt contract is thousands of tokens upstream by then).
CITATION_DIRECTIVE = (
    " CITATIONS: each result carries a cite_id. In your FINAL text answer, "
    "append [<cite_id>] immediately after every claim taken from that chunk "
    "(example: 'The total is 4,521 [s2].'). Use ONLY cite_ids from THIS turn's "
    "search results; an id from an earlier turn no longer resolves. Use plain "
    "ASCII square brackets [ ], not 【 】 or other bracket "
    "styles. Never invent ids; never write bare numeric citations like [1]."
)


def annotate_envelope_with_citations(
    envelope: dict[str, Any],
    results: list[Any],
    *,
    thread_id: str | None,
    query: str,
) -> None:
    """Register results in the thread ledger and stamp ``cite_id`` onto the
    wire chunks (in place). No-op when thread_id is missing — a search that
    cannot be correlated to a conversation cannot be cited.

    ``envelope["results"][i]`` must correspond to ``results[i]`` (the
    invariant of build_retrieval_envelope). ``by_source`` holds the SAME
    chunk dict objects, so stamping results also stamps the grouped view.
    """
    if not thread_id or not results:
        if not thread_id:
            logger.debug("knowledge search without thread_id — citations skipped")
        return
    try:
        ledger = get_ledger(thread_id)
        if ledger is None:
            return
        chunks = envelope.get("results") or []
        for chunk, result in zip(chunks, results):
            chunk["cite_id"] = ledger.register(result, query=query)
        retrieval = envelope.get("retrieval")
        if isinstance(retrieval, dict) and retrieval.get("reading_directive"):
            retrieval["reading_directive"] += CITATION_DIRECTIVE
    except Exception:
        logger.exception("citation stamping failed; returning unstamped envelope")


# --- enablement ---------------------------------------------------------------

# The server wires this to the session provider at startup (main.py); until then,
# and always in the SDK, enablement falls back to the agent-level KnowledgeConfig flag alone.
_override_lookup: Callable[[str], dict[str, Any] | None] | None = None


def set_session_override_lookup(
    fn: Callable[[str], dict[str, Any] | None] | None,
) -> None:
    global _override_lookup
    _override_lookup = fn


# The server/SDK wire this to read the CURRENT agent-level flag (the engine
# config can be replaced at runtime by apply_knowledge_config, so this must
# be a callable, not a captured value). Unwired -> default True.
_agent_flag_lookup: Callable[[], bool] | None = None


def set_agent_citations_lookup(fn: Callable[[], bool] | None) -> None:
    global _agent_flag_lookup
    _agent_flag_lookup = fn


def _coerce_override(value: Any, thread_id: str | None) -> bool | None:
    """Coerce a citations_enabled override to a bool; None when unusable."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    logger.warning(
        "ignoring non-boolean citations_enabled override %r for thread %s",
        value,
        thread_id,
    )
    return None


def _session_override(thread_id: str | None) -> bool | None:
    """Per-session citations override for *thread_id*; None when absent/unusable."""
    if not thread_id or _override_lookup is None:
        return None
    try:
        overrides = _override_lookup(thread_id) or {}
        if "citations_enabled" in overrides:
            return _coerce_override(overrides["citations_enabled"], thread_id)
    except Exception:
        logger.exception("session override lookup failed; using agent-level flag")
    return None


def citations_enabled_for(config: "KnowledgeConfig | Any", thread_id: str | None) -> bool:
    """Effective citations flag: per-session override wins over agent config."""
    override = _session_override(thread_id)
    if override is not None:
        return override
    return bool(getattr(config, "citations_enabled", True))


def effective_citations_enabled(thread_id: str | None) -> bool:
    """Effective flag with no config object in hand: wired agent-level flag
    (default True) overridden by the per-session setting when present."""
    override = _session_override(thread_id)
    if override is not None:
        return override
    base = True
    if _agent_flag_lookup is not None:
        try:
            base = bool(_agent_flag_lookup())
        except Exception:
            logger.exception("agent citations lookup failed; assuming enabled")
    return base
