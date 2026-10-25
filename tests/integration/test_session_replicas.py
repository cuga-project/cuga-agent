"""Two app instances sharing one database (#774).

Each "replica" is a FastAPI app over the real ``/stream``, ``/stop`` and ``/api/conversations``
routes, with its own connection pools, checkpointer, graph, lease object and ``app_state``, so
nothing but the Postgres database is shared. The module-level singletons the routes read
(``main.app_state``, ``main.get_conversation_db``, ``get_thread_lease``) resolve to the replica
that is serving the current request. Runs against the Postgres of the ``pgvector-integration`` job
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
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.server.conversation_history import ConversationHistoryDB
from cuga.backend.server.thread_lease import _STOP_POLL_SECONDS, ThreadLease
from cuga.backend.storage.checkpoint import create_checkpointer
from cuga.backend.storage.relational.prod import ProdRelationalStore

pytestmark = pytest.mark.pgvector

ANSWER = "The fake model answers."
AGENT_ID = "cuga-default"
USER_ID = "alice"

_current: ContextVar[Optional["Replica"]] = ContextVar("current_replica", default=None)


class Hold:
    """Blocks the turn whose question is ``query`` until ``release`` is set."""

    def __init__(self) -> None:
        self.query: Optional[str] = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()


def build_turn_graph(saver, hold: Optional[Hold] = None):
    """One node that appends the question and a fixed answer, and records the turn in a variable."""

    async def answer(state: AgentState):
        if hold is not None and state.input == hold.query:
            hold.started.set()
            await hold.release.wait()
        turn = len(state.chat_messages or []) // 2 + 1
        state.variables_manager.add_variable({"turn": turn}, description=f"recorded on turn {turn}")
        return {
            "final_answer": ANSWER,
            "messages": [AIMessage(content=ANSWER)],
            "chat_messages": [*(state.chat_messages or []), HumanMessage(state.input), AIMessage(ANSWER)],
            "variables_storage": state.variables_storage,
            "variable_counter_state": state.variable_counter_state,
            "variable_creation_order": state.variable_creation_order,
        }

    builder = StateGraph(AgentState)
    builder.add_node("FinalAnswerAgent", answer)
    builder.add_edge(START, "FinalAnswerAgent")
    builder.add_edge("FinalAnswerAgent", END)
    return builder.compile(checkpointer=saver)


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING")
    if not value:
        pytest.skip("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING not set")
    return value


class _ReplicaConversationDB(ConversationHistoryDB):
    """Conversation history on this replica's own connection, not the shared storage facade's."""

    def __init__(self, store) -> None:
        super().__init__()
        self._replica_store = store

    def _get_store(self):
        return self._replica_store


class _ByReplica:
    """Stands in for ``main.app_state``: resolves to the state of the replica serving this request."""

    def __getattr__(self, name):
        return getattr(_current.get().state, name)

    def __setattr__(self, name, value):
        setattr(_current.get().state, name, value)


class _ServedBy:
    """ASGI wrapper that marks every request, and the tasks it spawns, with the serving replica."""

    def __init__(self, replica: "Replica", app: FastAPI) -> None:
        self._replica, self._app = replica, app

    async def __call__(self, scope, receive, send) -> None:
        token = _current.set(self._replica)
        try:
            await self._app(scope, receive, send)
        finally:
            _current.reset(token)


class Replica:
    """One app instance: its own pool for checkpoints and leases, its own routes and state."""

    def __init__(self, dsn: str, hold: Optional[Hold] = None):
        self._dsn = dsn
        self._hold = hold
        self.handle = None
        self.graph = None
        self.client: Optional[httpx.AsyncClient] = None
        self.created: list[str] = []
        self.store = ProdRelationalStore(dsn, "conversation")
        self.db = _ReplicaConversationDB(self.store)
        self.lease = ThreadLease()
        self.lease._get_store = lambda: self.store  # this replica's connection, not the shared facade's

    async def start(self, main) -> "Replica":
        storage = SimpleNamespace(
            checkpointer="durable",
            mode="prod",
            postgres_url=self._dsn,
            checkpoint_pool_min_size=1,
            checkpoint_pool_max_size=2,
        )
        self.handle = await create_checkpointer(storage)
        assert self.handle.kind == "postgres"
        self.graph = build_turn_graph(self.handle.saver, self._hold)
        self.state = SimpleNamespace(
            agent=SimpleNamespace(graph=self.graph, policy_system=None, chat=None),
            agent_id=AGENT_ID,
            stop_events={},
            current_llm=None,
            output_format=None,
            knowledge_provider=None,
            checkpointer_handle=self.handle,
        )
        app = FastAPI()
        app.state.app_state = self.state
        app.add_api_route("/stream", main.stream, methods=["POST"])
        app.add_api_route("/stop", main.stop, methods=["POST"])
        app.add_api_route("/api/conversations", main.create_conversation, methods=["POST"])
        app.add_api_route("/api/conversations", main.get_conversations, methods=["GET"])
        app.dependency_overrides[main.require_chat_access] = lambda: SimpleNamespace(sub=USER_ID)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_ServedBy(self, app)), base_url="http://test"
        )
        return self

    async def create(self) -> str:
        response = await self.client.post("/api/conversations", json={})
        assert response.status_code == 200, response.text
        self.created.append(response.json()["id"])
        return self.created[-1]

    async def send(self, thread_id: str, query: str = "hello") -> httpx.Response:
        return await self.client.post("/stream", headers={"X-Thread-ID": thread_id}, json={"query": query})

    async def stop_turn(self, thread_id: str) -> httpx.Response:
        return await self.client.post("/stop", headers={"X-Thread-ID": thread_id})

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.handle.aclose()
        await self.store.close()


def event_names(response: httpx.Response) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in response.text.splitlines() if line.startswith("event:")]


@pytest.fixture
async def app_replicas(dsn, monkeypatch):
    """Start app instances on the shared database; each call returns a new one."""
    from cuga.backend.server import main, thread_lease

    hold = Hold()
    started: list[Replica] = []

    monkeypatch.setattr(main, "app_state", _ByReplica())
    monkeypatch.setattr(main, "get_conversation_db", lambda: _current.get().db)
    monkeypatch.setattr(thread_lease, "get_thread_lease", lambda: _current.get().lease)
    monkeypatch.setattr(main.agent_registry, "is_agent_registry_enabled", lambda: False)
    monkeypatch.setattr(main.events_bridge, "forwards_to_events", lambda *args: False)
    monkeypatch.setattr(main, "_knowledge_enabled_for_app_state", lambda _: False)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "_dispatch_slash_for_stream", AsyncMock(return_value=None))
    monkeypatch.setattr(main.settings.evolve, "enabled", False)
    monkeypatch.setattr(main.settings.advanced_features, "mode", "api")
    monkeypatch.setattr(main.settings.advanced_features, "langfuse_tracing", False)

    async def start() -> Replica:
        replica = await Replica(dsn, hold).start(main)
        started.append(replica)
        replica.hold = hold
        return replica

    try:
        yield start
    finally:
        hold.release.set()
        await asyncio.sleep(0)  # let a released turn finish before its pools close
        if started:
            first = started[0]
            await first.db._ensure_schema()
            await first.lease._store()
            for replica in started:
                for thread_id in replica.created:
                    key = main._checkpoint_thread_id(thread_id, USER_ID, AGENT_ID)
                    await first.handle.saver.adelete_thread(key)
                    await first.store.execute("DELETE FROM thread_leases WHERE thread_id = ?", (key,))
                    for table in ("runtime_thread_owners", "conversation_history", "stream_events"):
                        await first.store.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
            await first.store.commit()
        for replica in started:
            await replica.aclose()


async def test_second_replica_continues_a_conversation_started_on_the_first(app_replicas):
    from cuga.backend.server.main import _checkpoint_thread_id

    first, second = await app_replicas(), await app_replicas()
    thread_id = await first.create()
    config = {"configurable": {"thread_id": _checkpoint_thread_id(thread_id, USER_ID, AGENT_ID)}}

    one = await first.send(thread_id, "first question")
    assert one.status_code == 200, one.text
    assert "Error" not in event_names(one)

    # The second app instance has never seen this conversation: it continues from the shared database.
    two = await second.send(thread_id, "second question")
    assert two.status_code == 200, two.text
    assert "Error" not in event_names(two)
    assert "thread_created" not in event_names(two)  # it continued; it did not start a new conversation

    # The first instance sees the turn the second one added.
    continued = (await first.graph.aget_state(config)).values
    assert [m.content for m in continued["chat_messages"]] == [
        "first question",
        ANSWER,
        "second question",
        ANSWER,
    ]
    assert continued["variables_storage"]["variable_1"]["value"] == {"turn": 1}
    assert continued["variables_storage"]["variable_2"]["value"] == {"turn": 2}
    listed = {c["id"]: c["status"] for c in (await second.client.get("/api/conversations")).json()}
    assert listed[thread_id] == "active"


async def test_stop_sent_to_another_replica_stops_the_running_turn(app_replicas):
    running, other = await app_replicas(), await app_replicas()
    thread_id = await running.create()
    running.hold.query = "long question"
    turn = asyncio.create_task(running.send(thread_id, "long question"))
    try:
        await asyncio.wait_for(running.hold.started.wait(), timeout=30)

        # The other instance cannot start a second turn on the conversation meanwhile.
        busy = await other.send(thread_id, "another question")
        assert busy.status_code == 409
        assert busy.json()["error"] == "conflict"
        assert not turn.done()

        stopped = await other.stop_turn(thread_id)
        assert stopped.status_code == 200, stopped.text

        # The stop reaches the running instance through the shared lease, not through shared memory.
        response = await asyncio.wait_for(turn, timeout=_STOP_POLL_SECONDS + 15)
        assert response.status_code == 200, response.text
        assert "Stopped" in event_names(response)
    finally:
        running.hold.release.set()
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)

    # The stopped turn released its lease, so the next turn can start on either instance.
    running.hold.query = None
    after = await other.send(thread_id, "after the stop")
    assert after.status_code == 200, after.text
    assert "Error" not in event_names(after)


async def test_retention_sweep_prunes_idle_conversation_on_postgres(dsn, tmp_path, monkeypatch):
    """The retention sweep marks an idle conversation legacy and removes its checkpoint.

    Uses Postgres so the pgvector-integration job exercises the full path. The relational
    store (owner rows) and the checkpointer both point at the CI database.
    """
    import datetime

    import cuga.backend.storage.facade as facade

    monkeypatch.setattr(facade, "_local_db_path", lambda: str(tmp_path / "cuga.db"))
    monkeypatch.setattr(facade, "_storage_mode", lambda: "prod")
    monkeypatch.setattr(facade, "_postgres_url", lambda: dsn)
    facade.get_storage().invalidate_relational_stores()

    from cuga.backend.server.main import _checkpoint_thread_id
    from cuga.backend.server.retention import _prune_batch

    storage = SimpleNamespace(
        checkpointer="durable",
        mode="prod",
        postgres_url=dsn,
        checkpoint_pool_min_size=1,
        checkpoint_pool_max_size=2,
    )
    handle = await create_checkpointer(storage)
    assert handle.kind == "postgres"

    thread_old, thread_recent = f"ret-old-{uuid.uuid4().hex}", f"ret-new-{uuid.uuid4().hex}"
    user_id, agent_id = "u-ret", "a-ret"
    try:
        conv_db = ConversationHistoryDB()

        for tid in (thread_old, thread_recent):
            await conv_db.create_conversation(tid, user_id, agent_id)

        # Push the old one's last_turn_at far into the past
        store = facade.get_storage().get_relational_store("conversation")
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
        # Remove this test's rows from the shared database, whatever happened above.
        store = facade.get_storage().get_relational_store("conversation")
        for tid in (thread_old, thread_recent):
            await handle.saver.adelete_thread(_checkpoint_thread_id(tid, user_id, agent_id))
            await store.execute("DELETE FROM runtime_thread_owners WHERE thread_id = ?", (tid,))
        await store.commit()
        await handle.aclose()
        await facade.get_storage().close_relational_stores()
