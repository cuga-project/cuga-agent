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
            self._evict_if_needed()
            return record.cite_id

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

# Optional hook set by the server so a fresh process can rebuild cited
# entries from persisted conversation events (see main.py wiring).
_rehydrator: Callable[[str, SourceLedger], None] | None = None


def set_rehydrator(fn: Callable[[str, SourceLedger], None] | None) -> None:
    global _rehydrator
    _rehydrator = fn


def get_ledger(thread_id: str, create: bool = True) -> SourceLedger | None:
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
        # Publish to registry before rehydration so concurrent callers can
        # locate the ledger.  Accepted tradeoff: a concurrent caller may
        # briefly see an un-rehydrated ledger, and a failed rehydrator is not
        # retried.
        _ledgers[thread_id] = ledger
        while len(_ledgers) > _MAX_THREADS:
            _ledgers.popitem(last=False)
    if _rehydrator is not None:
        try:
            _rehydrator(thread_id, ledger)
        except Exception:
            logger.exception("source-ledger rehydration failed for thread %s", thread_id)
    return ledger


def drop_ledger(thread_id: str) -> None:
    with _registry_lock:
        _ledgers.pop(thread_id, None)


def _reset_all_ledgers_for_tests() -> None:
    with _registry_lock:
        _ledgers.clear()


# --- citation resolution ------------------------------------------------------

# [s1] or [s1, s4] or [s1 s4] — case-insensitive. Requires the s-prefix so
# plain bracketed numbers/text ("[1]", "[note]") never match.
_MARKER_RE = re.compile(r"\[\s*([sS]\d+(?:[\s,]+[sS]\d+)*)\s*\]")
# Segments the answer so markers inside code are never rewritten.
# Handles: fenced blocks (``` or ~~~, terminated or unterminated/truncated),
# double-backtick inline spans, and single-backtick inline spans.
_CODE_RE = re.compile(
    r"(```[\s\S]*?```|~~~[\s\S]*?~~~|```[\s\S]*$|~~~[\s\S]*$|``[^`\n](?:[^`\n]|`[^`\n])*``|`[^`\n]*`)"
)


def has_citation_markers(text: str) -> bool:
    return bool(text) and _MARKER_RE.search(text) is not None


def resolve_citations(
    text: str, ledger: SourceLedger | None
) -> tuple[str, list[dict[str, Any]]]:
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
    if not text or not has_citation_markers(text):
        return text, []

    numbers: dict[str, int] = {}          # cite_id -> display n
    ordered: list[SourceRecord] = []
    warned: set[str] = set()

    def _sub(match: re.Match[str]) -> str:
        out = []
        for raw_id in re.split(r"[\s,]+", match.group(1)):
            cite_id = raw_id.strip().lower()
            if not cite_id:
                continue
            record = ledger.get(cite_id) if ledger is not None else None
            if record is None:
                if cite_id not in warned:
                    logger.warning("citation marker [%s] not in ledger — stripped", cite_id)
                    warned.add(cite_id)
                continue
            if cite_id not in numbers:
                numbers[cite_id] = len(numbers) + 1
                ledger.mark_cited(cite_id)
                ordered.append(record)
            out.append(f"[{numbers[cite_id]}]")
        return "".join(out)

    parts = _CODE_RE.split(text)
    resolved = "".join(
        part if i % 2 else _MARKER_RE.sub(_sub, part) for i, part in enumerate(parts)
    )
    sources = [rec.to_snapshot(n=i + 1) for i, rec in enumerate(ordered)]
    return resolved, sources


# --- envelope stamping --------------------------------------------------------

# Rides on retrieval.reading_directive so the LLM sees it at composition time
# (the system-prompt contract is thousands of tokens upstream by then).
CITATION_DIRECTIVE = (
    " CITATIONS: each result carries a cite_id. In your FINAL text answer, "
    "append [<cite_id>] immediately after every claim taken from that chunk "
    "(example: 'The total is 4,521 [s2].'). Use ONLY cite_ids you received in "
    "this conversation; ids from earlier searches this conversation remain "
    "valid. Never invent ids; never write bare numeric citations like [1]."
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
    ledger = get_ledger(thread_id)
    if ledger is None:
        return
    chunks = envelope.get("results") or []
    for chunk, result in zip(chunks, results):
        chunk["cite_id"] = ledger.register(result, query=query)
    retrieval = envelope.get("retrieval")
    if isinstance(retrieval, dict) and retrieval.get("reading_directive"):
        retrieval["reading_directive"] += CITATION_DIRECTIVE


# --- enablement ---------------------------------------------------------------

# The server wires this to the session provider at startup (main.py); until then,
# and always in the SDK, enablement falls back to the agent-level KnowledgeConfig flag alone.
_override_lookup: Callable[[str], dict[str, Any] | None] | None = None


def set_session_override_lookup(
    fn: Callable[[str], dict[str, Any] | None] | None,
) -> None:
    global _override_lookup
    _override_lookup = fn


def citations_enabled_for(config: "KnowledgeConfig | Any", thread_id: str | None) -> bool:
    """Effective citations flag: per-session override wins over agent config."""
    base = bool(getattr(config, "citations_enabled", True))
    if thread_id and _override_lookup is not None:
        try:
            overrides = _override_lookup(thread_id) or {}
            if "citations_enabled" in overrides:
                value = overrides["citations_enabled"]
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
        except Exception:
            logger.exception("session override lookup failed; using agent-level flag")
    return base
