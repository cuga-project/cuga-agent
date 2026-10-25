"""Two replicas sharing one database (#774).

Each "replica" has its own connection pool, checkpointer, graph and lease object, so nothing
but the database is shared. Runs against the Postgres of the ``pgvector-integration`` job
(``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING``) and skips when it is unset. Local run::

    docker run --rm -d --name cuga-pgvector-test \\
        -e POSTGRES_PASSWORD=cuga -e POSTGRES_USER=cuga \\
        -e POSTGRES_DB=cuga_test -p 5432:5432 \\
        pgvector/pgvector:0.8.0-pg16
    export CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING=\\
        postgresql://cuga:cuga@localhost:5432/cuga_test  # pragma: allowlist secret
    uv run pytest tests/integration/test_session_replicas.py -m pgvector -v
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.server.thread_lease import _STOP_POLL_SECONDS, ThreadBusy, ThreadLease, keep_lease
from cuga.backend.storage.checkpoint import create_checkpointer
from cuga.backend.storage.relational.prod import ProdRelationalStore

pytestmark = pytest.mark.pgvector

ANSWER = "The fake model answers."


def build_turn_graph(saver):
    """One node that appends the question and a fixed answer, and records the turn in a variable."""

    async def answer(state: AgentState):
        turn = len(state.chat_messages or []) // 2 + 1
        state.variables_manager.add_variable({"turn": turn})
        return {
            "final_answer": ANSWER,
            "chat_messages": [*(state.chat_messages or []), HumanMessage(state.input), AIMessage(ANSWER)],
            "variables_storage": state.variables_storage,
            "variable_counter_state": state.variable_counter_state,
            "variable_creation_order": state.variable_creation_order,
        }

    builder = StateGraph(AgentState)
    builder.add_node("Answer", answer)
    builder.add_edge(START, "Answer")
    builder.add_edge("Answer", END)
    return builder.compile(checkpointer=saver)


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING")
    if not value:
        pytest.skip("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING not set")
    return value


@pytest.fixture
def thread_id() -> str:
    return f"replicas-{uuid.uuid4().hex}"


class Replica:
    """One app instance: its own pool for checkpoints, its own pool for leases."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self.handle = None
        self.graph = None
        self.store = ProdRelationalStore(dsn, "conversation")
        self.lease = ThreadLease()
        self.lease._get_store = lambda: self.store  # this replica's connection, not the shared facade's

    async def start(self) -> "Replica":
        storage = SimpleNamespace(
            checkpointer="durable",
            mode="prod",
            postgres_url=self._dsn,
            checkpoint_pool_min_size=1,
            checkpoint_pool_max_size=2,
        )
        self.handle = await create_checkpointer(storage)
        assert self.handle.kind == "postgres"
        self.graph = build_turn_graph(self.handle.saver)
        return self

    async def stop(self) -> None:
        await self.handle.aclose()
        await self.store.close()


@pytest.fixture
async def replicas(dsn, thread_id):
    started: list[Replica] = []

    async def start() -> Replica:
        replica = await Replica(dsn).start()
        started.append(replica)
        return replica

    try:
        yield start
    finally:
        if started:
            await started[0].handle.saver.adelete_thread(thread_id)
            await started[0].store.execute("DELETE FROM thread_leases WHERE thread_id = ?", (thread_id,))
        for replica in started:
            await replica.stop()


async def test_second_replica_continues_a_thread_started_on_the_first(replicas, thread_id):
    config = {"configurable": {"thread_id": thread_id}}
    first = await replicas()
    await first.graph.ainvoke({"input": "first question"}, config)

    second = await replicas()
    restored = (await second.graph.aget_state(config)).values
    assert [m.content for m in restored["chat_messages"]] == ["first question", ANSWER]
    assert restored["variables_storage"]["variable_1"]["value"] == {"turn": 1}

    await second.graph.ainvoke({"input": "second question"}, config)

    # The first replica sees the turn the second one added.
    continued = (await first.graph.aget_state(config)).values
    assert [m.content for m in continued["chat_messages"]] == [
        "first question",
        ANSWER,
        "second question",
        ANSWER,
    ]
    assert continued["variables_storage"]["variable_2"]["value"] == {"turn": 2}


