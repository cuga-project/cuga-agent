"""Runtime knowledge context must survive ToolGuard and stay out of prompts."""

from types import SimpleNamespace
from typing import Annotated, Literal
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.tools import InjectedToolArg, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import ShortlistCandidate
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.doc import tool_document
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.render import render_tools_markdown
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.schema import model_tool_schema
from cuga.backend.knowledge.client import KnowledgeClient
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import SearchResult, _JunkFilterStats
from cuga.backend.knowledge.sources import _reset_all_ledgers_for_tests, get_ledger

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _client():
    engine = SimpleNamespace(
        _config=KnowledgeConfig(enabled=True, agent_level_enabled=True, session_level_enabled=True),
        get_task=AsyncMock(return_value={"status": "completed"}),
        health=AsyncMock(return_value={"status": "healthy"}),
        get_settings=Mock(return_value={}),
    )
    client = KnowledgeClient(engine)
    for method in ("search_envelope", "ingest", "ingest_url", "list_documents", "delete_document"):
        setattr(client, method, AsyncMock(return_value=[] if method == "list_documents" else {"ok": True}))
    return client


def _guard(client, tool_name, *, captured_thread="construction-thread", blocked=False):
    tools = client.get_langchain_tools(thread_id=captured_thread)
    provider = ToolGuardingToolProvider(DirectLangChainToolsProvider(tools), policy_storage=None)
    runtime = SimpleNamespace(guard_tool_call=AsyncMock(return_value="denied" if blocked else None))
    provider._get_or_create_toolguard_runtime = AsyncMock(return_value=runtime)
    raw = next(tool for tool in tools if tool.name == tool_name)
    return provider._wrap_tool(raw, "runtime_tools"), runtime


@pytest.mark.parametrize(
    ("tool_name", "arguments", "method"),
    [
        ("knowledge_search_knowledge", {"query": "q", "scope": "session"}, "search_envelope"),
        ("knowledge_ingest_knowledge", {"file_path": "report.txt", "scope": "session"}, "ingest"),
        ("knowledge_ingest_knowledge_url", {"url": "https://example.com", "scope": "session"}, "ingest_url"),
        ("knowledge_list_knowledge_documents", {"scope": "session"}, "list_documents"),
        (
            "knowledge_delete_knowledge_document",
            {"filename": "report.txt", "scope": "session"},
            "delete_document",
        ),
        ("knowledge_get_ingestion_status", {"task_id": "task-1"}, None),
        ("knowledge_get_knowledge_status", {}, None),
    ],
)
async def test_guarded_knowledge_tools_accept_runtime_context(tool_name, arguments, method):
    client = _client()
    tool, runtime = _guard(client, tool_name)
    # The local adapter calls the guarded coroutine directly with injected kwargs.
    result = await tool.coroutine(**arguments, thread_id="runtime-thread")
    assert "error" not in result
    assert runtime.guard_tool_call.await_args.kwargs["arguments"]["thread_id"] == "runtime-thread"
    if method:
        assert getattr(client, method).await_args.kwargs["thread_id"] == "runtime-thread"
    else:
        assert client._engine.get_task.await_count + client._engine.health.await_count == 1
    assert "thread_id" not in tool.tool_call_schema.model_json_schema()["properties"]
    assert "thread_id" not in PromptUtils.get_tool_params_str(tool)
    assert "thread_id" not in PromptUtils.get_tool_docs(tool)[0]
    payload, _ = PromptUtils._build_shortlister_payload([tool], [])
    assert "thread_id" not in payload[tool_name]["args_schema"]["properties"]
    markdown = render_tools_markdown([ShortlistCandidate(name=tool_name)], [tool], "knowledge")
    assert "thread_id" not in markdown
    assert "thread id" not in tool_document(tool)


async def test_guarded_search_uses_construction_context_without_runtime_override():
    client = _client()
    tool, _ = _guard(client, "knowledge_search_knowledge")
    await tool.ainvoke({"query": "q"})
    assert client.search_envelope.await_args.kwargs["thread_id"] == "construction-thread"


async def test_guarded_search_keeps_unknown_argument_validation():
    client = _client()
    tool, runtime = _guard(client, "knowledge_search_knowledge")
    result = await tool.coroutine(query="q", thread_id="runtime-thread", unexpected="bad")
    assert result == {"error": "Unexpected argument(s) for knowledge_search_knowledge: unexpected"}
    runtime.guard_tool_call.assert_not_awaited()
    client.search_envelope.assert_not_awaited()


async def test_guarded_search_still_enforces_policy():
    client = _client()
    tool, runtime = _guard(client, "knowledge_search_knowledge", blocked=True)
    result = await tool.coroutine(query="q", thread_id="runtime-thread")
    assert result["blocked_by_policy"] is True
    runtime.guard_tool_call.assert_awaited_once()
    client.search_envelope.assert_not_awaited()


@pytest.mark.parametrize("scope", ["agent", "session"])
async def test_guarded_search_registers_citations_in_runtime_thread(scope):
    _reset_all_ledgers_for_tests()
    try:
        client = _client()
        # Use the real search envelope and citation code, mocking only retrieval.
        del client.search_envelope
        hit = SearchResult(text="Q4 revenue was 42 million", filename="report.txt", page=1, score=0.9)
        client._engine.search_with_stats = AsyncMock(return_value=([hit], _JunkFilterStats(candidates=1)))
        tool, _ = _guard(client, "knowledge_search_knowledge", captured_thread=None)
        result = await tool.coroutine(query="Q4 revenue", scope=scope, thread_id="runtime-thread")
        assert result["results"][0]["cite_id"] == "s1"
        assert len(get_ledger("runtime-thread")) == 1
        assert len(get_ledger("other-thread")) == 0
        expected_collection = "kb_sess_runtime_thread" if scope == "session" else "kb_agent_default"
        assert client._engine.search_with_stats.await_args.kwargs["collection"] == expected_collection
    finally:
        _reset_all_ledgers_for_tests()


async def test_model_schema_preserves_argument_constraints():
    async def constrained_tool(
        scope: Literal["agent", "session"],
        limit: Annotated[int, Field(ge=1, le=10)],
        thread_id: Annotated[str | None, InjectedToolArg] = None,
    ):
        """Search with a bounded result limit."""

    tool = StructuredTool.from_function(coroutine=constrained_tool)
    schema = model_tool_schema(tool)
    assert schema["required"] == ["scope", "limit"]
    assert schema["properties"]["scope"]["enum"] == ["agent", "session"]
    assert schema["properties"]["limit"]["minimum"] == 1
    assert schema["properties"]["limit"]["maximum"] == 10
    assert "thread_id" not in schema["properties"]


async def test_model_schema_preserves_field_and_model_metadata():
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")
        value: int = Field(alias="amount", json_schema_extra={"multipleOf": 3})
        thread_id: Annotated[str, InjectedToolArg] = Field(alias="context_id")

    async def constrained_tool(**kwargs):
        """Tool with custom schema metadata."""

    tool = StructuredTool.from_function(coroutine=constrained_tool, args_schema=Args)
    schema = model_tool_schema(tool)
    assert schema["properties"]["amount"]["multipleOf"] == 3
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["amount"]
    assert "context_id" not in schema["properties"]
    assert "thread_id" not in schema["properties"]
