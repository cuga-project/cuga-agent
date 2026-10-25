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
