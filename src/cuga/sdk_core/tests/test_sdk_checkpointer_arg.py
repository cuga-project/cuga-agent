"""Tests for the ``checkpointer=`` argument of CugaAgent and CugaSupervisor (#774)."""

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


_BUILDERS = [pytest.param(_agent, id="agent"), pytest.param(_supervisor, id="supervisor")]


@pytest.mark.parametrize("build", _BUILDERS)
def test_default_is_a_fresh_memory_saver_per_instance(build):
    first, second = build().graph, build().graph
    assert isinstance(first.checkpointer, MemorySaver)
    assert first.checkpointer is not second.checkpointer


@pytest.mark.parametrize("build", _BUILDERS)
async def test_passed_saver_is_used(build, tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        await _assert_graph_writes_to(build(checkpointer=saver).graph, saver, "sdk-sqlite")


@pytest.mark.parametrize("build", _BUILDERS)
def test_sync_only_saver_is_rejected_on_first_compile(build):
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        # SqliteSaver overrides the async methods only to raise NotImplementedError.
        for saver in (_SyncOnlySaver(), SqliteSaver(conn)):
            with pytest.raises(TypeError, match="async-capable checkpointer"):
                build(checkpointer=saver).graph
    finally:
        conn.close()
