"""In-process knowledge engine using LangChain vector stores + Docling.

Replaces OpenRAG with zero external services. All document parsing, embedding,
vector storage, and search happen in-process.
"""

from __future__ import annotations

import asyncio
import collections
import functools
import hashlib
import ipaddress
from loguru import logger as loguru_logger
import re
import shutil
import socket
import threading
import time
import uuid
from dataclasses import dataclass, fields as dc_fields
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from pydantic import ConfigDict

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.indexing import InMemoryRecordManager
from langchain_docling import DoclingLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from cuga.backend.knowledge.config import (
    KnowledgeConfig,
    client_adaptation_hash as _client_adapt_hash,
    client_glossary_hash as _client_glossary_hash,
    knowledge_vector_backend_for_settings,
)
from cuga.backend.knowledge.interprocess_lock import acquire_exclusive_nonblocking, release_exclusive
from cuga.backend.knowledge.metadata import create_knowledge_metadata
from cuga.backend.storage.facade import get_storage_connection_params
from cuga.backend.knowledge.vector_store_base import VectorStoreAdapter

logger = loguru_logger

# Hard ceiling on a background reindex worker. A wedged embedding-provider call
# (no per-request timeout at the litellm layer) would otherwise hang
# asyncio.gather forever, leaving the collection pinned in _reindex_in_progress
# and blocking every future reindex/publish for that agent with no self-heal
# short of a process restart.
_REINDEX_WORKER_TIMEOUT_S = 1800  # 30 min; matches the deferred-flip wall-clock cap

# Strong-ref set for fire-and-forget reindex worker tasks — the event loop keeps
# only WEAK refs to create_task() results, so a GC mid-run would drop the finally
# that clears the busy flag. done_callback discards on completion.
_BACKGROUND_REINDEX_TASKS: set[Any] = set()


# Docling's plugin factory emits a WARNING every time it scans for plugins:
#   "The plugin langchain_docling will not be loaded because Docling is being
#    executed with allow_external_plugins=false."
# This fires 4-8x per ingest. It's COMPLETELY BENIGN — Docling's plugin
# security policy is working as designed; we don't use external plugins.
# Filter the message out of stdlib logging so it doesn't pollute every log.
def _install_docling_plugin_log_filter() -> None:
    import logging as _stdlib_logging

    class _DropDoclingPluginNotice(_stdlib_logging.Filter):
        def filter(self, record: _stdlib_logging.LogRecord) -> bool:  # type: ignore[override]
            msg = record.getMessage()
            return "will not be loaded because Docling is being executed" not in msg

    target = _stdlib_logging.getLogger("docling.models.factories.base_factory")
    # Guard against double-install (engine instantiation can happen multiple
    # times in tests / SDK use).
    if not any(isinstance(f, _DropDoclingPluginNotice) for f in target.filters):
        target.addFilter(_DropDoclingPluginNotice())


_install_docling_plugin_log_filter()


# Docling's inference engines emit a single line on init confirming the
# device the model was actually moved to (e.g. "Transformers engine ready
# (device=mps, ...)"). It goes to Python's stdlib logger, which cuga's
# loguru setup doesn't capture by default — so users can't verify GPU
# engagement from the cuga log. Bridge only the two engine loggers we
# need; broader interception would pollute the log with Docling's
# per-page progress chatter.
def _bridge_docling_engine_logs_to_loguru() -> None:
    import logging as _stdlib_logging

    class _LoguruBridge(_stdlib_logging.Handler):
        def emit(self, record: _stdlib_logging.LogRecord) -> None:
            try:
                level = logger.level(record.levelname).name
            except (ValueError, AttributeError):
                level = "INFO"
            try:
                msg = record.getMessage()
            except Exception:  # pragma: no cover - defensive
                msg = str(record.msg)
            logger.opt(depth=2).log(level, "[docling] {}", msg)

    targets = (
        "docling.models.inference_engines.object_detection.transformers_engine",
        "docling.models.inference_engines.object_detection.onnxruntime_engine",
    )
    for name in targets:
        lg = _stdlib_logging.getLogger(name)
        if any(isinstance(h, _LoguruBridge) for h in lg.handlers):
            continue
        lg.addHandler(_LoguruBridge())
        # Show device confirmation (INFO) and the "Loading model to device X"
        # debug line so we have ground truth on both the requested device and
        # the device the model actually landed on.
        if lg.level == _stdlib_logging.NOTSET or lg.level > _stdlib_logging.DEBUG:
            lg.setLevel(_stdlib_logging.DEBUG)
        # Don't double-emit through the root logger.
        lg.propagate = False


_bridge_docling_engine_logs_to_loguru()


BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "169.254.169.254"}
ALLOWED_PORTS = {80, 443, 8080, 8443}
_VS_CACHE_MAX = 64  # max cached vector store connections


def _iter_exception_messages(exc: BaseException) -> list[str]:
    """Collect exception messages across cause/context chains."""
    messages: list[str] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]

    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        message = str(current).strip()
        if message:
            messages.append(message)

        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None:
            stack.append(current.__context__)

    return messages


def _translate_document_load_error(file_path: Path, exc: BaseException) -> Exception:
    """Map low-level parser errors to actionable ingestion failures."""
    if file_path.suffix.lower() == ".pdf":
        lowered = " | ".join(_iter_exception_messages(exc)).lower()
        if any(token in lowered for token in ("incorrect password", "password error", "encrypted")):
            return ValueError(
                f"PDF is password-protected and cannot be indexed without a password: {file_path.name}"
            )
        # Tesseract traineddata not installed — surfaces during OCR. Tell the user
        # which pack is missing and how to install it.
        if "traineddata" in lowered or "tessdata" in lowered:
            return ValueError(
                f"Tesseract is missing a language pack required to OCR {file_path.name}.\n"
                "Install the matching pack:\n"
                "  macOS: brew install tesseract-lang\n"
                "  Linux: apt install tesseract-ocr-all"
            )

    if isinstance(exc, Exception):
        return exc
    return RuntimeError(str(exc))


# Docling chunk item labels that represent pure page chrome (gutters,
# running headers, footers, line-number columns from academic PDFs).
# Chunks composed *entirely* of these are noise — the academic-paper
# `'1998\n1999\n...2074'` chunk that polluted the live trace is exactly
# this case: a run of page numbers Docling correctly labelled, that we
# nevertheless indexed and surfaced to the LLM as "data".
_DOCLING_PAGE_CHROME_LABELS: frozenset[str] = frozenset(
    {
        "page_footer",
        "page_header",
    }
)


def _chunk_is_pure_page_chrome(dl_meta: Any) -> bool:
    """Return True when EVERY contributing Docling item is page chrome.

    Conservative on purpose:
      - mixed chunk (body + footer) → False (keep — body content matters)
      - empty / missing labels → False (don't drop unknown things)
      - non-Docling docs (markdown, txt) without ``dl_meta`` → False
    The "all items must be chrome" rule ensures we never lose a real
    paragraph just because Docling stapled a footer label onto it.
    """
    if not isinstance(dl_meta, dict):
        return False
    doc_items = dl_meta.get("doc_items")
    if not isinstance(doc_items, list) or not doc_items:
        return False
    labels: list[str] = []
    for item in doc_items:
        if isinstance(item, dict):
            lab = item.get("label")
            if isinstance(lab, str):
                labels.append(lab)
    if not labels:
        return False
    return all(lab in _DOCLING_PAGE_CHROME_LABELS for lab in labels)


# Code/markup file extensions whose chunks legitimately have low alphabetic
# ratios (lots of punctuation, brackets, identifiers, numbers). Skip the
# alpha-ratio rule for these so a Python file or JSON config doesn't get
# nuked as "noise."
_CODE_FILE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".ini",
        ".conf",
        ".sh",
        ".bash",
        ".zsh",
        ".rs",
        ".go",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".java",
        ".kt",
        ".swift",
        ".rb",
        ".php",
        ".css",
        ".html",
        ".xml",
        ".sql",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
    }
)


import re as _stdlib_re  # noqa: E402
import string as _stdlib_string  # noqa: E402 — placed near consumer for cohesion

# Module-level frozenset for the digit + whitespace + ASCII-punct check
# in ``_classify_junk_chunk``. Built once at import time so the hot path
# does ONE membership test per char instead of rebuilding the set per
# call. Unicode letters (Hebrew, CJK, accented Latin) are deliberately
# NOT in here — the digit/punct rule is ASCII-only by design so it
# doesn't false-positive on non-Latin content.
_DIGIT_PUNCT_WS_CHARS: frozenset[str] = frozenset(
    _stdlib_string.digits + _stdlib_string.punctuation + " \t\n\r\v\f"
)

# CID-glyph token detector. Scanned PDFs whose text layer is the font's
# CID index (not actual letters) deliver text like
# ``/CE3/CE5/CE1/CEB/CEC /CF8/CE1`` — slashes concatenate glyphs WITHIN
# a token; whitespace separates tokens. The other noise rules MISS
# these because the A–F hex digits count as alphabetic (``isalpha()``
# returns True), pushing the alpha ratio past the 25% floor. A token-
# shape detector catches them at the tokenisation level: ONE or more
# repetitions of ``/?C`` + 2–4 hex chars, with no other content. A live
# production trace had 3/14 results from a single non-OCR'd Hebrew scan
# in exactly this shape.
_CID_GLYPH_TOKEN_RE: _stdlib_re.Pattern[str] = _stdlib_re.compile(r"^(/?C[0-9A-Fa-f]{2,4}){1,}$")
# Threshold: fraction of tokens that must be CID-shaped before we flag
# the chunk. 30% catches the production trace's chunks (≥60% CID by
# count) with comfortable margin against mixed prose-plus-glyph chunks
# where a stray ``/C2E`` reference shouldn't trigger.
_CID_GLYPH_RATIO_THRESHOLD: float = 0.3
# Minimum length of a consecutive run of CID-shape tokens before we flag
# the chunk via the run rule. 3 is the smallest run that has zero false
# positives on legitimate path fragments (a single ``/usr/local/bin`` is
# fine; three back-to-back ``/Cxx`` tokens is OCR garbage in every
# production sample). Independent of the ratio threshold so mixed-content
# chunks with a glyph cluster get caught even when the prose floods the
# token count.
_CID_GLYPH_RUN_MIN: int = 3


def _classify_junk_chunk(text: str, filename: str) -> str | None:
    """Heuristic noise detector. Returns the first matching reason or ``None``.

    Rules — kept few and explicable so an SRE reading a log line can
    instantly diagnose a false positive:
      - ``"too_short"`` : ``len(text.strip()) < 30`` — orphan caption fragments.
      - ``"cid_glyph_run"``: a consecutive run of ≥3 CID-shape tokens.
        Catches mixed-content chunks where a glyph fragment is intermixed
        with prose (the ratio rule below misses these because the prose
        floods the token count). Production trace: a Hebrew insurance
        PDF emitted ``"- /C2E/CEF/CEB /C2D /C4C... <Hebrew sentence>"`` —
        12 tokens of CID garbage drowned out by trailing prose pushed
        ``cid_hits / len(tokens)`` under the ratio threshold.
      - ``"cid_glyph_ratio"``: ≥30% of whitespace-split tokens match the
        ``/?C[0-9A-Fa-f]{2,4}`` CID-glyph shape. Catches scanned PDFs
        whose Docling text layer is the font CID index (not letters).
        These bypass ``low_alpha_ratio`` because the hex chars A-F count
        as letters under ``isalpha()``.
      - ``"digit_punct_ratio"``: 65%+ of chars are digit/whitespace/ASCII-punct
        — the academic line-number runs like ``'1998\\n1999\\n...'``.
        SKIPPED for code/markup files (their punctuation density is normal).
      - ``"low_alpha_ratio"``: <25% alphabetic (Unicode-aware, so Hebrew /
        CJK still count). Catches mojibake. SKIPPED for code/markup files.

    Hot path: single pass over the stripped string, two counters, no
    per-call set construction. ``isalpha()`` covers Unicode letters
    natively. Returns ``None`` when nothing matches → caller keeps the chunk.
    """
    stripped = text.strip()
    total = len(stripped)
    if total < 30:
        return "too_short"

    # CID-glyph rules run BEFORE the code-file whitelist because no
    # legitimate ``.py`` / ``.json`` source contains long runs of
    # ``/Cxx`` tokens. The RUN rule is checked first — it's a more
    # specific signal than the ratio rule and catches mixed-content
    # fragments the ratio rule misses (Hebrew prose + glyph cluster).
    tokens = stripped.split()
    # RUN rule: scan for a consecutive run of 3+ CID-shape tokens.
    # 3 is the smallest run length that's vanishingly unlikely to occur
    # naturally (a token shape like ``/Cxx`` matches both isalnum-ish
    # text AND legitimate path fragments — but three IN A ROW is OCR
    # garbage in every production trace we've seen).
    _run = 0
    for t in tokens:
        if _CID_GLYPH_TOKEN_RE.match(t):
            _run += 1
            if _run >= _CID_GLYPH_RUN_MIN:
                return "cid_glyph_run"
        else:
            _run = 0
    # RATIO rule: catches pure-CID chunks where the run rule might
    # not trigger (e.g. CID tokens scattered with single non-CID tokens
    # between them — each run is 1-2, but the overall ratio is high).
    if len(tokens) >= 5:
        cid_hits = sum(1 for t in tokens if _CID_GLYPH_TOKEN_RE.match(t))
        if cid_hits / len(tokens) >= _CID_GLYPH_RATIO_THRESHOLD:
            return "cid_glyph_ratio"

    # Filename whitelist for code/markup. The two ratio rules are disabled
    # for these — Python/JSON/YAML chunks legitimately have punctuation
    # densities far above the prose threshold.
    suffix = ""
    if "." in filename:
        suffix = "." + filename.rsplit(".", 1)[1].lower()
    is_code = suffix in _CODE_FILE_EXTENSIONS
    if is_code:
        return None

    # One pass: count digit/punct/ws and alpha simultaneously. Saves a
    # second iteration of the string in the common case.
    np = 0
    alpha = 0
    for ch in stripped:
        if ch in _DIGIT_PUNCT_WS_CHARS:
            np += 1
        elif ch.isalpha():
            alpha += 1

    if np / total > 0.65:
        return "digit_punct_ratio"
    if alpha / total < 0.25:
        return "low_alpha_ratio"
    return None


