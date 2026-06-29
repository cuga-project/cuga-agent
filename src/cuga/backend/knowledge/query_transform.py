"""Standalone query transformation for the knowledge engine — multi-query + HyDE.

Zero cuga imports. The HOST injects a ChatGenerator (it adapts its own LLM); the
knowledge package never reaches back into the host. When no generator is injected,
or the LLM call fails/times out, this fails OPEN to the plain query — query
transformation is an additive recall aid, never a hard dependency.

HyDE here ALSO searches the real query (dense + lexical). The hypothetical
document is added as an EXTRA dense leg, so a hallucinated passage can only
contribute additional candidates to RRF — it can never replace the user's actual
query. (Hallucinated tokens are kept OUT of the lexical/BM25 leg, where they
would poison exact-term matching.)

Why not LangChain's MultiQueryRetriever / HypotheticalDocumentEmbedder: they
union-merge results and discard rank — which would destroy the engine's RRF
fusion and the reranker's overfetch window — and wrap a deprecated LLMChain. A
thin module that just emits query strings and lets the engine's existing
``_rrf_fuse``/``_rrf_fuse_lists`` fuse them is both correct and cheaper.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)

VALID_QUERY_TRANSFORMS = ("off", "multi_query", "hyde")
_DEFAULT_TIMEOUT_S = 2.0
_CACHE_MAX = 512


class ChatGenerator(Protocol):
    """Minimal LLM interface the host adapts its own chat model to (one call → one completion)."""

    async def generate(self, prompt: str) -> str: ...


@dataclass
class QueryVariants:
    """EXTRA queries beyond the original (the engine always retrieves the original
    on its normal dense+lexical path). ``dense_extra`` feed the dense leg;
    ``lexical_extra`` feed BM25. HyDE puts its hypothetical doc in ``dense_extra``
    only."""

    dense_extra: list[str] = field(default_factory=list)
    lexical_extra: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.dense_extra or self.lexical_extra)


# Bounded LRU cache keyed (mode, query, n) so search_multi's per-scope fan-out and
# agent re-queries don't re-pay the LLM call.
_CACHE: "collections.OrderedDict[tuple, QueryVariants]" = collections.OrderedDict()

_MULTI_PROMPT = (
    "You are helping search a document knowledge base. Generate {k} alternative "
    "search queries that capture the SAME intent as the user query but use "
    "different wording, synonyms, or angle. One query per line. No numbering, no "
    "preamble, no quotes.\n\nUser query: {q}"
)
_HYDE_PROMPT = (
    "Write a short, factual passage (2-4 sentences) that would directly answer the "
    "question, as if quoted from a relevant document. No preamble.\n\nQuestion: {q}"
)


async def expand_query(
    mode: str,
    query: str,
    generator: ChatGenerator | None,
    *,
    n: int = 3,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> QueryVariants:
    """Return EXTRA query legs for ``mode``. Fails open to no-extras on anything
    unexpected (off, no generator, empty query, timeout, LLM/parse error)."""
    if generator is None or mode not in VALID_QUERY_TRANSFORMS or mode == "off":
        return QueryVariants()
    q = (query or "").strip()
    if not q:
        return QueryVariants()

    key = (mode, q, n)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached

    try:
        variants = await asyncio.wait_for(_generate(mode, q, generator, n), timeout=timeout_s)
    except Exception as e:
        # Fail open — search proceeds on the plain query, never blocked/broken.
        logger.warning(f"cuga.knowledge.query_transform_degraded mode={mode} err={e!r}")
        return QueryVariants()

    _CACHE[key] = variants
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return variants


async def _generate(mode: str, query: str, generator: ChatGenerator, n: int) -> QueryVariants:
    if mode == "multi_query":
        k = max(1, n - 1)  # n total legs = original + k rewrites
        text = await generator.generate(_MULTI_PROMPT.format(k=k, q=query))
        rewrites = _parse_lines(text, query)[:k]
        return QueryVariants(dense_extra=rewrites, lexical_extra=list(rewrites))
    if mode == "hyde":
        doc = (await generator.generate(_HYDE_PROMPT.format(q=query))).strip()
        # Real query keeps its normal dense+lexical path; the hypothetical doc is
        # an ADDITIONAL dense leg only (kept out of lexical/BM25).
        return QueryVariants(dense_extra=[doc] if doc else [])
    return QueryVariants()


_ENUM_PREFIX = re.compile(r"^\s*(?:[-*•]\s*|\d+[.)]\s*)+")


def _parse_lines(text: str, original: str) -> list[str]:
    """Newline-split rewrites; strip bullets/numbering and quotes; drop blanks and
    any that duplicate the original or each other (case-insensitive)."""
    out: list[str] = []
    seen = {original.strip().lower()}
    for line in (text or "").splitlines():
        s = _ENUM_PREFIX.sub("", line).strip().strip('"').strip()
        low = s.lower()
        if not s or low in seen:
            continue
        seen.add(low)
        out.append(s)
    return out


if __name__ == "__main__":  # smallest runnable check — multi_query parse, hyde real-query, fail-open
    import asyncio as _aio

    class _Gen:
        def __init__(self, out):
            self._out = out

        async def generate(self, prompt):
            return self._out

    # multi_query: 2 rewrites parsed, numbering stripped, original deduped
    mq = _aio.run(
        expand_query(
            "multi_query",
            "reset my password",
            _Gen("1. how to change password\n2. password recovery steps\nreset my password"),
            n=3,
        )
    )
    assert mq.dense_extra == ["how to change password", "password recovery steps"], mq.dense_extra
    assert mq.lexical_extra == mq.dense_extra  # rewrites are real queries → safe for BM25

    # hyde: hypothetical doc is dense-only; the real query is NOT replaced (engine still runs it)
    hy = _aio.run(expand_query("hyde", "what is the SLA?", _Gen("The SLA guarantees 99.9% uptime."), n=3))
    assert hy.dense_extra == ["The SLA guarantees 99.9% uptime."]
    assert hy.lexical_extra == []  # hallucinated tokens kept out of BM25

    # fail-open: no generator, and off, and LLM error
    assert not _aio.run(expand_query("multi_query", "x", None)).active
    assert not _aio.run(expand_query("off", "x", _Gen("a\nb"))).active

    class _Boom:
        async def generate(self, prompt):
            raise RuntimeError("llm down")

    assert not _aio.run(expand_query("hyde", "x", _Boom())).active  # degrades to no-extras
    print("query_transform self-check passed")
