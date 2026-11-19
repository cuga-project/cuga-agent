"""Public SDK stream lifecycle with a deterministic graph-source seam."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar

import pytest

from cuga.backend.observability import langfuse_sdk
from cuga.sdk import CugaAgent

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def stream_agent(monkeypatch):
    agent = CugaAgent.__new__(CugaAgent)
    agent._callbacks = []
    context = ContextVar("stream_test_context", default="caller")
    events = []

    @asynccontextmanager
    async def trace(*args, **kwargs):
        token = context.set("sdk root")

        class Span:
            def end(self, output):
                events.append(("end", output))

        try:
            yield Span()
        finally:
            context.reset(token)
            events.append("closed trace")

    monkeypatch.setattr(langfuse_sdk, "enabled", lambda: True)
    monkeypatch.setattr(langfuse_sdk, "run_trace", trace)
    return agent, context, events


@pytest.mark.parametrize("mode", ["off", "caller", "sdk"])
@pytest.mark.parametrize("early", [False, True])
async def test_stream_context_and_update_parity(stream_agent, monkeypatch, mode, early):
    agent, context, events = stream_agent
    tasks = []
    updates = [{"node": {"final_answer": "first"}}, {"node": {"final_answer": "second"}}]
    monkeypatch.setattr(langfuse_sdk, "enabled", lambda: mode != "off")

    async def source(*args, **kwargs):
        token = context.set("source")
        try:
            for state in updates:
                tasks.append(asyncio.current_task())
                yield state
        finally:
            context.reset(token)
            events.append("closed source")

    monkeypatch.setattr(agent, "_stream_untraced", source)
    config = {"configurable": {"langfuse_trace_id": "caller-trace"}} if mode == "caller" else None
    stream = agent.stream("message", thread_id="thread", config=config)
    consumer = asyncio.current_task()
    assert await anext(stream) is updates[0]
    assert context.get() == ("caller" if mode == "sdk" else "source")
    if not early:
        second = await asyncio.create_task(anext(stream)) if mode == "sdk" else await anext(stream)
        assert second is updates[1]
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    else:
        await stream.aclose()
    assert context.get() == "caller"
    assert events[0] == "closed source"
    if mode == "sdk":
        assert events == ["closed source", ("end", "first" if early else "second"), "closed trace"]
        assert tasks[0] is not consumer and all(task is tasks[0] for task in tasks)
    else:
        assert events == ["closed source"] and all(task is consumer for task in tasks)


@pytest.mark.parametrize("second_cancel", [False, True])
async def test_cancelled_pull_does_not_wait_forever_for_graph_finally(
    stream_agent, monkeypatch, second_cancel
):
    agent, _, events = stream_agent
    entered = asyncio.Event()
    cleanup_entered = asyncio.Event()
    release = asyncio.Event()
    driver_task = None

    async def source(*args, **kwargs):
        nonlocal driver_task
        driver_task = asyncio.current_task()
        try:
            entered.set()
            await asyncio.Future()
            yield None
        finally:
            cleanup_entered.set()
            await release.wait()

    monkeypatch.setattr(agent, "_stream_untraced", source)
    stream = agent.stream("message", thread_id="thread")
    pull = asyncio.create_task(asyncio.wait_for(anext(stream), timeout=0.05))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(cleanup_entered.wait(), 1)
        if second_cancel:
            pull.cancel()
        done, _ = await asyncio.wait({pull}, timeout=0.25 if second_cancel else 1.0)
        assert done == {pull}, "cancelled pull waited indefinitely for stuck graph cleanup"
        with pytest.raises(asyncio.CancelledError if second_cancel else TimeoutError):
            pull.result()
    finally:
        release.set()
        await asyncio.gather(pull, return_exceptions=True)
        if driver_task is not None:
            await asyncio.gather(driver_task, return_exceptions=True)
        await stream.aclose()
    assert events[-1] == "closed trace"
