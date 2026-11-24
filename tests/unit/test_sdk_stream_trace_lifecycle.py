"""A pull stream owns its tracing context and closes every layer before its trace ends."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import MethodType, SimpleNamespace

import pytest

from cuga import sdk
from cuga.backend.observability import langfuse_sdk as lf

pytestmark = pytest.mark.unit

_CURRENT = ContextVar("test_stream_trace", default=None)
_RUN = ContextVar("test_stream_run_config", default=None)


@pytest.fixture
def traces(monkeypatch):
    events = []

    @asynccontextmanager
    async def run_trace(name, message, thread_id, config, *, default_callbacks=None):
        token = _CURRENT.set(thread_id)
        events.append((thread_id, "start"))

        def end(output=None, *, error=None):
            events.append((thread_id, "output", output) if error is None else (thread_id, "error-end", error))

        def close():
            events.append((thread_id, "closed-early"))

        try:
            yield SimpleNamespace(end=end, close=close)
        except BaseException as exc:
            events.append((thread_id, "error", type(exc)))
            raise
        finally:
            # A real OTel token must be reset in the Context that created it, too.
            _CURRENT.reset(token)
            events.append((thread_id, "exit"))

    monkeypatch.setattr(lf, "enabled", lambda: True)
    monkeypatch.setattr(lf, "run_trace", run_trace)
    return events


def agent_for(source):
    agent = SimpleNamespace(
        _callbacks=None,
        _trace_run_config=lambda config: dict(config or {}),
        _trace_thread_id=lambda message, thread_id, action_response, config: thread_id,
        _stream_untraced=source,
    )
    agent.stream = MethodType(sdk.CugaAgent.stream, agent)
    return agent


@pytest.mark.asyncio
async def test_interleaved_streams_do_not_leak_context_or_prefetch(traces):
    steps = []

    async def source(message, *, thread_id, **kwargs):
        assert _CURRENT.get() == thread_id
        _RUN.set(thread_id)
        try:
            for i in range(2):
                assert _CURRENT.get() == thread_id and _RUN.get() == thread_id
                steps.append((thread_id, i))
                yield {"final_answer": f"{thread_id}-{i}"}
        finally:
            assert _CURRENT.get() == thread_id
            traces.append((thread_id, "closed"))

    token = _CURRENT.set("caller")
    run_token = _RUN.set("caller-config")
    a = agent_for(source).stream("a", thread_id="a")
    b = agent_for(source).stream("b", thread_id="b")
    try:
        assert await a.__anext__() == {"final_answer": "a-0"}
        assert (_CURRENT.get(), _RUN.get()) == ("caller", "caller-config")
        assert await b.__anext__() == {"final_answer": "b-0"}
        await asyncio.sleep(0)
        assert steps == [("a", 0), ("b", 0)]  # consumer has not requested another update
        assert (_CURRENT.get(), _RUN.get()) == ("caller", "caller-config")
        await a.aclose()
        assert traces[-3:] == [("a", "closed"), ("a", "output", "a-0"), ("a", "exit")]
        assert await b.__anext__() == {"final_answer": "b-1"}
        with pytest.raises(StopAsyncIteration):
            await b.__anext__()
        assert not any(event[1] == "error" for event in traces)
        assert (_CURRENT.get(), _RUN.get()) == ("caller", "caller-config")
    finally:
        await a.aclose()
        await b.aclose()
        _CURRENT.reset(token)
        _RUN.reset(run_token)


@pytest.mark.asyncio
async def test_stream_error_preserves_last_output_and_closes_before_trace_exit(traces):
    async def source(message, **kwargs):
        try:
            yield {"final_answer": "partial"}
            raise ValueError("graph failed")
        finally:
            await asyncio.sleep(0)
            traces.append(("t", "closed"))

    stream = agent_for(source).stream("hi", thread_id="t")
    await stream.__anext__()
    with pytest.raises(ValueError, match="graph failed"):
        await stream.__anext__()
    assert traces == [
        ("t", "start"),
        ("t", "closed"),
        ("t", "output", "partial"),
        ("t", "error", ValueError),
        ("t", "exit"),
    ]
    assert _CURRENT.get() is None


@pytest.mark.asyncio
async def test_cancellation_waits_for_inner_cleanup(traces):
    started = asyncio.Event()

    async def source(message, **kwargs):
        try:
            yield {"final_answer": "partial"}
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            traces.append(("t", "closed"))

    stream = agent_for(source).stream("hi", thread_id="t")
    await stream.__anext__()
    pending = asyncio.create_task(stream.__anext__())
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert traces.index(("t", "closed")) < traces.index(("t", "output", "partial"))
    assert traces[-1] == ("t", "exit")
    assert _CURRENT.get() is None


@pytest.mark.asyncio
async def test_trace_start_failure_reaches_consumer(monkeypatch):
    @asynccontextmanager
    async def broken_trace(*args, **kwargs):
        raise RuntimeError("trace setup failed")
        yield

    monkeypatch.setattr(lf, "enabled", lambda: True)
    monkeypatch.setattr(lf, "run_trace", broken_trace)

    async def source(message, **kwargs):
        yield None

    stream = agent_for(source).stream("hi")
    with pytest.raises(RuntimeError, match="trace setup failed"):
        await asyncio.wait_for(stream.__anext__(), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("with_hooks", [False, True])
async def test_close_cascades_through_actual_untraced_stream(monkeypatch, traces, resume, with_hooks):
    async def graph_stream(*args, **kwargs):
        try:
            yield {"final_answer": "partial"}
            pytest.fail("the graph was advanced without consumer demand")
        finally:
            await asyncio.sleep(0)
            assert _CURRENT.get() == "t"
            traces.append(("t", "graph closed"))

    async def hooks_stream(updates, thread_id):
        try:
            async for update in updates:
                yield update
        finally:
            await updates.aclose()
            traces.append(("t", "hooks closed"))

    async def initialized():
        pass

    agent = agent_for(None)
    agent._stream_untraced = MethodType(sdk.CugaAgent._stream_untraced, agent)
    agent._in_hooks_run = MethodType(sdk.CugaAgent._in_hooks_run, agent)
    agent.graph = SimpleNamespace(astream=graph_stream)
    agent.hooks = SimpleNamespace(in_run=lambda: False, run_stream=hooks_stream) if with_hooks else None
    agent._ensure_initialized = initialized
    agent._auto_load_policies = False
    agent._policy_system = None
    agent._enable_skills = None
    agent._skills_folder = None
    agent._prepare_run_config = lambda config: {"configurable": {}}
    agent._apply_shortlister = lambda *args: None
    agent._apply_execution_mode = lambda *args: None
    agent._apply_callbacks = lambda *args: None
    agent._inject_knowledge_to_config = lambda *args: None
    monkeypatch.setattr(sdk, "init_openlit", lambda: None)
    monkeypatch.setattr(sdk, "set_session_attribute", lambda thread_id: None)
    stream = agent.stream(None if resume else "hi", thread_id="t")
    await stream.__anext__()
    await stream.aclose()
    close_events = [("t", "graph closed")]
    if with_hooks:
        close_events.append(("t", "hooks closed"))
    assert traces == [("t", "start"), *close_events, ("t", "output", "partial"), ("t", "exit")]


@pytest.mark.asyncio
async def test_disabled_stream_stays_in_caller_task_and_closes(monkeypatch):
    monkeypatch.setattr(lf, "enabled", lambda: False)
    monkeypatch.setattr(lf, "run_trace", lambda *args, **kwargs: pytest.fail("tracing is disabled"))
    caller = asyncio.current_task()
    seen = []

    async def source(message, **kwargs):
        assert asyncio.current_task() is caller
        try:
            seen.append("advance")
            yield {"final_answer": "partial"}
            pytest.fail("consumer stopped after the first update")
        finally:
            await asyncio.sleep(0)
            assert asyncio.current_task() is caller
            seen.append("closed")

    from contextlib import aclosing

    stream = agent_for(source).stream("hi", thread_id="t")
    async with aclosing(stream) as updates:
        async for state in updates:
            assert state == {"final_answer": "partial"}
            break
        assert seen == ["advance"]
    assert seen == ["advance", "closed"]


@pytest.mark.asyncio
async def test_driver_cancelled_between_pulls_does_not_hang(traces):
    drivers = []

    async def source(message, **kwargs):
        drivers.append(asyncio.current_task())
        yield {"final_answer": "partial"}
        yield {"final_answer": "done"}

    stream = agent_for(source).stream("hi", thread_id="t")
    await stream.__anext__()
    drivers[0].cancel()
    await asyncio.gather(*drivers, return_exceptions=True)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stream.__anext__(), timeout=0.2)


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership", ["trace_id", "config_handler", "constructor_handler"])
async def test_caller_owned_stream_stays_in_caller_context(monkeypatch, ownership):
    from cuga.backend.cuga_graph.utils import langfuse_tracing

    class CallbackHandler:
        pass

    monkeypatch.setattr(langfuse_tracing, "_langfuse_handler_classes", lambda: (CallbackHandler,))
    monkeypatch.setattr(lf, "enabled", lambda: True)
    monkeypatch.setattr(lf, "run_trace", lambda *args, **kwargs: pytest.fail("caller owns tracing"))
    caller = asyncio.current_task()
    value = ContextVar("caller_owned_stream", default=None)

    async def source(message, **kwargs):
        assert asyncio.current_task() is caller
        value.set("visible")
        try:
            yield "first"
        finally:
            assert asyncio.current_task() is caller
            value.set("closed")

    agent = agent_for(source)
    config = {}
    if ownership == "trace_id":
        config = {"configurable": {"langfuse_trace_id": "trace"}}
    elif ownership == "config_handler":
        config = {"callbacks": [CallbackHandler()]}
    else:
        agent._callbacks = [CallbackHandler()]
    stream = agent.stream("hi", config=config)
    assert await stream.__anext__() == "first"
    assert value.get() == "visible"
    await stream.aclose()
    assert value.get() == "closed"


def test_public_stream_doc_preserves_early_close_guidance():
    assert "aclosing" in sdk.CugaAgent.stream.__doc__
    assert "aclose()" in sdk.CugaAgent.stream.__doc__
    assert "action_response" in sdk.CugaAgent.stream.__doc__


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeated_cancellation_releases_caller_before_uncooperative_graph_cleanup(traces):
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()
    driver = None

    async def source(message, **kwargs):
        nonlocal driver
        driver = asyncio.current_task()
        try:
            started.set()
            await asyncio.Event().wait()
            yield None
        finally:
            cleanup_started.set()
            try:
                await release_cleanup.wait()
            except asyncio.CancelledError:
                cleanup_cancelled.set()
                # asyncio cannot force a cancellation-suppressing generator to finish.
                await release_cleanup.wait()
            traces.append(("t", "closed"))

    stream = agent_for(source).stream("hi", thread_id="t")
    pull = asyncio.create_task(stream.__anext__())
    try:
        await asyncio.wait_for(started.wait(), 1)
        pull.cancel()
        await asyncio.wait_for(cleanup_started.wait(), 1)
        pull.cancel()
        done, _ = await asyncio.wait({pull}, timeout=0.25)
        assert pull in done
        with pytest.raises(asyncio.CancelledError):
            pull.result()
        await asyncio.wait_for(cleanup_cancelled.wait(), 1)
        assert driver is not None and not driver.done()
        assert ("t", "exit") not in traces
        # The detached driver is held until it finishes, and its root observation is already ended.
        assert driver in sdk._DETACHED_STREAM_DRIVERS
        assert ("t", "closed-early") in traces
        assert any(event[:2] == ("t", "error-end") for event in traces)
    finally:
        release_cleanup.set()
        await asyncio.gather(pull, return_exceptions=True)
        if driver is not None:
            await asyncio.gather(driver, return_exceptions=True)
        await stream.aclose()

    assert traces.index(("t", "closed")) < traces.index(("t", "exit"))
    assert driver not in sdk._DETACHED_STREAM_DRIVERS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_past_grace_period_detaches_driver_and_ends_trace(traces):
    started = asyncio.Event()
    release_cleanup = asyncio.Event()
    driver = None

    async def source(message, **kwargs):
        nonlocal driver
        driver = asyncio.current_task()
        try:
            started.set()
            await asyncio.Event().wait()
            yield None
        finally:
            while not release_cleanup.is_set():
                try:
                    await release_cleanup.wait()
                except asyncio.CancelledError:
                    pass  # suppresses every cancel until released

    stream = agent_for(source).stream("hi", thread_id="g")
    pull = asyncio.create_task(stream.__anext__())
    try:
        await asyncio.wait_for(started.wait(), 1)
        pull.cancel()  # one cancel: the caller waits the 500 ms grace period, then returns
        done, _ = await asyncio.wait({pull}, timeout=2)
        assert pull in done
        assert driver in sdk._DETACHED_STREAM_DRIVERS
        assert ("g", "closed-early") in traces
        assert ("g", "exit") not in traces
    finally:
        release_cleanup.set()
        await asyncio.gather(pull, return_exceptions=True)
        if driver is not None:
            await asyncio.gather(driver, return_exceptions=True)
    assert driver not in sdk._DETACHED_STREAM_DRIVERS
