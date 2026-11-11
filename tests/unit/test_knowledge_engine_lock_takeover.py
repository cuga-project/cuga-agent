"""A new engine takes a knowledge directory over from this process's engine for it once the agent
that owned that engine is gone (#1138), and never from a live agent or another process."""

import asyncio
import gc
import tempfile
from pathlib import Path

import pytest

from cuga.backend.knowledge import engine as engine_mod
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine
from cuga.backend.knowledge.interprocess_lock import acquire_exclusive_nonblocking

pytestmark = pytest.mark.unit


class _Agent:
    """Stands in for a CugaAgent: it sits in a reference cycle, so only a collection frees it."""

    def __init__(self):
        self.me = self


def _cfg(persist_dir: Path) -> KnowledgeConfig:
    return KnowledgeConfig(enabled=True, persist_dir=persist_dir, embedding_provider="fastembed")


@pytest.fixture
def kb_dir():
    return Path(tempfile.mkdtemp(prefix="cuga-lock-"))


@pytest.fixture
def no_auto_gc():
    gc.disable()
    yield
    gc.enable()


def test_a_dropped_agent_s_engine_is_taken_over_while_something_still_holds_it(kb_dir, no_auto_gc):
    """The Postgres case: asyncpg timers keep the finished run's context, and through it the old
    engine, alive for about five minutes after the agent itself is collected."""
    agent = _Agent()
    old = KnowledgeEngine(_cfg(kb_dir), owner=agent)
    held_by_timers = [old]
    del agent

    new_owner = _Agent()
    new = KnowledgeEngine(_cfg(kb_dir), owner=new_owner)

    assert held_by_timers[0]._lock_file.closed and old._shutdown_event.is_set()
    assert engine_mod._LOCK_HOLDERS[str(kb_dir.resolve())] is new
    new.shutdown()


def test_an_unreachable_engine_is_collected_and_its_directory_reopened(kb_dir, no_auto_gc):
    """The SQLite case: nothing holds the old engine, but it waits for a collection in a cycle."""
    agent = _Agent()
    agent.engine = KnowledgeEngine(_cfg(kb_dir), owner=agent)
    del agent

    KnowledgeEngine(_cfg(kb_dir)).shutdown()


def test_a_live_agent_keeps_its_directory(kb_dir):
    agent = _Agent()
    old = KnowledgeEngine(_cfg(kb_dir), owner=agent)
    with pytest.raises(RuntimeError, match="already open in another knowledge engine"):
        KnowledgeEngine(_cfg(kb_dir))
    assert not old._lock_file.closed
    old.shutdown()


def test_an_engine_without_an_owner_is_never_taken_over(kb_dir):
    old = KnowledgeEngine(_cfg(kb_dir))  # the server's engine
    with pytest.raises(RuntimeError, match="already open in another knowledge engine"):
        KnowledgeEngine(_cfg(kb_dir))
    old.shutdown()


def test_an_engine_with_an_ingest_in_flight_is_not_taken_over(kb_dir, no_auto_gc):
    agent = _Agent()
    old = KnowledgeEngine(_cfg(kb_dir), owner=agent)
    old._active_tasks["t1"] = asyncio.Event()
    del agent
    with pytest.raises(RuntimeError, match="already open in another knowledge engine"):
        KnowledgeEngine(_cfg(kb_dir))
    assert not old._lock_file.closed
    old._active_tasks.clear()
    old.shutdown()


def test_another_process_s_lock_is_never_taken_over(kb_dir, monkeypatch):
    kb_dir.mkdir(parents=True, exist_ok=True)
    held = open(kb_dir / ".lock", "w+b")  # a lock no engine of this process registered
    acquire_exclusive_nonblocking(held)
    monkeypatch.setattr(engine_mod.gc, "collect", lambda: pytest.fail("collected for another process's lock"))
    try:
        with pytest.raises(RuntimeError, match="already open in another knowledge engine"):
            KnowledgeEngine(_cfg(kb_dir))
    finally:
        held.close()


def test_an_engine_with_a_reindex_pending_is_not_taken_over(kb_dir, no_auto_gc):
    """reindex() queues its worker before the worker registers an ingest task."""
    agent = _Agent()
    old = KnowledgeEngine(_cfg(kb_dir), owner=agent)
    old._reindex_in_progress.add("kb_agent")
    del agent
    with pytest.raises(RuntimeError, match="already open in another knowledge engine"):
        KnowledgeEngine(_cfg(kb_dir))
    old._reindex_in_progress.clear()
    old.shutdown()


def test_a_client_kept_after_its_agent_was_dropped_is_refused_after_the_takeover(kb_dir, no_auto_gc):
    """The kept client must not read or write the directory the new engine now owns."""
    agent = _Agent()
    kept = KnowledgeEngine(_cfg(kb_dir), owner=agent)  # what `kb = agent.knowledge` keeps
    del agent
    new = KnowledgeEngine(_cfg(kb_dir), owner=_Agent())

    with pytest.raises(RuntimeError, match="was shut down: another agent opened"):
        asyncio.run(kept.list_documents("kb_agent"))
    with pytest.raises(RuntimeError, match="was shut down: another agent opened"):
        asyncio.run(kept.delete_document("kb_agent", "f.txt"))
    new.shutdown()
