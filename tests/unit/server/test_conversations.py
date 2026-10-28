"""Continuing a conversation over ``/stream`` with server-issued IDs (#774, design §6, §10, §11.1, §12).

The ``server`` fixture is a small FastAPI app over the real conversation routes, a real graph
and the per-test SQLite store.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from cuga.backend.server.turn_prelude import ThreadUnavailable, check_turn_state

pytestmark = pytest.mark.unit


@pytest.fixture
async def server(monkeypatch):
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.server import main
    from cuga.backend.server.conversation_history import ConversationHistoryDB
    from cuga.backend.storage.checkpoint import CheckpointerHandle

    turns: list[str] = []
    gate: dict = {}  # set "release" to an asyncio.Event to hold a turn open

    async def node(state: AgentState):
        if "release" in gate:
            gate["started"].set()
            await gate["release"].wait()
        turns.append(state.input)
        return {
            "url": str(int(state.url or "0") + 1),
            "final_answer": state.input,
            "messages": [AIMessage(content=state.input)],
        }

    saver = MemorySaver()
    builder = StateGraph(AgentState)
    builder.add_node("FinalAnswerAgent", node)
    builder.add_edge(START, "FinalAnswerAgent")
    builder.add_edge("FinalAnswerAgent", END)
    graph = builder.compile(checkpointer=saver)
    db = ConversationHistoryDB()
    state = SimpleNamespace(
        agent=SimpleNamespace(graph=graph, policy_system=None, chat=None),
        agent_id="cuga-default",
        stop_events={},
        current_llm=None,
        output_format=None,
        knowledge_provider=None,
        checkpointer_handle=CheckpointerHandle(saver=saver, kind="sqlite"),
    )
    monkeypatch.setattr(main, "get_conversation_db", lambda: db)
    monkeypatch.setattr(main, "app_state", state)
    monkeypatch.setattr(main.agent_registry, "is_agent_registry_enabled", lambda: False)
    monkeypatch.setattr(main.events_bridge, "forwards_to_events", lambda *args: False)
    monkeypatch.setattr(main, "_knowledge_enabled_for_app_state", lambda _: False)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "_dispatch_slash_for_stream", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "_delete_session_knowledge_for_thread", AsyncMock())
    monkeypatch.setattr(main, "delete_thread_uploads", AsyncMock())
    monkeypatch.setattr(main.settings.evolve, "enabled", False)
    monkeypatch.setattr(main.settings.advanced_features, "mode", "api")
    monkeypatch.setattr(main.settings.advanced_features, "langfuse_tracing", False)

    app = FastAPI()
    app.state.app_state = state
    app.add_api_route("/stream", main.stream, methods=["POST"])
    app.add_api_route("/api/conversations", main.get_conversations, methods=["GET"])
    app.add_api_route("/api/conversations", main.create_conversation, methods=["POST"])
    app.add_api_route("/api/conversations/{conversation_id}", main.delete_conversation, methods=["DELETE"])
    user = {"sub": "alice"}
    app.dependency_overrides[main.require_chat_access] = lambda: SimpleNamespace(sub=user["sub"])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:

        async def create(as_user="alice"):
            user["sub"] = as_user
            response = await client.post("/api/conversations", json={})
            assert response.status_code == 200, response.text
            return response.json()["id"]

        async def send(thread_id, query="hello", as_user="alice"):
            user["sub"] = as_user
            headers = {"X-Thread-ID": thread_id} if thread_id else {}
            return await client.post("/stream", headers=headers, json={"query": query})

        def hold_turns():
            gate["started"], gate["release"] = asyncio.Event(), asyncio.Event()
            return gate

        def key(thread_id, user_id="alice"):
            return main._checkpoint_thread_id(thread_id, user_id, "cuga-default")

        yield SimpleNamespace(
            client=client,
            db=db,
            graph=graph,
            saver=saver,
            state=state,
            turns=turns,
            user=user,
            create=create,
            send=send,
            hold_turns=hold_turns,
            key=key,
            main=main,
        )


def _events(response) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in response.text.splitlines() if line.startswith("event:")]


async def test_a_created_conversation_continues_on_its_id(server):
    thread_id = await server.create()

    for query in ("first", "second"):
        response = await server.send(thread_id, query)
        assert response.status_code == 200, response.text
        assert "Error" not in _events(response)

    snapshot = await server.graph.aget_state({"configurable": {"thread_id": server.key(thread_id)}})
    assert snapshot.values["url"] == "2"  # the second turn continued from the first one's state
    assert await server.db.get_thread_status(thread_id, "alice", "cuga-default") == "active"


async def test_stream_without_a_thread_id_creates_one_and_announces_it_first(server):
    response = await server.send(None)

    assert response.status_code == 200, response.text
    assert _events(response)[0] == "thread_created"
    rows = await server.db._get_store().fetchall("SELECT thread_id, user_id FROM runtime_thread_owners")
    assert len(rows) == 1 and rows[0]["user_id"] == "alice"
    assert rows[0]["thread_id"] in response.text.split("event: thread_created", 1)[1].split("event:", 1)[0]


@pytest.mark.parametrize(
    "case", ["unknown", "other_user", "legacy", "deleted", "lookup_error", "fresh_other_user"]
)
async def test_threads_the_caller_cannot_continue_all_get_the_same_404(server, monkeypatch, case):
    thread_id = str(uuid.uuid4())
    as_user = "alice"
    if case in ("other_user", "fresh_other_user"):
        thread_id = await server.create()
        if case == "other_user":
            assert (await server.send(thread_id)).status_code == 200
        as_user = "bob"  # a brand-new thread with no turns yet is protected too
    elif case == "legacy":
        await server.db.save_conversation("cuga-default", thread_id, 1, "alice", [])
    elif case == "deleted":
        thread_id = await server.create()
        assert (await server.client.delete(f"/api/conversations/{thread_id}")).status_code == 200
    elif case == "lookup_error":
        monkeypatch.setattr(server.db, "get_thread_status", AsyncMock(side_effect=RuntimeError("db down")))
    reference = await server.send(str(uuid.uuid4()))
    turns_before = list(server.turns)

    response = await server.send(thread_id, "attack", as_user=as_user)

    assert response.status_code == 404
    assert response.content == reference.content
    assert server.turns == turns_before


async def test_two_sends_at_once_give_one_turn_and_one_conflict(server):
    thread_id = await server.create()
    gate = server.hold_turns()
    first = asyncio.create_task(server.send(thread_id, "first"))
    await asyncio.wait_for(gate["started"].wait(), timeout=10)

    second = await server.send(thread_id, "second")
    gate["release"].set()

    assert second.status_code == 409
    assert second.json() == {"error": "conflict", "retry_after_ms": 2000}
    assert (await first).status_code == 200
    assert server.turns == ["first"]
    assert (await server.send(thread_id, "third")).status_code == 200  # the lease was released


async def test_listing_marks_history_without_a_conversation_record_as_legacy(server):
    active = await server.create()
    await server.send(active)
    message = [{"role": "user", "content": "x"}]
    await server.db.save_conversation("cuga-default", active, 1, "alice", message)
    await server.db.save_conversation("cuga-default", "from-before", 1, "alice", message)

    listed = {c["id"]: c["status"] for c in (await server.client.get("/api/conversations")).json()}

    assert listed == {active: "active", "from-before": "legacy"}


async def test_delete_removes_checkpoint_and_history_and_the_id_cannot_return(server):
    thread_id = await server.create()
    await server.send(thread_id)

    response = await server.client.delete(f"/api/conversations/{thread_id}")

    assert response.status_code == 200, response.text
    snapshot = await server.graph.aget_state({"configurable": {"thread_id": server.key(thread_id)}})
    assert not snapshot.values
    assert await server.db.get_thread_history(thread_id) == []
    assert await server.db.get_thread_status(thread_id, "alice", "cuga-default") == "deleted"
    assert (await server.send(thread_id)).status_code == 404


async def test_delete_waits_for_a_running_turn(server):
    thread_id = await server.create()
    gate = server.hold_turns()
    turn = asyncio.create_task(server.send(thread_id))
    await asyncio.wait_for(gate["started"].wait(), timeout=10)

    busy = await server.client.delete(f"/api/conversations/{thread_id}")
    gate["release"].set()
    await turn

    assert busy.status_code == 409
    assert (await server.client.delete(f"/api/conversations/{thread_id}")).status_code == 200


async def test_legacy_delete_removes_only_the_callers_own_history(server):
    await server.db.save_conversation("cuga-default", "shared", 1, "alice", [])
    await server.db.save_conversation("cuga-default", "shared", 1, "bob", [])

    assert (await server.client.delete("/api/conversations/shared")).status_code == 200
    server.main._delete_session_knowledge_for_thread.assert_not_awaited()  # keyed by thread only

    assert await server.db.get_latest_version("cuga-default", "shared", "alice") == 0
    assert await server.db.get_latest_version("cuga-default", "shared", "bob") == 1
    server.user["sub"] = "carol"
    assert (await server.client.delete("/api/conversations/shared")).status_code == 404


async def test_owner_rows_from_before_the_status_column_become_legacy(tmp_path):
    from cuga.backend.server.conversation_history import ConversationHistoryDB, _instance_id, _tenant_id
    from cuga.backend.storage.relational.local import LocalRelationalStore

    store = LocalRelationalStore(str(tmp_path / "old.db"))
    await store.execute("""CREATE TABLE runtime_thread_owners (
        tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, thread_id TEXT NOT NULL,
        user_id TEXT NOT NULL, agent_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, instance_id, thread_id))""")
    await store.execute(
        "INSERT INTO runtime_thread_owners VALUES (?, ?, 'old', 'alice', 'cuga-default', 'then')",
        (_tenant_id(), _instance_id()),
    )
    await store.commit()
    db = ConversationHistoryDB()
    db._get_store = lambda: store
    try:
        assert await db.get_thread_status("old", "alice", "cuga-default") == "legacy"
        db._schema_ensured = False
        await db._ensure_schema()  # running the migration again is harmless
    finally:
        await store.close()


# -- fail-closed checkpoint check before a turn (design §6.2 step 4, §10) ----------

_PRESENT = SimpleNamespace(values={"input": "hi"}, next=())
_PAUSED = SimpleNamespace(values={}, next=("WaitForResponse",))
_ABSENT = SimpleNamespace(values={}, next=())
_INCOMPATIBLE = SimpleNamespace(values={"chat_messages": "not a list"}, next=())


@pytest.mark.parametrize(
    ("status", "snapshot", "kind", "expected"),
    [
        ("active", _PRESENT, "sqlite", None),
        ("new", _PRESENT, "sqlite", None),  # a checkpoint always wins over the status
        ("active", _PAUSED, "postgres", None),
        ("new", _ABSENT, "sqlite", None),  # a new conversation starts empty
        ("active", _ABSENT, "sqlite", "missing"),  # never silently starts empty
        ("active", _ABSENT, "memory", None),  # in-memory: a restart loses state by design
        ("active", RuntimeError("db down"), "sqlite", "unreadable"),
        ("active", RuntimeError("db down"), "memory", "unreadable"),
        ("active", _INCOMPATIBLE, "sqlite", "incompatible"),
    ],
)
async def test_checkpoint_rules(status, snapshot, kind, expected):
    graph = SimpleNamespace(
        aget_state=AsyncMock(side_effect=snapshot)
        if isinstance(snapshot, Exception)
        else AsyncMock(return_value=snapshot)
    )
    if expected is None:
        await check_turn_state(graph, "key", status, kind)
        return
    with pytest.raises(ThreadUnavailable) as exc:
        await check_turn_state(graph, "key", status, kind)
    assert exc.value.reason == expected


async def test_stream_returns_409_unavailable_without_running_the_turn(server):
    thread_id = await server.create()
    await server.send(thread_id)
    await server.saver.adelete_thread(server.key(thread_id))  # e.g. the checkpoint DB was lost

    response = await server.send(thread_id, "again")

    assert response.status_code == 409
    assert response.json() == {"error": "unavailable", "reason": "missing"}
    assert server.turns == ["hello"]
    assert (await server.send(thread_id)).status_code == 409  # and the lease was released
