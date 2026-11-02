"""Delegated ToolGuard calls must bind raw knowledge tools to trusted context."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.knowledge import _wrap_knowledge_tool
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
from cuga.backend.cuga_graph.policy.tool_guard.tool_invoker import ToolGuardInvoker
from cuga.backend.knowledge.client import KnowledgeClient
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.run_context import KnowledgeRunContext, current_run_token, issue_run_token

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_SCOPED_CALLS = [
    ("knowledge_search_knowledge", {"query": "q"}, "search_envelope"),
    ("knowledge_ingest_knowledge", {"file_path": "report.txt"}, "ingest"),
    ("knowledge_ingest_knowledge_url", {"url": "https://example.com"}, "ingest_url"),
    ("knowledge_list_knowledge_documents", {}, "list_documents"),
    ("knowledge_delete_knowledge_document", {"filename": "report.txt"}, "delete_document"),
]


def _stack(captured_thread=None):
    engine = SimpleNamespace(
        _config=KnowledgeConfig(enabled=True, agent_level_enabled=True, session_level_enabled=True),
        get_task=AsyncMock(return_value={"status": "completed"}),
        health=AsyncMock(return_value={"status": "healthy"}),
        get_settings=Mock(return_value={}),
    )
    client = KnowledgeClient(engine)
    for method in ("search_envelope", "ingest", "ingest_url", "list_documents", "delete_document"):
        setattr(client, method, AsyncMock(return_value=[] if method == "list_documents" else {"ok": True}))
    provider = ToolGuardingToolProvider(
        DirectLangChainToolsProvider(client.get_langchain_tools(thread_id=captured_thread))
    )
    return client, provider, ToolGuardInvoker(provider)


@pytest.mark.parametrize(("tool_name", "arguments", "method"), _SCOPED_CALLS)
@pytest.mark.parametrize("context", ["runtime", "no-thread", "no-run"])
async def test_raw_knowledge_invocation_replaces_supplied_identity(tool_name, arguments, method, context):
    client, _, invoker = _stack()
    run = KnowledgeRunContext("selected-agent", "mine" if context == "runtime" else None, ("agent",))
    token = current_run_token.set(issue_run_token(run) if context != "no-run" else None)
    supplied = {**arguments, "scope": "agent", "thread_id": "victim", "agent_id": "victim-agent"}
    try:
        result = await invoker.invoke(tool_name, supplied, dict)
    finally:
        current_run_token.reset(token)

    assert "error" not in result
    assert getattr(client, method).await_args.kwargs["thread_id"] == ("mine" if context == "runtime" else "")
    assert supplied["thread_id"] == "victim"
    assert supplied["agent_id"] == "victim-agent"


@pytest.mark.parametrize(("tool_name", "arguments", "method"), _SCOPED_CALLS)
async def test_raw_knowledge_invocation_enforces_runtime_scope(tool_name, arguments, method):
    client, _, invoker = _stack()
    token = current_run_token.set(issue_run_token(KnowledgeRunContext("agent", "mine", ("agent",))))
    try:
        result = await invoker.invoke(tool_name, {**arguments, "scope": "session"}, dict)
    finally:
        current_run_token.reset(token)

    assert result == {
        "error": "Knowledge scope 'session' is unavailable in this context. Allowed scopes: agent"
    }
    getattr(client, method).assert_not_awaited()


@pytest.mark.parametrize(("tool_name", "arguments", "method"), _SCOPED_CALLS)
async def test_raw_knowledge_invocation_defaults_to_permitted_runtime_scope(tool_name, arguments, method):
    client, _, invoker = _stack()
    token = current_run_token.set(issue_run_token(KnowledgeRunContext("agent", "mine", ("session",))))
    try:
        result = await invoker.invoke(tool_name, arguments, dict)
    finally:
        current_run_token.reset(token)

    assert "error" not in result
    assert getattr(client, method).await_args.args[1 if method != "list_documents" else 0] == "session"
    assert getattr(client, method).await_args.kwargs["thread_id"] == "mine"


async def test_raw_knowledge_invocation_without_run_preserves_captured_context():
    client, _, invoker = _stack(captured_thread="captured")
    result = await invoker.invoke("knowledge_search_knowledge", {"query": "q", "thread_id": "victim"}, dict)
    assert "error" not in result
    assert client.search_envelope.await_args.kwargs["thread_id"] == "captured"


async def test_raw_knowledge_invocation_rejects_omitted_scope_when_none_are_allowed():
    client, _, invoker = _stack()
    token = current_run_token.set(issue_run_token(KnowledgeRunContext("agent", None, ())))
    try:
        result = await invoker.invoke("knowledge_search_knowledge", {"query": "q"}, dict)
    finally:
        current_run_token.reset(token)

    assert "unavailable in this context" in result["error"]
    client.search_envelope.assert_not_awaited()


async def test_guard_delegate_uses_runtime_context_for_raw_tools():
    client, provider, invoker = _stack()

    async def guard(**kwargs):
        await invoker.invoke(
            "knowledge_delete_knowledge_document",
            {"filename": "report.txt", "scope": "session", "thread_id": "victim"},
            dict,
        )

    provider._get_or_create_toolguard_runtime = AsyncMock(
        return_value=SimpleNamespace(guard_tool_call=AsyncMock(side_effect=guard))
    )
    guarded = next(
        tool for tool in await provider.get_all_tools() if tool.name == "knowledge_search_knowledge"
    )
    run = KnowledgeRunContext("selected-agent", "mine", ("agent", "session"))
    wrapped = _wrap_knowledge_tool(guarded.coroutine, "mine", run.scopes, "session", knowledge_run=run)

    assert await wrapped(query="q", thread_id="victim") == {"ok": True}
    assert client.delete_document.await_args.kwargs["thread_id"] == "mine"
    assert client.search_envelope.await_args.kwargs["thread_id"] == "mine"
    assert current_run_token.get() is None


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("knowledge_get_ingestion_status", {"task_id": "task-1"}),
        ("knowledge_get_knowledge_status", {}),
    ],
)
async def test_raw_status_tools_do_not_receive_identity_arguments(tool_name, arguments):
    client, _, invoker = _stack()
    token = current_run_token.set(issue_run_token(KnowledgeRunContext("agent", "mine", ("session",))))
    try:
        result = await invoker.invoke(
            tool_name, {**arguments, "thread_id": "victim", "agent_id": "victim"}, dict
        )
    finally:
        current_run_token.reset(token)

    assert "error" not in result
    assert client._engine.get_task.await_count + client._engine.health.await_count == 1


async def test_raw_knowledge_invocation_rejects_invalid_runtime_capability():
    client, _, invoker = _stack()
    token = current_run_token.set("invalid")
    try:
        with pytest.raises(ValueError, match="Invalid or expired knowledge run capability"):
            await invoker.invoke("knowledge_search_knowledge", {"query": "q", "thread_id": "victim"}, dict)
    finally:
        current_run_token.reset(token)

    client.search_envelope.assert_not_awaited()
