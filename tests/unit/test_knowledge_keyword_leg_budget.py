"""A slow keyword leg must not stall search: after an upgrade, the first keyword call rebuilds
the keyword index (about a minute at 50,000 chunks), longer than the agent's 60 s tool timeout."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


class _CountingAdapter(_Adapter):
    def __init__(self, keyword_seconds: float):
        super().__init__(keyword_seconds)
        self.keyword_calls = 0
        self.finished = 0

    def search_lexical(self, q, k=10):
        self.keyword_calls += 1
        try:
            return super().search_lexical(q, k)
        finally:
            self.finished += 1


def _wait_until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        time.sleep(0.02)
    return predicate()


def test_timed_out_keyword_calls_do_not_hold_up_the_vector_leg(monkeypatch):
    # Each slow keyword call keeps its thread after its search gives up on it. On a shared
    # two-thread default executor, two such calls would leave the third search's vector leg
    # waiting for them.
    monkeypatch.setattr(engine_mod, "_KEYWORD_LEG_BUDGET_S", 0.1)
    adapter = _CountingAdapter(keyword_seconds=1.5)
    eng = _engine(monkeypatch, adapter)

    async def three_searches():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=2))
        times = []
        for _ in range(3):
            t = time.monotonic()
            results, _ = await eng.search_with_stats("kb", "invoice INV-7710", limit=5, scope="agent")
            times.append(time.monotonic() - t)
            assert [r.text for r in results] == ["dense hit"]
        return times

    times = asyncio.run(three_searches())

    assert max(times) < 1.0, f"searches took {times}"
    assert _wait_until(lambda: adapter.finished == 3)


def test_keyword_leg_is_skipped_while_every_keyword_thread_is_busy(monkeypatch):
    monkeypatch.setattr(engine_mod, "_KEYWORD_LEG_BUDGET_S", 0.1)
    monkeypatch.setattr(engine_mod, "_KEYWORD_POOL", ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(engine_mod, "_KEYWORD_SLOTS", threading.BoundedSemaphore(1))
    adapter = _CountingAdapter(keyword_seconds=1.0)
    eng = _engine(monkeypatch, adapter)

    async def search():
        results, _ = await eng.search_with_stats("kb", "invoice INV-7710", limit=5, scope="agent")
        return {r.text for r in results}

    assert asyncio.run(search()) == {"dense hit"}  # timed out; the only keyword thread stays busy
    assert asyncio.run(search()) == {"dense hit"}  # no free keyword thread: skipped, not queued
    assert adapter.keyword_calls == 1

    assert _wait_until(lambda: adapter.finished == 1)
    adapter.keyword_seconds = 0.0
    assert asyncio.run(search()) == {"dense hit", "keyword hit"}  # the thread is free again
    assert adapter.keyword_calls == 2


def test_a_keyword_call_cancelled_before_it_starts_frees_its_slot(monkeypatch):
    # Two slots, one thread: the second call waits in the pool's queue, times out there and is
    # cancelled without ever running. Its slot must come back all the same.
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(engine_mod, "_KEYWORD_LEG_BUDGET_S", 0.1)
    monkeypatch.setattr(engine_mod, "_KEYWORD_POOL", ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(engine_mod, "_KEYWORD_SLOTS", slots)
    adapter = _CountingAdapter(keyword_seconds=0.5)
    eng = _engine(monkeypatch, adapter)

    async def two_searches():
        for _ in range(2):
            await eng.search_with_stats("kb", "invoice INV-7710", limit=5, scope="agent")

    asyncio.run(two_searches())

    assert _wait_until(lambda: adapter.finished == 1)
    assert adapter.keyword_calls == 1  # the queued call was cancelled before it ran
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False), "a slot leaked"


def test_keyword_leg_within_budget_is_fused(monkeypatch):
    adapter = _Adapter(keyword_seconds=0.0)
    eng = _engine(monkeypatch, adapter)

    results, _ = asyncio.run(eng.search_with_stats("kb", "invoice INV-7710", limit=5, scope="agent"))

    assert {r.text for r in results} == {"dense hit", "keyword hit"}
