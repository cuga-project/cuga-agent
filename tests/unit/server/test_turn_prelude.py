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
        return _snap({}, next_=("node",))
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


# ---------------------------------------------------------------------------
# W09.7 + W09.8: event_stream finally block lifecycle calls
# ---------------------------------------------------------------------------


def _make_mock_app_state():
    """Build a minimal app_state that lets event_stream initialise."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock


    graph = MagicMock()

    async def _aget_state(cfg):
        snap = SimpleNamespace(
            values={"messages": []},
            next=(),
            config={"configurable": {"checkpoint_id": "ckpt-test"}},
        )
        return snap

    graph.aget_state = AsyncMock(side_effect=_aget_state)

    async def _aupdate_state(cfg, vals):
        pass

    graph.aupdate_state = AsyncMock(side_effect=_aupdate_state)

    agent = SimpleNamespace(
        graph=graph,
        policy_system=None,
        enable_todos=None,
        reflection_enabled=None,
        shortlisting_tool_threshold=None,
        cuga_lite_max_steps=None,
        enable_filesystem_tools=None,
        special_instructions=None,
        supervisor_enabled=False,
        _checkpoint_config_version=None,
    )

    env = MagicMock()
    env.get_url = MagicMock(return_value="http://localhost")
    env.tool_implementation_provider = None
    env.extension_communicator = None

    pu = MagicMock()
    pu.img = None
    pu.string_representation = ""
    pu.focused_element_bid = ""
    pu.page_content = ""

    async def _step(_):
        return None, None, None, None, None

    async def _pu_transform(**_):
        return pu

    env.step = AsyncMock(side_effect=_step)
    env.pu_processor = MagicMock()
    env.pu_processor.transform = AsyncMock(side_effect=_pu_transform)

    return SimpleNamespace(
        agent=agent,
        agent_id="cuga-default",
        output_format=None,
        current_llm=None,
        knowledge_provider=None,
        knowledge_engine=None,
        config_version=None,
        env=env,
        stop_events={},
        get_subsystem_statuses=lambda: {},
    )


def _drain_gen(gen):
    """Drain an async generator synchronously."""
    import asyncio

    async def _collect():
        events = []
        async for ev in gen:
            events.append(ev)
        return events

    return asyncio.run(_collect())


@pytest.mark.unit
def test_successful_turn_calls_mark_turn_committed_and_mark_projected(monkeypatch):
    """W09.7: after a completed turn, mark_turn_committed AND mark_projected are called."""
    from unittest.mock import AsyncMock, MagicMock

    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoopAnswer

    from cuga.backend.server import main

    app_state = _make_mock_app_state()
    monkeypatch.setattr(main, "app_state", app_state)
    monkeypatch.setattr(main, "_assert_run_thread_access", AsyncMock())
    monkeypatch.setattr(main, "_canonical_thread_id", lambda x: x)
    monkeypatch.setattr(main, "_checkpoint_thread_id", lambda tid, uid, aid: tid)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "format_upload_context", MagicMock(return_value=None))
    monkeypatch.setattr(main, "_save_conversation_and_events_async", AsyncMock())
    monkeypatch.setattr(main, "log_error_ref", MagicMock(return_value="ref-x"))
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-t")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "inst-t")

    committed_calls = []
    projected_calls = []

    class FakeThreadsDB:
        async def mark_turn_committed(self, scope, tid, **kwargs):
            committed_calls.append((tid, kwargs))

        async def mark_projected(self, scope, tid, checkpoint_id):
            projected_calls.append((tid, checkpoint_id))

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        lambda: FakeThreadsDB(),
    )

    # Stub AgentLoop to emit a finished answer
    answer = AgentLoopAnswer(answer="Hello", end=True, interrupt=False, has_tools=False, tools=[])

    async def _fake_run_stream(state=None, resume=None):
        yield answer

    fake_loop = MagicMock()
    fake_loop.run_stream = _fake_run_stream
    fake_loop.get_langfuse_trace_id = MagicMock(return_value=None)
    monkeypatch.setattr("cuga.backend.cuga_graph.utils.agent_loop.AgentLoop", MagicMock(return_value=fake_loop))

    _drain_gen(
        main.event_stream(
            query="hi",
            api_mode=True,
            thread_id="thread-success",
            user_id="user-x",
            agent_id="cuga-default",
        )
    )

    assert len(committed_calls) == 1, f"expected 1 committed call, got {committed_calls}"
    assert committed_calls[0][0] == "thread-success"
    assert committed_calls[0][1]["pending_interrupt"] is False

    assert len(projected_calls) == 1, f"expected 1 projected call, got {projected_calls}"
    assert projected_calls[0][0] == "thread-success"
    assert projected_calls[0][1] == "ckpt-test"


@pytest.mark.unit
def test_errored_turn_calls_mark_projected_only(monkeypatch):
    """W09.8: after a turn that errors, mark_turn_committed is NOT called; mark_projected IS called."""
    from unittest.mock import AsyncMock, MagicMock

    from cuga.backend.server import main

    app_state = _make_mock_app_state()
    monkeypatch.setattr(main, "app_state", app_state)
    monkeypatch.setattr(main, "_assert_run_thread_access", AsyncMock())
    monkeypatch.setattr(main, "_canonical_thread_id", lambda x: x)
    monkeypatch.setattr(main, "_checkpoint_thread_id", lambda tid, uid, aid: tid)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "format_upload_context", MagicMock(return_value=None))
    monkeypatch.setattr(main, "log_error_ref", MagicMock(return_value="ref-err"))
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-t")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "inst-t")

    committed_calls = []
    projected_calls = []

    class FakeThreadsDB:
        async def mark_turn_committed(self, scope, tid, **kwargs):
            committed_calls.append(tid)

        async def mark_projected(self, scope, tid, checkpoint_id):
            projected_calls.append(tid)

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        lambda: FakeThreadsDB(),
    )

    # Stub AgentLoop to raise
    async def _fake_run_stream(state=None, resume=None):
        raise RuntimeError("boom")
        yield  # make it an async generator

    fake_loop = MagicMock()
    fake_loop.run_stream = _fake_run_stream
    fake_loop.get_langfuse_trace_id = MagicMock(return_value=None)
    monkeypatch.setattr("cuga.backend.cuga_graph.utils.agent_loop.AgentLoop", MagicMock(return_value=fake_loop))

    _drain_gen(
        main.event_stream(
            query="hi",
            api_mode=True,
            thread_id="thread-error",
            user_id="user-x",
            agent_id="cuga-default",
        )
    )

    assert committed_calls == [], f"mark_turn_committed should NOT be called on error, got {committed_calls}"
    # W09.8: errored turns write checkpoints for completed steps, so projected_checkpoint_id must be updated to avoid stale-projection repair
    assert len(projected_calls) == 1, f"expected 1 projected call, got {projected_calls}"
    assert projected_calls[0] == "thread-error"


@pytest.mark.unit
def test_interrupted_turn_calls_mark_turn_committed_and_mark_projected(monkeypatch):
    """W09.7+W09.8: after a paused (interrupt) turn, both mark_turn_committed and mark_projected are called."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoopAnswer

    from cuga.backend.server import main

    app_state = _make_mock_app_state()

    # aget_state for interrupt returns snap with next=(something,) so pending_interrupt=True
    async def _aget_state_interrupted(cfg):
        return SimpleNamespace(
            values={"messages": []},
            next=("__interrupt__",),
            config={"configurable": {"checkpoint_id": "ckpt-interrupted"}},
        )

    app_state.agent.graph.aget_state = AsyncMock(side_effect=_aget_state_interrupted)

    monkeypatch.setattr(main, "app_state", app_state)
    monkeypatch.setattr(main, "_assert_run_thread_access", AsyncMock())
    monkeypatch.setattr(main, "_canonical_thread_id", lambda x: x)
    monkeypatch.setattr(main, "_checkpoint_thread_id", lambda tid, uid, aid: tid)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "format_upload_context", MagicMock(return_value=None))
    monkeypatch.setattr(main, "log_error_ref", MagicMock(return_value="ref-x"))
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-t")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "inst-t")

    committed_calls = []
    projected_calls = []

    class FakeThreadsDB:
        async def mark_turn_committed(self, scope, tid, **kwargs):
            committed_calls.append((tid, kwargs))

        async def mark_projected(self, scope, tid, checkpoint_id):
            projected_calls.append((tid, checkpoint_id))

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        lambda: FakeThreadsDB(),
    )

    # Emit an interrupt answer
    interrupt_answer = AgentLoopAnswer(answer="", end=False, interrupt=True, has_tools=False, tools=[])

    async def _fake_run_stream(state=None, resume=None):
        yield interrupt_answer

    fake_loop = MagicMock()
    fake_loop.run_stream = _fake_run_stream
    fake_loop.get_langfuse_trace_id = MagicMock(return_value=None)
    monkeypatch.setattr("cuga.backend.cuga_graph.utils.agent_loop.AgentLoop", MagicMock(return_value=fake_loop))

    _drain_gen(
        main.event_stream(
            query="hi",
            api_mode=True,
            thread_id="thread-interrupt",
            user_id="user-x",
            agent_id="cuga-default",
        )
    )

    assert len(committed_calls) == 1, f"expected committed call, got {committed_calls}"
    assert committed_calls[0][0] == "thread-interrupt"
    assert committed_calls[0][1]["pending_interrupt"] is True  # snap.next is non-empty

    assert projected_calls == [], f"mark_projected must NOT be called for interrupted turns, got {projected_calls}"
