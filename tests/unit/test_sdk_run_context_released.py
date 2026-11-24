"""A finished CugaAgent.invoke/stream leaves the caller's run-context variables as they were.

The SDK copies the run config into context variables in the caller's context. Left set, they kept
the finished run's config, and the knowledge engine it holds, alive; a CugaAgent created afterwards
in the same process could then not open knowledge.
"""

import asyncio

import pytest

from cuga.backend.cuga_graph.utils import langfuse_tracing as lt

pytestmark = pytest.mark.unit

_VARS = (
    lt._langgraph_run_config,
    lt._langfuse_callbacks,
    lt._langfuse_trace_id,
    lt._langfuse_primary_handler,
)


def _set_run_context():
    lt.sync_langfuse_callbacks_from_config(
        {"configurable": {"knowledge_engine": object(), "langfuse_trace_id": "t"}}
    )


def test_restore_run_context_puts_every_variable_back():
    before = [v.get() for v in _VARS]
    with lt.restore_run_context():
        _set_run_context()
        assert lt._langgraph_run_config.get() is not None
    assert [v.get() for v in _VARS] == before


def test_invoke_and_stream_restore_the_callers_context():
    from cuga.sdk import CugaAgent, _restores_run_context

    @_restores_run_context
    async def invoke():
        _set_run_context()
        return "done"

    @_restores_run_context
    async def stream():
        _set_run_context()
        yield "step"

    async def run():
        assert await invoke() == "done"
        assert lt._langgraph_run_config.get() is None
        assert [s async for s in stream()] == ["step"]
        assert lt._langgraph_run_config.get() is None

    asyncio.run(run())
    assert CugaAgent.invoke.__wrapped__ and CugaAgent.stream.__wrapped__


def _stream_under_test(seen, closed):
    from cuga.sdk import _restores_run_context

    @_restores_run_context
    async def stream():
        try:
            config = {"configurable": {"knowledge_engine": object(), "langfuse_trace_id": "t"}}
            lt.sync_langfuse_callbacks_from_config(config)
            yield 1
            seen.append(lt._langgraph_run_config.get())
            yield 2
        finally:
            closed.append(True)

    return stream()


def test_a_stream_keeps_its_own_context_between_steps_and_leaves_its_consumers_alone():
    """Task A starts the stream, task B takes the next step and closes it. Each task's context ends
    as it was, and the stream sees its own run config in every step."""
    seen, closed = [], []
    mine = object()

    async def run():
        s = _stream_under_test(seen, closed)

        async def task_a():
            assert await s.__anext__() == 1
            return lt._langgraph_run_config.get()

        async def task_b():
            lt._langgraph_run_config.set(mine)
            assert await s.__anext__() == 2
            await s.aclose()
            return lt._langgraph_run_config.get()

        a_after = await asyncio.create_task(task_a())
        b_after = await asyncio.create_task(task_b())
        return a_after, b_after

    a_after, b_after = asyncio.run(run())
    assert a_after is None  # A does not keep the stream's config
    assert b_after is mine  # B is not given A's saved context
    assert seen and seen[0] is not None and seen[0] is not mine  # the stream's own config in step 2
    assert closed == [True]


def test_closing_a_stream_early_closes_the_wrapped_generator_at_once():
    seen, closed = [], []

    async def run():
        s = _stream_under_test(seen, closed)
        assert await s.__anext__() == 1
        await s.aclose()
        return list(closed), lt._langgraph_run_config.get()

    closed_at_once, after = asyncio.run(run())
    assert closed_at_once == [True]
    assert after is None
