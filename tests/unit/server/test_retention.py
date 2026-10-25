"""Scheduled checkpoint retention: idle conversations lose their checkpoint and become legacy (#774)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.server import retention
from cuga.backend.server.conversation_history import ConversationHistoryDB, _instance_id, _tenant_id
from cuga.backend.server.thread_lease import ThreadLease

pytestmark = pytest.mark.unit

USER = "alice"
AGENT = "agent-1"


def _key(thread_id: str, user_id: str, agent_id: str) -> str:
    return f"{thread_id}:{user_id}:{agent_id}"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture
async def db():
    return ConversationHistoryDB()


@pytest.fixture
def lease():
    return ThreadLease()


@pytest.fixture
def saver():
    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)
    return saver


async def _seed(db: ConversationHistoryDB, thread_id: str, *, status: str, created_days_ago: float):
    await db.create_conversation(thread_id, USER, AGENT)
    store = db._get_store()
    await store.execute(
        """UPDATE runtime_thread_owners SET status = ?, created_at = ?
        WHERE tenant_id = ? AND instance_id = ? AND thread_id = ?""",
        (status, _iso(created_days_ago), _tenant_id(), _instance_id(), thread_id),
    )
    await store.commit()


async def _sweep(db, lease, saver, cutoff_days: float = 90) -> int:
    return await retention._prune_batch(
        saver=saver,
        conversation_db=db,
        lease=lease,
        checkpoint_thread_id_fn=_key,
        cutoff_iso=_iso(cutoff_days),
    )


async def test_idle_conversation_loses_its_checkpoint_and_becomes_legacy(db, lease, saver):
    await _seed(db, "old", status="active", created_days_ago=120)

    assert await _sweep(db, lease, saver) == 1

    saver.adelete_thread.assert_awaited_once_with(_key("old", USER, AGENT))
    assert await db.get_thread_status("old", USER, AGENT) == "legacy"


async def test_recent_and_non_continuable_conversations_are_left_alone(db, lease, saver):
    await _seed(db, "fresh", status="active", created_days_ago=1)
    await _seed(db, "legacy", status="legacy", created_days_ago=400)
    await _seed(db, "deleted", status="deleted", created_days_ago=400)

    assert await _sweep(db, lease, saver) == 0

    saver.adelete_thread.assert_not_awaited()
    assert await db.get_thread_status("fresh", USER, AGENT) == "active"


async def test_last_turn_at_overrides_created_at(db, lease, saver):
    await _seed(db, "revived", status="active", created_days_ago=400)
    await db.mark_last_turn_at("revived", USER)

    assert await _sweep(db, lease, saver) == 0

    saver.adelete_thread.assert_not_awaited()
    assert await db.get_thread_status("revived", USER, AGENT) == "active"


async def test_conversation_with_a_running_turn_is_skipped(db, lease, saver):
    await _seed(db, "busy", status="active", created_days_ago=120)
    token = await ThreadLease().acquire(_key("busy", USER, AGENT))

    assert await _sweep(db, lease, saver) == 0

    saver.adelete_thread.assert_not_awaited()
    assert await db.get_thread_status("busy", USER, AGENT) == "active"
    await ThreadLease().release(token)


async def test_failed_delete_keeps_the_conversation_continuable_and_releases_the_lease(db, lease, saver):
    await _seed(db, "flaky", status="active", created_days_ago=120)
    saver.adelete_thread.side_effect = RuntimeError("db down")

    assert await _sweep(db, lease, saver) == 0

    assert await db.get_thread_status("flaky", USER, AGENT) == "active"
    await lease.release(await lease.acquire(_key("flaky", USER, AGENT)))  # lease is free again


async def test_loop_sweeps_each_interval_and_survives_a_failing_sweep(monkeypatch, saver):
    sweeps = []

    async def prune_batch(**kwargs):
        sweeps.append(kwargs["cutoff_iso"])
        if len(sweeps) == 1:
            raise RuntimeError("transient")
        return 0

    monkeypatch.setattr(retention, "_prune_batch", prune_batch)
    task = asyncio.create_task(
        retention.run_retention_loop(
            handle=MagicMock(saver=saver),
            checkpoint_thread_id_fn=_key,
            interval_seconds=0.01,
            retention_days=90,
        )
    )
    try:
        await asyncio.wait_for(_until(lambda: len(sweeps) >= 2), timeout=5.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(sweeps) >= 2


async def _until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)
