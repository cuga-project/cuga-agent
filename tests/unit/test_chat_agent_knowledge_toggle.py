from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent import ChatAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.knowledge.client import KnowledgeClient


@tool
def base_chat_tool() -> str:
    """Base chat tool."""
    return "ok"


@pytest.mark.asyncio
async def test_chat_agent_skips_knowledge_runtime_tools_when_disabled(monkeypatch):
    fake_backend_app = SimpleNamespace(
        state=SimpleNamespace(
            app_state=SimpleNamespace(
                knowledge_engine=SimpleNamespace(_config=SimpleNamespace(enabled=False)),
                agent_id="cuga-default",
            )
        )
    )
    monkeypatch.setitem(sys.modules, "cuga.backend.server.main", SimpleNamespace(app=fake_backend_app))

    agent = ChatAgent()
    agent.base_tools = [base_chat_tool]

    async def _get_apps():
        return []

    monkeypatch.setattr(agent.tool_provider, "get_apps", _get_apps)

    state = AgentState(input="Hello", url="", thread_id="thread-123")
    runtime_tools, prompt_inputs = await agent._build_runtime_context(state)

    assert [tool.name for tool in runtime_tools] == ["base_chat_tool"]
    assert prompt_inputs["knowledge_block"] == ""
    assert prompt_inputs["knowledge_instructions"] == ""


def test_knowledge_client_rejects_disabled_session_scope():
    engine = SimpleNamespace(
        _config=SimpleNamespace(
            enabled=True,
            agent_level_enabled=True,
            session_level_enabled=False,
            default_limit=10,
            default_score_threshold=0.0,
        )
    )
    client = KnowledgeClient(engine, default_agent_id="cuga-default")

    assert client.allowed_scopes() == ("agent",)
    with pytest.raises(ValueError, match="Session-level knowledge is disabled"):
        client._resolve_collection("session", thread_id="thread-123")


def test_knowledge_client_uses_agent_collection_hash_when_provided():
    engine = SimpleNamespace(
        _config=SimpleNamespace(
            enabled=True,
            agent_level_enabled=True,
            session_level_enabled=True,
            default_limit=10,
            default_score_threshold=0.0,
        )
    )
    client = KnowledgeClient(
        engine,
        default_agent_id="cuga-default",
        agent_collection_hash="abc123",
    )

    assert client._resolve_collection("agent") == "kb_agent_cuga_default_abc123"
