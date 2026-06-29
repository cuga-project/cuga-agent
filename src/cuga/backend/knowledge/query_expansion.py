"""Query expansion driven by the client-adaptation glossary.

Context: a dense embedder (e.g. fastembed all-MiniLM-L6-v2) cannot bridge
cross-language synonyms or project-specific codes that aren't in its training
distribution. Observed failure modes are field-name drift and exact-token
loss — a prompt rule can't fix a token that never makes it into the top-k
retrieved chunks.

This module gives the operator a retrieval-side lever: a glossary of
``{term, aliases, definition}`` entries that get injected into the user's
query before embedding. When the query mentions any term or alias, we append
the canonical term + its other aliases as additional semantic anchors, so the
embedder pulls neighboring vectors from multiple synonymous regions.

Trade-offs (be honest in the doc):
- This is heuristic expansion, not true hybrid (no BM25 sparse component).
  cuga's storage is dense-only via fastembed/Milvus; a true hybrid lives in
  a future PR.
- Appending too many aliases can shift the embedding *away* from useful
  regions. We cap aliases per match and de-dup aggressively.
- Word-boundary matching only — substring matches would over-trigger.
"""

from __future__ import annotations

import functools
import logging
import re
from typing import Any

from cuga.backend.knowledge import trace as _trace

logger = logging.getLogger("cuga.knowledge")

# How many extra anchor tokens to append per matched glossary entry. Cap
# keeps the expanded query close to the original embedding region while
# still adding 2-3 synonymous anchors. Empirically: more than ~5 anchors
# starts to drag the embedding away from intent.
MAX_EXPANSIONS_PER_ENTRY = 5
# Hard cap on total appended tokens regardless of how many entries match,
# to bound the expansion bloat for a multi-term query.
MAX_TOTAL_EXPANSIONS = 12


@functools.lru_cache(maxsize=2048)
def _word_boundary_pattern(needle: str) -> re.Pattern[str]:
    """Build a case-insensitive word-boundary regex for a glossary key.

    ``\b`` doesn't work cleanly with non-ASCII identifiers (Hebrew, CJK), so
    we use lookarounds anchored on whitespace / punctuation / string edges.

    Audit-finding S1 fix: this used to recompile O(entries × aliases) regexes
    per search call. A 50-entry × 10-alias glossary meant ~550 compiles per
    query; measured at ~14 ms / query in the no-match path. lru_cache(2048)
    covers a generous glossary size while keeping the cache bounded.
    """
    # Escape regex metacharacters in the needle, then anchor with non-word
    # neighbors. ``[^\w]`` is a reasonable cross-script "word boundary" proxy.
    escaped = re.escape(needle)
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE)


def expand_query_with_glossary(
    query: str,
    glossary: list[dict[str, Any]] | None,
    *,
    emit_audit_trace: bool = False,
    audit_q_idx: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(expanded_query, match_log)``.

    - ``expanded_query``: original query + " " + space-joined extra anchors
    - ``match_log``: list of {"matched": "...", "anchors_added": [...]}
      records, useful for observability — included in the search response
      envelope so operators can confirm their glossary fired.

    Empty glossary / no matches → returns (query, []) verbatim. Never errors
    on malformed entries — silently skips them (entries are validated at
    config-set time, so by the time we hit this path they're already clean).

    When ``emit_audit_trace=True``, the match_log is also written to the
    ``trace`` sink as a single JSONL record with kind="expansion_audit".
    The expanded query string is unchanged regardless of this flag —
    observability only.
    """
    if not query or not glossary:
        if emit_audit_trace and _trace.is_enabled():
            _trace.emit(
                {
                    "kind": "expansion_audit",
                    "q_idx": audit_q_idx,
                    "original_query": query,
                    "aliases_fired": [],
                    "dropped_aliases": [],
                    "truncated_at": None,
                }
            )
        return query, []

    appended: list[str] = []
    seen_anchors: set[str] = set()
    match_log: list[dict[str, Any]] = []
    expansions_used = 0
    truncated_at: int | None = None

    for idx, entry in enumerate(glossary):
        if expansions_used >= MAX_TOTAL_EXPANSIONS:
            truncated_at = idx
            break
        if not isinstance(entry, dict):
            continue
        term = (entry.get("term") or "").strip()
        if not term:
            continue
        aliases = [a for a in (entry.get("aliases") or []) if isinstance(a, str) and a.strip()]

        # Build the set of needles that should trigger expansion for this entry.
        # Match against the term + each alias.
        needles = [term] + list(aliases)
        matched_via: str | None = None
        for n in needles:
            if _word_boundary_pattern(n).search(query):
                matched_via = n
                break
        if matched_via is None:
            continue

        # When a match fires, the extra anchors are everything in the entry
        # OTHER than the matched needle (the query already has that one).
        anchors_for_entry: list[str] = []
        for n in needles:
            if n.casefold() == matched_via.casefold():
                continue
            if n.casefold() in seen_anchors:
                continue
            anchors_for_entry.append(n)
            seen_anchors.add(n.casefold())
            if len(anchors_for_entry) >= MAX_EXPANSIONS_PER_ENTRY:
                break
            if expansions_used + len(anchors_for_entry) >= MAX_TOTAL_EXPANSIONS:
                break

        if anchors_for_entry:
            appended.extend(anchors_for_entry)
            expansions_used += len(anchors_for_entry)
            match_log.append(
                {
                    "matched_via": matched_via,
                    "term": term,
                    "anchors_added": anchors_for_entry,
                }
            )

    # Audit-finding S2 fix: if the total-expansion cap silenced entries
    # 4..50 in a 50-entry glossary, operators currently get zero signal.
    # Emit a single structured log line so support can grep this.
    if truncated_at is not None and (len(glossary) - truncated_at) > 0:
        logger.info(
            "cuga.knowledge.glossary_truncated",
            extra={
                "cuga_knowledge_glossary_truncated_at": truncated_at,
                "cuga_knowledge_glossary_skipped_entries": len(glossary) - truncated_at,
                "cuga_knowledge_glossary_max_total_expansions": MAX_TOTAL_EXPANSIONS,
            },
        )

    if emit_audit_trace and _trace.is_enabled():
        # Flatten the per-entry match_log to a single audit record. Captures
        # which aliases were appended ("fired") and how many entries were
        # skipped by the per-call cap ("dropped"). The expanded query itself
        # is the merge of the original + aliases joined by single spaces.
        aliases_fired = [
            {
                "matched_via": rec["matched_via"],
                "term": rec["term"],
                "anchors_added": rec["anchors_added"],
            }
            for rec in match_log
        ]
        dropped_count = (len(glossary) - truncated_at) if truncated_at is not None else 0
        _trace.emit(
            {
                "kind": "expansion_audit",
                "q_idx": audit_q_idx,
                "original_query": query,
                "aliases_fired": aliases_fired,
                "dropped_aliases_count": dropped_count,
                "truncated_at": truncated_at,
                "total_expansions": expansions_used,
            }
        )

    if not appended:
        return query, []

    expanded = f"{query} {' '.join(appended)}".strip()
    return expanded, match_log
