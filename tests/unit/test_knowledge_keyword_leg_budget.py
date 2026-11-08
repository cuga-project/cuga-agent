"""A slow keyword leg must not stall search: after an upgrade, the first keyword call rebuilds
the keyword index (about a minute at 50,000 chunks), longer than the agent's 60 s tool timeout."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cuga.backend.knowledge import engine as engine_mod
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine, _sanitize_collection

pytestmark = pytest.mark.unit


def _doc(text: str):
    return SimpleNamespace(page_content=text, metadata={"filename": "f.txt", "page": 1})


def _engine(monkeypatch, adapter) -> KnowledgeEngine:
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-kwbudget-")),
        embedding_provider="fastembed",
        search_hybrid_mode="auto",
        search_junk_filter="off",
        rerank_enabled=False,
    )
    eng = KnowledgeEngine(cfg)

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(eng, "_ensure_metadata_ready", _noop)
    monkeypatch.setattr(eng, "_ensure_vector_store_cached", _noop)
    eng._vector_stores[_sanitize_collection("kb")] = adapter
    return eng


class _Adapter:
    def __init__(self, keyword_seconds: float):
        self.keyword_seconds = keyword_seconds
        self.keyword_done = threading.Event()

    def search(self, q, k=10):
        return [(_doc("dense hit"), 0.9)]

    def search_lexical(self, q, k=10):
        time.sleep(self.keyword_seconds)
        self.keyword_done.set()
        return [(_doc("keyword hit"), 0.8)]


def test_slow_keyword_leg_is_left_out_and_finishes_in_background(monkeypatch):
    monkeypatch.setattr(engine_mod, "_KEYWORD_LEG_BUDGET_S", 0.2)
    adapter = _Adapter(keyword_seconds=1.5)
    eng = _engine(monkeypatch, adapter)

    async def timed_search():
        # Timed inside the loop: asyncio.run itself waits for leftover threads when it closes.
        t = time.monotonic()
        results, _ = await eng.search_with_stats("kb", "invoice INV-7710", limit=5, scope="agent")
        return results, time.monotonic() - t

    results, elapsed = asyncio.run(timed_search())

    assert elapsed < 1.0, f"search waited {elapsed:.1f}s for the keyword leg"
    assert [r.text for r in results] == ["dense hit"]
    # The keyword call was not abandoned: the one-time index rebuild it carries completes.
    assert adapter.keyword_done.wait(timeout=5)


def test_keyword_leg_within_budget_is_fused(monkeypatch):
    adapter = _Adapter(keyword_seconds=0.0)
    eng = _engine(monkeypatch, adapter)

    results, _ = asyncio.run(eng.search_with_stats("kb", "invoice INV-7710", limit=5, scope="agent"))

    assert {r.text for r in results} == {"dense hit", "keyword hit"}
