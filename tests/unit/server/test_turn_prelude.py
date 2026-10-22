"""Unit tests for the /stream turn prelude (W09.1, W09.3, W09.4)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.server.conversation_threads import ThreadRecord, ThreadStatus
from cuga.backend.server.thread_lease import LeaseToken, ThreadBusy
from cuga.backend.server.turn_prelude import PreludeResult, ThreadUnavailable, run_turn_prelude

THREAD_ID = "thread-1"


def _record(status: str) -> ThreadRecord:
    return ThreadRecord(
        thread_id=THREAD_ID,
        tenant_id="t",
        instance_id="i",
        agent_id="a",
        user_id="u",
        status=status,
        config_agent_hash="",
        config_runtime_ver="",
        pending_interrupt=False,
        latest_checkpoint_id="",
        projected_checkpoint_id="",
        created_at="now",
        updated_at="now",
    )


def _snap(values=None, next_=()):
    return SimpleNamespace(values=values or {}, next=next_)


def _make(aget_state):
    """Build (graph, lease, token, order) where order records call sequence."""
    order: list[str] = []
    token = LeaseToken(thread_id=THREAD_ID, holder_id="h", expires_at=0.0)

    async def _acquire(thread_id, ttl):
        order.append("acquire")
        return token

    async def _release(tok):
        order.append("release")

    async def _aget_state(config):
        order.append("aget_state")
        if isinstance(aget_state, Exception):
            raise aget_state
        return aget_state

    lease = MagicMock()
    lease.acquire = AsyncMock(side_effect=_acquire)
    lease.release = AsyncMock(side_effect=_release)
    graph = MagicMock()
    graph.aget_state = AsyncMock(side_effect=_aget_state)
    return graph, lease, token, order


async def _run(status, aget_state, kind="sqlite"):
    graph, lease, token, order = _make(aget_state)
    coro = run_turn_prelude(
        record=_record(status),
        thread_id=THREAD_ID,
        graph=graph,
        lease=lease,
        checkpointer_kind=kind,
    )
    return coro, graph, lease, token, order


@pytest.mark.unit
async def test_lease_acquired_before_checkpoint_load():
    coro, graph, lease, token, order = await _run(ThreadStatus.ACTIVE, _snap({"a": 1}))
    result = await coro
    assert order == ["acquire", "aget_state"]
    assert isinstance(result, PreludeResult)
    assert result.lease_token is token
    assert result.notices == []
    assert result.resume_allowed is True
    graph.aget_state.assert_awaited_once_with({"configurable": {"thread_id": THREAD_ID}})
    lease.release.assert_not_awaited()


@pytest.mark.unit
async def test_lease_released_when_checkpoint_load_fails():
    coro, graph, lease, token, order = await _run(ThreadStatus.ACTIVE, RuntimeError("boom"))
    with pytest.raises(ThreadUnavailable) as exc:
        await coro
    assert exc.value.reason == "unreadable"
    assert order == ["acquire", "aget_state", "release"]
    lease.release.assert_awaited_once_with(token)


@pytest.mark.unit
async def test_thread_busy_propagates_without_loading_checkpoint():
    graph, lease, _token, _order = _make(_snap({"a": 1}))
    lease.acquire = AsyncMock(side_effect=ThreadBusy(THREAD_ID))
    with pytest.raises(ThreadBusy):
        await run_turn_prelude(
            record=_record(ThreadStatus.ACTIVE),
            thread_id=THREAD_ID,
            graph=graph,
            lease=lease,
            checkpointer_kind="sqlite",
        )
    graph.aget_state.assert_not_awaited()
    lease.release.assert_not_awaited()


_PRESENT = "present"
_ABSENT = "absent"
_RAISES = "raises"


def _checkpoint(kind):
    if kind == _PRESENT:
        return _snap({"messages": ["hi"]})
    if kind == _ABSENT:
        return _snap()
    return RuntimeError("unreadable")


@pytest.mark.unit
@pytest.mark.parametrize(
    "status, checkpoint, ckpt_kind, expected",
    [
        (ThreadStatus.NEW, _PRESENT, "sqlite", "snapshot"),
        (ThreadStatus.NEW, _ABSENT, "sqlite", None),
        (ThreadStatus.ACTIVE, _PRESENT, "sqlite", "snapshot"),
        (ThreadStatus.ACTIVE, _ABSENT, "sqlite", ThreadUnavailable("missing")),
        (ThreadStatus.ACTIVE, _ABSENT, "memory", None),
        (ThreadStatus.ACTIVE, _RAISES, "sqlite", ThreadUnavailable("unreadable")),
        (ThreadStatus.ACTIVE, _RAISES, "memory", ThreadUnavailable("unreadable")),
        (ThreadStatus.EXPIRED, _ABSENT, "sqlite", ThreadUnavailable("missing")),
    ],
)
async def test_status_checkpoint_matrix(status, checkpoint, ckpt_kind, expected):
    aget_state = _checkpoint(checkpoint)
    coro, _graph, lease, token, _order = await _run(status, aget_state, ckpt_kind)
    if isinstance(expected, ThreadUnavailable):
        with pytest.raises(ThreadUnavailable) as exc:
            await coro
        assert exc.value.reason == expected.reason
        lease.release.assert_awaited_once_with(token)
        return
    result = await coro
    if expected == "snapshot":
        assert result.snapshot is aget_state
    else:
        assert result.snapshot is None
    lease.release.assert_not_awaited()


@pytest.mark.unit
async def test_pending_next_without_values_counts_as_checkpoint():
    snap = _snap({}, next_=("node",))
    coro, *_ = await _run(ThreadStatus.ACTIVE, snap)
    result = await coro
    assert result.snapshot is snap


@pytest.mark.unit
async def test_none_snapshot_from_graph_is_treated_as_missing():
    coro, _graph, lease, token, _order = await _run(ThreadStatus.ACTIVE, None)
    with pytest.raises(ThreadUnavailable) as exc:
        await coro
    assert exc.value.reason == "missing"
    lease.release.assert_awaited_once_with(token)


@pytest.mark.unit
async def test_history_rows_but_no_checkpoint_durable_mode_is_unavailable():
    """W11.4: active thread with history but an empty durable checkpoint -> unavailable."""
    coro, _graph, lease, token, _order = await _run(ThreadStatus.ACTIVE, _snap(), "postgres")
    with pytest.raises(ThreadUnavailable) as exc:
        await coro
    assert exc.value.reason == "missing"
    lease.release.assert_awaited_once_with(token)


@pytest.mark.unit
async def test_memory_mode_active_without_checkpoint_starts_fresh(caplog):
    """W04.3 / W09.4: memory-mode switch starts from an empty state and warns."""
    with caplog.at_level("WARNING", logger="cuga.backend.server.turn_prelude"):
        coro, _graph, lease, token, _order = await _run(ThreadStatus.ACTIVE, _snap(), "memory")
        result = await coro
    assert result.snapshot is None
    assert result.lease_token is token
    assert any("starting fresh" in r.getMessage() for r in caplog.records)
    lease.release.assert_not_awaited()
