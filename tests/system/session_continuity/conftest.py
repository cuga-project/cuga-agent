"""Fixtures for the session-continuity restart tests (#774).

A restart closes the durable checkpointer and the relational stores, then opens new ones on the
same files, in the same process. The app, the compiled graph and every in-memory structure are
rebuilt, so a conversation can only continue from what reached the database.

The app is the real ``/stream`` and ``/api/conversations`` routes over a real
``create_checkpointer(storage.checkpointer="durable")`` and a graph whose only node answers with a
fake chat model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.storage.checkpoint import CheckpointerHandle, create_checkpointer

ANSWER = "The fake model answers."
AGENT_ID = "cuga-default"
USER_ID = "alice"


@pytest.fixture(autouse=True)
def _isolate_local_storage_db(tmp_path, monkeypatch):
    """Point the local relational store at a per-test SQLite file, never the developer's."""
    import cuga.backend.storage.facade as facade

    monkeypatch.setattr(facade, "_local_db_path", lambda: str(tmp_path / "cuga.db"))
    monkeypatch.setattr(facade, "_storage_mode", lambda: "local")
    facade.get_storage().invalidate_relational_stores()
    yield
    facade.get_storage().invalidate_relational_stores()


class DurableCheckpoints:
    """The durable checkpointer on one SQLite file. Each ``open()`` is a process start."""

    def __init__(self, db_path: Path):
        self.storage = SimpleNamespace(checkpointer="durable", mode="local", checkpoint_db_path=str(db_path))
        self.handle: CheckpointerHandle | None = None

    async def open(self) -> CheckpointerHandle:
        await self.close()
        self.handle = await create_checkpointer(self.storage)
        assert self.handle.kind == "sqlite"
        return self.handle

    async def close(self) -> None:
        if self.handle is not None:
            await self.handle.aclose()
            self.handle = None


@pytest.fixture
async def checkpoints(tmp_path):
    durable = DurableCheckpoints(tmp_path / "checkpoints.db")
    yield durable
    await durable.close()


def build_turn_graph(saver):
    """Answer with a fake chat model; record the turn in the messages and in a variable.

    The variable (``{"turn": n}``) lets a test tell which turns a restored checkpoint holds.
    """
    model = FakeListChatModel(responses=[ANSWER])

    async def final_answer(state: AgentState):
        reply = (await model.ainvoke(state.input)).content
        turn = len(state.chat_messages or []) // 2 + 1
        state.variables_manager.add_variable({"turn": turn}, description=f"recorded on turn {turn}")
        return {
            "final_answer": reply,
            "messages": [AIMessage(content=reply)],
            "chat_messages": [*(state.chat_messages or []), HumanMessage(state.input), AIMessage(reply)],
            "variables_storage": state.variables_storage,
            "variable_counter_state": state.variable_counter_state,
            "variable_creation_order": state.variable_creation_order,
        }

    builder = StateGraph(AgentState)
    builder.add_node("FinalAnswerAgent", final_answer)
    builder.add_edge(START, "FinalAnswerAgent")
    builder.add_edge("FinalAnswerAgent", END)
    return builder.compile(checkpointer=saver)


class ConversationServer:
    """The conversation routes over a durable checkpointer, restartable on the same files."""

    answer = ANSWER
    agent_id = AGENT_ID

    def __init__(self, checkpoints: DurableCheckpoints, state: SimpleNamespace, main: Any):
        self._checkpoints = checkpoints
        self.state = state
        self.main = main
        self.user = {"sub": USER_ID}
        self.graph_factory: Callable[[Any], Any] = build_turn_graph  # saver -> compiled graph
        self.graph = None
        self.db = None
        self.client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        from cuga.backend.server.conversation_history import ConversationHistoryDB

        handle = await self._checkpoints.open()
        self.graph = self.graph_factory(handle.saver)
        self.db = ConversationHistoryDB()
        self.state.agent = SimpleNamespace(graph=self.graph, policy_system=None, chat=None)
        self.state.stop_events = {}
        self.state.checkpointer_handle = handle

        app = FastAPI()
        app.state.app_state = self.state
        app.add_api_route("/stream", self.main.stream, methods=["POST"])
        app.add_api_route("/api/conversations", self.main.get_conversations, methods=["GET"])
        app.add_api_route("/api/conversations", self.main.create_conversation, methods=["POST"])
        app.dependency_overrides[self.main.require_chat_access] = lambda: SimpleNamespace(
            sub=self.user["sub"]
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def stop(self) -> None:
        from cuga.backend.storage.facade import get_storage

        if self.client is not None:
            await self.client.aclose()
            self.client = None
        await self._checkpoints.close()
        await get_storage().close_relational_stores()

    async def restart(self) -> None:
        """Drop the app, graph, saver and stores, then rebuild them from the files on disk."""
        await self.stop()
        await self.start()

    async def create(self) -> str:
        response = await self.client.post("/api/conversations", json={})
        assert response.status_code == 200, response.text
        return response.json()["id"]

    async def send(self, thread_id: str | None, query: str = "hello") -> httpx.Response:
        headers = {"X-Thread-ID": thread_id} if thread_id else {}
        return await self.client.post("/stream", headers=headers, json={"query": query})

    async def listing(self) -> list[dict]:
        response = await self.client.get("/api/conversations")
        assert response.status_code == 200, response.text
        return response.json()

    def config(self, thread_id: str) -> dict:
        key = self.main._checkpoint_thread_id(thread_id, self.user["sub"], AGENT_ID)
        return {"configurable": {"thread_id": key}}

    async def checkpoint_values(self, thread_id: str) -> dict:
        return (await self.graph.aget_state(self.config(thread_id))).values

    @staticmethod
    def event_names(response: httpx.Response) -> list[str]:
        return [
            line.split(":", 1)[1].strip() for line in response.text.splitlines() if line.startswith("event:")
        ]


@pytest.fixture
async def conversation_server(checkpoints, monkeypatch):
    from cuga.backend.server import main

    state = SimpleNamespace(
        agent_id=AGENT_ID,
        current_llm=None,
        output_format=None,
        knowledge_provider=None,
    )
    server = ConversationServer(checkpoints, state, main)
    monkeypatch.setattr(main, "get_conversation_db", lambda: server.db)
    monkeypatch.setattr(main, "app_state", state)
    monkeypatch.setattr(main.agent_registry, "is_agent_registry_enabled", lambda: False)
    monkeypatch.setattr(main.events_bridge, "forwards_to_events", lambda *args: False)
    monkeypatch.setattr(main, "_knowledge_enabled_for_app_state", lambda _: False)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "_dispatch_slash_for_stream", AsyncMock(return_value=None))
    monkeypatch.setattr(main.settings.evolve, "enabled", False)
    monkeypatch.setattr(main.settings.advanced_features, "mode", "api")
    monkeypatch.setattr(main.settings.advanced_features, "langfuse_tracing", False)

    await server.start()
    try:
        yield server
    finally:
        await server.stop()
        await asyncio.sleep(0)  # let the closed aiosqlite worker thread finish
