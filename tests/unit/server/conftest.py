"""A small FastAPI app over the real conversation routes, a real graph and the per-test SQLite store."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph


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
