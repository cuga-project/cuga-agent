"""Tests for the ``checkpointer=`` argument of CugaAgent and CugaSupervisor (#774, W15.1)."""

import inspect
import sqlite3
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver

from cuga import CugaAgent, CugaSupervisor

pytestmark = pytest.mark.unit


class _SyncOnlySaver(BaseCheckpointSaver):
    """Implements only the sync API, so ``aget_tuple`` is the base-class stub."""

    def get_tuple(self, config):
        return None

    def list(self, config, *, filter=None, before=None, limit=None):
        return iter(())

    def put(self, config, checkpoint, metadata, new_versions):
        return config

    def put_writes(self, config, writes, task_id, task_path=""):
        return None


def _agent(**kwargs) -> CugaAgent:
    return CugaAgent(
        tools=[],
        model=MagicMock(),
        auto_load_policies=False,
        reset_policy_storage=True,
        **kwargs,
    )


def _supervisor(**kwargs) -> CugaSupervisor:
    return CugaSupervisor(
        agents={"helper": _agent()},
        model=MagicMock(),
        auto_load_policies=False,
        **kwargs,
    )


async def _assert_graph_writes_to(graph, saver: BaseCheckpointSaver, thread_id: str) -> None:
    # Under LANGGRAPH_STRICT_MSGPACK, compile() swaps in saver.with_allowlist(...),
    # a shallow clone of the passed saver, so identity only holds in non-strict mode.
    # Prove the compiled graph writes to the passed saver's backing store instead.
    graph_saver = graph.checkpointer
    assert type(graph_saver) is type(saver)
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    written = await graph_saver.aput(
        config, empty_checkpoint(), {"source": "input", "step": -1, "parents": {}}, {}
    )

    stored = await saver.aget_tuple(written)
    assert stored is not None
    assert stored.checkpoint["id"] == written["configurable"]["checkpoint_id"]


@pytest.mark.parametrize("cls", [CugaAgent, CugaSupervisor])
def test_checkpointer_is_keyword_only_and_last(cls):
    params = list(inspect.signature(cls.__init__).parameters.values())
    assert params[-1].name == "checkpointer"
    assert params[-1].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[-1].default is None


class TestCugaAgentCheckpointer:
    def test_default_is_fresh_memory_saver(self):
        first = _agent().graph
        second = _agent().graph

        assert isinstance(first.checkpointer, MemorySaver)
        assert first.checkpointer is not second.checkpointer

    @pytest.mark.asyncio
    async def test_passed_memory_saver_is_used(self):
        saver = MemorySaver()

        graph = _agent(checkpointer=saver).graph

        await _assert_graph_writes_to(graph, saver, "sdk-agent-memory")

    @pytest.mark.asyncio
    async def test_passed_async_sqlite_saver_is_used(self, tmp_path):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
            graph = _agent(checkpointer=saver).graph

            await _assert_graph_writes_to(graph, saver, "sdk-agent-sqlite")

    def test_sync_only_saver_raises_on_first_compile(self):
        agent = _agent(checkpointer=_SyncOnlySaver())  # construction does not validate

        with pytest.raises(TypeError, match="CugaAgent requires an async-capable checkpointer"):
            agent.graph
        assert agent._compiled_graph is None

    def test_sync_sqlite_saver_raises(self):
        # SqliteSaver overrides aget_tuple only to raise NotImplementedError.
        from langgraph.checkpoint.sqlite import SqliteSaver

        conn = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            agent = _agent(checkpointer=SqliteSaver(conn))
            with pytest.raises(TypeError, match="async-capable checkpointer"):
                agent.graph
        finally:
            conn.close()


class TestCugaSupervisorCheckpointer:
    def test_default_is_fresh_memory_saver(self):
        first = _supervisor().graph
        second = _supervisor().graph

        assert isinstance(first.checkpointer, MemorySaver)
        assert first.checkpointer is not second.checkpointer

    @pytest.mark.asyncio
    async def test_passed_saver_is_used(self):
        saver = MemorySaver()

        graph = _supervisor(checkpointer=saver).graph

        await _assert_graph_writes_to(graph, saver, "sdk-supervisor-memory")

    def test_passed_saver_is_not_given_to_internal_agents(self):
        saver = MemorySaver()
        helper = _agent()
        supervisor = CugaSupervisor(
            agents={"helper": helper}, model=MagicMock(), auto_load_policies=False, checkpointer=saver
        )

        supervisor.graph

        assert helper._checkpointer is None

    def test_sync_only_saver_raises_on_first_compile(self):
        supervisor = _supervisor(checkpointer=_SyncOnlySaver())

        with pytest.raises(TypeError, match="CugaSupervisor requires an async-capable checkpointer"):
            supervisor.graph
        assert supervisor._compiled_graph is None
