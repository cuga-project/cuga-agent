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