@dataclass
class _MultiSearchStats:
    """Aggregated stats from a multi-scope (``scope='all'``) search.

    ``search_multi`` returns this in place of the old single-scope
    ``_JunkFilterStats`` so the route can build the per-scope
    ``retrieval`` block without re-aggregating server-side.

    Fields:
      - ``by_scope``: scope_name → per-scope ``_JunkFilterStats`` (the
        same shape the single-scope ``search_with_stats`` returns,
        kept side-by-side instead of summed so the wire can surface
        per-scope detail).
      - ``failed_scopes``: scopes whose per-scope search raised. Empty
        list when all scopes succeeded.
      - ``partial``: True iff ``len(failed_scopes) > 0``. Redundant with
        ``failed_scopes`` but the boolean is the LLM-readable signal
        the contract teaches.
      - ``top_score_by_scope``: scope_name → top result score. Used by
        the observability layer to compute the ``recommendation``
        field (e.g. ``prefer_session`` when session top ≥ 1.5× agent
        top AND ≥ 0.5 absolute floor).
    """

    by_scope: dict[str, "_JunkFilterStats"] = None  # type: ignore[assignment]
    failed_scopes: list[str] = None  # type: ignore[assignment]
    partial: bool = False
    top_score_by_scope: dict[str, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_scope is None:
            self.by_scope = {}
        if self.failed_scopes is None:
            self.failed_scopes = []
        if self.top_score_by_scope is None:
            self.top_score_by_scope = {}


@dataclass
class _JunkFilterStats:
    """Per-search filter accounting surfaced on the wire as
    ``retrieval.by_scope.<scope>``. Invariant per scope:
        candidates == returned + filtered_count + below_threshold + drain_drops + dedup_collapses

    Fields:
      - ``candidates``: raw retriever output count BEFORE any score
        threshold or junk filter.
      - ``filtered_count``: chunks the junk filter rejected. In
        ``dry_run`` mode chunks are counted here but kept in results;
        in ``enforce`` mode they're dropped.
      - ``below_threshold``: dense retriever returned the chunk but its
        cosine score was below ``score_threshold``. Counted across both
        dense and lexical legs (the lexical leg pre-fusion does not
        apply the cosine cutoff — see ``_materialize``).
      - ``drain_drops``: chunks dropped by the over-fetch drain pass
        (rare; only fires when ``filter_mode='enforce'`` and a pathological
        page wedges below the limit on the first pass).
      - ``dedup_collapses``: chunks that were collapsed during cross-scope
        dedup (search_multi). Attributed to the LOSER's scope so the
        operator-facing summary reads "your X scope had a duplicate that
        was eclipsed by a higher-scoring copy in Y".
      - ``reasons``: classifier reason → count map, surfaced on the wire
        as ``retrieval.by_scope.<scope>.reasons`` so the LLM can write a
        thoughtful follow-up ("low-quality OCR fragments were dropped").
        Cost: one dict-level deeper than the original "single int" shape,
        bounded by ~5 reasons.
    """

    candidates: int = 0
    filtered_count: int = 0
    below_threshold: int = 0
    drain_drops: int = 0
    dedup_collapses: int = 0
    reasons: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = {}


def _rrf_fuse(
    dense: list["SearchResult"],
    lexical: list["SearchResult"],
    k_rrf: int = 60,
) -> list["SearchResult"]:
    """Reciprocal Rank Fusion of dense + lexical result lists.

    Cormack et al. (2009). For each document ``d``:
        score(d) = sum over ranked lists where d appears of: 1 / (k_rrf + rank)

    Properties that matter for hybrid retrieval:
      - Rank-based, NOT score-based — defends against score-distribution
        drift between the two legs. Lexical BM25 and dense cosine live
        in different scales; comparing raw values would be unsound.
      - Both legs contribute additively; a chunk that ranks moderately
        in BOTH legs is preferred over a chunk that ranks #1 in only
        one leg. This is exactly the "robust to either leg being wrong"
        property hybrid retrieval is supposed to give us.
      - ``k_rrf=60`` is the literature default (Cormack 2009 + every
        production RAG using RRF since). Smaller k boosts top ranks
        more aggressively; larger k flattens. 60 is well-calibrated for
        top-K ~= 10-50.

    Documents are identified by ``(filename, page, text)`` — the same
    key search_multi uses for cross-scope dedup. A doc that appears in
    both legs is fused; one-leg-only docs still get included (rank=None
    on the other side contributes 0 to the sum).

    Returns a new list with the SAME SearchResult objects (no copy,
    same identity) reordered by fused score. The SearchResult.score
    field keeps its dense value so ``score_threshold`` semantics elsewhere
    in the code stay unchanged.
    """
    if not lexical:
        return dense  # No lexical signal → identity (cheap, common path).
    if not dense:
        return lexical

    def _key(r: SearchResult) -> tuple[str, int | None, str]:
        return (r.filename, r.page, r.text)

    # ``items[key] = (search_result, dense_rank | None, lexical_rank | None)``.
    # Iterating dense first lets us prefer the dense-side SearchResult
    # object (which carries the dense score field) when the chunk
    # appears in both.
    items: dict[tuple[str, int | None, str], list] = {}
    for rank, r in enumerate(dense, start=1):
        items.setdefault(_key(r), [r, rank, None])
    for rank, r in enumerate(lexical, start=1):
        k = _key(r)
        if k in items:
            items[k][2] = rank
        else:
            items[k] = [r, None, rank]

    def _rrf_score(dense_rank: int | None, lexical_rank: int | None) -> float:
        s = 0.0
        if dense_rank is not None:
            s += 1.0 / (k_rrf + dense_rank)
        if lexical_rank is not None:
            s += 1.0 / (k_rrf + lexical_rank)
        return s

    # Stamp per-leg observability onto the SearchResult so the route +
    # SDK can surface "why this chunk ranked where it did" without
    # re-running RRF. Mutates the kept SearchResult in place — safe
    # because RRF is the last step before junk-filter + cap, and the
    # result objects don't escape the engine before this point.
    for _r, _d, _l in items.values():
        _r.dense_rank = _d
        _r.lexical_rank = _l
        _r.rrf_score = round(_rrf_score(_d, _l), 6)

    # Deterministic ordering: primary key is fused score (desc); ties
    # break on (filename, page) so identical queries produce identical
    # orderings across runs.
    fused = sorted(
        items.values(),
        key=lambda v: (
            -v[0].rrf_score if v[0].rrf_score is not None else 0.0,
            v[0].filename,
            v[0].page if v[0].page is not None else -1,
        ),
    )
    return [v[0] for v in fused]


def _rrf_fuse_lists(result_lists: list[list["SearchResult"]], k_rrf: int = 60) -> list["SearchResult"]:
    """Reciprocal Rank Fusion over N ranked lists (same math as ``_rrf_fuse``, any
    number of legs). Used by the query-transform path, where each query variant ×
    leg is an independent RRF "expert". Keeps the FIRST list's SearchResult object
    for a shared chunk (callers pass the base hybrid result first, so it carries
    the dense score field ``score_threshold`` relies on). Empty/single inputs are
    returned as-is."""
    lists = [lst for lst in result_lists if lst]
    if not lists:
        return []
    if len(lists) == 1:
        return lists[0]

    def _key(r: SearchResult) -> tuple[str, int | None, str]:
        return (r.filename, r.page, r.text)

    items: dict[tuple[str, int | None, str], list] = {}
    for lst in lists:
        for rank, r in enumerate(lst, start=1):
            k = _key(r)
            if k in items:
                items[k][1] += 1.0 / (k_rrf + rank)
            else:
                items[k] = [r, 1.0 / (k_rrf + rank)]
    for r, s in items.values():
        r.rrf_score = round(s, 6)
    fused = sorted(
        items.values(),
        key=lambda v: (-v[1], v[0].filename, v[0].page if v[0].page is not None else -1),
    )
    return [v[0] for v in fused]


def _apply_junk_filter(
    results: list["SearchResult"], mode: str
) -> tuple[list["SearchResult"], _JunkFilterStats]:
    """Apply the junk filter according to ``mode``.

    Returns ``(maybe_filtered_results, stats)``:
      - ``"off"``: results unchanged, stats zeroed.
      - ``"dry_run"``: results NOT removed by default; **exception** — if
        the chunk is also far below the top score (``< 0.5 * top_score``)
        AND the top is itself non-trivial (``top_score >= 0.3``), drop
        it anyway. The reason gets a ``_low_relative_score`` suffix so
        SREs see the dual-trigger in logs.
      - ``"enforce"``: filtered chunks removed, stats reflect actual drops.
    """
    stats = _JunkFilterStats(candidates=len(results))
    if mode == "off" or not results:
        return results, stats

    # Relative-score gap reference: only consider dropping flagged
    # chunks under dry_run when the top result is comfortably above the
    # "low-confidence cluster" floor. Two guards combined:
    #   - ``top >= 0.3``  → skip on a tightly-clustered low-score batch
    #     (everything is junk-grade; the "0.5 * top" cutoff becomes
    #     arbitrary noise and may drop the only relevant chunk).
    #   - ``r.score < 0.5 * top`` → only candidates the model itself
    #     said were a lot worse than #1.
    top_score = results[0].score if results else 0.0
    _gap_active = top_score >= 0.3

    # B6 hardening: the classifier runs over potentially-malformed data
    # (text may be None from a buggy adapter, filename may be missing).
    # A raised exception here would 500 the entire search; catch +
    # log + degrade to "keep" semantics for the offending chunk so a
    # single bad row can't take down retrieval for the whole query.
    #
    # We intentionally narrow the catch to data-shape exceptions
    # (AttributeError / TypeError / ValueError). Catching bare Exception
    # masks future bugs introduced inside the classifier (a typo'd
    # regex, an unimported symbol) — those should fail loudly so a
    # silent quality regression is impossible.
    kept: list[SearchResult] = []
    for r in results:
        try:
            text = r.text if isinstance(r.text, str) else ""
            filename = r.filename if isinstance(r.filename, str) else ""
            reason = _classify_junk_chunk(text, filename)
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning(
                "junk filter classify failed for filename=%r (kept): %s",
                getattr(r, "filename", "?"),
                exc,
            )
            reason = None
        if reason is None:
            kept.append(r)
            continue
        # Relative-score gap rule: drop under dry_run too when the
        # chunk is BOTH classifier-flagged AND far below the top.
        # This catches the "1 high-quality hit + 15 noise" failure
        # mode without flipping the global dry_run knob.
        _drop_via_gap = mode == "dry_run" and _gap_active and r.score < 0.5 * top_score
        if _drop_via_gap:
            # Decorate the reason so a grep'd log line shows the
            # dual-trigger (classifier said junk AND score said junk).
            reason = f"{reason}+low_relative_score"
        stats.filtered_count += 1
        stats.reasons[reason] = stats.reasons.get(reason, 0) + 1
        if mode == "dry_run" and not _drop_via_gap:
            # Count but don't drop — gives operators a low-risk window to
            # observe before flipping the config flag to "enforce".
            kept.append(r)
    return kept, stats


def _extract_section_path(dl_meta: Any) -> str:
    """Collapse Docling's ``headings`` array into a flat ``A › B › C`` string.

    Stored as a single field instead of nested structure — sidesteps the
    "schema conflicts across formats" pain that previously caused us to
    drop heading metadata entirely. The character `›` (U+203A) is what
    Docling Core uses in its own breadcrumb rendering; matches user
    intuition and stays out of regex special-char territory.
    """
    if not isinstance(dl_meta, dict):
        return ""
    headings = dl_meta.get("headings")
    if not isinstance(headings, list):
        return ""
    parts = [h.strip() for h in headings if isinstance(h, str) and h.strip()]
    return " › ".join(parts)


def _page_from_docling_dl_meta(dl_meta: Any) -> int | None:
    """Infer PDF page from Docling chunk metadata (``doc_items`` → ``prov`` → ``page_no``).

    See https://docling-project.github.io/docling/concepts/chunking/ — chunk metadata
    lists contributing document items; each item carries provenance with ``page_no``.
    When a chunk spans multiple pages, we use the minimum page number (chunk start).
    """
    if not isinstance(dl_meta, dict):
        return None
    doc_items = dl_meta.get("doc_items")
    if not isinstance(doc_items, list):
        return None
    pages: list[int] = []
    for item in doc_items:
        if not isinstance(item, dict):
            continue
        prov = item.get("prov")
        if not isinstance(prov, list):
            continue
        for p in prov:
            if not isinstance(p, dict):
                continue
            pn = p.get("page_no")
            if isinstance(pn, int):
                pages.append(pn)
    if not pages:
        return None
    return min(pages)


# --- Data classes ---


@dataclass
class SearchResult:
    text: str
    filename: str
    page: int | None
    score: float
    # Which knowledge collection this chunk came from. "agent" = persistent
    # agent-level KB; "session" = current conversation's KB; "" = caller
    # didn't tag (legacy / tests that construct mock results directly).
    # Only meaningful when results from multiple scopes are combined.
    scope: str = ""
    # Section breadcrumb (Docling ``headings`` collapsed via " › ") so the
    # LLM can contextualize otherwise-orphan chunks — e.g. a caption-only
    # `'Table 27: ...'` reads as "[Section: D.2 Full performance metrics]
    # Table 27: ..." instead of floating without context. Empty for
    # non-Docling formats (txt, plain markdown) or when the doc lacks
    # heading structure.
    section_path: str = ""
    # Per-leg ranks for debugging "why X > Y" reports. Populated by
    # ``_rrf_fuse`` when hybrid mode produced both legs; ``None`` on the
    # leg where the chunk didn't appear. ``rrf_score`` is the fused
    # signal (sum of 1/(k_rrf+rank) per leg). All three are ``None`` for
    # pure-dense responses so SDK consumers can tell hybrid was off.
    # Wire-shape: only surfaced in the route response when non-None, so
    # single-leg / pre-upgrade responses stay terse.
    dense_rank: int | None = None
    lexical_rank: int | None = None
    rrf_score: float | None = None
    # Cross-scope RRF score, populated by ``search_multi`` when more
    # than one scope contributed to the fused ranking. Distinct from
    # ``rrf_score`` (which is the in-scope dense+lexical fusion); this
    # lets observability surface BOTH ("why this chunk ranked here in
    # its scope" AND "why this chunk beat the other scope's #1").
    # ``None`` for single-scope responses so wire shape stays terse.
    cross_scope_rrf_score: float | None = None


@dataclass
class DocInfo:
    filename: str
    chunk_count: int
    status: str
    ingested_at: str
    preview: str = ""


# --- Errors ---


class ReindexBusyError(Exception):
    """Raised when reindex cannot start because uploads are pending."""

    def __init__(self, pending_count: int):
        self.pending_count = pending_count
        super().__init__(f"Cannot reindex: {pending_count} upload(s) in progress")


class EmbeddingModelLoadError(Exception):
    """Raised when a config change selects an embedding model that fails to load
    (e.g. a large model still downloading, a bad model name, or unresolved API
    key). Carries the provider/model so callers can return an actionable message
    instead of a 500."""

    def __init__(self, provider: str, model: str, cause: Exception):
        self.provider = provider
        self.model = model
        self.cause = cause
        super().__init__(f"Failed to load embedding model {model!r} (provider {provider!r}): {cause}")


class ReindexInProgressError(Exception):
    """Raised when upload is attempted during reindex."""

    pass


class ReindexSupersededError(Exception):
    """Stale ingest worker: ``_apply_generation`` moved past the captured value."""

    def __init__(self, worker_gen: int, current_gen: int):
        self.worker_gen = worker_gen
        self.current_gen = current_gen
        super().__init__(f"Reindex superseded: gen {worker_gen} -> {current_gen}")


# --- Prepared update result ---


@dataclass
class PreparedKnowledgeUpdate:
    """Result of prepare_knowledge_update. Passed to commit without re-validation."""

    validated: KnowledgeConfig
    embedding_changed: bool
    chunking_changed: bool
    metric_changed: bool
    reindex_recommended: bool
    new_embeddings: Embeddings | None
    new_embedding_dim: int | None


# --- Accelerator detection ---


def _detect_accelerator(use_gpu: bool) -> tuple[str, list[str]]:
    """Detect available local accelerator. Returns (device_label, onnx_providers).

    device_label is one of "cuda", "mps", "cpu" — usable for PyTorch/HuggingFace.
    onnx_providers is the priority list to pass to onnxruntime-backed models
    (fastembed, Docling layout). Unsupported providers are skipped by ORT.
    """
    if not use_gpu:
        return "cpu", ["CPUExecutionProvider"]
    providers: list[str] = []
    device = "cpu"
    try:
        import onnxruntime as _ort

        avail = set(_ort.get_available_providers())
    except Exception:
        avail = set()
    if "CUDAExecutionProvider" in avail:
        providers.append("CUDAExecutionProvider")
        device = "cuda"
    if "CoreMLExecutionProvider" in avail and device == "cpu":
        providers.append("CoreMLExecutionProvider")
        # Best PyTorch analogue on Apple Silicon is MPS — used by HF embeddings.
        try:
            import torch

            if torch.backends.mps.is_available():
                device = "mps"
        except Exception:
            pass
    providers.append("CPUExecutionProvider")
    return device, providers


# --- Embedding factory ---


class _FastEmbedEmbeddings(Embeddings):
    """LangChain Embeddings adapter around fastembed.TextEmbedding.

    Includes E5-family prefix injection: intfloat/e5-* and intfloat/multilingual-e5-*
    require `"query: "` / `"passage: "` prefixes on inputs to hit their
    published MTEB recall numbers — without them, MRR drops 3-5 points
    on multilingual benchmarks. Detected from the model name; no-op for
    non-E5 models.
    """

    def __init__(self, model_name: str, providers: list[str] | None = None):
        from fastembed import TextEmbedding
        import os

        # fastembed defaults cache_dir to ~/fastembed_cache which resolves to
        # /tmp/fastembed_cache when HOME=/tmp (container). Explicitly pass the
        # build-time cache so it finds the pre-extracted model dir immediately,
        # before ever reaching the local_files_only check.
        cache_dir = os.environ.get("FASTEMBED_CACHE_PATH")
        local_files_only = os.environ.get("HF_HUB_OFFLINE", "0") == "1"
        kwargs: dict[str, Any] = {"cache_dir": cache_dir, "local_files_only": local_files_only}
        if providers:
            kwargs["providers"] = providers
        self._model = TextEmbedding(model_name, **kwargs)
        # Record what onnxruntime actually loaded so the engine can log honestly.
        self._active_providers = self._detect_active_providers()
        # GPU-aware embed kwargs:
        # On a CUDA host the per-call launch + PCIe H2D overhead is ~30-50 µs;
        # bge-small at fastembed's default ``parallel=None`` (worker-per-core)
        # spawns N workers ALL sharing the one GPU → CUDA-context thrash and
        # only ~2-3× CPU. Forcing ``parallel=1`` (single worker, large batch)
        # restores the 10-30× headroom. ``batch_size=256`` matches fastembed's
        # documented CPU default but is also the empirical GPU saturation
        # batch for 384-1024d encoders on H100. We DON'T touch CPU defaults
        # here — fastembed picks 256/None and that's fine for the multi-core
        # CPU case.
        self._is_cuda = bool(self._active_providers) and ("CUDAExecutionProvider" in self._active_providers)
        self._embed_kwargs: dict[str, Any] = {"batch_size": 256, "parallel": 1} if self._is_cuda else {}
        # E5-family prefix: required for any intfloat/e5-* model to reach
        # published recall numbers. Detected from model name. Non-E5 models
        # see a no-op (empty prefixes).
        m = (model_name or "").lower()
        is_e5 = "e5" in m and ("intfloat" in m or "multilingual" in m or m.startswith("e5"))
        self._query_prefix = "query: " if is_e5 else ""
        self._passage_prefix = "passage: " if is_e5 else ""

    def _detect_active_providers(self) -> list[str]:
        # fastembed nests the ORT session under model.model.model — versions vary.
        # Best-effort: walk a few likely attribute paths.
        candidate_attrs = ("model", "_model", "session", "_session", "ort_session")
        cur = self._model
        for _ in range(4):
            sess = None
            for a in candidate_attrs:
                obj = getattr(cur, a, None)
                if obj is None:
                    continue
                if hasattr(obj, "get_providers"):
                    sess = obj
                    break
                cur = obj
                break
            if sess is not None:
                try:
                    return list(sess.get_providers())
                except Exception:
                    return []
            if cur is self._model:
                break
        return []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._passage_prefix:
            texts = [self._passage_prefix + t for t in texts]
        return [v.tolist() for v in self._model.embed(texts, **self._embed_kwargs)]

    def embed_query(self, text: str) -> list[float]:
        if self._query_prefix:
            text = self._query_prefix + text
        # Cold-CUDA latency cliff: single-query embed on a cold CUDA
        # context is ~80-250 ms vs ~3-5 ms on CPU. The warm session
        # path here is fine, but a freshly-forked worker pays the cold-start
        # cost on its first query. A CUDA-Graphs + warm-worker bridge is
        # left as a follow-up; the practical mitigation today is to keep the
        # worker pool warm (engine.start_background_tasks does this).
        return next(self._model.embed([text])).tolist()


class _PyTorchEmbeddings(Embeddings):
    """Dep-free PyTorch embeddings — uses transformers + torch only.

    Both ``transformers`` and ``torch`` are already pulled in by Docling, so
    this provider adds **zero** new packages. Does what sentence-transformers
    does for sentence embeddings: tokenize -> forward -> mean-pool over
    attention mask -> L2 normalize.

    The whole reason this exists is GPU acceleration on Apple Silicon (MPS)
    and NVIDIA (CUDA) for local embedding inference, since fastembed's ONNX
    backend doesn't have working CoreML support for bge-small-class models.

    Verified on Mac M-series: same BAAI/bge-small-en-v1.5 model is ~8x faster
    on MPS than fastembed CPU.
    """

    def __init__(self, model_name: str, device: str = "cpu"):
        try:
            import torch  # noqa: F401
            from transformers import AutoModel, AutoTokenizer
        except ImportError as e:  # pragma: no cover - transformers always present via docling
            raise ImportError(
                f"_PyTorchEmbeddings requires transformers and torch (already CUGA deps). "
                f"Underlying error: {e}"
            )

        self._device = device or "cpu"
        # Models load with float32 — float16 trades precision for speed but
        # is fiddly on CPU. We default to float32; a user wanting fp16 can
        # set torch_dtype via the engine if we expose it later.
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModel.from_pretrained(model_name).to(self._device)
        self._model.eval()
        self._max_seq_len = getattr(self._tokenizer, "model_max_length", 512) or 512
        # Some tokenizers report a sentinel like 1e30 for "no limit"; cap to 512.
        if self._max_seq_len > 8192:
            self._max_seq_len = 512

    @staticmethod
    def _mean_pool(last_hidden_state, attention_mask):
        """Mean-pool token embeddings over the attention mask."""
        import torch

        mask = attention_mask.unsqueeze(-1).expand(last_hidden_state.size()).float()
        summed = torch.sum(last_hidden_state * mask, dim=1)
        counts = torch.clamp(mask.sum(dim=1), min=1e-9)
        return summed / counts

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        import torch

        enc = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self._max_seq_len,
            return_tensors="pt",
        ).to(self._device)
        with torch.no_grad():
            out = self._model(**enc)
        pooled = self._mean_pool(out.last_hidden_state, enc["attention_mask"])
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        # Move back to CPU before .tolist() — MPS tensors need explicit transfer.
        return pooled.detach().to("cpu").tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Smaller internal batch keeps MPS memory bounded; outer batching is
        # done by the adapter for progress granularity.
        BATCH = 32
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            out.extend(self._embed_batch(texts[i : i + BATCH]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]


def _is_local_http_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _bool_extra(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


class _LiteLLMEmbeddings(Embeddings):
    """LangChain Embeddings adapter around litellm.embedding.

    LiteLLM gives a unified embedding interface across providers: model names
    carry the provider prefix (``openai/text-embedding-3-small``,
    ``cohere/embed-english-v3.0``, ``azure/my-deployment``, ...) and LiteLLM
    routes to the right backend. We surface this as a first-class CUGA
    embedding provider so users don't have to know the OpenAI-compatible
    proxy convention.
    """

    def __init__(
        self,
        model: str,
        api_key: str = "",
        base_url: str = "",
        extra_params: dict[str, Any] | None = None,
    ):
        # Imported here so the module load doesn't pay litellm's import cost
        # for users who don't pick this provider.
        try:
            import litellm  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "LiteLLM embedding provider requires the 'litellm' package "
                "(already a CUGA dependency). Original error: " + str(e)
            )
        if not (model or "").strip():
            raise ValueError(
                "litellm embedding provider requires an explicit model name "
                "with the provider prefix, e.g. 'openai/text-embedding-3-small'."
            )
        self._model = model.strip()
        self._api_key = (api_key or "").strip() or None
        base_url_clean = (base_url or "").strip()
        allow_insecure_transport = _bool_extra((extra_params or {}).get("allow_insecure_transport"))
        if base_url_clean:
            parsed = urlparse(base_url_clean)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("LiteLLM embedding_base_url must start with https:// or http://localhost")
            if (
                parsed.scheme == "http"
                and not allow_insecure_transport
                and not _is_local_http_host(parsed.hostname)
            ):
                raise ValueError(
                    "LiteLLM embedding_base_url must use https:// for remote hosts. "
                    "Use embedding_extra_params.allow_insecure_transport=true only for trusted internal networks."
                )
        self._base_url = base_url_clean or None
        # Provider-specific extras (Azure api_version, Bedrock region, ...).
        # Reserved kwargs that we set ourselves are filtered out to avoid
        # surprising override of api_base/api_key/model/input.
        _reserved = {"model", "input", "api_key", "api_base", "allow_insecure_transport"}
        self._extra_params: dict[str, Any] = {
            k: v for k, v in (extra_params or {}).items() if k not in _reserved
        }

    def _embed(self, texts: list[str]) -> list[list[float]]:
        from litellm import embedding as litellm_embedding

        kwargs: dict[str, Any] = {"model": self._model, "input": texts}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["api_base"] = self._base_url
        if self._extra_params:
            kwargs.update(self._extra_params)
        resp = litellm_embedding(**kwargs)
        # LiteLLM normalises to an OpenAI-shaped response object.
        # ``resp.data`` is a list of {"embedding": [...], "index": ...}.
        data = list(resp.data) if hasattr(resp, "data") else resp["data"]
        # LiteLLM does NOT guarantee returned order matches input order — sort by index.
        ordered = sorted(
            data, key=lambda d: int(d.get("index", 0) if isinstance(d, dict) else getattr(d, "index", 0))
        )
        out: list[list[float]] = []
        for item in ordered:
            vec = item["embedding"] if isinstance(item, dict) else item.embedding
            out.append(list(vec))
        if len(out) != len(texts):
            raise RuntimeError(
                f"litellm returned {len(out)} vectors for {len(texts)} inputs (model={self._model!r})"
            )
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


def _fastembed_docling_seq_limit(model_name: str) -> int:
    """Upper input length (tokens) for the configured fastembed ONNX model."""
    m = (model_name or "").lower()
    if "m-long" in m or "embed-m-long" in m:
        return 2048
    return 512


@functools.lru_cache(maxsize=1)
def _tiktoken_docling_tokenizer_cls():
    """HybridChunker tokenizer backed by tiktoken — for OpenAI-family providers.

    Why: Docling's default HybridChunker downloads ``sentence-transformers/
    all-MiniLM-L6-v2`` (~90 MB) from HuggingFace just to count tokens. For
    users on openai / openrouter / litellm (i.e. OpenAI-compatible cloud
    embeddings), the EMBEDDING model uses tiktoken's cl100k_base BPE, NOT
    MiniLM's word-piece. So:

      1. We avoid the 90 MB download (tiktoken's encodings ship in the wheel)
      2. Chunk boundaries fall at the SAME token positions the embedder will
         count, so ``max_tokens`` is honest (chunks neither overflow nor
         under-fill the embedder's context).

    For non-OpenAI models behind these providers (Cohere, Mistral via
    OpenRouter / LiteLLM), cl100k_base is a reasonable BPE approximation —
    still much closer than MiniLM's word-piece.
    """
    from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer

    class _TiktokenDoclingTokenizer(BaseTokenizer):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        encoding: Any
        max_tokens: int

        def count_tokens(self, text: str) -> int:
            return len(self.encoding.encode(text, disallowed_special=()))

        def get_max_tokens(self) -> int:
            return self.max_tokens

        def get_tokenizer(self) -> Any:
            return self.encoding

    return _TiktokenDoclingTokenizer


# Route prefixes LiteLLM / OpenRouter / watsonx use to address embedders.
# Stripped once before checking tiktoken / HF Hub naming. Single-strip only —
# ``litellm/openai/text-embedding-3-small`` -> ``openai/text-embedding-3-small``.
_LITELLM_ROUTE_PREFIXES = (
    "openai/",
    "azure/",
    "openrouter/",
    "litellm/",
    "watsonx/",
    "ibm/",
    "huggingface/",
    "hf/",
)


def _strip_litellm_route_prefix(name: str) -> str:
    """Strip ONE matching route prefix (case-insensitive). Single-strip so
    ``litellm/openai/x`` becomes ``openai/x``, not ``x``."""
    n = (name or "").strip()
    lo = n.lower()
    for p in _LITELLM_ROUTE_PREFIXES:
        if lo.startswith(p):
            return n[len(p) :]
    return n


# Aliases for litellm/openrouter model names that DON'T match a public HF
# repo path directly. Saves one Hub HEAD miss per process per unknown
# repo (cf. workflow w5i1mbchd synth, R1+R3 fix #3). Conservative:
# only entries where the public HF mirror's existence is well-known.
# Add via PR, not at runtime — the value of the map is that it's
# auditable, not exhaustive.
#
# Note: cohere/voyage embeddings DON'T have public HF tokenizer
# mirrors (Cohere and Voyage publish models but not their tokenizers
# as standalone HF repos). They fall through to the approximate kind
# via char-based RecursiveCharacterTextSplitter, which is the correct
# behavior for closed-vendor embedders.
_HF_REPO_ALIASES: dict[str, str] = {
    # Jina embeddings — litellm exposes ``jina_ai/jina-embeddings-v3``,
    # ``jina-embeddings-v3`` and similar shorter forms; the canonical
    # HF path is ``jinaai/`` (no underscore).
    "jina-embeddings-v3": "jinaai/jina-embeddings-v3",
    "jina-embeddings-v2-base-en": "jinaai/jina-embeddings-v2-base-en",
    "jina_ai/jina-embeddings-v3": "jinaai/jina-embeddings-v3",
    # Nomic embeddings — the ``nomic-ai/`` org prefix isn't always set
    # explicitly by users; map the bare name.
    "nomic-embed-text-v1.5": "nomic-ai/nomic-embed-text-v1.5",
    "nomic-embed-text-v1": "nomic-ai/nomic-embed-text-v1",
}


def _hf_repo_id_candidate(model_name: str) -> Optional[str]:
    """Return the stripped model name as a CANDIDATE HF repo id (e.g.
    ``intfloat/multilingual-e5-large``) when ``model_name`` looks
    HF-style (contains a slash after route-prefix stripping).
    The loader (``_load_hf_tokenizer_for_chunking``) does the
    actual ``AutoTokenizer.from_pretrained`` and decides via try/except
    whether the repo exists on the Hub. We let HF Hub be the source of
    truth instead of maintaining a curated allow-list (deleted in PR-A,
    cf. workflow ``w9y9xtyse`` synth) — failed lookups log + lru_cache
    the None so each unknown model costs at most one Hub HEAD per
    process.

    Aliases (``_HF_REPO_ALIASES``) are consulted FIRST so litellm
    model names like ``jina-embeddings-v3`` resolve to the canonical
    HF repo ``jinaai/jina-embeddings-v3`` without a wasted 404 HEAD."""
    stripped = _strip_litellm_route_prefix(model_name)
    alias = _HF_REPO_ALIASES.get(stripped.lower())
    if alias is not None:
        return alias
    if "/" not in stripped:
        return None
    return stripped


@functools.lru_cache(maxsize=8)
def _load_hf_tokenizer_cached(repo_id: str):
    """Inner cache for ``_load_hf_tokenizer_for_chunking``. Only caches
    SUCCESSFUL loads — failures propagate to the outer wrapper, which
    logs them but does NOT cache. Reason: a transient Hub 503 used to
    permanently downgrade a model to char-based until process restart
    (workflow w5i1mbchd synth, R2's operational quirk). The cost of
    a retry on permanent 404 is one Hub HEAD per ingest (~50ms);
    cheaper than the operational pager-call on a transient 503."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(repo_id)


def _load_hf_tokenizer_for_chunking(repo_id: str):
    """Load HF tokenizer with retry-on-transient-failure semantics.
    Successes are lru_cached via ``_load_hf_tokenizer_cached``; failures
    are logged and dropped (NOT cached) so a flaky Hub doesn't lock the
    model out of HF-tokenizer-mode for the rest of the process."""
    try:
        tok = _load_hf_tokenizer_cached(repo_id)
        # [#400] Fires once per (process, repo_id) — lru_cache on the
        # inner function makes the second call a hit. Grep ``[#400]
        # hub-hit`` to count distinct HF HEADs the deleted allow-list
        # now causes us to perform.
        logger.debug(
            f"[#400] hub-hit repo={repo_id!r} model_max_length={getattr(tok, 'model_max_length', '?')}"
        )
        return tok
    except ImportError:
        logger.debug("transformers missing; HF tokenizer skipped for chunk sizing.")
        return None
    except Exception as e:
        logger.warning(
            f"[#400] hub-miss repo={repo_id!r} err={e!r}; "
            f"falling back to tiktoken. Pre-cache via HF_HOME to silence."
        )
        return None


# Backwards-compat shim. The lru_cache moved to ``_load_hf_tokenizer_cached``
# (workflow w5i1mbchd synth fix #4: only successes are cached, failures
# retry on transient Hub 503s). Existing tests call
# ``_load_hf_tokenizer_for_chunking.cache_clear()`` — forward to the
# inner cache so they don't need to know about the split.
_load_hf_tokenizer_for_chunking.cache_clear = _load_hf_tokenizer_cached.cache_clear  # type: ignore[attr-defined]


# Safety margin subtracted from the HF tokenizer's raw model_max_length
# before we hand the cap to the chunker / splitter. Covers two
# embedder-side overheads the local tokenizer doesn't see at chunk-count
# time:
#
#   (a) BOS/EOS asymmetry — ``tokenizer.encode(text)`` adds them when the
#       chunker measures, but some hosted embedders re-tokenize the
#       chunk text with their own special-token policy on top.
#   (b) e5-family ``"query: "`` / ``"passage: "`` prefix that hosted
#       providers (notably watsonx) auto-prepend before embed — 3-4
#       XLM-RoBERTa tokens the chunker never sees.
#
# User-reported smoking gun on PR #383 manual QA (cuga 16:22:28 log):
# chunker capped at 512, watsonx still rejected with
# ``This model's maximum context length is 512 tokens. However, you
#  requested 518 tokens`` — a +6 overhead consistent with prefix + BOS.
# 16 is generous enough for any current provider's wrapping; we'd
# rather lose ~3% of context window than fail ingest on edge chunks.
_HF_TOKEN_SAFETY_MARGIN = 16


def _hf_tokenizer_seq_limit(tok) -> int:
    """Return the safe chunk-token cap for this tokenizer's model.

    NOT the raw ``model_max_length`` — that's the embedder's HARD limit,
    and the chunker can't count provider-side wrapping (special tokens,
    e5 prefixes). Subtracts ``_HF_TOKEN_SAFETY_MARGIN`` so chunks at
    the returned cap survive the embedder's wrapping. See the constant's
    docstring for the user-reported bug this prevents.

    Sentinel (>= 1e6) means 'unset' → defaults to 512 - margin (BERT /
    XLM-RoBERTa convention)."""
    try:
        mml = int(getattr(tok, "model_max_length", 0) or 0)
    except (TypeError, ValueError):
        return max(1, 512 - _HF_TOKEN_SAFETY_MARGIN)
    raw = mml if 0 < mml < 1_000_000 else 512
    return max(1, raw - _HF_TOKEN_SAFETY_MARGIN)


def _resolve_tiktoken_encoding(model_name: str):
    """Pick the right tiktoken encoding for the embedding model.

    Falls back to ``cl100k_base`` (OpenAI's standard for embedding / GPT-4 era
    models) when the model isn't in tiktoken's registry — which is the right
    default for Azure-via-proxy and OpenRouter-routed models.
    """
    import tiktoken

    name = _strip_litellm_route_prefix(model_name).lower()
    try:
        return tiktoken.encoding_for_model(name)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


@dataclass(frozen=True)
class ChunkingTokenizer:
    """Single-dispatch contract: given a provider+model, what tokenizer
    sizes the chunks?

    Replaces the per-callsite branching that existed in both
    ``_build_docling_chunker`` and ``_build_text_splitter`` (each ~60 LOC
    of provider dispatch repeating the same logic). After this refactor,
    each builder switches on ``kind`` once and hands off to the
    appropriate primitive.

    Always present — never ``None``. The ``approximate`` kind covers the
    no-precise-tokenizer case (cohere / voyage / gemini / mistral-embed
    via litellm) so callers can keep a single switch.

    Two distinct caps (workflow w5i1mbchd synth, fix #2):

      * ``safe_max_tokens`` is the HARD CEILING — chunks above this will
        be truncated by the embedder (silent retrieval-quality loss).
        Already margined via ``_hf_tokenizer_seq_limit`` for HF kinds.
      * ``recommended_chunk_tokens`` is the RETRIEVAL-QUALITY DEFAULT.
        For ≥2K-ctx embedders, capped at 512 to match published
        evidence (LongEmbed EMNLP 2024, BAAI bge-m3 maintainer at HF
        discussion #59, voyage-context-3's own 512 default on its 32K
        model). Letting a chunk grow to 8K just because the embedder
        allows it makes retrieval WORSE — pooled embeddings dilute the
        signal across more concepts.

    Callers use ``min(chunk_size, safe_max_tokens)`` as today; the
    chunker builders also warn when ``chunk_size > recommended_chunk_tokens``
    so users with long-context embedders don't crank chunk_size
    thinking 'more context = better'."""

    kind: str  # Literal["hf", "tiktoken", "fastembed", "approximate"]
    encoder: Any  # HF tokenizer | tiktoken Encoding | fastembed model | None
    name: str  # repo id / "cl100k_base" / "fastembed:<model>" / "char-based"
    safe_max_tokens: int  # already margined for HF; sensible default for others
    recommended_chunk_tokens: int  # retrieval-quality default (≤512 for long-ctx)


# OpenAI text-embedding-3-* hard limit. The API rejects at >8191 tokens
# with InvalidRequestError. Subtracting the same safety margin used for
# HF embedders so the tiktoken branch is internally consistent (workflow
# w5i1mbchd synth, fix #1: previously this branch used 8192 with zero
# margin, an off-by-one + missing-margin both flagged by R1 and R2).
_OPENAI_TIKTOKEN_HARD_CAP = 8191
_TIKTOKEN_SAFE_MAX = _OPENAI_TIKTOKEN_HARD_CAP - _HF_TOKEN_SAFETY_MARGIN  # 8175

# Sensible char-based fallback cap when no precise tokenizer is
# available. 8192 lets the user's chunk_size knob remain the effective
# limit; the char-based splitter doesn't risk an embedder-side
# context-window-exceeded error because it splits by chars, not tokens.
_DEFAULT_APPROXIMATE_CAP = 8192


@functools.lru_cache(maxsize=32)
def _warn_chunk_oversized_for_retrieval(
    model_name: str, chunk_size: int, recommended: int, safe_max: int
) -> None:
    """Dedup'd one-shot warning when a user's chunk_size is within the
    hard ceiling but above the retrieval-quality recommendation. The
    lru_cache key makes this fire at most once per (model, chunk_size,
    recommended) tuple per process — chunker rebuilds on every file
    ingest don't spam the log. Workflow w5i1mbchd synth fix #2."""
    logger.warning(
        f"[#400] chunk_size={chunk_size} exceeds retrieval-quality "
        f"recommendation {recommended} for embedder {model_name!r} "
        f"(hard ceiling {safe_max}). Published evidence (LongEmbed "
        f"EMNLP 2024 +24% MRR, voyage-context-3 default, BAAI bge-m3 "
        f"maintainer) shows 256-512 token chunks retrieve BETTER than "
        f"larger chunks regardless of embedder context. Consider "
        f"chunk_size={recommended} in config unless you have a "
        f"specific reason to chunk larger."
    )


def _recommended_chunk_tokens(safe_max: int) -> int:
    """Retrieval-quality default. For ≥2K-ctx embedders, cap at 512
    regardless of the hard ceiling. For <2K-ctx (bge-small at 496,
    e5-large at 496) just use the full safe_max — there's no quality
    reason to chunk smaller than the embedder's own window.

    Evidence (workflow w5i1mbchd synth):
      - LongEmbed (EMNLP 2024): 512-chunking outperforms 1024+ by
        +24% MRR on long-context retrieval benchmarks.
      - BAAI maintainer (bge-m3 HF discussion #59): 'despite 8192 ctx,
        512-token chunks remain recommended default'.
      - Voyage AI: voyage-context-3 ships with chunk_size=512 default
        on its OWN 32K-context model.
      - Cohere: recommends 300 of their 512-token window.
      - Jina: recommends 128-512 of jina-v3's 8192."""
    return min(safe_max, 512) if safe_max >= 2048 else safe_max


@functools.lru_cache(maxsize=1)
def _fastembed_docling_tokenizer_cls():
    """HybridChunker tokenizer using fastembed's Rust tokenizer (avoids HF MiniLM download)."""
    from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer

    class _FastEmbedDoclingTokenizer(BaseTokenizer):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        text_embedding: Any
        max_tokens: int

        def _rust_tokenizer(self):
            inner = self.text_embedding.model
            if inner.tokenizer is None:
                inner.load_onnx_model()
            return inner.tokenizer

        def count_tokens(self, text: str) -> int:
            enc = self._rust_tokenizer().encode(text)
            return int(sum(enc.attention_mask))

        def get_max_tokens(self) -> int:
            return self.max_tokens

        def get_tokenizer(self) -> Any:
            return self._rust_tokenizer()

    return _FastEmbedDoclingTokenizer


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def create_embeddings(config: "KnowledgeConfig") -> Embeddings:
    """Create an Embeddings instance for the configured provider.

    Providers:
        fastembed   — lightweight local embeddings (default, installed with cuga)
        huggingface — HuggingFace sentence-transformers (optional: pip install sentence-transformers)
        openai      — OpenAI API (requires api_key); also covers OpenAI-compatible
                      endpoints when ``embedding_base_url`` is set (Together, Fireworks, ...)
        ollama      — local Ollama server
        openrouter  — OpenRouter (https://openrouter.ai). Single key for many
                      embedding models from openrouter.ai/models?output_modalities=embeddings.
                      Requires both ``embedding_api_key`` and an explicit
                      ``embedding_model`` (e.g. "openai/text-embedding-3-small").
                      Base URL auto-set to https://openrouter.ai/api/v1 unless
                      the user overrides ``embedding_base_url``.
    """
    import os

    provider = config.embedding_provider
    model = config.embedding_model

    device, onnx_providers = _detect_accelerator(config.use_gpu)

    if provider == "fastembed":
        return _FastEmbedEmbeddings(
            model or "BAAI/bge-small-en-v1.5",
            providers=onnx_providers,
        )

    if provider == "huggingface":
        # Dep-free PyTorch path. Uses transformers + torch (both already
        # CUGA deps via docling) — no sentence-transformers needed.
        # Honors detected device: 'mps' on Apple Silicon, 'cuda' on NVIDIA,
        # 'cpu' otherwise. Verified ~8x faster on Mac MPS vs fastembed CPU.
        return _PyTorchEmbeddings(
            model_name=(model or "BAAI/bge-small-en-v1.5"),
            device=device,
        )

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        # Strip whitespace: copy-paste from a docs page or terminal often leaves
        # a trailing newline / space, which then silently fails auth.
        # Empty key at construction time is allowed so an imported published
        # snapshot (secrets stripped) can apply on a machine where
        # OPENAI_API_KEY is set in the env at embed time. Actual auth failure
        # surfaces at first embed call, not at config-apply time.
        api_key = (config.embedding_api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
        if not api_key:
            logger.warning(
                "OpenAI embedding provider has no API key in config or OPENAI_API_KEY env. "
                "Embeddings will fail at first call unless the env var is set before then."
            )
        model_clean = (model or "text-embedding-3-small").strip()
        # langchain-openai accepts empty string at init (only fails at first HTTP call).
        # Pass a placeholder when truly empty so SecretStr validation doesn't trip.
        kwargs: dict[str, Any] = {"model": model_clean, "api_key": api_key or "MISSING"}
        base_url_clean = (config.embedding_base_url or "").strip()
        if base_url_clean:
            kwargs["base_url"] = base_url_clean
            # When pointed at a non-OpenAI OpenAI-compatible endpoint (Together,
            # Fireworks, custom proxy, ...), tiktoken tokenization is unsafe:
            # those providers often only accept raw strings, not the integer
            # arrays tiktoken produces. langchain-openai's default sends ints
            # for OpenAI model names; flipping both flags here forces raw text.
            kwargs["tiktoken_enabled"] = False
            kwargs["check_embedding_ctx_length"] = False
        # Provider-specific extras (e.g. Azure: api_version, deployment, organization).
        # langchain-openai's OpenAIEmbeddings accepts these as kwargs.
        if config.embedding_extra_params:
            _reserved = {"model", "api_key", "base_url"}
            for k, v in config.embedding_extra_params.items():
                if k in _reserved:
                    continue
                kwargs[k] = v
        return OpenAIEmbeddings(**kwargs)

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        base_url = config.embedding_base_url or "http://localhost:11434"
        return OllamaEmbeddings(model=model or "nomic-embed-text", base_url=base_url)

    if provider == "openrouter":
        # OpenRouter is OpenAI-compatible — reuse langchain-openai's client.
        # Both api_key and model are REQUIRED (no defaults): the user picks
        # an embeddings model from openrouter.ai/models?output_modalities=embeddings
        # and pastes their key. embedding_base_url is auto-set but honored if
        # the user overrides it (escape hatch if OpenRouter ever changes URL).
        from langchain_openai import OpenAIEmbeddings

        # Strip whitespace to defend against copy-paste with a trailing
        # newline/space — silently fails auth otherwise.
        # Empty key allowed at construction time — see openai branch comment;
        # actual auth failure surfaces at first embed call.
        api_key = (config.embedding_api_key or os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not api_key:
            logger.warning(
                "OpenRouter embedding provider has no API key in config or OPENROUTER_API_KEY env. "
                "Embeddings will fail at first call unless the env var is set before then."
            )
        model_clean = (model or "").strip()
        if not model_clean:
            raise ValueError(
                "OpenRouter embedding provider requires an explicit model name. "
                "Pick one from https://openrouter.ai/models?output_modalities=embeddings "
                "(e.g. 'openai/text-embedding-3-small') and set "
                "knowledge.embeddings.model accordingly."
            )
        # Defense in depth: an `embedding_base_url` left over from a previous
        # provider (e.g. a corporate LiteLLM proxy URL) would otherwise send
        # the user's OpenRouter key to the wrong host. Only honor a custom
        # base_url here if it actually looks like OpenRouter; otherwise fall
        # back to the canonical OpenRouter URL and warn.
        user_base = (config.embedding_base_url or "").strip()
        if user_base and "openrouter" not in user_base.lower():
            logger.warning(
                "OpenRouter provider ignoring non-OpenRouter base_url={!r}; "
                "falling back to {}. Clear embedding_base_url to silence this.",
                user_base,
                OPENROUTER_BASE_URL,
            )
            user_base = ""
        base_url = (user_base or OPENROUTER_BASE_URL).strip()
        # OpenRouter routes many non-OpenAI models (BAAI, Mistral, Qwen,
        # Nvidia, ...). Most of those reject the tiktoken-encoded integer
        # arrays langchain-openai sends by default ("Input should be a
        # valid string", HTTP 422). Force raw-string mode so EVERY model
        # in OpenRouter's catalog works, including the OpenAI-prefixed
        # ones (which accept both shapes).
        return OpenAIEmbeddings(
            model=model_clean,
            # Placeholder when key is missing — see openai branch comment.
            api_key=api_key or "MISSING",
            base_url=base_url,
            tiktoken_enabled=False,
            check_embedding_ctx_length=False,
        )

    if provider == "litellm":
        # LiteLLM gives a single embedding interface across providers — the
        # user picks a model with a provider prefix
        # (``openai/text-embedding-3-small``, ``cohere/embed-english-v3.0``,
        # ``azure/<deployment>``, ``bedrock/cohere.embed-english-v3``, ...)
        # and LiteLLM routes accordingly. API key lookup order:
        #   1. config.embedding_api_key (explicit)
        #   2. provider-specific env var (e.g. OPENAI_API_KEY) — LiteLLM handles this
        # base_url is honored if set (useful for self-hosted LiteLLM proxies).
        api_key = (config.embedding_api_key or "").strip()
        # When the model is OpenAI-prefixed, fall back to OPENAI_API_KEY so
        # users get the same UX as the openai provider without re-pasting.
        if not api_key and (model or "").strip().lower().startswith("openai/"):
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        return _LiteLLMEmbeddings(
            model=(model or "").strip(),
            api_key=api_key,
            base_url=(config.embedding_base_url or "").strip(),
            extra_params=dict(config.embedding_extra_params or {}),
        )

    raise ValueError(
        f"Unknown embedding provider: {provider}. "
        "Supported: fastembed, huggingface, openai, ollama, openrouter, litellm"
    )


def _get_embedding_dim(embeddings: Embeddings) -> int:
    """Get embedding dimension by embedding a test string."""
    test_vec = embeddings.embed_query("test")
    return len(test_vec)


# --- Engine ---


class KnowledgeEngine:
    """In-process knowledge engine (chunking, embeddings, pluggable vector backends)."""

    def __init__(self, config: KnowledgeConfig, chat_generator: Any = None):
        config.validate()
        self._config = config
        # Optional, host-injected chat model for query transformation (multi_query
        # / HyDE). Duck-typed to the query_transform.ChatGenerator Protocol so the
        # knowledge package stays standalone (no host import). None → transforms
        # are inert and search runs on the plain query.
        self._chat_generator = chat_generator
        self._files_dir = config.persist_dir / "files"
        _smode, _, _pghost = get_storage_connection_params()
        self._metadata = create_knowledge_metadata(config.persist_dir, mode=_smode, postgres_url=_pghost)

        # Ensure directories exist
        config.persist_dir.mkdir(parents=True, exist_ok=True)
        self._files_dir.mkdir(parents=True, exist_ok=True)

        # Single-writer lock (flock / msvcrt — race-free)
        self._lock_file = open(config.persist_dir / ".lock", "w+b")
        try:
            acquire_exclusive_nonblocking(self._lock_file)
        except OSError:
            self._lock_file.close()
            raise RuntimeError("Knowledge engine already running in another process. Start with --workers 1")

        # Default embeddings (lazy — initialized on first use to speed up startup)
        self._default_embeddings = None
        self._default_embedding_dim = None

        # Vector store LRU cache (bounded)
        self._vector_stores: collections.OrderedDict[str, VectorStoreAdapter] = collections.OrderedDict()

        # Record managers for dedup (InMemoryRecordManager per collection)
        self._record_managers: dict[str, InMemoryRecordManager] = {}

        # Docling converter (lazy, reused across all document loads)
        # Docling converters are cached per-mode (fast / balanced / accurate)
        # so flipping ``docling_pdf_mode`` in settings does not require a
        # process restart. Keyed by the mode string.
        self._docling_converters: dict[str, Any] = {}

        self._vector_store_lock = threading.Lock()

        # Per-collection async ingest locks
        self._collection_locks: dict[str, asyncio.Lock] = {}

        # Bumped by ``commit_knowledge_update`` on embedder/chunking/metric
        # change. Workers capture at start, recheck per batch, raise
        # ``ReindexSupersededError`` if it moved past them.
        self._apply_generation: int = 0

        # Bounds concurrent Docling parses process-wide (the heavy, GPU/CPU-bound
        # step). Inserts still serialize per-collection via the lock above.
        # ponytail: one global semaphore; per-collection parse limits if a single
        # collection ever needs to starve others.
        self._ingest_sem = asyncio.Semaphore(config.max_ingest_workers)

        # Active tasks for cancellation
        self._active_tasks: dict[str, asyncio.Event] = {}

        # Reindex coordination flags (in-memory, single-process only — flock ensures this)
        self._reindex_in_progress: set[str] = set()
        self._reindex_deferred: set[str] = set()

        # Cached live-availability probe of the active embedder, surfaced in
        # health() so the UI can warn when a collection's vectors are stranded
        # behind an unreachable embedder. (vector_config_hash, available, error,
        # monotonic_ts); invalidated on config apply. ponytail: a single cached
        # tuple, not a probe-scheduler.
        self._embedder_probe_cache: tuple[str, bool, str | None, float] | None = None

        # Background tasks
        self._shutdown_event = asyncio.Event()
        self._background_tasks: list[asyncio.Task] = []

        self._metadata_ready = False
        self._metadata_init_lock = asyncio.Lock()

        from cuga.config import settings as _settings

        _vb = knowledge_vector_backend_for_settings(_settings)
        _sm = getattr(getattr(_settings, "storage", None), "mode", "local")
        _device, _providers = _detect_accelerator(config.use_gpu)
        logger.info(
            f"Knowledge engine started: "
            f"storage.mode={_sm} vector_backend={_vb}, "
            f"embedding={config.embedding_provider}/{config.embedding_model or 'auto'}, "
            f"accelerator={_device} (use_gpu={config.use_gpu}, onnx_providers={_providers}), "
            f"metric={config.metric_type}, "
            f"persist_dir={config.persist_dir}"
        )
        # GPU-transparency check: a single-line warning conflated three
        # very different failure modes — operators read it and pick the
        # wrong remediation. This now distinguishes:
        #
        #   (a) GPU image, no device visible  — user forgot ``--gpus all``
        #       or the K8s pod lacks ``nvidia.com/gpu`` request. Fix is to
        #       pass the device into the container, NOT to rebuild.
        #
        #   (b) CPU image, GPU requested      — user requested the GPU
        #       runtime but only the CPU build is shipped today. GPU
        #       image is deferred to a follow-up release.
        #
        #   (c) Partial GPU                   — torch sees CUDA but ORT is
        #       CPU-only. Embed runs on CPU even though reranker / Docling-
        #       transformers will use CUDA. Fix is the GPU image (which
        #       ships fastembed-gpu).
        #
        # ``gpu_required=True`` (or env CUGA_GPU_REQUIRED=1) converts these
        # warnings into a fatal RuntimeError — the vLLM/TGI-style fail-fast
        # posture for operators who never want silent CPU regressions.
        if config.use_gpu:
            import os as _os

            only_cpu_onnx = _providers == ["CPUExecutionProvider"]
            try:
                import torch  # noqa: F401

                _torch_cuda = torch.cuda.is_available()
            except Exception:
                _torch_cuda = False
            _gpu_build = _os.environ.get("CUGA_GPU_BUILD") == "1"
            _gpu_required = bool(getattr(config, "gpu_required", False)) or (
                _os.environ.get("CUGA_GPU_REQUIRED") == "1"
            )

            msg: str | None = None
            if only_cpu_onnx and not _torch_cuda:
                if _gpu_build:
                    # Case (a): GPU image but no GPU visible to the container.
                    msg = (
                        "use_gpu=True and CUGA_GPU_BUILD=1 (this is the GPU image), "
                        "but no GPU device is visible to the container — embed, "
                        "reranker, and Docling will all run on CPU. Fix: pass "
                        "`--gpus all` to `docker run` (local) or add `resources.limits."
                        "nvidia.com/gpu: 1` to the pod spec (K8s). Also confirm the "
                        "host has NVIDIA Container Toolkit installed and the node "
                        "has nvidia-device-plugin."
                    )
                else:
                    # Case (b): CPU image, GPU requested.
                    msg = (
                        "use_gpu=True but onnxruntime is CPU-only — embed, reranker, "
                        "and Docling will all run on CPU. Run the GPU image "
                        "(Dockerfile.gpu) or, on a CUDA host, `uv sync --extra gpu "
                        "--no-install-package onnxruntime`. Or set use_gpu=False to "
                        "silence this warning."
                    )
            elif only_cpu_onnx and _torch_cuda:
                # Case (c): partial GPU.
                msg = (
                    "use_gpu=True: torch sees CUDA but onnxruntime is CPU-only — "
                    "reranker + Docling-transformers will use CUDA, but fastembed "
                    "(embedder) + Docling-onnx layout will run on CPU. Add the GPU "
                    "runtime: `uv sync --extra gpu --no-install-package onnxruntime` "
                    "(or run Dockerfile.gpu)."
                )

            if msg:
                if _gpu_required:
                    raise RuntimeError("[cuga] gpu_required=True but GPU runtime is not loaded. " + msg)
                logger.warning(msg)
            elif _torch_cuda and config.max_ingest_workers <= 2:
                # GPU is fully wired but ingest concurrency is sized for a
                # laptop. Expert D (2026-06-09 Docling deep-dive): a single
                # H100 fits 10-14 workers (4-6 GB VRAM each); leaving the
                # default at 2 leaves ~80% of the GPU idle on ingest-heavy
                # workloads. Worth a one-line nudge — not an error because
                # the operator may have set it deliberately for a small
                # GPU (T4 / MIG slice / shared device).
                logger.warning(
                    "use_gpu=True with CUDA engaged but max_ingest_workers=%d. "
                    "An H100/A100 fits ~8-14 Docling workers; raise "
                    "knowledge.engine.max_ingest_workers (or set ``CUGA_MAX_INGEST_WORKERS`` "
                    "env) to amortize GPU. Keep at 2-4 on T4 / MIG / shared GPUs.",
                    config.max_ingest_workers,
                )

    def start_background_tasks(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start background maintenance tasks. Call after event loop is running."""

        async def _maintenance_loop():
            while not self._shutdown_event.is_set():
                try:
                    await asyncio.sleep(3600)  # every hour
                    if self._shutdown_event.is_set():
                        break
                    if self._metadata_ready:
                        await self._reconcile_deletes()
                        await self._metadata.purge_old_tasks(max_age_days=7)
                        await self._cleanup_expired_sessions()
                    logger.debug("Background maintenance completed")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Background maintenance error: {e}")

        task = asyncio.ensure_future(_maintenance_loop())
        self._background_tasks.append(task)

    async def _ensure_metadata_ready(self) -> None:
        if self._metadata_ready:
            return
        async with self._metadata_init_lock:
            if self._metadata_ready:
                return
            await self._metadata.ensure_ready()
            recovered = await self._metadata.recover_stale_tasks()
            if recovered:
                logger.info(f"Recovered {recovered} stale task(s) from previous crash")
            await self._reconcile_deletes()
            purged = await self._metadata.purge_old_tasks(max_age_days=7)
            if purged:
                logger.debug(f"Purged {purged} old task(s)")
            self._metadata_ready = True

    async def aclose(self) -> None:
        """Close all engine-held async resources.

        Required at the end of any event loop that called engine methods
        which acquired a pool — otherwise the next loop's first call
        would re-use a pool bound to the closed loop and raise
        ``RuntimeError: ... attached to a different loop``. Production
        runs on a single long-lived loop and rarely hits this; tests
        and short-lived script harnesses MUST call aclose() before the
        loop exits.
        """
        try:
            await self._metadata.close()
        except Exception as e:
            logger.debug(f"Knowledge metadata close: {e}")
        # Close every cached vector store's underlying connection pool.
        # Without this, the asyncpg pool inside ProdEmbeddingStore stays
        # bound to whatever loop first called ``_get_pool()`` — fine in
        # production (one process, one loop) but a foot-gun for tests
        # that use ``asyncio.run`` per call.
        for collection, adapter in list(self._vector_stores.items()):
            close = getattr(adapter, "close_pool", None)
            if close is None:
                continue
            try:
                await close()
            except Exception as e:
                logger.debug(f"close_pool failed for {collection}: {e}")
        self._vector_stores.clear()

    def shutdown(self) -> None:
        """Release resources."""
        self._shutdown_event.set()
        for task in self._background_tasks:
            task.cancel()
        try:
            release_exclusive(self._lock_file)
            self._lock_file.close()
        except Exception:
            pass
        logger.info("Knowledge engine stopped")

    # --- Embeddings (lazy init) ---

    def _ensure_embeddings(self) -> None:
        """Initialize embeddings on first use (not at engine startup)."""
        if self._default_embeddings is None:
            self._default_embeddings = create_embeddings(self._config)
            self._default_embedding_dim = _get_embedding_dim(self._default_embeddings)
            active = getattr(self._default_embeddings, "_active_providers", None)
            extra = f", onnx_active={active}" if active else ""
            logger.info(
                f"Embeddings initialized: provider={self._config.embedding_provider}, "
                f"model={self._config.embedding_model or '(default)'}, "
                f"dim={self._default_embedding_dim}{extra}"
            )

    async def warmup(self) -> dict[str, Any]:
        """Preload heavyweight resources so callers can gate on readiness.

        Three preloads, all in worker threads so we don't block the event loop:
          1. metadata DB (sqlite/pg schema bootstrap)
          2. embeddings (fastembed ONNX model load: ~100-300 MB)
          3. Docling layout model (~22 s on first call; 770-weight load)

        Doing these at startup means the user's FIRST ingest doesn't pay the
        cold-start tax — important because the first ingest is also when the
        user is most impatient.
        """
        import time as _time

        await self._ensure_metadata_ready()
        # Step 2: embeddings — same call existing code paths use.
        await asyncio.to_thread(self._ensure_embeddings)

        # Step 3: Docling. Only build the converter for the *configured* mode
        # — if the user later switches to a different mode it'll rebuild then.
        # Loading the layout model is the single biggest cold-start cost on
        # the parse stage, so this is where the user-visible win comes from.
        docling_t0 = _time.monotonic()
        docling_loaded = False
        docling_error: str | None = None
        try:
            # Step A: build the converter (cheap — just registers options).
            converter = await asyncio.to_thread(self._get_docling_converter)
            # Step B: FORCE the pipeline + models to load right now. Without
            # this, Docling lazy-loads the layout-model weights (~22s) on the
            # first convert() call, defeating the whole point of prewarm.
            try:
                from docling.datamodel.base_models import InputFormat

                await asyncio.to_thread(converter.initialize_pipeline, InputFormat.PDF)
            except Exception as init_err:  # pragma: no cover - defensive
                logger.warning(
                    "Docling pipeline init during prewarm raised; weights will "
                    "still lazy-load on first ingest: {}",
                    init_err,
                )
            docling_loaded = True
        except Exception as e:  # pragma: no cover - environmental
            docling_error = str(e)
            logger.warning("Docling prewarm failed (will lazy-load on first ingest): {}", e)
        docling_dt = _time.monotonic() - docling_t0
        if docling_loaded:
            logger.info(
                "Knowledge prewarm complete: embeddings + Docling (mode={!r}) loaded in {:.2f}s",
                self._config.docling_pdf_mode,
                docling_dt,
            )

        # Step 4: reranker — only when a profile has it enabled. Load the
        # cross-encoder now so the first search doesn't pay the model load
        # (~1-3s). Best-effort: on failure search degrades to fusion ranking.
        reranker_prewarmed = False
        if getattr(self._config, "rerank_enabled", False):
            try:
                from cuga.backend.knowledge.reranker import prewarm as _rerank_prewarm

                await asyncio.to_thread(
                    _rerank_prewarm, str(getattr(self._config, "rerank_model", "BAAI/bge-reranker-base"))
                )
                reranker_prewarmed = True
            except Exception as e:  # pragma: no cover - environmental
                logger.warning("Reranker prewarm failed (will background-load on first search): {}", e)

        return {
            "embedding_provider": self._config.embedding_provider,
            "embedding_model": self._config.embedding_model or "auto",
            "embeddings_initialized": self._default_embeddings is not None,
            "docling_prewarmed": docling_loaded,
            "docling_prewarm_seconds": round(docling_dt, 2),
            "docling_prewarm_error": docling_error,
            "reranker_prewarmed": reranker_prewarmed,
        }

    def accelerator_status(self) -> dict[str, Any]:
        """Return what hardware acceleration is requested + actually engaged.

        Surfaces this so the UI can show users the truth next to the
        ``use_gpu`` toggle (e.g. "GPU requested but CPU is loaded").
        """
        device, requested = _detect_accelerator(self._config.use_gpu)
        provider = (self._config.embedding_provider or "").lower()
        cloud_providers = {"openai", "openrouter", "ollama"}
        emb_relevant = provider not in cloud_providers
        active: list[str] = []
        if emb_relevant and self._default_embeddings is not None:
            active = list(getattr(self._default_embeddings, "_active_providers", []) or [])
        # Map ONNX EP -> friendly label for the UI.
        if not emb_relevant:
            label = "N/A — cloud embedding provider"
        elif not self._config.use_gpu:
            label = "CPU (forced via use_gpu=False)"
        elif device == "cuda":
            label = "NVIDIA CUDA"
        elif device == "mps":
            label = "Apple GPU (CoreML/MPS)"
        else:
            label = "CPU (no GPU detected)"
        # Honest mismatch flag: requested non-CPU but only CPU loaded.
        mismatch = bool(
            emb_relevant
            and self._config.use_gpu
            and device != "cpu"
            and active
            and not any(p != "CPUExecutionProvider" for p in active)
        )
        # Where the API key actually came from — config UI ("ui"), one of the
        # provider-specific env vars ("env:OPENAI_API_KEY"), or "missing".
        key_source = self._resolve_key_source()
        return {
            "use_gpu": self._config.use_gpu,
            "embedding_provider": self._config.embedding_provider,
            "embedding_model": self._config.embedding_model or "",
            "embedding_relevant": emb_relevant,
            "device_label": label,
            "device": device,
            "providers_requested": requested,
            "providers_active": active,
            "fallback_to_cpu": mismatch,
            "key_source": key_source,
        }

    def _resolve_key_source(self) -> dict[str, Any]:
        """Where is the key being used coming from? UI / env / missing."""
        import os as _os

        provider = (self._config.embedding_provider or "").lower()
        # Providers that don't need a key
        if provider in ("fastembed", "huggingface", "ollama"):
            return {"required": False, "source": "n/a"}
        if (self._config.embedding_api_key or "").strip():
            return {"required": True, "source": "ui"}
        # Env-var fallback paths must match what create_embeddings actually uses.
        if provider == "openai" and _os.environ.get("OPENAI_API_KEY"):
            return {"required": True, "source": "env:OPENAI_API_KEY"}
        if provider == "openrouter" and _os.environ.get("OPENROUTER_API_KEY"):
            return {"required": True, "source": "env:OPENROUTER_API_KEY"}
        if provider == "litellm":
            model = (self._config.embedding_model or "").lower()
            if model.startswith("openai/") and _os.environ.get("OPENAI_API_KEY"):
                return {"required": True, "source": "env:OPENAI_API_KEY"}
            # Other LiteLLM providers (cohere/azure/bedrock) have their own env vars
            # — we don't enumerate them here; LiteLLM reads them at call time.
            return {"required": True, "source": "env:provider-specific (LiteLLM)"}
        return {"required": True, "source": "missing"}

    def _knowledge_vector_backend(self) -> str:
        from cuga.config import settings

        return knowledge_vector_backend_for_settings(settings)

    # --- Vector store (LRU cache, bounded) ---

    def _get_record_manager(self, collection: str) -> InMemoryRecordManager:
        if collection not in self._record_managers:
            rm = InMemoryRecordManager(namespace=collection)
            rm.create_schema()
            self._record_managers[collection] = rm
        return self._record_managers[collection]

    async def _resolve_embeddings_for_collection(self, collection: str) -> Embeddings:
        self._ensure_embeddings()
        cfg = await self._metadata.get_collection_config(collection)
        if cfg:
            pinned_provider = cfg["embedding_provider"]
            pinned_model = cfg["embedding_model"]
            if (
                pinned_provider == self._config.embedding_provider
                and pinned_model == self._config.embedding_model
            ):
                return self._default_embeddings
            from dataclasses import replace

            pinned_cfg = replace(
                self._config, embedding_provider=pinned_provider, embedding_model=pinned_model
            )
            return create_embeddings(pinned_cfg)
        return self._default_embeddings

    def _create_vector_adapter(self, collection: str, embeddings: Embeddings):
        from cuga.backend.knowledge.vector_store import create_vector_store

        # C4 (issue #183 step 4): network embedders benefit from concurrent
        # sub-batches; local ones (fastembed/huggingface) do not because their
        # runtimes already use internal threads. The engine knows the provider
        # so it makes the dispatch decision once, here, rather than at every
        # embed call.
        # ``litellm`` proxies to network providers (OpenAI / Azure / OpenRouter
        # via its prefix scheme) so it belongs in the network bucket — without
        # it, the adapter would serialise embed against a cloud endpoint and
        # hit provider rate-limit WAF responses on large ingests.
        embedder_is_network = self._config.embedding_provider in (
            "openai",
            "ollama",
            "openrouter",
            "litellm",
        )
        return create_vector_store(
            backend=self._knowledge_vector_backend(),
            collection=collection,
            embeddings=embeddings,
            persist_dir=self._config.persist_dir,
            metric_type=self._config.metric_type,
            pgvector_connection_string=self._config.pgvector_connection_string,
            embedding_batch_size=self._config.embedding_batch_size,
            embedding_concurrency=self._config.embedding_concurrency,
            embedder_is_network=embedder_is_network,
            vector_insert_batch_size=self._config.vector_insert_batch_size,
        )

    def _vector_cache_put(self, collection: str, adapter: VectorStoreAdapter) -> None:
        while len(self._vector_stores) >= _VS_CACHE_MAX:
            evicted_name, _ = self._vector_stores.popitem(last=False)
            logger.debug(f"Evicted vector store cache: {evicted_name}")
        self._vector_stores[collection] = adapter

    async def _ensure_vector_store_cached(self, collection: str) -> None:
        if collection in self._vector_stores:
            self._vector_stores.move_to_end(collection)
            return
        embeddings = await self._resolve_embeddings_for_collection(collection)

        def _sync_put() -> None:
            with self._vector_store_lock:
                if collection in self._vector_stores:
                    self._vector_stores.move_to_end(collection)
                    return
                adapter = self._create_vector_adapter(collection, embeddings)
                self._vector_cache_put(collection, adapter)

        await asyncio.to_thread(_sync_put)

    async def _ensure_collection_config(self, collection: str) -> None:
        self._ensure_embeddings()
        if not await self._metadata.get_collection_config(collection):
            provider = self._config.embedding_provider
            model = self._config.embedding_model
            await self._metadata.set_collection_config(
                collection, provider, model, self._default_embedding_dim
            )
            logger.info(f"Created collection {collection} (dim={self._default_embedding_dim})")

    def _get_collection_lock(self, collection: str) -> asyncio.Lock:
        if collection not in self._collection_locks:
            self._collection_locks[collection] = asyncio.Lock()
        return self._collection_locks[collection]

    # --- Ingest ---

    async def _sanitize_and_validate(
        self, collection: str, file_path: Path, replace_duplicates: bool, original_filename: str | None = None
    ) -> str:
        """Validate file and return sanitized filename. Raises on error."""
        filename = _sanitize_filename(original_filename or file_path.name)
        collection = _sanitize_collection(collection)

        if collection in self._reindex_in_progress:
            raise ReindexInProgressError()

        pending = [
            t for t in await self._metadata.list_tasks(collection) if t["status"] in ("pending", "running")
        ]
        if len(pending) >= self._config.max_pending_tasks:
            raise IngestionQueueFullError(self._config.max_pending_tasks)

        # Fast-fail check (advisory): reject a concurrent ingest of the SAME
        # file in the SAME collection, regardless of replace_duplicates. Two
        # parallel uploads (browser double-click, React StrictMode, retry
        # on a network blip) would otherwise both pass validation and both
        # insert their chunks, leaving the collection with duplicate content.
        # The atomic guarantee lives in ``_create_task_entry_internal``; this
        # check just surfaces a clear 409 earlier in the request flow.
        for t in pending:
            if filename in (t.get("file_tasks") or {}):
                raise DocumentExistsError(
                    f"{filename} (an ingest for this file is already {t.get('status', 'pending')}; "
                    f"task_id={t.get('task_id')})"
                )

        if not replace_duplicates and await self._metadata.document_exists(collection, filename):
            raise DocumentExistsError(filename)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        file_size = file_path.stat().st_size
        max_bytes = self._config.max_upload_size_mb * 1024 * 1024
        if file_size > max_bytes:
            raise FileTooLargeError(file_size, max_bytes)

        return filename

    async def _create_task_entry(self, collection: str, filename: str) -> dict[str, Any]:
        coll = _sanitize_collection(collection)
        if coll in self._reindex_in_progress:
            raise ReindexInProgressError()
        return await self._create_task_entry_internal(coll, filename)

    async def _create_reindex_task_entry(self, collection: str, filename: str) -> dict[str, Any]:
        return await self._create_task_entry_internal(_sanitize_collection(collection), filename)

    async def _create_task_entry_internal(self, collection: str, filename: str) -> dict[str, Any]:
        # Hold the collection lock for the (final dedup check + task insert)
        # window. Without this lock, two requests that both passed
        # ``_sanitize_and_validate`` can each insert a task — exactly the
        # double-ingest race that fills the DB with duplicate chunks.
        async with self._get_collection_lock(collection):
            existing = None
            for t in await self._metadata.list_tasks(collection):
                if t.get("status") not in ("pending", "running"):
                    continue
                if filename in (t.get("file_tasks") or {}):
                    existing = t
                    break
            if existing is not None:
                raise DocumentExistsError(
                    f"{filename} (an ingest for this file is already "
                    f"{existing.get('status', 'pending')}; task_id={existing.get('task_id')})"
                )
            task_id = f"task_{uuid.uuid4().hex[:12]}"
            file_tasks = {filename: {"filename": filename, "status": "pending"}}
            return await self._metadata.create_task(task_id, collection, 1, file_tasks)

    async def _run_ingest(
        self,
        collection: str,
        file_path: Path,
        filename: str,
        task_id: str,
        replace_duplicates: bool,
        skip_file_copy: bool = False,
    ) -> None:
        """Run ingestion for a single file in a background thread.

        Parsing runs concurrently, bounded by ``_ingest_sem`` (max_ingest_workers).
        Only the vector-store insert is serialized per-collection (lock taken
        inside ``_ingest_inner``). Docling parsing runs in a thread to avoid
        blocking the event loop.
        """
        cancel_event = asyncio.Event()
        self._active_tasks[task_id] = cancel_event

        coll = _sanitize_collection(collection)
        await self._ensure_metadata_ready()

        # Parse runs concurrently (bounded by the semaphore); the per-collection
        # lock is taken inside _ingest_inner around just the vector-store insert.
        async with self._ingest_sem:
            await self._ingest_inner(
                coll,
                file_path,
                filename,
                task_id,
                replace_duplicates,
                cancel_event,
                skip_file_copy,
            )

    async def ingest(
        self,
        collection: str,
        file_path: Path,
        replace_duplicates: bool = True,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a document file into a collection. Validates, creates task, runs ingestion."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        filename = await self._sanitize_and_validate(
            collection, file_path, replace_duplicates, original_filename
        )
        task_info = await self._create_task_entry(collection, filename)
        await self._run_ingest(collection, file_path, filename, task_info["task_id"], replace_duplicates)
        return await self._metadata.get_task(task_info["task_id"])

    async def _ingest_inner(
        self,
        collection: str,
        file_path: Path,
        filename: str,
        task_id: str,
        replace_duplicates: bool,
        cancel_event: asyncio.Event,
        skip_file_copy: bool = False,
    ) -> None:
        start = time.monotonic()
        # Step 0 (issue #183): per-stage timings collected in-place. Values are
        # seconds, rounded at emit time, populated by the engine and (via
        # _insert_documents_async) by the adapter.
        stage_timings: dict[str, Any] = {}
        worker_apply_gen = self._apply_generation

        def _check_supersede() -> None:
            current = self._apply_generation
            if current != worker_apply_gen:
                raise ReindexSupersededError(worker_apply_gen, current)

        try:
            await self._metadata.update_task(task_id, status="running")
            await self._metadata.update_task(
                task_id,
                file_tasks={filename: {"filename": filename, "status": "processing"}},
            )
            logger.info(f"Task {task_id}: pending -> running for {filename} in {collection}")

            if cancel_event.is_set():
                await self._metadata.update_task(
                    task_id,
                    status="cancelled",
                    file_tasks={filename: {"filename": filename, "status": "skipped"}},
                )
                return

            _check_supersede()

            t_parse = time.monotonic()
            docs = await asyncio.to_thread(self._load_document, file_path)
            stage_timings["parse_s"] = round(time.monotonic() - t_parse, 3)
            if not docs:
                raise ValueError(f"No content extracted from {filename}")
            _check_supersede()

            # C5 (issue #183 step 6): emit "parsed" stage so callers polling
            # get_ingestion_status see progress as soon as Docling finishes
            # rather than waiting for the whole ingest. Status field is
            # deliberately omitted from progress emits so a stray write that
            # races with the completion update at the bottom cannot un-flip
            # status="completed" back to "processing".
            await self._metadata.update_task(
                task_id,
                file_tasks={
                    filename: {
                        "filename": filename,
                        "stage": "parsed",
                        "progress": {"done": len(docs), "total": len(docs)},
                    }
                },
            )

            # The adapter's progress_cb is called from the worker thread (via
            # asyncio.to_thread(_vector_mutation)). To get those updates back
            # into the metadata store, we capture the outer event loop here
            # and ferry each emit via run_coroutine_threadsafe. Futures are
            # tracked and drained before the completion update so the final
            # status="completed" write cannot be overwritten by a late
            # progress emit.
            import concurrent.futures as _cf

            outer_loop = asyncio.get_running_loop()
            progress_futures: list[_cf.Future] = []

            async def _emit_progress(stage: str, done: int, total: int) -> None:
                await self._metadata.update_task(
                    task_id,
                    file_tasks={
                        filename: {
                            "filename": filename,
                            "stage": stage,
                            "progress": {"done": done, "total": total},
                        }
                    },
                )

            def progress_cb(stage: str, done: int, total: int) -> None:
                fut = asyncio.run_coroutine_threadsafe(_emit_progress(stage, done, total), outer_loop)
                progress_futures.append(fut)

            # D3 — drop chunks that are entirely page chrome (page-footer /
            # page-header items, like the academic-paper line-number runs
            # that polluted the live trace). We do this at INGEST so the
            # noise never enters the vector index — far cheaper than
            # filtering it out on every query forever. Three modes (same
            # pattern as ``search_junk_filter`` for operator muscle-memory):
            #   "off"      — never drop
            #   "dry_run"  — count + log what would be dropped, keep all
            #   "enforce"  — drop (default; the live trace's headline fix)
            _chrome_mode = getattr(self._config, "docling_drop_page_chrome", "enforce")
            if _chrome_mode != "off":
                # Single pass over docs, single classifier call per doc.
                # Previous two-pass version was an inefficiency caught in
                # code review (2× the classifier work per ingest).
                kept_docs: list[Any] = []
                flagged_pages: list[int | None] = []
                for d in docs:
                    if _chunk_is_pure_page_chrome(d.metadata.get("dl_meta")):
                        # Capture the page for the log — operators triaging
                        # "D3 wrongly dropped my doc" need to know which
                        # page got nuked, not just the count.
                        _pg = d.metadata.get("page")
                        if _pg is None:
                            _pg = _page_from_docling_dl_meta(d.metadata.get("dl_meta"))
                        flagged_pages.append(_pg)
                    else:
                        kept_docs.append(d)
                if flagged_pages:
                    logger.info(
                        "docling_drop_page_chrome mode=%s collection=%s task_id=%s "
                        "file=%s would_drop=%d sample_pages=%s",
                        _chrome_mode,
                        collection,
                        task_id,
                        filename,
                        len(flagged_pages),
                        flagged_pages[:5],
                    )
                if _chrome_mode == "enforce":
                    docs = kept_docs

            # Enforce chunk limit
            if len(docs) > self._config.max_chunks_per_document:
                docs = docs[: self._config.max_chunks_per_document]
                logger.warning(f"Truncated {filename} to {self._config.max_chunks_per_document} chunks")

            # Normalize metadata — keep only fields we need + the Docling
            # extras that actually improve retrieval (section_path collapses
            # the headings array into one flat string so the LLM can attribute
            # otherwise-orphan chunks like caption-only "Table 27: ..."
            # to their parent section).
            source_id = f"{collection}/{filename}"
            for doc in docs:
                page = doc.metadata.get("page")
                dl_meta = doc.metadata.get("dl_meta")
                if page is None and isinstance(dl_meta, dict):
                    page = dl_meta.get("page")
                if page is None and isinstance(dl_meta, dict):
                    page = _page_from_docling_dl_meta(dl_meta)
                if page is not None:
                    try:
                        page = int(page)
                    except (TypeError, ValueError):
                        page = None
                section_path = _extract_section_path(dl_meta)
                meta = {
                    "source": source_id,
                    "filename": filename,
                }
                # Only include page when set — keeps chunk metadata JSON-friendly
                if page is not None:
                    meta["page"] = page
                # D1 — flat string so it doesn't trip the cross-format schema
                # conflicts that motivated stripping ``headings`` before. Empty
                # string is also acceptable but we omit the key to keep the
                # metadata payload terse for non-Docling formats.
                if section_path:
                    meta["section_path"] = section_path
                doc.metadata = meta
                # Coerce exotic types for vector backends
                for key, val in doc.metadata.items():
                    if val is not None and not isinstance(val, (str, int, float, bool)):
                        logger.warning(f"Coercing metadata {key}={type(val).__name__} to str for {filename}")
                        doc.metadata[key] = str(val)

            if docs:
                logger.debug(f"Sample metadata for {filename}: {docs[0].metadata}")

            _check_supersede()

            logger.info(
                f"Inserting {len(docs)} chunks into {self._knowledge_vector_backend()} "
                f"collection {collection} for {filename}"
            )
            t_insert_total = time.monotonic()
            # Per-collection critical section: ensure config/vector-store exist
            # and insert. Dedup correctness depends on this being serialized;
            # the parse above is not.
            async with self._get_collection_lock(collection):
                # Re-check supersede INSIDE the lock, immediately before the
                # collection config is pinned + vectors inserted. The check at
                # 2477 is outside the lock, so a concurrent commit_knowledge_update
                # could bump _apply_generation between there and here; without
                # this a stale worker would pin _ensure_collection_config to the
                # NEW provider/dim and write old-embedder vectors under it — a
                # name-vs-content mismatch within the target collection.
                _check_supersede()
                await self._ensure_collection_config(collection)
                await self._ensure_vector_store_cached(collection)
                result = await self._insert_documents_async(
                    collection,
                    docs,
                    source_id,
                    filename,
                    replace_duplicates,
                    stage_timings=stage_timings,
                    progress_cb=progress_cb,
                )
            stage_timings["insert_total_s"] = round(time.monotonic() - t_insert_total, 3)

            # Drain any in-flight progress emits before the completion
            # update so a late write cannot overwrite ``status="completed"``
            # back to ``"processing"``. Per-future timeout caps total wait;
            # progress is best-effort, never block on it.
            #
            # Use ``asyncio.wait_for`` over a wrapped concurrent Future so
            # the drain stays on the event loop — ``fut.result(timeout=...)``
            # is a blocking call that would stall the asyncio thread for
            # up to ``len(progress_futures) * timeout`` (~20s for 10 emits).
            # Per Sami's review (Dec 2026) — actual production block.
            for fut in progress_futures:
                try:
                    await asyncio.wait_for(asyncio.wrap_future(fut), timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    pass
            logger.info(
                f"{self._knowledge_vector_backend()} insert complete for {filename}: "
                f"added={result.get('num_added', 0)}, skipped={result.get('num_skipped', 0)}"
            )

            duration = time.monotonic() - start
            chunk_count = result.get("num_added", 0) + result.get("num_updated", 0)
            stage_timings["chunk_count"] = len(docs)
            # Build a short preview from the first chunk(s) for knowledge awareness
            _PREVIEW_MAX_CHARS = 500
            preview_parts: list[str] = []
            preview_len = 0
            for d in docs:
                text = d.page_content.strip()
                if not text:
                    continue
                remaining = _PREVIEW_MAX_CHARS - preview_len
                if remaining <= 0:
                    break
                preview_parts.append(text[:remaining])
                preview_len += len(preview_parts[-1])
            preview = " ".join(preview_parts).replace("\n", " ").strip()
            if len(preview) > _PREVIEW_MAX_CHARS:
                preview = preview[:_PREVIEW_MAX_CHARS].rsplit(" ", 1)[0] + "..."
            t_finalize = time.monotonic()
            await self._metadata.add_document(collection, filename, chunk_count or len(docs), preview=preview)

            if not skip_file_copy:
                dest_dir = self._files_dir / collection
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / filename
                if file_path.resolve() != dest.resolve():
                    try:
                        await asyncio.to_thread(shutil.copy2, str(file_path), str(dest))
                    except Exception:
                        if dest.exists():
                            try:
                                await asyncio.to_thread(dest.unlink)
                            except OSError:
                                pass
                        raise
            stage_timings["finalize_s"] = round(time.monotonic() - t_finalize, 3)
            stage_timings["total_s"] = round(time.monotonic() - start, 3)

            await self._metadata.update_task(
                task_id,
                status="completed",
                processed_files=1,
                successful_files=1,
                file_tasks={
                    filename: {
                        "filename": filename,
                        "status": "indexed",
                        "duration_seconds": round(duration, 2),
                        "timings": dict(stage_timings),
                    }
                },
            )
            logger.info(
                f"Ingested {filename} -> {len(docs)} chunks in {collection} "
                f"(added={result.get('num_added', 0)}, skipped={result.get('num_skipped', 0)})"
            )
            # End-to-end timing breakdown so users can see WHERE time went
            # and validate config changes (fast mode, layout_engine, ...) at a
            # glance. Also includes the active config so the line is
            # self-describing if shared in a bug report.
            # Per-stage device honesty:
            #   - layout: depends on the resolved engine (transformers → MPS/CUDA;
            #     ONNX is CPU-only regardless of device_label).
            #   - embed: the local accelerator backing fastembed/huggingface,
            #     or "N/A — cloud" for openai/openrouter/ollama.
            # We surface both so a single timing line is enough to diagnose.
            _device_label, _ = _detect_accelerator(self._config.use_gpu)
            _eff_layout_engine, _layout_device = self._resolve_layout(
                self._config.docling_layout_engine or "auto", _device_label
            )
            _accel = self.accelerator_status()
            _embed_device = _accel.get("device_label", "?")
            if _accel.get("fallback_to_cpu"):
                _embed_device += " (⚠ GPU requested but CPU loaded)"
            logger.info(
                "Ingest timings ({filename}): total={total}s | parse={parse}s "
                "(docling: pdf_mode={pdf_mode}, layout={layout_engine}/{layout_device}) | "
                "embed={embed}s + insert={insert}s = insert_total={insert_total}s "
                "(provider={provider}, model={model}, embed_device={embed_device}) | "
                "finalize={finalize}s | chunks={chunks}",
                filename=filename,
                total=stage_timings.get("total_s", "?"),
                parse=stage_timings.get("parse_s", "?"),
                pdf_mode=self._config.docling_pdf_mode,
                layout_engine=_eff_layout_engine,
                layout_device=_layout_device,
                embed=stage_timings.get("embed_s", "?"),
                insert=stage_timings.get("insert_s", "?"),
                insert_total=stage_timings.get("insert_total_s", "?"),
                provider=self._config.embedding_provider,
                model=(self._config.embedding_model or "default"),
                embed_device=_embed_device,
                finalize=stage_timings.get("finalize_s", "?"),
                chunks=len(docs),
            )

        except ReindexSupersededError as e:
            # SQL status "cancelled" (CHECK admits no new value); supersede
            # audit lives in file_tasks[filename].
            duration = time.monotonic() - start
            logger.info(f"Task {task_id} superseded for {filename} after {duration:.1f}s ({e})")
            await self._metadata.update_task(
                task_id,
                status="cancelled",
                processed_files=1,
                file_tasks={
                    filename: {
                        "filename": filename,
                        "status": "superseded",
                        "reason": f"config changed mid-ingest (gen {e.worker_gen} -> {e.current_gen})",
                        "duration_seconds": round(duration, 2),
                    }
                },
            )
        except Exception as e:
            duration = time.monotonic() - start
            logger.error(f"Failed to ingest {filename}: {e}")
            await self._metadata.update_task(
                task_id,
                status="failed",
                processed_files=1,
                failed_files=1,
                file_tasks={
                    filename: {
                        "filename": filename,
                        "status": "failed",
                        "error": str(e),
                        "duration_seconds": round(duration, 2),
                    }
                },
            )
        finally:
            self._active_tasks.pop(task_id, None)

    async def _insert_documents_async(
        self,
        collection: str,
        docs: list,
        source_id: str,
        filename: str,
        replace_duplicates: bool,
        retry: bool = True,
        stage_timings: dict[str, Any] | None = None,
        progress_cb: Any = None,
    ) -> dict:
        try:
            doc_exists = await self._metadata.document_exists(collection, filename)

            def _vector_mutation() -> dict:
                with self._vector_store_lock:
                    adapter = self._vector_stores[collection]
                    common_kwargs: dict[str, Any] = {}
                    if stage_timings is not None:
                        common_kwargs["stage_timings"] = stage_timings
                    if progress_cb is not None:
                        common_kwargs["progress_cb"] = progress_cb
                    if replace_duplicates and doc_exists:
                        try:
                            adapter.delete_by_source(source_id)
                        except Exception as e:
                            logger.debug(f"Pre-delete for {source_id}: {e}")
                        rm = self._record_managers.get(collection)
                        if rm:
                            try:
                                rm.delete_keys([source_id])
                            except Exception:
                                pass
                        # C2 (issue #183 step 5): single adapter call now that
                        # bulk insert is done inside the adapter via add_many.
                        return adapter.add_documents(docs, **common_kwargs)
                    if not doc_exists:
                        return adapter.add_documents(docs, **common_kwargs)
                    return {"num_added": 0, "num_skipped": len(docs)}

            return await asyncio.to_thread(_vector_mutation)
        except Exception as e:
            if retry and ("DataNotMatch" in str(e) or "schema" in str(e).lower()):
                logger.warning(f"Schema mismatch in {collection}, dropping and recreating: {e}")
                with self._vector_store_lock:
                    self._vector_stores.pop(collection, None)
                self._record_managers.pop(collection, None)
                try:
                    await self._ensure_vector_store_cached(collection)

                    def _drop_vec() -> None:
                        with self._vector_store_lock:
                            ad = self._vector_stores.get(collection)
                            if ad:
                                ad.drop()

                    await asyncio.to_thread(_drop_vec)
                except Exception:
                    pass
                await self._metadata.delete_collection_metadata(collection)
                await self._ensure_vector_store_cached(collection)
                return await self._insert_documents_async(
                    collection,
                    docs,
                    source_id,
                    filename,
                    replace_duplicates,
                    retry=False,
                    stage_timings=stage_timings,
                    progress_cb=progress_cb,
                )
            raise

    async def ingest_url(self, collection: str, url: str) -> dict[str, Any]:
        """Ingest a document from URL."""
        self._validate_url(url)
        collection = _sanitize_collection(collection)

        import httpx
        import tempfile

        max_redirects = 5
        current_url = url
        fetch_result: tuple[str, bytes] | None = None
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=30.0,
            trust_env=False,
        ) as client:
            redirect_count = 0
            while True:
                async with client.stream("GET", current_url, follow_redirects=False) as resp:
                    if resp.is_redirect:
                        if redirect_count >= max_redirects:
                            raise ValueError(f"Too many redirects (max {max_redirects})")
                        location = resp.headers.get("location")
                        if not location:
                            raise ValueError("Redirect response missing Location header")
                        next_url = urljoin(str(resp.url), location.strip())
                        self._validate_url(next_url)
                        current_url = next_url
                        redirect_count += 1
                        continue
                    resp.raise_for_status()
                    max_bytes = self._config.max_url_download_size_mb * 1024 * 1024
                    total = 0
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes(8192):
                        n = len(chunk)
                        total += n
                        if total > max_bytes:
                            raise FileTooLargeError(total, max_bytes)
                        buf.extend(chunk)
                    fetch_result = (str(resp.url), bytes(buf))
                    break

        if fetch_result is None:
            raise RuntimeError("URL download finished without a response body")
        final_url, downloaded = fetch_result

        parsed = urlparse(final_url)
        filename = _sanitize_filename(Path(parsed.path).name or "downloaded_page.html")

        # Write to temp file — kept alive until ingest completes (ingest is awaited)
        with tempfile.NamedTemporaryFile(suffix=f"_{filename}", delete=False) as tmp:
            tmp.write(downloaded)
            tmp_path = Path(tmp.name)

        try:
            return await self.ingest(collection, tmp_path, replace_duplicates=True)
        finally:
            tmp_path.unlink(missing_ok=True)

    # --- Delete (5-step compensating flow per plan) ---

    async def delete_document(self, collection: str, filename: str) -> None:
        """Delete a document. Idempotent compensating flow across stores."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        filename = _sanitize_filename(filename)

        # Reject deletes while THIS collection is being reindexed (mirrors the
        # upload guard in _sanitize_and_validate / _create_task_entry). During a
        # config-change reindex the source files were already mirrored into the
        # in-flight target and its file_list snapshotted; deleting from the
        # active (source) collection now would let the worker re-embed the
        # deleted doc into the target, RESURRECTING it after the pointer flips.
        # The source collection stays in _reindex_in_progress for the whole
        # migration lifetime (see _migrate_and_reindex_for_agent), so this
        # exact-collection check covers the active-collection delete.
        # Fast-path reject (re-checked under the lock below).
        if collection in self._reindex_in_progress:
            raise ReindexInProgressError(
                f"Reindex in progress for {collection}; deletes are rejected until it completes."
            )

        async with self._get_collection_lock(collection):
            # Re-check under the lock: reindex() flags the collection AND
            # snapshots its file_list under this same lock, so a delete that
            # slipped the fast-path guard above is caught here before it can
            # race that snapshot and resurrect the doc (CR-D).
            if collection in self._reindex_in_progress:
                raise ReindexInProgressError(
                    f"Reindex in progress for {collection}; deletes are rejected until it completes."
                )
            if not await self._metadata.mark_deleting(collection, filename):
                raise DocumentNotFoundError(filename)
            await self._ensure_vector_store_cached(collection)
            try:
                await asyncio.to_thread(self._delete_vector_and_file, collection, filename)
                await self._metadata.remove_document(collection, filename)
                logger.info(f"Deleted {filename} from {collection}")
            except Exception as e:
                logger.error(f"Delete incomplete for {filename} in {collection}: {e}")

    def _delete_vector_and_file(self, collection: str, filename: str) -> None:
        source_id = f"{collection}/{filename}"
        with self._vector_store_lock:
            try:
                adapter = self._vector_stores.get(collection)
                if adapter:
                    adapter.delete_by_source(source_id)
            except Exception as e:
                logger.debug(f"{self._knowledge_vector_backend()} delete for {source_id}: {e}")

        rm = self._record_managers.get(collection)
        if rm:
            try:
                rm.delete_keys([source_id])
            except Exception as e:
                logger.debug(f"RecordManager delete for {source_id}: {e}")

        file_path = self._files_dir / collection / filename
        file_path.unlink(missing_ok=True)

    async def _finalize_stale_delete(self, collection: str, filename: str) -> None:
        collection = _sanitize_collection(collection)
        filename = _sanitize_filename(filename)
        async with self._get_collection_lock(collection):
            await self._ensure_vector_store_cached(collection)
            try:
                await asyncio.to_thread(self._delete_vector_and_file, collection, filename)
                await self._metadata.remove_document(collection, filename)
                logger.info(f"Reconciled delete: {filename} from {collection}")
            except Exception as e:
                logger.error(f"Reconcile delete incomplete for {filename} in {collection}: {e}")

    async def _reconcile_deletes(self) -> None:
        stale = await self._metadata.get_deleting_documents()
        for doc in stale:
            logger.info(f"Reconciling stale delete: {doc['filename']} in {doc['collection']}")
            await self._finalize_stale_delete(doc["collection"], doc["filename"])

    async def _cleanup_expired_sessions(self, max_age_days: int = 7) -> None:
        all_configs = await self._metadata.list_all_collection_configs()
        for col_name in all_configs:
            if not col_name.startswith("kb_sess_"):
                continue
            cfg = await self._metadata.get_collection_config(col_name)
            if not cfg:
                continue
            from datetime import datetime, timezone, timedelta

            try:
                created = datetime.fromisoformat(cfg["created_at"])
                if datetime.now(timezone.utc) - created > timedelta(days=max_age_days):
                    await self.drop_collection(col_name)
                    logger.info(f"Cleaned up expired session collection: {col_name}")
            except Exception as e:
                logger.debug(f"Could not check age for {col_name}: {e}")

    # --- Search ---

    async def search(
        self,
        collection: str,
        query: str,
        limit: int = 10,
        score_threshold: float = 0.0,
        scope: str = "",
    ) -> list[SearchResult]:
        """Search documents in a collection — returns just the result list.

        Thin back-compat wrapper around :meth:`search_with_stats`. Most
        callers don't need the filter accounting; the route and
        ``search_multi`` use the stat-aware variant directly.
        """
        results, _stats = await self.search_with_stats(
            collection=collection,
            query=query,
            limit=limit,
            score_threshold=score_threshold,
            scope=scope,
        )
        return results

    async def search_with_stats(
        self,
        collection: str,
        query: str,
        limit: int = 10,
        score_threshold: float = 0.0,
        scope: str = "",
    ) -> tuple[list[SearchResult], _JunkFilterStats]:
        """Search a collection AND return per-call junk-filter accounting.

        Same retrieval logic as :meth:`search`; surfaces the filter stats
        so multi-scope callers can aggregate ``filtered_count`` across
        scopes for the LLM-facing response.

        ``scope`` is a tag attached to every result so downstream callers
        and the LLM can attribute chunks correctly. The engine doesn't
        interpret it.

        If a client-adaptation glossary is configured, the query is first
        expanded with synonymous anchors so cross-language / abbreviated /
        domain-specific aliases lift recall (see ``query_expansion``).
        Expansion is opt-in via config and silent (no-op) when the
        glossary is empty.
        """
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        limit = max(1, min(limit, 100))
        score_threshold = max(0.0, min(score_threshold, 1.0))

        await self._ensure_vector_store_cached(collection)

        # C3 — drain loop for high-junk pages. ``limit*2`` covers the
        # common case (a few junk chunks in the top-K); a drain loop
        # bounded to 2 iterations covers pathological pages (80%+ junk —
        # the academic line-number gutters that motivated D3). On clean
        # data we exit after one iteration with the same cost as before.
        _filter_mode = getattr(self._config, "search_junk_filter", "dry_run")
        # Step 5 (BM25 hybrid) — when ``auto`` we run dense + lexical in
        # parallel and RRF-fuse. ``off`` skips the lexical leg entirely
        # (zero extra cost on machines / collections where hybrid is
        # explicitly disabled).
        _hybrid_mode = getattr(self._config, "search_hybrid_mode", "auto")
        _hybrid_on = _hybrid_mode == "auto"

        # Cross-encoder reranker overfetch — when reranker is on, pull the
        # top ``rerank_top_k_in`` candidates from the adapter so the encoder
        # has a wide enough window to find the gold chunk. Without overfetch
        # the encoder can only re-order an already-narrow result set.
        rerank_on = bool(getattr(self._config, "rerank_enabled", False))
        # candidate_k vs return_k: when reranking, keep a WIDE candidate window
        # (overfetch) so the cross-encoder has room to surface the gold chunk,
        # then trim to ``limit`` (return_k) in the rerank step. When rerank is
        # off this collapses to ``limit`` — no behavior change. (Previously the
        # overfetch value was computed and discarded, so reranking only ever
        # re-ordered the top ``limit`` — defeating the point of reranking.)
        _overfetch_k = max(limit, int(getattr(self._config, "rerank_top_k_in", 20))) if rerank_on else limit

        # Glossary-driven query expansion (client-adaptation). The expanded
        # query hits the embedder + lexical index; the ORIGINAL is preserved
        # for (a) the empty-guard retry below if expansion drove embedding
        # away from useful regions, and (b) the cross-encoder rerank step
        # which scores on the user's actual intent, not alias-padded text.
        original_query = query
        glossary = getattr(self._config, "client_adaptation_glossary", None)
        if glossary:
            from cuga.backend.knowledge.query_expansion import expand_query_with_glossary

            query, match_log = expand_query_with_glossary(original_query, glossary)
            if match_log:
                logger.info(
                    "cuga.knowledge.glossary_expanded",
                    extra={
                        "cuga_knowledge_glossary_matches": len(match_log),
                        "cuga_knowledge_query_orig_len": len(original_query),
                        "cuga_knowledge_query_expanded_len": len(query),
                    },
                )
                logger.debug(
                    f"Glossary expanded query: {original_query!r} -> {query!r} (matches: {match_log})"
                )

        # ``q`` defaults to the (glossary-expanded) query; the query-transform
        # fan-out and the empty-guard retry pass an explicit query string.
        def _do_dense_search(k: int, q: str = query):
            # Hold the lock only long enough to fetch the adapter reference.
            # Adapters are safe to call concurrently for reads (sqlite-vec
            # uses per-call connections; pgvector wraps an asyncpg pool).
            # Holding the global lock across ``adapter.search`` would
            # serialize the parallel fan-out in ``search_multi`` and double
            # p50 latency on multi-scope queries.
            with self._vector_store_lock:
                adapter = self._vector_stores[collection]
            return adapter.search(q, k=k)

        def _do_lexical_search(k: int, q: str = query):
            with self._vector_store_lock:
                adapter = self._vector_stores[collection]
            # Adapters without a lexical index inherit the base class's
            # ``[]`` so this never raises — the engine degrades cleanly
            # to pure-dense when (a) the adapter is pgvector, (b) the
            # FTS table hasn't been created yet for a pre-upgrade
            # collection, or (c) the query has no usable tokens after
            # FTS5 sanitization.
            return adapter.search_lexical(q, k=k)

        # ``below_threshold_counter`` is a one-element list so the closure
        # can mutate it (avoid `nonlocal` since this function is itself a
        # closure of ``search_with_stats``). Counts dense+lexical chunks
        # whose dense cosine was below ``score_threshold``. The lexical
        # leg pre-fusion does not apply this cutoff (BM25 ranks aren't
        # comparable to cosine), so its ``apply_score_threshold=False``
        # path is intentionally counted as 0 here.
        below_threshold_counter = [0]

        def _materialize(scored, seen_texts: set[str], *, apply_score_threshold: bool) -> list[SearchResult]:
            out: list[SearchResult] = []
            for doc, score in scored:
                if apply_score_threshold and score < score_threshold:
                    below_threshold_counter[0] += 1
                    continue
                text = doc.page_content
                if text in seen_texts:
                    continue
                seen_texts.add(text)
                out.append(
                    SearchResult(
                        text=text,
                        filename=doc.metadata.get("filename", "unknown"),
                        page=doc.metadata.get("page", None),
                        score=round(score, 4),
                        scope=scope,
                        section_path=doc.metadata.get("section_path", "") or "",
                    )
                )
            return out

        # Pass 1: limit*2 (or just limit when filter is off), but never below the
        # reranker's candidate window — otherwise overfetch would request more
        # than we fetched and the rerank step would see a narrow set.
        _base_k = (limit * 2) if _filter_mode != "off" else limit
        first_k = min(200, max(_base_k, _overfetch_k))

        # Query transformation (multi_query / HyDE), opt-in via
        # search_query_transform. Kick the LLM generation NOW so it overlaps the
        # base dense/lexical retrieval below; we await it only when fanning out the
        # variant legs (after the base fuse). Inert when the knob is off or no chat
        # model was injected — search then runs exactly the plain-query path. The
        # transform sees the ORIGINAL query (cleaner intent than alias-padded text).
        _qt_mode = getattr(self._config, "search_query_transform", "off")
        _qt_task = None
        if _qt_mode != "off" and self._chat_generator is not None:
            from cuga.backend.knowledge.query_transform import expand_query as _expand_query

            _qt_task = asyncio.ensure_future(
                _expand_query(
                    _qt_mode,
                    original_query,
                    self._chat_generator,
                    n=int(getattr(self._config, "search_query_transform_n", 3)),
                )
            )

        # Run dense + lexical in parallel when hybrid is on. ``gather``
        # over ``to_thread`` means wall-clock ≈ max(dense, lexical)
        # instead of dense + lexical. Lexical against an FTS5 index is
        # usually faster than dense (no embedding round-trip), so the
        # added latency is bounded by the dense call.
        if _hybrid_on:
            dense_task = asyncio.to_thread(_do_dense_search, first_k)
            lexical_task = asyncio.to_thread(_do_lexical_search, first_k)
            dense_scored, lexical_scored = await asyncio.gather(dense_task, lexical_task)
        else:
            dense_scored = await asyncio.to_thread(_do_dense_search, first_k)
            lexical_scored = []

        # Empty-guard retry: if glossary expansion drove the dense embedding
        # away from useful regions (rare, but observed when aliases conflict
        # with the original term semantics), retry the dense leg once with
        # the unexpanded query. Lexical leg is left as-is — if expansion
        # found nothing in BM25 either, the un-expanded query likely won't
        # rescue lexical either.
        if not dense_scored and query != original_query:
            logger.info(
                "cuga.knowledge.empty_retry",
                extra={
                    "cuga_knowledge_collection": collection,
                    "cuga_knowledge_reason": "glossary_expansion_returned_empty",
                },
            )

            dense_scored = await asyncio.to_thread(_do_dense_search, first_k, original_query)

        if dense_scored:
            logger.debug(
                f"Search '{query[:30]}' on {collection}: "
                f"top_score={dense_scored[0][1]:.4f}, count={len(dense_scored)}, "
                f"lexical_count={len(lexical_scored)}, "
                f"backend={self._knowledge_vector_backend()}"
            )

        # Materialize each leg with its own dedup set so a chunk that
        # appears in both can contribute to BOTH ranks (which is
        # exactly what RRF needs to score it higher). The score_threshold
        # is dense-leg-only — the lexical leg uses BM25 ranks, not
        # comparable to a cosine cutoff.
        seen_dense: set[str] = set()
        dense_results = _materialize(dense_scored, seen_dense, apply_score_threshold=True)
        seen_lex: set[str] = set()
        lexical_results = _materialize(lexical_scored, seen_lex, apply_score_threshold=False)

        # Track the raw candidate count BEFORE any threshold / dedup
        # collapse, for the wire ``candidates`` field. ``candidates``
        # on the wire is intentionally pre-cutoff so the user/LLM see
        # what the retriever actually returned (the invariant
        # ``candidates = returned + filtered + below_threshold +
        # drain_drops`` requires this).
        _raw_candidate_count = len(dense_scored) + len(lexical_scored)

        # RRF fusion (no-op when ``lexical_results`` is empty, so the
        # pure-dense path keeps its original ordering for back-compat).
        results = _rrf_fuse(dense_results, lexical_results)

        # Query-transform fan-out (opt-in). Retrieve each EXTRA variant leg and
        # RRF-fuse it with the base hybrid result. The base (original query,
        # dense+lexical) always participates and is passed FIRST, so a bad HyDE
        # passage or off-target rewrite can only ADD candidates to the pool — it
        # never replaces the user's query. Variant legs skip the dense
        # score_threshold (RRF + the reranker decide quality) so they don't perturb
        # the base below_threshold stat. Whole block is best-effort: any failure
        # leaves ``results`` as the plain-query fusion.
        if _qt_task is not None:
            try:
                _variants = await _qt_task
            except Exception:
                _variants = None
            if _variants is not None and _variants.active:
                _legs = [asyncio.to_thread(_do_dense_search, first_k, qv) for qv in _variants.dense_extra]
                if _hybrid_on:
                    _legs += [
                        asyncio.to_thread(_do_lexical_search, first_k, qv) for qv in _variants.lexical_extra
                    ]
                _scored_legs = await asyncio.gather(*_legs, return_exceptions=True)
                _variant_lists = [
                    _materialize(sc, set(), apply_score_threshold=False)
                    for sc in _scored_legs
                    if isinstance(sc, list)
                ]
                _variant_lists = [vl for vl in _variant_lists if vl]
                if _variant_lists:
                    _raw_candidate_count += sum(len(vl) for vl in _variant_lists)
                    results = _rrf_fuse_lists([results, *_variant_lists])
                    # loguru: format inline (extra={} is silently dropped by loguru),
                    # so this line is actually visible/grep-able in the console.
                    logger.info(
                        "cuga.knowledge.query_transform_applied mode={} dense_legs={} "
                        "lexical_legs={} fused={} query={!r}",
                        _qt_mode,
                        len(_variants.dense_extra),
                        len(_variants.lexical_extra) if _hybrid_on else 0,
                        len(results),
                        original_query[:60],
                    )

        # Junk filter applies to the unified post-fusion list.
        results, _stats = _apply_junk_filter(results, _filter_mode)
        # Overwrite the ``candidates`` count with the raw pre-cutoff
        # value so the invariant holds on the wire. ``_apply_junk_filter``
        # initialized it to ``len(results)`` (post-fusion, post-cutoff)
        # which would understate.
        _stats.candidates = _raw_candidate_count
        _stats.below_threshold = below_threshold_counter[0]

        # Pass 2 (drain): only when (a) the filter is actually enforcing
        # AND (b) we returned less than asked AND (c) the dense adapter
        # produced a full page (otherwise there's nothing more to refill
        # from). Hybrid already over-fetches via two legs, so the drain
        # is only needed for pure-dense pathological pages.
        if _filter_mode == "enforce" and len(results) < limit and len(dense_scored) >= first_k:
            second_k = min(200, max(limit * 2, first_k + limit))
            scored2 = await asyncio.to_thread(_do_dense_search, second_k)
            seen_drain = set(r.text for r in results)
            # Snapshot the threshold counter so we can split drain-pass
            # rejects from the original pass for the wire ``drain_drops``
            # field.
            _bt_before_drain = below_threshold_counter[0]
            new_results = _materialize(
                scored2[first_k:],
                seen_drain,
                apply_score_threshold=True,
            )
            if new_results:
                new_results, new_stats = _apply_junk_filter(new_results, _filter_mode)
                results.extend(new_results)
                _stats.candidates += new_stats.candidates
                _stats.filtered_count += new_stats.filtered_count
                # Drain-pass threshold rejects count toward drain_drops,
                # not below_threshold. Different operator action: drain
                # tuning vs. raise/lower the cutoff.
                _stats.drain_drops += below_threshold_counter[0] - _bt_before_drain
                for k, v in new_stats.reasons.items():
                    _stats.reasons[k] = _stats.reasons.get(k, 0) + v

        # Cap at the candidate window AFTER the optional drain. When rerank is
        # off ``_overfetch_k == limit`` so this is the caller's budget; when on,
        # we keep the wider window and the rerank step below trims to ``limit``.
        results = results[:_overfetch_k]
        if _stats.filtered_count:
            logger.info(
                "search_filter mode=%s collection=%s candidates=%d filtered=%d reasons=%s scope=%s query=%r",
                _filter_mode,
                collection,
                _stats.candidates,
                _stats.filtered_count,
                _stats.reasons,
                scope or "-",
                (query or "")[:60],
            )

        # Cross-encoder rerank step — opt-in, post-cap on adapter results.
        # We rescore the overfetched candidates jointly with the (original,
        # unexpanded) query — glossary expansion already helped retrieval
        # find the candidates; for rescoring we want the user's actual intent,
        # not the alias-padded version. On any reranker failure (model not
        # available, OOM, etc.) we fall back to the fusion ranking and log
        # a warning so search degrades gracefully.
        if rerank_on and len(results) > 1:
            # A user query MUST NEVER block on a model download. When the
            # reranker model isn't loaded yet (e.g. just switched to a profile
            # that enables it — bge-reranker-base is ~1.1GB), kick a background
            # load and serve fusion-ranked results NOW; subsequent queries
            # rerank automatically once it's ready. Any failure (import, load,
            # rerank) degrades to fusion ranking, never crashes the request.
            _rerank_model = str(getattr(self._config, "rerank_model", "BAAI/bge-reranker-base"))
            try:
                from cuga.backend.knowledge import reranker as _rr

                if not _rr.is_ready(_rerank_model):
                    _rr.ensure_loading(_rerank_model)  # non-blocking background download
                    logger.info(
                        "cuga.knowledge.rerank_warming",
                        extra={"cuga_knowledge_rerank_model": _rerank_model},
                    )
                    results = results[:limit]  # fusion ranking until the model is ready
                else:
                    # Tag each candidate with its index into ``results`` so the
                    # reorder after rerank preserves the ORIGINAL SearchResult
                    # (scope, section_path, dense_rank, lexical_rank, rrf_score).
                    # Without this, rebuilding fresh SearchResults at the end
                    # silently zeroes scope → "" — and the envelope's by_source
                    # bucket lumps every chunk under that phantom empty scope
                    # when scope="all" + rerank_enabled. Sami C1 review.
                    candidates = [
                        _rr.RerankedCandidate(
                            text=r.text,
                            score=r.score,
                            metadata={"filename": r.filename, "page": r.page, "_orig_idx": i},
                            original_score=r.score,
                        )
                        for i, r in enumerate(results)
                    ]
                    reranked = await asyncio.to_thread(
                        _rr.rerank, original_query, candidates, limit, _rerank_model
                    )
                    logger.info(
                        "cuga.knowledge.rerank_applied",
                        extra={
                            "cuga_knowledge_rerank_in": len(candidates),
                            "cuga_knowledge_rerank_out": len(reranked),
                            "cuga_knowledge_rerank_top_score_orig": (
                                candidates[0].original_score if candidates else 0
                            ),
                            "cuga_knowledge_rerank_top_score_new": (reranked[0].score if reranked else 0),
                        },
                    )
                    # Reorder original results by the reranker's verdict. All
                    # provenance fields (scope, section_path, dense_rank,
                    # lexical_rank, rrf_score) ride through unchanged. The
                    # displayed score stays the fusion score for unit
                    # consistency (cross-encoder logits are unbounded and
                    # would confuse the score_threshold gate downstream).
                    # Defensive ``isinstance`` guard: a misbehaved reranker
                    # that drops the index key shouldn't crash search.
                    reordered: list["SearchResult"] = []
                    for c in reranked:
                        idx = c.metadata.get("_orig_idx") if isinstance(c.metadata, dict) else None
                        if isinstance(idx, int) and 0 <= idx < len(results):
                            reordered.append(results[idx])
                    results = reordered or results[:limit]
            except Exception as e:
                logger.warning(f"Reranker unavailable/failed ({e}); using fusion ranking.")
                results = results[:limit]
        else:
            # No reranker → still respect the requested limit (we may have
            # overfetched to feed the reranker; if it's off, trim now).
            results = results[:limit]

        if results:
            logger.debug(
                f"Search '{query[:30]}' on {collection}: top_score={results[0].score}, count={len(results)}"
            )
        return results, _stats

    async def search_multi(
        self,
        scoped_collections: list[tuple[str, str]],
        query: str,
        limit: int = 10,
        score_threshold: float = 0.0,
        per_scope_limit: bool = True,
    ) -> tuple[list[SearchResult], "_MultiSearchStats"]:
        """Fan out a query across multiple scoped collections.

        Two budget modes (``per_scope_limit`` toggles between them):

          - **per_scope_limit=True (default — Option B)**: every scope
            contributes up to ``limit`` chunks; total cap is
            ``min(100, limit × n_scopes)``. Use when the caller chose
            ``scope="all"`` deliberately: "I genuinely need both
            sources, show me each one's best."

          - **per_scope_limit=False (Option F — fixed total)**: classic
            quota allocation. Each non-empty scope reserves
            ``min(N_s, ceil(limit / num_non_empty_scopes))`` slots;
            spare capacity from undersized scopes redistributes; sum
            hard-clamped to ``limit``. Use when the caller did NOT ask
            for breadth — the auto-fallback path
            (``scope="session"`` → 0 hits → retry as "all") sets this
            so the LLM's response-size expectation is preserved.

        Both modes share the same retrieval + RRF + dedup pipeline:

          1. Fan out: one ``search_with_stats`` per (scope, collection)
             in parallel via ``asyncio.gather`` (wall-clock ≈ slowest
             single search).
          2. **Cross-scope RRF**: per-scope rank fused with cross-scope
             rank via ``1/(k_rrf + rank)``. Stored on
             ``cross_scope_rrf_score`` for observability.
          3. **Dedup** by ``(filename, page, sha1(text)[:16])`` keeping
             the higher cross-scope RRF score. Collapses attributed to
             the loser's scope (``dedup_collapses``).
          4. **Budget application**: per the mode above.

        Returns ``(results, stats)`` where ``stats`` is a per-scope
        ``_MultiSearchStats``. We do NOT raise on partial failure: a
        degraded answer beats no answer when one collection is down.
        """
        if not scoped_collections:
            return [], _MultiSearchStats()

        limit = max(1, min(limit, 100))

        async def _scoped_search(
            scope_name: str, collection: str
        ) -> tuple[list[SearchResult], _JunkFilterStats]:
            return await self.search_with_stats(
                collection=collection,
                query=query,
                limit=limit,
                score_threshold=score_threshold,
                scope=scope_name,
            )

        outcomes = await asyncio.gather(
            *(_scoped_search(s, c) for s, c in scoped_collections),
            return_exceptions=True,
        )

        # Track per-scope results & stats. Per-scope is the load-bearing
        # change vs. the old "single aggregate" return shape — quota and
        # observability both need per-scope buckets.
        # Pre-seed ``by_scope`` with every searched scope (with empty
        # stats) so the wire envelope can distinguish "searched but no
        # hits" from "scope wasn't searched". Failed scopes are recorded
        # separately in ``failed_scopes`` and do NOT appear in by_scope.
        by_scope_results: dict[str, list[SearchResult]] = {}
        stats = _MultiSearchStats()
        for (scope_name, collection), outcome in zip(scoped_collections, outcomes):
            if isinstance(outcome, BaseException):
                stats.failed_scopes.append(scope_name)
                stats.partial = True
                logger.warning(
                    "search_multi: scope=%s collection=%s failed (%s); continuing with remaining scopes",
                    scope_name,
                    collection,
                    outcome,
                )
                continue
            scoped_results, scoped_stats = outcome
            by_scope_results[scope_name] = scoped_results
            stats.by_scope[scope_name] = scoped_stats
            if scoped_results:
                stats.top_score_by_scope[scope_name] = scoped_results[0].score

        # Stamp cross-scope RRF rank on every chunk. Each scope contributes
        # its in-scope ranked list; chunks are fused across scopes via
        # the standard RRF formula ``1 / (k_rrf + rank)``. ``k_rrf = 60``
        # matches the in-scope fusion default and the literature default
        # (Cormack 2009).
        _k_rrf = 60
        for scope_name, scoped_results in by_scope_results.items():
            for rank, r in enumerate(scoped_results, start=1):
                r.cross_scope_rrf_score = round(1.0 / (_k_rrf + rank), 6)

        # Dedup across scopes BEFORE quota so dedup_collapses are
        # attributed before slots are reserved. Key: (filename, page,
        # text_sha1[:16]). Winner: higher cross-scope RRF (matches the
        # fusion the caller will see). Loser's scope accrues a
        # ``dedup_collapses`` count for observability ("your X scope
        # had a duplicate eclipsed by Y").
        best_by_key: dict[tuple[str, int | None, str], SearchResult] = {}
        loser_scopes_by_key: dict[tuple[str, int | None, str], str] = {}
        for scope_name, scoped_results in by_scope_results.items():
            for r in scoped_results:
                text_hash = hashlib.sha1(r.text.encode("utf-8", errors="replace")).hexdigest()[:16]
                key = (r.filename, r.page, text_hash)
                existing = best_by_key.get(key)
                if existing is None:
                    best_by_key[key] = r
                    continue
                # Tiebreak: higher cross_scope_rrf_score wins. Raw score
                # is a secondary tiebreak (stable on collections that
                # produce identical fused ranks).
                e_rrf = existing.cross_scope_rrf_score or 0.0
                r_rrf = r.cross_scope_rrf_score or 0.0
                if r_rrf > e_rrf or (r_rrf == e_rrf and r.score > existing.score):
                    # ``existing`` loses → its scope accrues a collapse.
                    loser_scope = existing.scope or "?"
                    if loser_scope in stats.by_scope:
                        stats.by_scope[loser_scope].dedup_collapses += 1
                    loser_scopes_by_key[key] = loser_scope
                    best_by_key[key] = r
                else:
                    # ``r`` loses.
                    if scope_name in stats.by_scope:
                        stats.by_scope[scope_name].dedup_collapses += 1
                    loser_scopes_by_key[key] = scope_name

        # Rebucket post-dedup by scope so the quota step sees the
        # surviving lists in rank order.
        post_dedup_by_scope: dict[str, list[SearchResult]] = {}
        for r in best_by_key.values():
            post_dedup_by_scope.setdefault(r.scope or "?", []).append(r)
        # Preserve in-scope ranking (drop = preserve insertion order from
        # the per-scope search; cross_scope_rrf_score equals the original
        # rank-derived value so sorting by it descending = rank order).
        for scope_name in post_dedup_by_scope:
            post_dedup_by_scope[scope_name].sort(
                key=lambda r: (
                    -(r.cross_scope_rrf_score or 0.0),
                    r.filename,
                    r.page if r.page is not None else -1,
                ),
            )

        non_empty_scopes = [s for s, lst in post_dedup_by_scope.items() if lst]
        n_scopes = len(non_empty_scopes)
        if n_scopes == 0:
            return [], stats

        # Option B: per-source cap. Each non-empty scope contributes up
        # to ``limit`` chunks; total cap is ``min(100, limit × n_scopes)``.
        # Final ordering is cross-scope RRF descending (same key as the
        # quota path below) so the LLM sees the most strongly-fused
        # chunks first regardless of which scope they came from.
        if per_scope_limit:
            total_cap = min(100, limit * n_scopes)
            merged: list[SearchResult] = []
            for s in non_empty_scopes:
                merged.extend(post_dedup_by_scope[s][:limit])
            merged.sort(
                key=lambda r: (
                    -(r.cross_scope_rrf_score or 0.0),
                    r.filename,
                    r.page if r.page is not None else -1,
                ),
            )
            return merged[:total_cap], stats

        # Per-scope quota allocation (Option F — fixed total).
        # Step 1: each non-empty scope reserves min(N_s, ceil(limit/num)).
        # Step 2: redistribute leftover (capped scopes returning fewer
        # than their quota) to other non-empty scopes in scope order
        # until either limit is filled or no scope wants more.
        # Step 3: hard clamp sum(quota) <= limit (defends limit=1 with
        # two non-empty scopes where ceil(1/2)=1 each = 2 slots).
        import math as _math

        base_quota = _math.ceil(limit / n_scopes)
        quota: dict[str, int] = {s: min(len(post_dedup_by_scope[s]), base_quota) for s in non_empty_scopes}
        # Redistribute leftover capacity.
        spare = limit - sum(quota.values())
        while spare > 0:
            grew = False
            for s in non_empty_scopes:
                if quota[s] < len(post_dedup_by_scope[s]):
                    quota[s] += 1
                    spare -= 1
                    grew = True
                    if spare == 0:
                        break
            if not grew:
                break
        # Hard clamp (in case of off-by-one with small limits).
        while sum(quota.values()) > limit:
            # Trim from the largest quota first to preserve thin-scope
            # representation when limit is tight.
            biggest = max(non_empty_scopes, key=lambda s: quota[s])
            quota[biggest] -= 1
            if quota[biggest] == 0:
                non_empty_scopes = [s for s in non_empty_scopes if s != biggest]

        # Take top-N per scope per quota. Then sort the unified result
        # set by cross-scope RRF for the final returned ordering — quota
        # guarantees representation; the cross-scope rank gives
        # deterministic interleaving.
        merged: list[SearchResult] = []
        for s in non_empty_scopes:
            merged.extend(post_dedup_by_scope[s][: quota[s]])
        merged.sort(
            key=lambda r: (
                -(r.cross_scope_rrf_score or 0.0),
                r.filename,
                r.page if r.page is not None else -1,
            ),
        )
        return merged[:limit], stats

    # --- List ---

    async def list_documents(self, collection: str) -> list[DocInfo]:
        """List documents in a collection (hides 'deleting' status)."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        rows = await self._metadata.list_documents(collection)
        return [DocInfo(**r) for r in rows]

    def get_document_file_path(self, collection: str, filename: str) -> Path:
        """Return the stored original file path for a document."""
        collection = _sanitize_collection(collection)
        filename = _sanitize_filename(filename)
        file_path = self._files_dir / collection / filename
        if not file_path.exists():
            raise DocumentNotFoundError(filename)
        return file_path

    # --- Tasks ---

    async def get_tasks(self, collection: str | None = None) -> list[dict[str, Any]]:
        await self._ensure_metadata_ready()
        return await self._metadata.list_tasks(collection)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        await self._ensure_metadata_ready()
        return await self._metadata.get_task(task_id)

    async def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        await self._ensure_metadata_ready()
        task = await self._metadata.get_task(task_id)
        if not task:
            return None
        if task["status"] in ("completed", "failed", "cancelled"):
            return task

        cancel_event = self._active_tasks.get(task_id)
        if cancel_event:
            cancel_event.set()

        if task["status"] == "pending":
            file_tasks = task["file_tasks"]
            for ft in file_tasks.values():
                if ft["status"] == "pending":
                    ft["status"] = "skipped"
            await self._metadata.update_task(task_id, status="cancelled", file_tasks=file_tasks)
            logger.debug(f"Task {task_id}: cancelled (was pending)")

        return await self._metadata.get_task(task_id)

    # --- Knowledge config update (prepare / commit) ---

    def prepare_knowledge_update(self, knowledge_cfg: dict) -> PreparedKnowledgeUpdate:
        """Validate, coerce, preflight. No mutation. Raises ValueError/TypeError on bad input.

        All external calls (embedding creation, dimension check) happen here.
        If the incoming dict contains a ``rag_profile``, its parameters are
        expanded into the dict before coercion so the existing change-detection
        logic works unchanged.
        """
        from cuga.backend.knowledge.config import load_profile, VALID_PROFILES

        profile_name = knowledge_cfg.get("rag_profile")
        if profile_name and profile_name in VALID_PROFILES:
            try:
                profile_data = load_profile(profile_name)
                search = profile_data.get("search", {})
                chunking = profile_data.get("chunking", {})
                # Profile values are defaults; explicit keys in knowledge_cfg win
                expanded = {
                    "max_search_attempts": search.get("max_search_attempts"),
                    "default_limit": search.get("default_limit"),
                    "default_score_threshold": search.get("default_score_threshold"),
                    "chunk_size": chunking.get("chunk_size"),
                    "chunk_overlap": chunking.get("chunk_overlap"),
                    "rag_profile": profile_name,
                }
                # Remove None values and merge (profile as base, explicit overrides win)
                expanded = {k: v for k, v in expanded.items() if v is not None}
                knowledge_cfg = {**expanded, **knowledge_cfg}
            except FileNotFoundError:
                logger.warning(f"Profile {profile_name} not found, ignoring")

        validated = KnowledgeConfig.coerce_and_validate(knowledge_cfg, base=self._config)

        embedding_changed = (
            validated.embedding_provider != self._config.embedding_provider
            or validated.embedding_model != self._config.embedding_model
        )
        chunking_changed = (
            validated.chunk_size != self._config.chunk_size
            or validated.chunk_overlap != self._config.chunk_overlap
        )
        metric_changed = validated.metric_type != self._config.metric_type
        reindex_recommended = embedding_changed or chunking_changed or metric_changed

        new_embeddings = None
        new_dim = None
        if embedding_changed:
            # Local providers (fastembed/huggingface) load the model eagerly here,
            # so a large model still downloading (e.g. multilingual-e5-large is
            # ~2.2GB with external ONNX weights), a bad model name, or an
            # unresolved key fails RIGHT HERE. Surface a typed, actionable error
            # instead of letting an opaque ONNX/HTTP error 500 the publish.
            try:
                new_embeddings = create_embeddings(validated)
            except Exception as e:
                raise EmbeddingModelLoadError(
                    validated.embedding_provider, validated.embedding_model or "(default)", e
                ) from e
            # Probe the new dim only when we have an existing dim to compare
            # against (reindex decision). On a fresh engine with no ingested
            # data we'd be calling a remote API for nothing — and worse, that
            # call would fail at config-apply time when the key is missing
            # (e.g. importing a published openai snapshot whose key was
            # stripped, with OPENAI_API_KEY not yet set on this machine).
            if self._default_embedding_dim is not None:
                try:
                    new_dim = _get_embedding_dim(new_embeddings)
                except Exception as e:  # noqa: BLE001 — keep config-apply tolerant
                    logger.warning(
                        "Could not probe embedding dim for new provider {!r}/{!r}: {}. "
                        "Skipping reindex check; first ingest will surface any real auth/connectivity issue.",
                        validated.embedding_provider,
                        validated.embedding_model,
                        e,
                    )

        return PreparedKnowledgeUpdate(
            validated=validated,
            embedding_changed=embedding_changed,
            chunking_changed=chunking_changed,
            metric_changed=metric_changed,
            reindex_recommended=reindex_recommended,
            new_embeddings=new_embeddings,
            new_embedding_dim=new_dim,
        )

    def commit_knowledge_update(self, prepared: PreparedKnowledgeUpdate) -> dict[str, Any]:
        """Commit a prepared update. Pure in-memory mutation, no external calls."""
        # Any config apply (incl. a key/base-url fix that doesn't change the
        # vector hash) should force a fresh embedder probe on the next health.
        self._embedder_probe_cache = None
        old_use_gpu = self._config.use_gpu
        old_dim = self._default_embedding_dim
        old_pdf_mode = self._config.docling_pdf_mode
        old_layout_engine = self._config.docling_layout_engine
        old_qt = getattr(self._config, "search_query_transform", "off")
        old_api_key = getattr(self._config, "embedding_api_key", "") or ""
        old_base_url = getattr(self._config, "embedding_base_url", "") or ""
        old_extra = dict(getattr(self._config, "embedding_extra_params", {}) or {})

        for f in dc_fields(KnowledgeConfig):
            if f.name != "persist_dir":
                setattr(self._config, f.name, getattr(prepared.validated, f.name))

        # Confirm a query-transform toggle at SWITCH time (it otherwise only logs
        # per knowledge search). Search-only change — no model load, no reindex.
        new_qt = getattr(self._config, "search_query_transform", "off")
        if new_qt != old_qt:
            if new_qt == "off":
                logger.info("cuga.knowledge.query_transform disabled (was {})", old_qt)
            else:
                logger.info(
                    "cuga.knowledge.query_transform enabled mode={} (fires per knowledge search; "
                    "needs an injected chat model; rerank={})",
                    new_qt,
                    getattr(self._config, "rerank_enabled", False),
                )

        dim_changed = False
        if prepared.new_embeddings:
            new_dim = prepared.new_embedding_dim
            # Dim changed only if old was set AND differs from new. None → new is a
            # first-time init, not a dim change — no reindex needed.
            dim_changed = old_dim is not None and new_dim is not None and old_dim != new_dim
            self._default_embeddings = prepared.new_embeddings
            self._default_embedding_dim = new_dim
            with self._vector_store_lock:
                self._vector_stores.clear()
                self._record_managers.clear()
        elif old_use_gpu != self._config.use_gpu and self._config.embedding_provider in (
            "fastembed",
            "huggingface",
        ):
            # Same model, new device. ORT/PyTorch sessions are immutable so we
            # rebind the inference backend — but stored vectors are still valid
            # (same weights) and no reindex is needed. _vector_stores stays.
            self._default_embeddings = create_embeddings(self._config)
            logger.info(
                "Embedding inference backend rebound (use_gpu={} → {}); model unchanged, "
                "existing vectors remain valid",
                old_use_gpu,
                self._config.use_gpu,
            )
        elif self._default_embeddings is not None and (
            old_api_key != (getattr(self._config, "embedding_api_key", "") or "")
            or old_base_url != (getattr(self._config, "embedding_base_url", "") or "")
            or old_extra != (getattr(self._config, "embedding_extra_params", {}) or {})
        ):
            # Credential / base-url-only fix: provider+model unchanged, so
            # prepare_knowledge_update produced no new_embeddings — but the OLD
            # client (rejected key / stale base_url) is still cached. Rebuild it
            # so the fix takes effect WITHOUT a restart; vectors stay valid (same
            # model) → no reindex. Construction doesn't hit the network, so a
            # still-bad key surfaces later on embed, not here.
            try:
                self._default_embeddings = create_embeddings(self._config)
                with self._vector_store_lock:
                    self._vector_stores.clear()
                    self._record_managers.clear()
                logger.info("Embedder client rebuilt after credential/base-url change; vectors unchanged.")
            except Exception as _rebuild_err:
                logger.warning(f"Failed to rebuild embedder client after credential change: {_rebuild_err!r}")

        # Invalidate any cached Docling converters whose shape depended on the
        # changed knobs. Without this, switching layout_engine at runtime would
        # silently return the stale converter built against the old setting.
        # use_gpu is included because layout_engine="auto" resolves to ONNX
        # (CPU) or transformers (GPU) based on the detected device — toggling
        # use_gpu must re-resolve, not return a stale GPU/CPU converter.
        docling_changed = (
            old_pdf_mode != self._config.docling_pdf_mode
            or old_layout_engine != self._config.docling_layout_engine
            or old_use_gpu != self._config.use_gpu
        )
        if docling_changed:
            self._docling_converters.clear()
            logger.info(
                "Docling converter cache cleared (pdf_mode {!r}->{!r}, "
                "layout_engine {!r}->{!r}, use_gpu {}->{})",
                old_pdf_mode,
                self._config.docling_pdf_mode,
                old_layout_engine,
                self._config.docling_layout_engine,
                old_use_gpu,
                self._config.use_gpu,
            )

        # Reranker: if this update has it enabled, start the model download in
        # the background NOW (non-blocking) so the user's first search after
        # switching to balanced/max_quality doesn't pay the ~1.1GB fetch. No-op
        # if already loaded/loading. Searches serve fusion ranking until ready.
        if getattr(self._config, "rerank_enabled", False):
            try:
                from cuga.backend.knowledge.reranker import ensure_loading as _rerank_ensure_loading

                _rerank_ensure_loading(str(self._config.rerank_model))
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Could not start reranker background load: {}", e)

        if prepared.embedding_changed or prepared.chunking_changed or prepared.metric_changed:
            self._apply_generation += 1
            logger.info(
                f"apply_generation -> {self._apply_generation} "
                f"(e={prepared.embedding_changed} c={prepared.chunking_changed} m={prepared.metric_changed})"
            )

        return {
            "embedding_changed": prepared.embedding_changed,
            "chunking_changed": prepared.chunking_changed,
            "metric_changed": prepared.metric_changed,
            "reindex_recommended": prepared.reindex_recommended,
            "dim_changed": dim_changed,
            "previous_dim": old_dim,
            "new_dim": prepared.new_embedding_dim if prepared.new_embeddings else old_dim,
            "docling_changed": docling_changed,
        }

    def _incoming_changes_vector_config(self, knowledge_cfg: dict) -> bool:
        """Cheap (no model load / no network) check: does this incoming config
        plainly change a vector-affecting field vs the live config? Used to
        reject a reindex conflict BEFORE the expensive embedding preflight.
        Conservative — ``prepare_knowledge_update`` is the authoritative check.
        """
        if not isinstance(knowledge_cfg, dict):
            return False
        # A rag_profile switch expands into chunk_size/overlap, so a *changed*
        # profile is potentially vector-affecting (same profile is not).
        prof = knowledge_cfg.get("rag_profile")
        if prof and str(prof) != str(getattr(self._config, "rag_profile", None)):
            return True
        for field in ("embedding_provider", "embedding_model", "chunk_size", "chunk_overlap", "metric_type"):
            if field in knowledge_cfg:
                incoming = knowledge_cfg.get(field)
                if incoming is not None and str(incoming) != str(getattr(self._config, field, None)):
                    return True
        return False

    def apply_knowledge_config(self, knowledge_cfg: dict) -> dict[str, Any]:
        """Convenience: prepare + commit in one call. Used by update_settings() compat.

        Refuses vector-affecting changes (embedding / chunking / metric) while
        any reindex is in flight — committing a new embedder mid-reindex
        causes the in-flight workers to write the WRONG-dim vectors into
        the collection named for the OLD config's hash, producing a
        name-vs-content lie that breaks future resolve_collection lookups.
        Non-vector-affecting updates (rerank, search settings, rag_profile
        knobs that don't change chunking) are still allowed mid-reindex
        because they don't perturb the worker contract.
        """
        # Cheap reindex-conflict guard BEFORE prepare (Sami review): prepare
        # runs a model load + embedding preflight (a provider round-trip). If a
        # reindex is in flight and the incoming config plainly changes a
        # vector-affecting field, reject now rather than paying that cost for a
        # change we'll refuse anyway. The authoritative post-prepare check below
        # still backstops profile-expansion / coercion edge cases.
        if self._reindex_in_progress and self._incoming_changes_vector_config(knowledge_cfg):
            raise ReindexInProgressError(
                f"Reindex in progress for {sorted(self._reindex_in_progress)}; "
                f"vector-affecting changes (embedding/chunking/metric) are rejected "
                f"until all worker tasks terminate."
            )
        prepared = self.prepare_knowledge_update(knowledge_cfg)
        vector_affecting = prepared.embedding_changed or prepared.chunking_changed or prepared.metric_changed
        if vector_affecting and self._reindex_in_progress:
            raise ReindexInProgressError(
                f"Reindex in progress for {sorted(self._reindex_in_progress)}; "
                f"vector-affecting changes (embedding/chunking/metric) are rejected "
                f"until all worker tasks terminate."
            )
        return self.commit_knowledge_update(prepared)

    # --- Settings ---

    def get_knowledge_config(self) -> dict[str, Any]:
        return self._config.to_dict()

    def get_settings(self) -> dict[str, Any]:
        from cuga.backend.knowledge.config import list_profiles

        return {
            "knowledge": {
                "enabled": self._config.enabled,
                "agent_level_enabled": self._config.agent_level_enabled,
                "session_level_enabled": self._config.session_level_enabled,
                "rag_profile": self._config.rag_profile,
                "embedding_provider": self._config.embedding_provider,
                "embedding_model": self._config.embedding_model,
                # New fields wrapped in getattr for mock / older-snapshot
                # compatibility — test fixtures use SimpleNamespace stubs
                # that don't always set every field.
                "embedding_base_url": getattr(self._config, "embedding_base_url", ""),
                # Secret field — never leak the actual key; only its
                # presence so the UI can show "configured" vs "missing".
                "embedding_api_key_set": bool(getattr(self._config, "embedding_api_key", "")),
                "embedding_batch_size": getattr(self._config, "embedding_batch_size", 64),
                "embedding_concurrency": getattr(self._config, "embedding_concurrency", 4),
                "use_gpu": self._config.use_gpu,
                "gpu_required": getattr(self._config, "gpu_required", False),
                "chunk_size": self._config.chunk_size,
                "chunk_overlap": self._config.chunk_overlap,
                "metric_type": self._config.metric_type,
                "default_limit": getattr(self._config, "default_limit", 10),
                "default_score_threshold": getattr(self._config, "default_score_threshold", 0.0),
                "max_search_attempts": getattr(self._config, "max_search_attempts", 3),
                "max_ingest_workers": getattr(self._config, "max_ingest_workers", 2),
                "max_pending_tasks": self._config.max_pending_tasks,
                "vector_insert_batch_size": getattr(self._config, "vector_insert_batch_size", 200),
                "max_upload_size_mb": self._config.max_upload_size_mb,
                "max_url_download_size_mb": self._config.max_url_download_size_mb,
                "max_files_per_request": self._config.max_files_per_request,
                "max_chunks_per_document": self._config.max_chunks_per_document,
                # Docling parse knobs — operators need these visible after a
                # live PATCH to confirm pdf_mode / layout_engine took effect
                # without tailing the engine logs (Sami's review, Dec 2026).
                "docling_pdf_mode": getattr(self._config, "docling_pdf_mode", "accurate"),
                "docling_layout_engine": getattr(self._config, "docling_layout_engine", "auto"),
                "docling_drop_page_chrome": self._config.docling_drop_page_chrome,
                # Reranker knobs — same observability story; toggling rerank
                # on/off via PATCH should be confirmable from a settings dump.
                "rerank_enabled": getattr(self._config, "rerank_enabled", False),
                "rerank_top_k_in": getattr(self._config, "rerank_top_k_in", 20),
                "rerank_model": getattr(self._config, "rerank_model", "BAAI/bge-reranker-base"),
                # Noise-filter knobs — readable from UI / SDK / CLI so
                # operators can confirm a TOML edit took effect without
                # tailing logs. Both default to safe modes.
                "search_junk_filter": self._config.search_junk_filter,
                "search_hybrid_mode": self._config.search_hybrid_mode,
                # Client adaptation — text/glossary surfaced for the UI and
                # CLI to show the live state. Audit/observability hashes let
                # SREs correlate complaints to a specific adaptation version
                # without ever exposing the text content itself. Use getattr
                # for resilience against mocks / older snapshot shapes that
                # predate these fields.
                "client_adaptation_text": getattr(self._config, "client_adaptation_text", ""),
                "client_adaptation_glossary": list(
                    getattr(self._config, "client_adaptation_glossary", []) or []
                ),
                "client_adaptation_hash": _client_adapt_hash(
                    getattr(self._config, "client_adaptation_text", "")
                ),
                "client_adaptation_len": len(getattr(self._config, "client_adaptation_text", "")),
                "client_adaptation_glossary_hash": _client_glossary_hash(
                    getattr(self._config, "client_adaptation_glossary", []) or []
                ),
                "client_adaptation_glossary_count": len(
                    getattr(self._config, "client_adaptation_glossary", []) or []
                ),
            },
            "rag_profiles": {
                # Expose every section the UI needs to fully populate the
                # config when the user picks a profile. Previously only
                # name/description/search/chunking were exposed; the UI
                # therefore couldn't write back embedding_model /
                # docling_pdf_mode / rerank_* on profile-click and the
                # autosave POST re-sent stale values that overrode the
                # profile loader on the backend side. Closes the
                # "switched to max_quality but engine still using
                # bge-small" bug.
                name: {
                    "name": data.get("profile", {}).get("name", name),
                    "description": data.get("profile", {}).get("description", ""),
                    "search": data.get("search", {}),
                    "chunking": data.get("chunking", {}),
                    "embeddings": data.get("embeddings", {}),
                    "docling": data.get("docling", {}),
                    "rerank": data.get("rerank", {}),
                    "engine": data.get("engine", {}),
                }
                for name, data in list_profiles().items()
            },
        }

    def update_settings(self, **kwargs) -> dict[str, Any]:
        """Deprecated: use apply_knowledge_config() instead."""
        logger.warning("update_settings() is deprecated; use apply_knowledge_config()")
        self.apply_knowledge_config(kwargs)
        return self.get_settings()

    def _scrub_secret_text(self, text: str) -> str:
        """Strip secret material from provider error strings before they reach a
        log or an HTTP response. Replaces the ACTUAL configured key first (it may
        not match a known shape — e.g. a watsonx / custom key), then shape-based
        tokens (credentialed base_url, bearer, sk-*, api_key=...)."""
        import re as _re

        configured_key = (getattr(self._config, "embedding_api_key", "") or "").strip()
        if len(configured_key) >= 6:
            text = text.replace(configured_key, "***")
        text = _re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)
        text = _re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{6,}", r"\1***", text)
        text = _re.sub(r"\b(sk-|xai-|or-v1-|or-)[A-Za-z0-9._\-]{6,}", r"\1***", text)
        text = _re.sub(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)[A-Za-z0-9._\-]{6,}", r"\1***", text)
        return text

    async def probe_active_embedder(self) -> dict[str, Any]:
        """Cached live availability probe of the ACTIVE embedder.

        A collection's vectors are useless if its embedder can't embed queries
        (missing/invalid key, provider down, model unresolvable) — search would
        fail or return nothing. We round-trip a tiny ``embed_query`` to detect
        that, cached per vector-config-hash with a 60s TTL so the polled health
        endpoint doesn't hit the provider on every call. Cache is invalidated on
        config apply (see ``commit_knowledge_update``) so fixing a key re-probes.

        Returns ``{available, error, model}``; ``available`` is None when
        knowledge is disabled (nothing to probe).
        """
        import time as _t

        model = f"{self._config.embedding_provider or ''}/{self._config.embedding_model or ''}".strip("/")
        if not self._config.enabled:
            return {"available": None, "error": None, "model": model}
        try:
            cfg_hash = self._config.vector_config_hash()
        except Exception:
            cfg_hash = ""
        now = _t.monotonic()
        cache = self._embedder_probe_cache
        if cache and cache[0] == cfg_hash and (now - cache[3]) < 60:
            return {"available": cache[1], "error": cache[2], "model": model}

        available, error = True, None
        try:
            # _ensure_embeddings can load a local model / construct a provider
            # client — run it off the event loop so a cold engine doesn't stall
            # the polled health endpoint.
            await asyncio.to_thread(self._ensure_embeddings)
            embeddings = self._default_embeddings
            if embeddings is None:
                raise RuntimeError("embedding initialization produced no client")
            await asyncio.to_thread(embeddings.embed_query, "ping")
        except Exception as e:  # noqa: BLE001 — any failure means "unavailable"
            available = False
            # Scrub the FULL error THEN truncate — truncating first could cut a
            # configured key mid-string so the literal replace no longer matches.
            error = self._scrub_secret_text(str(e))[:300] or e.__class__.__name__
        self._embedder_probe_cache = (cfg_hash, available, error, now)
        if not available:
            logger.warning(f"Active embedder probe failed ({model}): {error}")
        return {"available": available, "error": error, "model": model}

    async def health(self, collection: str | None = None) -> dict[str, Any]:
        _emb = await self.probe_active_embedder()
        h: dict[str, Any] = {
            "status": "healthy",
            "engine": f"knowledge-{self._knowledge_vector_backend()}",
            "settings": self.get_settings()["knowledge"],
            "embeddings_initialized": self._default_embeddings is not None,
            "embedder_available": _emb["available"],
            "embedder_error": _emb["error"],
            "embedder_model": _emb["model"],
            "reindex_in_progress": list(self._reindex_in_progress),
            "stale": False,
            "reindex_deferred": False,
        }
        if collection:
            await self._ensure_metadata_ready()
            import re as _re

            _has_hash = bool(_re.search(r"_[0-9a-f]{12}$", collection))
            if not _has_hash:
                pinned = await self._metadata.get_collection_config(collection)
                if pinned and (
                    pinned.get("embedding_provider") != self._config.embedding_provider
                    or pinned.get("embedding_model") != self._config.embedding_model
                ):
                    h["stale"] = True
            if collection in self._reindex_deferred:
                h["reindex_deferred"] = True
        return h

    # --- Collection lifecycle ---

    async def drop_collection(self, collection: str) -> None:
        """Drop a collection and all its data."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)

        with self._vector_store_lock:
            adapter = self._vector_stores.pop(collection, None)
            self._record_managers.pop(collection, None)
            if adapter:
                try:
                    adapter.drop()
                except Exception as e:
                    logger.debug(f"Drop collection {collection}: {e}")

        files_dir = self._files_dir / collection
        if files_dir.exists():
            shutil.rmtree(files_dir)

        await self._metadata.delete_collection_metadata(collection)
        logger.info(f"Dropped collection {collection}")

    async def drop_collection_vectors(self, collection: str) -> None:
        """Drop vectors and metadata but preserve source files for re-indexing."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        embeddings = await self._resolve_embeddings_for_collection(collection)
        with self._vector_store_lock:
            adapter = self._vector_stores.pop(collection, None)
            self._record_managers.pop(collection, None)
            if adapter:
                try:
                    adapter.drop()
                except Exception as e:
                    logger.debug(f"Drop collection vectors {collection}: {e}")
            else:
                try:
                    temp = self._create_vector_adapter(collection, embeddings)
                    temp.drop()
                except Exception as e:
                    logger.debug(f"Drop uncached collection {collection}: {e}")
        await self._metadata.delete_collection_metadata(collection)
        logger.info(f"Dropped collection vectors {collection} (files preserved)")

    async def copy_source_files(self, source_collection: str, target_collection: str) -> int:
        """Mirror source's files into target. Removes stale files in target
        (files not present in source) BEFORE copying so a prior failed
        migration can't leave ghosts that get re-embedded at next reindex.
        Refuses source==target (would delete source's own files). Does not
        re-ingest — call reindex() on target after."""
        import shutil

        source_collection = _sanitize_collection(source_collection)
        target_collection = _sanitize_collection(target_collection)
        if source_collection == target_collection:
            return 0
        src_dir = self._files_dir / source_collection
        dst_dir = self._files_dir / target_collection
        if not src_dir.exists():
            return 0

        def _mirror() -> tuple[int, int]:
            dst_dir.mkdir(parents=True, exist_ok=True)
            src_names = {f.name for f in src_dir.iterdir() if f.is_file()}
            _stale = 0
            for f in list(dst_dir.iterdir()):
                if f.is_file() and f.name not in src_names:
                    f.unlink()
                    _stale += 1
            _count = 0
            for f in src_dir.iterdir():
                if f.is_file():
                    shutil.copy2(str(f), str(dst_dir / f.name))
                    _count += 1
            return _stale, _count

        # Run the synchronous filesystem mirror off the event loop (Sami
        # review): a large migration (many/large files) would otherwise block
        # every concurrent request for its whole duration. Same to_thread
        # pattern used for ingest + delete I/O in this file.
        stale, count = await asyncio.to_thread(_mirror)
        if stale:
            logger.info(
                f"Removed {stale} stale file(s) from {target_collection}; copied {count} from {source_collection}"
            )
        else:
            logger.info(f"Copied {count} source files from {source_collection} to {target_collection}")
        return count

    async def reindex(self, collection: str) -> dict[str, Any]:
        """Drop collection vectors and re-ingest all files with current settings.

        Creates per-file tasks. Returns immediately; ingestion runs in background.
        Raises ReindexBusyError if uploads are in progress.
        Sets _reindex_in_progress flag to block new uploads during reindex.
        """
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        files_dir = self._files_dir / collection
        if not files_dir.exists():
            return {"status": "no_documents", "count": 0}

        task_ids: list[str] = []
        # Parallel list of {task_id, filename} pairs so the route can return
        # filenames in the POST response and the FE can render the reindex
        # tile with real names from millisecond 0 — instead of flashing
        # ``task_xxx`` placeholders until the next /tasks poll arrives
        # (#402 follow-up: production sweep).
        task_entries: list[dict[str, str]] = []
        try:
            lock = self._get_collection_lock(collection)
            async with lock:
                pending = [
                    t
                    for t in await self._metadata.list_tasks(collection)
                    if t["status"] in ("pending", "running")
                ]
                if pending:
                    raise ReindexBusyError(len(pending))
                self._reindex_in_progress.add(collection)
                # Snapshot the file list AFTER flagging, under the same lock, so a
                # concurrent delete either ran before the flag (and is excluded)
                # or is rejected by delete_document's in-lock re-check — closing
                # the delete/reindex TOCTOU that could resurrect a deleted doc
                # (CR-D).
                file_list = [f for f in files_dir.iterdir() if f.is_file()]
                if not file_list:
                    self._reindex_in_progress.discard(collection)
                    return {"status": "no_documents", "count": 0}
                await self.drop_collection_vectors(collection)

            for file_path in file_list:
                task_info = await self._create_reindex_task_entry(collection, file_path.name)
                task_ids.append(task_info["task_id"])
                task_entries.append({"task_id": task_info["task_id"], "filename": file_path.name})

            # Background worker. Files re-ingest concurrently — _run_ingest is
            # bounded by _ingest_sem (max_ingest_workers), so this amortizes the
            # GPU/CPU across files instead of one-at-a-time. return_exceptions
            # keeps one bad file from aborting the rest (_ingest_inner already
            # marks its own task failed).
            async def _reindex_worker():
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            *(
                                self._run_ingest(
                                    collection, fp, fp.name, tid, replace_duplicates=True, skip_file_copy=True
                                )
                                for fp, tid in zip(file_list, task_ids)
                            ),
                            return_exceptions=True,
                        ),
                        timeout=_REINDEX_WORKER_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    # A wedged provider call must not pin the collection forever.
                    logger.error(
                        f"Reindex worker for {collection} exceeded "
                        f"{_REINDEX_WORKER_TIMEOUT_S}s (wedged provider call?); "
                        f"failing unfinished tasks + releasing busy flag."
                    )
                    # Terminalize any still-pending/running task rows — otherwise
                    # the next reindex() sees them as active and raises
                    # ReindexBusyError, so the timeout wouldn't actually self-heal
                    # (the finally only clears the collection busy flag).
                    try:
                        _rows = {t["task_id"]: t for t in await self._metadata.list_tasks(collection)}
                    except Exception:
                        _rows = {}
                    for fp, tid in zip(file_list, task_ids):
                        _row = _rows.get(tid)
                        if _row is None or _row.get("status") in ("pending", "running"):
                            try:
                                await self._metadata.update_task(
                                    tid,
                                    status="failed",
                                    file_tasks={
                                        fp.name: {
                                            "filename": fp.name,
                                            "status": "failed",
                                            "error": "reindex timeout",
                                        }
                                    },
                                )
                            except Exception as _term_err:
                                logger.warning(f"Failed to terminalize timed-out task {tid}: {_term_err!r}")
                finally:
                    self._reindex_in_progress.discard(collection)
                    self._reindex_deferred.discard(collection)

            _bg_reindex = asyncio.create_task(_reindex_worker())
            _BACKGROUND_REINDEX_TASKS.add(_bg_reindex)
            _bg_reindex.add_done_callback(_BACKGROUND_REINDEX_TASKS.discard)
            _bg_reindex.add_done_callback(lambda t: t.exception())
        except ReindexBusyError:
            raise  # Don't clear flag (was never set for this collection)
        except Exception:
            self._reindex_in_progress.discard(collection)
            for tid in task_ids:
                try:
                    await self._metadata.update_task(tid, status="failed", file_tasks={})
                except Exception:
                    pass
            raise

        return {
            "status": "started",
            "count": len(file_list),
            "task_ids": task_ids,
            "tasks": task_entries,
        }

    # --- Document loading ---

    _DOCLING_FORMATS = {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".htm",
        ".md",
        ".csv",
        ".asciidoc",
        ".adoc",
        ".tex",
        ".latex",
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
        ".bmp",
        ".webp",
    }

    def _get_effective_chunk_settings(self) -> tuple[int, int]:
        """Get chunk_size and chunk_overlap from _config (source of truth after publish)."""
        return self._config.chunk_size, self._config.chunk_overlap

    def get_chunking_tokenizer(self) -> ChunkingTokenizer:
        """Resolve the tokenizer + safe max for the active embedder.
        SINGLE dispatcher. Both ``_build_docling_chunker`` and
        ``_build_text_splitter`` consume this; before the refactor each
        had its own copy of the provider branching.

        Dispatch (first match wins):
          1. fastembed -> Rust ONNX tokenizer (exact match w/ embedder)
          2. huggingface -> the embedder's own ``_tokenizer`` attr
          3a. litellm/openrouter/openai routed to openai/azure -> tiktoken
              cl100k_base (no Hub HEAD; correct encoding for these models)
          3b. litellm/openrouter/openai with any other slash-containing
              model -> AutoTokenizer.from_pretrained via HF Hub (PR-A
              made the Hub the source of truth; no curated allow-list)
          4. everything else -> 'approximate' kind (cohere/voyage/gemini
              when their HF lookup 404s, plain-named local models, etc.)
        """
        provider = (self._config.embedding_provider or "").lower()
        model_raw = self._config.embedding_model or ""

        def _make(kind: str, encoder: Any, name: str, safe_max: int) -> ChunkingTokenizer:
            """Construct + log a ChunkingTokenizer in one place. Computes
            ``recommended_chunk_tokens`` from ``safe_max`` via the global
            policy (≤2K-ctx → safe_max; ≥2K-ctx → 512). Workflow
            w5i1mbchd synth fix #2 + #6."""
            t = ChunkingTokenizer(
                kind=kind,
                encoder=encoder,
                name=name,
                safe_max_tokens=safe_max,
                recommended_chunk_tokens=_recommended_chunk_tokens(safe_max),
            )
            # [#400] INFO (not DEBUG) — operators need this visible by
            # default. When a customer reports a context-window error,
            # the first question is which dispatch branch ran (workflow
            # w5i1mbchd synth fix #6, R1's explicit ask).
            logger.info(
                f"[#400] tokenizer-dispatch provider={provider!r} "
                f"model={model_raw!r} -> kind={t.kind} name={t.name!r} "
                f"safe_max_tokens={t.safe_max_tokens} "
                f"recommended_chunk_tokens={t.recommended_chunk_tokens}"
            )
            return t

        # 1. fastembed — exact match via the embedder's own ONNX tokenizer.
        if provider == "fastembed":
            try:
                self._ensure_embeddings()
                emb = self._default_embeddings
                if isinstance(emb, _FastEmbedEmbeddings):
                    seq = _fastembed_docling_seq_limit(self._config.embedding_model or "")
                    return _make(
                        "fastembed",
                        emb._model,
                        f"fastembed:{self._config.embedding_model or 'default'}",
                        seq,
                    )
            except Exception:
                pass  # fall through to approximate

        # 2. HuggingFace provider — embedder loaded its own tokenizer.
        if provider == "huggingface":
            try:
                self._ensure_embeddings()
                emb = self._default_embeddings
                if getattr(emb, "_tokenizer", None) is not None:
                    return _make(
                        "hf",
                        emb._tokenizer,
                        self._config.embedding_model or "huggingface",
                        _hf_tokenizer_seq_limit(emb._tokenizer),
                    )
            except Exception:
                pass

        # 3. litellm/openrouter/openai routes.
        if provider in ("litellm", "openrouter", "openai"):
            # 3a. tiktoken FIRST for openai-native + azure routes (no HTTP
            # cost). Strip only the outer routing prefix (litellm/ or
            # openrouter/) so we can still see the inner openai/ or
            # azure/ marker.
            outer = model_raw.lower()
            for outer_prefix in ("litellm/", "openrouter/"):
                if outer.startswith(outer_prefix):
                    outer = outer[len(outer_prefix) :]
                    break
            if provider == "openai" or outer.startswith(("openai/", "azure/")):
                try:
                    import tiktoken

                    # _TIKTOKEN_SAFE_MAX = 8191 - 16 = 8175. The OpenAI API
                    # rejects at >8191; matching the HF branch's margin
                    # keeps the dispatcher internally consistent
                    # (workflow w5i1mbchd synth fix #1).
                    return _make(
                        "tiktoken",
                        tiktoken.get_encoding("cl100k_base"),
                        "cl100k_base",
                        _TIKTOKEN_SAFE_MAX,
                    )
                except Exception:
                    pass

            # 3b. Try HF Hub as the source of truth (PR-A, workflow
            # w9y9xtyse synth). Static aliases in ``_HF_REPO_ALIASES``
            # short-circuit known-good redirects (jina, nomic) to skip
            # a guaranteed Hub HEAD miss.
            repo_id = _hf_repo_id_candidate(model_raw)
            if repo_id:
                auto_tok = _load_hf_tokenizer_for_chunking(repo_id)
                if auto_tok is not None:
                    return _make(
                        "hf",
                        auto_tok,
                        repo_id,
                        _hf_tokenizer_seq_limit(auto_tok),
                    )

        # 4. everything else — no precise tokenizer available. Caller
        # decides how to react (chunker uses a HybridChunker default;
        # text-splitter falls back to char-based recursive split).
        return _make("approximate", None, "char-based", _DEFAULT_APPROXIMATE_CAP)

    def _warn_chunk_size_above_retrieval_recommended(self, chunk_size: int, tok: ChunkingTokenizer) -> None:
        """Advisory log when ``chunk_size`` is within the hard ceiling
        but exceeds the retrieval-quality recommendation. Fires at most
        once per (model, chunk_size, recommended) tuple per process so
        per-file chunker rebuilds don't spam the log.

        Workflow w5i1mbchd synth fix #2: published evidence (LongEmbed
        EMNLP 2024 +24% MRR for 512 vs 1024, voyage-context-3 default,
        BAAI bge-m3 maintainer) shows retrieval quality peaks at
        256-512 tokens regardless of embedder context length. Letting
        a chunk grow to 8K just because the embedder allows it tends
        to hurt retrieval (pooled embeddings dilute the signal). This
        warning surfaces the recommendation without changing the
        behavior — users who explicitly want larger chunks can ignore
        it; users who left chunk_size at 800 on a 32K embedder know
        they're potentially sub-optimal."""
        if chunk_size <= tok.recommended_chunk_tokens:
            return
        # Same-tuple dedup. The lru_cache is a single-call no-op that
        # ensures the wrapped logger.info fires at most once for any
        # (model, chunk_size, recommended) tuple this process sees.
        _warn_chunk_oversized_for_retrieval(
            tok.name, chunk_size, tok.recommended_chunk_tokens, tok.safe_max_tokens
        )

    def _build_text_splitter(self, chunk_size: int, chunk_overlap: int):
        """Token-aware splitter via the unified ``get_chunking_tokenizer``
        dispatch. See #387 follow-up — both this and ``_build_docling_chunker``
        used to carry their own provider-branch copy; now they share the
        accessor and just switch on ``tok.kind``.

        ``tok.safe_max_tokens`` already accounts for the embedder-wrapping
        margin for HF kinds (see ``_hf_tokenizer_seq_limit`` /
        ``_HF_TOKEN_SAFETY_MARGIN``)."""
        tok = self.get_chunking_tokenizer()
        self._warn_chunk_size_above_retrieval_recommended(chunk_size, tok)
        cap = min(chunk_size, tok.safe_max_tokens)
        overlap = min(chunk_overlap, max(cap // 4, 1))

        if tok.kind == "hf":
            try:
                # ``from_huggingface_tokenizer`` overcounts BOS/EOS by ~2
                # tokens (langchain#30184) — harmless under-fill, not the
                # 518>512 truncation that the safety margin handles.
                return RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
                    tok.encoder, chunk_size=cap, chunk_overlap=overlap
                )
            except Exception as e:
                logger.warning(f"from_huggingface_tokenizer failed ({e}); char fallback.")

        if tok.kind == "tiktoken":
            try:
                # cap (not raw chunk_size) — same bound the hf branch uses.
                # The Fix-4 post-chunk guard delegates its re-split here; if
                # this passed the raw chunk_size, an over-limit tiktoken chunk
                # (chunk_size > safe_max for an openai embedder) would be
                # re-split at the raw value and STILL exceed 8191 → the exact
                # silent-truncation the guard exists to prevent.
                return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                    encoding_name=tok.name, chunk_size=cap, chunk_overlap=overlap
                )
            except Exception as e:
                logger.warning(f"from_tiktoken_encoder failed ({e}); char fallback.")

        # ``approximate`` (cohere/voyage/gemini/etc.), ``fastembed`` plain-text
        # path, AND the hf/tiktoken exception fallbacks above all land here:
        # char-based fallback. Use the token-aware ``cap``/``overlap`` (NOT the
        # raw chunk_size) so a failed tokenizer path can't emit a chunk that
        # exceeds the embedder's boundary (CR — the whole point of the cap).
        return RecursiveCharacterTextSplitter(chunk_size=cap, chunk_overlap=overlap)

    @staticmethod
    def _exact_chunk_tokens(text: str, tok: "ChunkingTokenizer") -> int:
        """Exact token count for a finished chunk in the embedder's OWN
        tokenizer. Defined only for the kinds with a cheap local counter
        and a HARD context limit (``hf`` e.g. e5=512, ``tiktoken`` e.g.
        openai=8191) — the post-chunk boundary guard restricts itself to
        those. ``tok.encoder`` is the HF AutoTokenizer (``hf``) or the
        tiktoken Encoding (``tiktoken``)."""
        if tok.kind == "hf":
            return len(tok.encoder.tokenize(text))
        return len(tok.encoder.encode(text))

    def _build_docling_chunker(self, chunk_size: int):
        """Build a HybridChunker that respects our chunk_size config.

        HybridChunker combines hierarchical (heading-aware) splitting with
        token-based size limits.  Key features:
        - ``max_tokens`` enforces our configured chunk size
        - ``merge_peers=True`` merges small sibling chunks for density
        - ``repeat_table_header=True`` repeats table/form headers in every
          chunk so field labels are preserved alongside their values

        When embeddings use fastembed, the chunker tokenizer uses the same ONNX
        tokenizer (avoids downloading sentence-transformers/all-MiniLM-L6-v2).
        """
        try:
            from docling_core.transforms.chunker import HybridChunker

            tok_info = self.get_chunking_tokenizer()
            self._warn_chunk_size_above_retrieval_recommended(chunk_size, tok_info)
            cap = min(chunk_size, tok_info.safe_max_tokens)
            provider = (self._config.embedding_provider or "").lower()
            model_str = self._config.embedding_model or "default"

            if tok_info.kind == "fastembed":
                tok = _fastembed_docling_tokenizer_cls()(text_embedding=tok_info.encoder, max_tokens=cap)
                logger.debug(
                    f"HybridChunker tokenizer: fastembed ONNX (model={tok_info.name!r}, "
                    f"chunk_token_limit={cap}, model_seq_limit={tok_info.safe_max_tokens})"
                )
                return HybridChunker(tokenizer=tok)

            if tok_info.kind == "hf":
                try:
                    from docling_core.transforms.chunker.tokenizer.huggingface import (
                        HuggingFaceTokenizer,
                    )

                    if cap < chunk_size:
                        logger.info(
                            f"Capping chunk_size {chunk_size} -> {cap} for embedder "
                            f"{tok_info.name} (safe_max_tokens={tok_info.safe_max_tokens}). "
                            f"Chunks at the original size would have been truncated at embed time."
                        )
                    # Mute transformers' benign "Token indices sequence length is
                    # longer than the specified maximum (N > model_max)" warning.
                    # HybridChunker MEASURES candidate windows that transiently
                    # exceed model_max (via tokenizer.tokenize) before backing off
                    # to emit chunks <= max_tokens=cap — see _split_using_plain_text
                    # (reserves heading room) + the merge step's <= max_tokens guard.
                    # The warning fires from that measurement, NOT from an emitted
                    # or embedded chunk, but operators read "550 > 512" as a failure.
                    # Pre-set transformers' own fire-once flag rather than raising
                    # model_max_length (which would corrupt safe_max_tokens on the
                    # next dispatch for long-context HF embedders).
                    _dw = getattr(tok_info.encoder, "deprecation_warnings", None)
                    if isinstance(_dw, dict):
                        _dw["sequence-length-is-longer-than-the-specified-maximum"] = True
                    tok = HuggingFaceTokenizer(tokenizer=tok_info.encoder, max_tokens=cap)
                    logger.debug(
                        f"HybridChunker tokenizer: HF AutoTokenizer "
                        f"(repo={tok_info.name!r}, chunk_token_limit={cap}, "
                        f"safe_max_tokens={tok_info.safe_max_tokens}, provider={provider!r})"
                    )
                    return HybridChunker(tokenizer=tok)
                except Exception as wrap_err:
                    logger.warning(
                        f"HuggingFaceTokenizer wrap failed for {tok_info.name!r} ({wrap_err}); "
                        f"falling back to tiktoken."
                    )

            if tok_info.kind == "tiktoken":
                try:
                    tok = _tiktoken_docling_tokenizer_cls()(encoding=tok_info.encoder, max_tokens=cap)
                    logger.debug(
                        f"HybridChunker tokenizer: tiktoken (encoding={tok_info.name!r}, "
                        f"provider={provider!r}, model={model_str!r}); zero-download"
                    )
                    return HybridChunker(tokenizer=tok)
                except Exception as tok_err:  # pragma: no cover - defensive
                    logger.warning(f"tiktoken chunker tokenizer failed ({tok_err}); Docling default.")

            # ``approximate`` kind — last-resort tiktoken cl100k_base + canary.
            # Same path the pre-refactor "everything else" branch took, just
            # consolidated. Operator canary fires for unlisted slash-models
            # on litellm/openrouter routes (the splitter mirrors this with
            # "char_based_split" key so chunker vs splitter origin is
            # distinguishable in operator logs).
            try:
                import tiktoken

                encoding = tiktoken.get_encoding("cl100k_base")
                tok = _tiktoken_docling_tokenizer_cls()(encoding=encoding, max_tokens=cap)
                logger.debug(
                    f"HybridChunker tokenizer: cl100k_base (approximate; "
                    f"provider={provider!r}, model={model_str!r})"
                )
                return HybridChunker(tokenizer=tok)
            except Exception as tok_err:  # pragma: no cover - defensive
                logger.warning(f"tiktoken approximate path failed ({tok_err}); Docling default.")

            return HybridChunker(max_tokens=chunk_size)
        except Exception as e:
            logger.warning(f"HybridChunker init failed, falling back to default: {e}")
            return None

    @staticmethod
    def _resolve_layout(layout_engine_choice: str, device_label: str) -> tuple[str, str]:
        """Return (effective_layout_engine, layout_device) for the given choice + device.

        Resolves "auto" based on the detected device and computes the device
        the layout stage will actually run on. The ONNX engine is CPU-only
        regardless of `device_label`; only the PyTorch ("transformers")
        engine honors MPS/CUDA. Kept as a static helper so both the converter
        builder and the per-ingest timing log can produce the same answer
        without duplicating the resolution rule.

          auto + mps/cuda → transformers (engage the GPU)
          auto + cpu      → onnx        (lean CPU path)
          explicit onnx   → onnx        (escape hatch)
          explicit transformers → transformers (escape hatch)
        """
        choice = (layout_engine_choice or "auto").lower()
        if choice == "auto":
            effective = "transformers" if device_label in ("mps", "cuda") else "onnx"
        else:
            effective = choice
        layout_device = device_label if effective == "transformers" else "cpu"
        return effective, layout_device

    def _get_docling_converter(self):
        """Get or create a reusable Docling DocumentConverter.

        Caches per ``(pdf_mode, layout_engine)`` so toggling either between
        ingests doesn't reload model weights every time, but still picks up
        the change without a process restart. The PdfPipelineOptions
        mapping for each mode:

          fast:     OCR off, tables off — digital PDFs only, fastest.
          balanced: OCR off, tables on  — digital PDFs with table extraction.
          accurate: OCR on,  tables on  — Tesseract with ``lang=["auto"]``
                    so Docling does per-page script detection (osd.traineddata)
                    and OCRs with the matching language model. Covers Latin,
                    Hebrew, Arabic, Cyrillic, CJK, etc. in one path. Requires
                    the ``tesseract`` system binary + matching ``*.traineddata``
                    packs (cuga's Docker images bundle a curated set; macOS
                    devs: ``brew install tesseract tesseract-lang``).
        """
        import os
        from pathlib import Path

        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
        from docling.document_converter import DocumentConverter, PdfFormatOption

        mode = (self._config.docling_pdf_mode or "accurate").lower()
        if mode not in ("fast", "balanced", "accurate"):
            logger.warning("Unknown docling_pdf_mode={!r}; falling back to 'accurate'", mode)
            mode = "accurate"

        layout_engine_choice = (self._config.docling_layout_engine or "auto").lower()
        device_label, _ = _detect_accelerator(self._config.use_gpu)
        effective_layout_engine, _ = self._resolve_layout(layout_engine_choice, device_label)

        # Cache by EFFECTIVE engine so explicit "transformers" and "auto"
        # on a GPU host share one cached converter. Toggling use_gpu at
        # runtime invalidates the cache in commit_knowledge_update, so
        # auto re-resolves with the new device.
        cache_key = f"{mode}|{effective_layout_engine}"
        cached = self._docling_converters.get(cache_key)
        if cached is not None:
            return cached

        artifacts_path_str = os.environ.get("DOCLING_ARTIFACTS_PATH")
        artifacts_path = Path(artifacts_path_str) if artifacts_path_str else None

        pipeline_options = PdfPipelineOptions(artifacts_path=artifacts_path)
        # Pass the detected device to Docling. The transformers engine reads
        # it directly; the ONNX engine ignores the device field (its provider
        # list is what matters for ONNX, and that stays CPU).
        try:
            device_enum = {
                "cuda": AcceleratorDevice.CUDA,
                "mps": AcceleratorDevice.MPS,
                "cpu": AcceleratorDevice.CPU,
            }.get(device_label, AcceleratorDevice.AUTO)
            pipeline_options.accelerator_options = AcceleratorOptions(device=device_enum)
        except Exception as e:  # pragma: no cover - defensive against API drift
            logger.debug("Docling AcceleratorOptions unavailable: {}", e)

        # Skip page/picture image rendering in FAST mode only. Saves ~30 ms/
        # page of PNG encoding + RAM (Expert D, 2026-06-09: ~17 s on a 556-
        # page PDF). Balanced and accurate modes keep the default behavior
        # because users on those profiles may have downstream consumers
        # (UI previews, page-image exports, third-party extensions) that
        # rely on the rendered PNGs. ``fast`` is explicitly opinionated
        # toward speed — opting users out of that artifact there is
        # consistent with the profile's contract.
        # Best-effort setattr — Docling's PdfPipelineOptions surface has
        # drifted across releases.
        if mode == "fast":
            for _img_attr in ("generate_page_images", "generate_picture_images"):
                try:
                    setattr(pipeline_options, _img_attr, False)
                except (AttributeError, TypeError):
                    pass

        # Wire the layout engine. effective_layout_engine is always
        # "onnx" or "transformers" at this point (auto already resolved).
        try:
            from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions
            from docling.datamodel.object_detection_engine_options import (
                OnnxRuntimeObjectDetectionEngineOptions,
                TransformersObjectDetectionEngineOptions,
            )

            if effective_layout_engine == "transformers":
                # Disable torch.compile on MPS — PyTorch 2.11's Inductor MPS
                # codegen has a bug ("variable declared with deduced type
                # 'auto' cannot appear in its own initializer") that crashes
                # shader compilation on layout models. The PyTorch model
                # itself still runs on MPS via device_map; we only skip the
                # JIT optimization pass. Remove this when PyTorch fixes it.
                engine_opts = TransformersObjectDetectionEngineOptions(
                    compile_model=device_label != "mps",
                )
            else:
                engine_opts = OnnxRuntimeObjectDetectionEngineOptions()
            # ``create_orphan_clusters=True`` is LOAD-BEARING. Docling's
            # default ``LayoutOptions`` ships with this on; the alternative
            # ``LayoutObjectDetectionOptions`` we use here defaults it to
            # OFF (and the upstream docstring explicitly warns:
            # "Orphan cluster creation is disabled by default — enable
            # if unassigned elements must be preserved").
            #
            # Without it, layout boxes that the object-detection model
            # didn't classify into a structure get DROPPED — observed on
            # a Hebrew population-registry form where the field-label
            # column and the actual ID number (``מספר הזהות`` /
            # ``2 4585791 7``) lived in "orphan" text elements. Reproducer:
            # /tmp/diag_layout.py — without this flag the chunk text
            # collapses from 574 chars to 320 chars and the ID is gone.
            pipeline_options.layout_options = LayoutObjectDetectionOptions(
                engine_options=engine_opts,
                create_orphan_clusters=True,
            )
        except Exception as e:  # pragma: no cover - defensive against API drift
            logger.warning(
                "Failed to apply layout engine={!r}, falling back to Docling default: {}",
                effective_layout_engine,
                e,
            )
        # OCR engine — three-tier fallback so `pip install cuga` works without
        # any system install steps:
        #   1. Tesseract on PATH      → TesseractCliOcrOptions(lang=["auto"]).
        #      Best quality + full multilingual via osd.traineddata. Production
        #      Docker images bundle this (see Dockerfile).
        #   2. EasyOCR importable     → EasyOcrOptions(). Already a cuga pip
        #      dep, so no extra install. Latin-script default langs; non-Latin
        #      docs may need Tesseract for full coverage. EasyOCR's own
        #      `use_gpu=None` lets it auto-engage MPS/CUDA when available.
        #   3. Neither available      → degrade to `balanced` (no OCR, digital
        #      text layer only). Last-resort; never crashes ingest.
        # Reference: https://docling-project.github.io/docling/examples/tesseract_lang_detection/
        import shutil as _shutil

        effective_mode = mode
        selected_ocr_engine = "docling-default"
        # ``force_full_page_ocr=True`` (accurate mode only) re-OCRs the
        # page image even when the PDF advertises a text layer. Why this
        # is the safe default:
        #   - Docling's default (force_full_page_ocr=False) skips OCR
        #     for pages with ANY text layer present.
        #   - Some PDFs (Hebrew government forms, IBM Box exports, scan
        #     PDFs with embedded "OCR underlay") carry a text layer that
        #     is actually just font CID glyph IDs without Unicode mapping
        #     — Docling extracts ``/CE3/CE5/CE1/...`` mojibake verbatim.
        #     The cid_glyph_run junk filter (see `_classify_junk_chunk`)
        #     catches these AT RETRIEVAL but the chunks are still indexed
        #     and waste storage.
        #   - Forcing OCR on accurate adds ~5-15s/page for clean PDFs but
        #     eliminates the silent-corruption mode. ``accurate`` already
        #     means "quality over speed"; balanced/fast paths skip OCR
        #     entirely so they're unaffected.
        #   - Reproducer: ``/tmp/diag_insurance.py`` on a CID-encoded PDF.
        if mode == "accurate":
            if _shutil.which("tesseract"):
                from docling.datamodel.pipeline_options import TesseractCliOcrOptions

                pipeline_options.ocr_options = TesseractCliOcrOptions(
                    lang=["auto"],
                    force_full_page_ocr=True,
                )
                selected_ocr_engine = "tesseract-cli"
            else:
                try:
                    from docling.datamodel.pipeline_options import EasyOcrOptions
                    import easyocr  # noqa: F401 — guard the actual lib too

                    # Honor the user's GPU intent. EasyOCR's `use_gpu=None`
                    # auto-detects MPS/CUDA; setting False forces CPU when
                    # the user has explicitly opted out.
                    # ``force_full_page_ocr=True`` symmetric with the
                    # Tesseract path — see the comment block above for
                    # why this is the right default in accurate mode.
                    _easy_use_gpu = None if self._config.use_gpu else False
                    pipeline_options.ocr_options = EasyOcrOptions(
                        use_gpu=_easy_use_gpu,
                        force_full_page_ocr=True,
                    )
                    selected_ocr_engine = "easyocr"
                    logger.info(
                        "Tesseract not on PATH — using EasyOCR (bundled with cuga). "
                        "For best multilingual quality (Hebrew/Arabic/CJK via lang=auto), "
                        "install Tesseract:\n"
                        "  macOS: brew install tesseract tesseract-lang\n"
                        "  Linux: apt install tesseract-ocr tesseract-ocr-all"
                    )
                except ImportError:
                    logger.warning(
                        "Neither Tesseract (system binary) nor EasyOCR (pip dep) "
                        "available — degrading 'accurate' to 'balanced' (no OCR; "
                        "digital text layer only). Install one:\n"
                        "  macOS: brew install tesseract tesseract-lang\n"
                        "  Linux: apt install tesseract-ocr tesseract-ocr-all\n"
                        "  Any:   pip install easyocr"
                    )
                    effective_mode = "balanced"

        # Docling enrichments — DISABLED by default (off in every mode).
        # WHY: do_code_enrichment + do_formula_enrichment are backed by an
        # autoregressive vision-language model (docling.models.inference_engines.vlm).
        # Measured cost on Apple Silicon MPS for a typical 38-page paper:
        #   ~35-220 seconds PER BATCH of 5 images
        #   total: ~20+ minutes added to ingest
        # That's a 30× regression over a no-enrichment ingest of the same paper.
        # On CUDA the VLM is also expensive (single-digit seconds per image but
        # still adds 2-5 minutes to a 50-page paper). Until an opt-in config
        # surface exists, leaving them off everywhere is the only safe default.
        # The cost of skipping them: code blocks may flatten to prose and math
        # may garble — acceptable trade-off for default operation.

        # Apply per-mode overrides. Docling defaults today are
        # do_ocr=True, do_table_structure=True; we only override fields we
        # explicitly want to flip, so future Docling additions inherit
        # sensible defaults.
        if effective_mode == "fast":
            try:
                pipeline_options.do_ocr = False
            except (AttributeError, TypeError):  # pragma: no cover - defensive
                pass
            try:
                pipeline_options.do_table_structure = False
            except (AttributeError, TypeError):  # pragma: no cover
                pass
        elif effective_mode == "balanced":
            try:
                pipeline_options.do_ocr = False
            except (AttributeError, TypeError):  # pragma: no cover
                pass
            # do_table_structure stays at Docling default (True today).
        # "accurate" → leave all defaults as-is.

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        self._docling_converters[cache_key] = converter
        # Layout actually runs on `device_label` only via the transformers
        # engine; the ONNX engine is CPU-only regardless of device_label.
        layout_device = device_label if effective_layout_engine == "transformers" else "cpu"
        logger.info(
            "Docling DocumentConverter initialized "
            "(mode={!r} effective={!r}, do_ocr={}, do_table_structure={}, "
            "do_code_enrichment={}, do_formula_enrichment={}, "
            "ocr_engine={!r}, ocr_langs={}, "
            "layout_engine={!r}, layout_device={!r})",
            mode,
            effective_mode,
            getattr(pipeline_options, "do_ocr", "<default>"),
            getattr(pipeline_options, "do_table_structure", "<default>"),
            getattr(pipeline_options, "do_code_enrichment", "<missing>"),
            getattr(pipeline_options, "do_formula_enrichment", "<missing>"),
            selected_ocr_engine if effective_mode == "accurate" else "n/a",
            (getattr(pipeline_options.ocr_options, "lang", "n/a") if effective_mode == "accurate" else "n/a"),
            effective_layout_engine,
            layout_device,
        )
        return converter

    def _load_document(self, file_path: Path) -> list[Document]:
        """Load a document using Docling for supported formats, fallback for plain text."""
        suffix = file_path.suffix.lower()
        logger.info(
            f"Loading document: {file_path.name} (suffix={suffix}, size={file_path.stat().st_size} bytes)"
        )

        chunk_size, chunk_overlap = self._get_effective_chunk_settings()

        if suffix in self._DOCLING_FORMATS:
            try:
                from langchain_docling.loader import ExportType

                chunker = self._build_docling_chunker(chunk_size)
                loader_kwargs: dict = {
                    "file_path": str(file_path),
                    "export_type": ExportType.DOC_CHUNKS,
                    "converter": self._get_docling_converter(),
                }
                if chunker is not None:
                    loader_kwargs["chunker"] = chunker
                loader = DoclingLoader(**loader_kwargs)
                docs = loader.load()
            except Exception as e:
                translated = _translate_document_load_error(file_path, e)
                logger.error(
                    f"Docling failed to parse {file_path.name}: {type(translated).__name__}: {translated}"
                )
                raise translated from e
        elif suffix in (
            ".txt",
            ".text",
            ".log",
            ".json",
            ".xml",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
        ):
            text = file_path.read_text(errors="replace")
            # Token-aware split when we have an HF tokenizer for the active
            # embedder — guarantees no chunk exceeds the model's max_seq_length.
            # Falls back to char-based for providers without an HF tokenizer
            # (legacy fastembed default, cohere/voyage/gemini under cl100k).
            splitter = self._build_text_splitter(chunk_size, chunk_overlap)
            chunks = splitter.split_text(text)
            docs = [Document(page_content=chunk, metadata={"page": i + 1}) for i, chunk in enumerate(chunks)]
        else:
            try:
                from langchain_docling.loader import ExportType

                chunker = self._build_docling_chunker(chunk_size)
                loader_kwargs = {
                    "file_path": str(file_path),
                    "export_type": ExportType.DOC_CHUNKS,
                    "converter": self._get_docling_converter(),
                }
                if chunker is not None:
                    loader_kwargs["chunker"] = chunker
                loader = DoclingLoader(**loader_kwargs)
                docs = loader.load()
            except Exception:
                raise ValueError(f"Unsupported file format: {suffix}")

        logger.info(f"Loaded {len(docs)} raw chunks from {file_path.name}")

        # Post-process re-split (issue #387 follow-up). The old version of
        # this block fired whenever any chunk's CHAR length exceeded
        # ``chunk_size * 2`` (with ``chunk_size`` being the user's
        # token-target like 800). That conflates two units:
        #   - HybridChunker measures in TOKENS via the model's tokenizer
        #     and produces chunks bounded by ``min(chunk_size, model_max_seq_length)``
        #     in those tokens (after #387 fix).
        #   - RecursiveCharacterTextSplitter measures in CHARS. Feeding it
        #     ``chunk_size=800`` produced 800-char pieces, which for dense
        #     content (academic English, multilingual, code) can be
        #     600-800 XLM-RoBERTa tokens — past e5-large's 512 ceiling.
        #     The embedder then silently truncated, evidenced in user
        #     logs by ``[transformers] Token indices sequence length is
        #     longer than the specified maximum sequence length for this
        #     model (716 > 512)``.
        #
        # The re-split exists as a safety net for the rare case Docling
        # returns a single pathological mega-chunk (file with no natural
        # breaks, or a non-tokenizer-aware fallback path). Raise the
        # threshold so the safety net only fires for those, not for
        # normal HybridChunker output. Anything HybridChunker produces
        # with a configured tokenizer is already token-bounded; this
        # split would only WEAKEN that guarantee.
        # STRICT chunk->embedder boundary guard (issue #387). HybridChunker
        # already bounds chunks to max_tokens=cap, but for providers with a
        # HARD context limit we VERIFY in the embedder's own tokenizer and
        # re-split anything that slipped through (a single indivisible doc
        # item, or a docling-core regression). This converts a SILENT
        # embedder-side truncation — degraded retrieval, near-impossible to
        # debug — into a clean token-aware re-split plus one visible WARNING.
        # Only ``hf``/``tiktoken`` have a cheap exact local counter; fastembed
        # is docling-bounded (<=512 always) and the char-based ``approximate``
        # path has no exact counter here, so both fall to the coarse net below.
        if docs:
            tok_info = self.get_chunking_tokenizer()
            if tok_info.kind in ("hf", "tiktoken"):
                try:
                    over = sum(
                        1
                        for d in docs
                        if self._exact_chunk_tokens(d.page_content, tok_info) > tok_info.safe_max_tokens
                    )
                except Exception as e:  # noqa: BLE001 — never let the guard break ingest
                    logger.debug(f"post-chunk token check skipped for {file_path.name}: {e!r}")
                    over = 0
                if over:
                    splitter = self._build_text_splitter(chunk_size, chunk_overlap)
                    n_before = len(docs)
                    docs = splitter.split_documents(docs)
                    logger.warning(
                        f"Token-bound re-split for {file_path.name}: {over}/{n_before} chunk(s) "
                        f"exceeded safe_max_tokens={tok_info.safe_max_tokens} for "
                        f"{tok_info.name!r}; token-aware split -> {len(docs)} chunks. "
                        f"HybridChunker emitted an over-limit chunk — investigate if recurrent."
                    )

        # Coarse net for the paths WITHOUT an exact token counter above
        # (fastembed / char-based ``approximate``) and any pathological
        # mega-chunk Docling returns for a file with no natural breaks.
        _EMERGENCY_CHAR_THRESHOLD = 100_000  # ~25k tokens — past any embedder context
        if docs and any(len(d.page_content) > _EMERGENCY_CHAR_THRESHOLD for d in docs):
            # Use the token-aware splitter — guarantees the safety-split
            # output fits the embedder regardless of script density.
            splitter = self._build_text_splitter(chunk_size, chunk_overlap)
            n_before = len(docs)
            docs = splitter.split_documents(docs)
            logger.warning(
                f"Emergency re-split for {file_path.name}: HybridChunker "
                f"returned a chunk > {_EMERGENCY_CHAR_THRESHOLD} chars; "
                f"token-aware split {n_before} -> {len(docs)} chunks. "
                f"Investigate the source file's structure if this is recurrent."
            )

        return docs

    # --- URL validation ---

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only http/https URLs allowed")
        if parsed.hostname in BLOCKED_HOSTNAMES:
            raise ValueError("Blocked hostname")
        if "@" in (parsed.netloc or ""):
            raise ValueError("URL credentials not allowed")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in ALLOWED_PORTS:
            raise ValueError(f"Port {port} not allowed")
        for family, _, _, _, sockaddr in socket.getaddrinfo(parsed.hostname, None):
            addr = ipaddress.ip_address(sockaddr[0])
            if any(
                [
                    addr.is_private,
                    addr.is_loopback,
                    addr.is_link_local,
                    addr.is_reserved,
                    addr.is_multicast,
                    addr.is_unspecified,
                ]
            ):
                raise ValueError("Private/internal/reserved URLs not allowed")


# --- Helpers ---


def _sanitize_collection(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _sanitize_filename(name: str) -> str:
    if ".." in name:
        raise ValueError("Invalid filename: path traversal detected")
    # Strip path separators and control chars, but preserve Unicode (Hebrew, CJK, etc.)
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # Remove only control characters and problematic filesystem chars
    return re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)


# --- Exceptions ---


class IngestionQueueFullError(Exception):
    def __init__(self, max_pending: int):
        self.max_pending = max_pending
        super().__init__(f"Ingestion queue full (max {max_pending} pending tasks)")


class DocumentExistsError(Exception):
    def __init__(self, filename: str):
        self.filename = filename
        super().__init__(f"File already indexed: {filename}")


class DocumentNotFoundError(Exception):
    def __init__(self, filename: str):
        self.filename = filename
        super().__init__(f"Document not found: {filename}")


class FileTooLargeError(Exception):
    def __init__(self, size: int, max_size: int):
        self.size = size
        self.max_size = max_size
        super().__init__(f"File too large: {size} bytes (max {max_size})")