async def test_stop_sent_to_another_replica_stops_the_running_turn(replicas, thread_id):
    running, other = await replicas(), await replicas()
    token = await running.lease.acquire(thread_id)
    stop_event = asyncio.Event()

    async with keep_lease(running.lease, token, stop_event=stop_event, renew_seconds=30.0):
        with pytest.raises(ThreadBusy):  # the other replica cannot start a second turn meanwhile
            await other.lease.acquire(thread_id)
        assert not stop_event.is_set()

        await other.lease.request_stop(thread_id)

        await asyncio.wait_for(stop_event.wait(), timeout=_STOP_POLL_SECONDS + 5.0)

    # The turn's lease is released, so the next turn on any replica can start.
    await other.lease.release(await other.lease.acquire(thread_id))


async def test_retention_sweep_prunes_idle_conversation_on_postgres(dsn, tmp_path, monkeypatch):
    """W12.3/F9: the retention sweep marks an idle conversation legacy and removes its checkpoint.

    Uses Postgres so the pgvector-integration job exercises the full path.
    """
    import datetime

    import cuga.backend.storage.facade as facade

    monkeypatch.setattr(facade, "_local_db_path", lambda: str(tmp_path / "cuga.db"))
    monkeypatch.setattr(facade, "_storage_mode", lambda: "prod")
    facade.get_storage().invalidate_relational_stores()

    from cuga.backend.server.conversation_history import ConversationHistoryDB
    from cuga.backend.server.main import _checkpoint_thread_id
    from cuga.backend.server.retention import _prune_batch
    from cuga.backend.server.thread_lease import ThreadLease
    from cuga.backend.storage.checkpoint import create_checkpointer

    storage = SimpleNamespace(
        checkpointer="durable",
        mode="prod",
        postgres_url=dsn,
        checkpoint_pool_min_size=1,
        checkpoint_pool_max_size=2,
    )
    handle = await create_checkpointer(storage)
    assert handle.kind == "postgres"

    try:
        conv_db = ConversationHistoryDB()
        # Set up one old and one recent conversation
        thread_old, thread_recent = f"ret-old-{uuid.uuid4().hex}", f"ret-new-{uuid.uuid4().hex}"
        user_id, agent_id = "u-ret", "a-ret"

        for tid in (thread_old, thread_recent):
            await conv_db.create_conversation(tid, user_id, agent_id)

        # Push the old one's last_turn_at far into the past
        from cuga.backend.storage.facade import get_storage

        store = get_storage().get_relational_store("conversation")
        old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=200)).isoformat()
        await store.execute(
            "UPDATE runtime_thread_owners SET last_turn_at = ? WHERE thread_id = ?",
            (old_ts, thread_old),
        )
        await store.commit()

        # Write a real checkpoint for the old thread so adelete_thread has something to remove
        old_key = _checkpoint_thread_id(thread_old, user_id, agent_id)
        graph_old = build_turn_graph(handle.saver)
        await graph_old.ainvoke({"input": "old message"}, {"configurable": {"thread_id": old_key}})

        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)).isoformat()
        lease = ThreadLease()

        pruned = await _prune_batch(
            saver=handle.saver,
            conversation_db=conv_db,
            lease=lease,
            checkpoint_thread_id_fn=_checkpoint_thread_id,
            cutoff_iso=cutoff,
        )

        assert pruned == 1, "exactly the old conversation should be pruned"
        assert await conv_db.get_thread_status(thread_old, user_id, agent_id) == "legacy"
        assert await conv_db.get_thread_status(thread_recent, user_id, agent_id) != "legacy"

        # Checkpoint for the old thread is gone
        snap = await handle.saver.aget({"configurable": {"thread_id": old_key}})
        assert snap is None, "checkpoint should be deleted after pruning"

    finally:
        await handle.aclose()
        from cuga.backend.storage.facade import get_storage

        await get_storage().close_relational_stores()
        facade.get_storage().invalidate_relational_stores()
