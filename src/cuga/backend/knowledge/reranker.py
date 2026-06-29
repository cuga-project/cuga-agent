"""Cross-encoder reranker over fastembed's TextCrossEncoder (ONNX/CPU, no torch).

This is the module the search path imports for the cross-encoder rerank step.
It rescores the overfetched candidate window by joint query-document relevance
and returns the top ``limit``.

UX contract — a user query MUST NEVER block on a model download. The first time
a reranker model is needed (e.g. right after switching to the balanced /
max_quality profile, which pulls ~1.1GB for bge-reranker-base), search serves
fusion-ranked results immediately while the model loads in a background thread;
subsequent queries rerank automatically once it's ready. The engine drives this:
it calls ``is_ready`` and only reranks when loaded, otherwise ``ensure_loading``
kicks the background fetch. ``prewarm`` (blocking) is used at startup and by the
background loader.

fastembed is already a core cuga dependency and bge-reranker-base is in its
supported model list (Apache-2.0) — no new dependency, runs on CPU. The default
``bge-reranker-v2-m3`` is NOT fastembed-servable — use ``bge-reranker-base``
(light English: ``Xenova/ms-marco-MiniLM-L-12-v2``).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"
# After a failed background load (offline / model not cached), wait this long
# before another search is allowed to re-trigger it — avoids hammering the
# network and spamming logs on an airgapped deploy where rerank can't load.
_RETRY_COOLDOWN_S = 30.0


class RerankerUnavailableError(RuntimeError):
    """Reranker model/runtime could not be loaded — caller degrades to fusion ranking."""


@dataclass
class RerankedCandidate:
    """A retrieval candidate carried through reranking.

    ``score`` becomes the cross-encoder score (used only for ordering);
    ``original_score`` (the fusion score) is preserved for display so callers
    don't see suddenly-different score units.
    """

    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    original_score: float = 0.0


# Loaded encoders, keyed by model name. A load is a one-time weights load; reuse
# across requests. ``_LOADING`` tracks in-flight background loads so we spawn at
# most one loader thread per model. ``_RETRY_AFTER`` backs off failed loads.
# ``_LOCK`` guards only these fast dict/set ops — NEVER the download itself, so a
# slow fetch can't block searches for other models (or is_ready checks).
_ENCODERS: dict[str, Any] = {}
_LOADING: set[str] = set()
_RETRY_AFTER: dict[str, float] = {}
_LOCK = threading.Lock()


def is_ready(model_name: str = DEFAULT_RERANK_MODEL) -> bool:
    """True if the model is loaded and rerank() will run without a download."""
    return model_name in _ENCODERS


def _build_encoder(model_name: str) -> Any:
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    except ImportError as e:  # core dep, but be explicit rather than crash search
        raise RerankerUnavailableError(f"fastembed TextCrossEncoder unavailable: {e}") from e
    # Mirror the embedder's offline-cache handling (engine._FastEmbedEmbeddings)
    # so a container with a pre-baked model dir never reaches the network.
    cache_dir = os.environ.get("FASTEMBED_CACHE_PATH")
    local_files_only = os.environ.get("HF_HUB_OFFLINE", "0") == "1"
    try:
        return TextCrossEncoder(model_name=model_name, cache_dir=cache_dir, local_files_only=local_files_only)
    except Exception as e:
        raise RerankerUnavailableError(f"could not load reranker model {model_name!r}: {e}") from e


def prewarm(model_name: str = DEFAULT_RERANK_MODEL) -> None:
    """Blocking load (download + init). Idempotent. Used at startup and by the
    background loader. Raises RerankerUnavailableError on failure."""
    if model_name in _ENCODERS:
        return
    # Mark in-flight so a racing ensure_loading() doesn't spawn a second loader.
    with _LOCK:
        if model_name in _ENCODERS:
            return
        already = model_name in _LOADING
        if not already:
            _LOADING.add(model_name)
    if already:
        return  # another thread owns the load; caller serves fusion until ready
    try:
        enc = _build_encoder(model_name)  # the slow part — outside _LOCK
        with _LOCK:
            _ENCODERS[model_name] = enc
            _RETRY_AFTER.pop(model_name, None)
    except Exception:
        with _LOCK:
            _RETRY_AFTER[model_name] = time.monotonic() + _RETRY_COOLDOWN_S
        raise
    finally:
        with _LOCK:
            _LOADING.discard(model_name)


def ensure_loading(model_name: str = DEFAULT_RERANK_MODEL) -> None:
    """Non-blocking: start a background load if the model isn't ready/loading.

    Safe to call from the request path (event loop) — returns immediately.
    Backs off for ``_RETRY_COOLDOWN_S`` after a failed load.
    """
    with _LOCK:
        if model_name in _ENCODERS or model_name in _LOADING:
            return
        retry_at = _RETRY_AFTER.get(model_name)
        if retry_at is not None and time.monotonic() < retry_at:
            return

    def _worker() -> None:
        try:
            prewarm(model_name)
            logger.info(f"cuga.knowledge.reranker_loaded model={model_name}")
        except Exception as e:
            logger.warning(
                "Reranker background load failed for %s (search keeps using fusion "
                "ranking; will retry after %.0fs): %s",
                model_name,
                _RETRY_COOLDOWN_S,
                e,
            )

    threading.Thread(target=_worker, name="rerank-load", daemon=True).start()


def rerank(
    query: str,
    candidates: list[RerankedCandidate],
    limit: int,
    model_name: str = DEFAULT_RERANK_MODEL,
) -> list[RerankedCandidate]:
    """Rescore ``candidates`` by cross-encoder relevance to ``query``; return top ``limit``.

    Requires the model to already be loaded (engine gates on ``is_ready``); raises
    RerankerUnavailableError otherwise so a caller that skipped the gate still
    degrades to fusion ranking instead of blocking on a download.
    """
    if not candidates:
        return []
    encoder = _ENCODERS.get(model_name)
    if encoder is None:
        raise RerankerUnavailableError(f"reranker model {model_name!r} is not loaded yet")
    # fastembed yields one score per document, in input order.
    scores = list(encoder.rerank(query, [c.text for c in candidates]))
    if len(scores) != len(candidates):
        raise RerankerUnavailableError(
            f"reranker returned {len(scores)} scores for {len(candidates)} candidates"
        )
    for c, s in zip(candidates, scores):
        c.score = float(s)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[: max(0, limit)]


if __name__ == "__main__":  # smallest runnable check — sort + trim + readiness gate

    class _FakeEnc:
        def rerank(self, _q, docs):
            return [float(i) for i in range(len(docs))]  # last doc most relevant

    cands = [RerankedCandidate(text=f"d{i}", score=0.0, original_score=0.9 - i * 0.1) for i in range(4)]
    # Not loaded yet → rerank refuses (caller would use fusion), is_ready False.
    assert not is_ready("_fake")
    try:
        rerank("q", list(cands), 2, model_name="_fake")
        raise SystemExit("expected RerankerUnavailableError")
    except RerankerUnavailableError:
        pass
    _ENCODERS["_fake"] = _FakeEnc()
    assert is_ready("_fake")
    out = rerank("q", list(cands), limit=2, model_name="_fake")
    assert [c.text for c in out] == ["d3", "d2"], out
    assert out[0].original_score == 0.6 and out[0].score == 3.0  # fusion preserved, CE set
    assert rerank("q", [], 5, model_name="_fake") == []
    print("reranker self-check passed")
