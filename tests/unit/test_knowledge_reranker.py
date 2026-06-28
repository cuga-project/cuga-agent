"""Cross-encoder reranker: ordering/trim, readiness gate, and the no-hang
background-load UX (a search never blocks on a model download).

Uses a fake encoder injected via the module so tests never download a model.
"""

from __future__ import annotations

import time

import pytest

import cuga.backend.knowledge.reranker as rr
from cuga.backend.knowledge.reranker import (
    RerankedCandidate,
    RerankerUnavailableError,
    ensure_loading,
    is_ready,
    rerank,
)


class _FakeEnc:
    def rerank(self, query, docs):
        # Higher score for later docs, so reranking must reverse the input order.
        return [float(i) for i in range(len(docs))]


def _cands():
    return [
        RerankedCandidate(
            text=f"d{i}", score=0.0, metadata={"filename": "f", "page": i}, original_score=0.9 - i * 0.1
        )
        for i in range(4)
    ]


def _reset(model):
    rr._ENCODERS.pop(model, None)
    rr._LOADING.discard(model)
    rr._RETRY_AFTER.pop(model, None)


def test_rerank_sorts_trims_and_preserves_fusion_score():
    rr._ENCODERS["_fake"] = _FakeEnc()
    out = rerank("q", _cands(), limit=2, model_name="_fake")
    assert [c.text for c in out] == ["d3", "d2"]  # reordered by cross-encoder score
    assert len(out) == 2  # trimmed to return_k
    assert out[0].score == 3.0  # cross-encoder score set (for ordering)
    assert out[0].original_score == pytest.approx(0.6)  # fusion score preserved
    assert out[0].metadata["filename"] == "f"  # metadata carried through


def test_rerank_empty_candidates():
    rr._ENCODERS["_fake"] = _FakeEnc()
    assert rerank("q", [], 5, model_name="_fake") == []


def test_rerank_refuses_when_not_loaded():
    # The whole point: rerank() never downloads inline. Not loaded -> raise so
    # the engine serves fusion ranking instead of blocking the user's query.
    _reset("_notloaded")
    assert not is_ready("_notloaded")
    with pytest.raises(RerankerUnavailableError):
        rerank("q", _cands(), 2, model_name="_notloaded")


def test_ensure_loading_populates_in_background(monkeypatch):
    _reset("_bg")
    monkeypatch.setattr(rr, "_build_encoder", lambda m: _FakeEnc())
    assert not is_ready("_bg")
    ensure_loading("_bg")  # non-blocking
    for _ in range(100):  # wait for the daemon loader (<=2s)
        if is_ready("_bg"):
            break
        time.sleep(0.02)
    assert is_ready("_bg")


def test_ensure_loading_backs_off_after_failure(monkeypatch):
    _reset("_boom")

    def _boom(_m):
        raise RerankerUnavailableError("offline / not cached")

    monkeypatch.setattr(rr, "_build_encoder", _boom)
    ensure_loading("_boom")
    for _ in range(100):
        if "_boom" in rr._RETRY_AFTER:
            break
        time.sleep(0.02)
    assert "_boom" in rr._RETRY_AFTER  # backoff recorded, won't hammer the network
    assert not is_ready("_boom")
    # Within the cooldown, a second ensure_loading must not spawn another loader.
    monkeypatch.setattr(rr, "_build_encoder", lambda m: _FakeEnc())
    ensure_loading("_boom")
    time.sleep(0.1)
    assert not is_ready("_boom")  # still backed off
