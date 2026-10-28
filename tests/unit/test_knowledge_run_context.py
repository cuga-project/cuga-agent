"""Exercise registry -> real MCP metadata -> backend auth and knowledge routes."""

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastmcp import Client, Context, FastMCP
from langchain_core.messages import HumanMessage

from cuga.backend.knowledge import mcp_server
from cuga.backend.knowledge.routes import knowledge_router
from cuga.backend.knowledge.run_context import (
    KnowledgeRunContext,
    RUN_TOKEN_HEADER,
    RUN_TOKEN_META,
    current_run_token,
    issue_run_token,
    verify_run_token,
)

pytestmark = pytest.mark.unit


def capability(agent="named-agent", thread="real-thread", scopes=("agent", "session"), user=None):
    return issue_run_token(KnowledgeRunContext(agent, thread, scopes, user))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata,expected",
    [
        (None, {}),
        ({}, {}),
        ({RUN_TOKEN_META: "run-credential"}, {RUN_TOKEN_HEADER: "run-credential"}),
        ({RUN_TOKEN_META: 42}, {}),
    ],
)
async def test_identity_headers_use_real_mcp_metadata(metadata, expected):
    server = FastMCP("Knowledge metadata regression")

    @server.tool()
    def identity_headers(ctx: Context) -> dict[str, str]:
        # Exercise the SDK's actual request metadata, not a fabricated context.
        assert ctx.request_context is not None
        assert ctx.request_context.meta is None or isinstance(ctx.request_context.meta, dict)
        return mcp_server._identity_headers(ctx)

    async with Client(server) as client:
        result = await client.call_tool("identity_headers", {}, meta=metadata)
    assert result.data == expected


@pytest.fixture
def sessions():
    from cuga.backend.knowledge.session_provider import SessionProvider

    return SessionProvider()


@pytest_asyncio.fixture
async def stack(monkeypatch, sessions):
    from cuga.backend.server.auth import dependencies
    from cuga.backend.tools_env.registry.registry import api_registry_server as server
    from cuga.backend.tools_env.registry.registry.api_registry import ApiRegistry
    from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager

    # Auth disabled externally must not turn invalid internal auth into a bypass.
    monkeypatch.setattr(dependencies, "_auth_enabled", lambda: False)
    engine = MagicMock()
    engine._config = SimpleNamespace(enabled=True, agent_level_enabled=True, session_level_enabled=True)
    engine.list_documents = AsyncMock(return_value=[])
    engine.delete_document = AsyncMock(return_value={"deleted": True})
    engine.ingest_url = AsyncMock(return_value={"status": "completed"})
    backend = FastAPI()
    backend.state.app_state = SimpleNamespace(
        internal_token="machine-token",
        agent_id="cuga-default",
        knowledge_engine=engine,
        knowledge_provider=sessions,
        knowledge_config_hash=None,
    )
    backend.include_router(knowledge_router)
    transport = httpx.ASGITransport(app=backend, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://backend") as backend_client:
        monkeypatch.setattr(mcp_server, "_get_client", lambda: backend_client)
        monkeypatch.setattr(mcp_server, "_get_token", lambda: "machine-token")
        manager = MCPManager(config={})
        names = (
            "list_knowledge_documents",
            "delete_knowledge_document",
            "ingest_knowledge_url",
            "get_ingestion_status",
            "get_knowledge_status",
            "ingest_knowledge",
        )
        manager.server_by_tool = {f"knowledge_{name}": "knowledge" for name in names}
        manager.original_tool_name_by_sanitized = {f"knowledge_{name}": name for name in names}
        manager.mcp_clients = {"knowledge": "in-memory"}
        manager.mcp_transports = {"knowledge": mcp_server.mcp}
        monkeypatch.setattr(manager, "get_apis_for_application", lambda *a, **kw: {})
        monkeypatch.setattr(server, "registry", ApiRegistry(manager), raising=False)
        monkeypatch.setattr(server, "mcp_manager", manager, raising=False)
        monkeypatch.setattr(server, "database_mode", False)
        monkeypatch.setattr(server.rejected_call_guard, "check", lambda *a, **kw: None)
        monkeypatch.setattr(server.tracker, "collect_step_external", lambda *a, **kw: None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://registry"
        ) as registry:
            yield registry, backend_client, engine


async def call(registry, name="list_knowledge_documents", token=None, **args):
    return await registry.post(
        "/functions/call",
        json={
            "app_name": "knowledge",
            "function_name": f"knowledge_{name}",
            "args": args,
            "knowledge_run_token": token,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "forged", "machine-token"])
async def test_registry_bypass_rejected(stack, token):
    registry, _, engine = stack
    response = await call(registry, token=token, scope="session", thread_id="victim")
    # Existing registry transport represents MCP tool failures as HTTP 200 text.
    assert response.status_code >= 400 or "403 Forbidden" in response.text, response.text
    engine.list_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_named_agent_and_session_survive_real_metadata_transport(stack):
    registry, _, engine = stack
    for scope, expected in (("agent", "kb_agent_named_agent"), ("session", "kb_sess_real_thread")):
        response = await call(registry, token=capability(), scope=scope, thread_id="victim")
        assert response.status_code == 200, response.text
        engine.list_documents.assert_awaited_with(expected)


@pytest.mark.asyncio
async def test_named_agent_writes_and_deletes_stay_in_own_collection(stack):
    registry, _, engine = stack
    response = await call(
        registry, "ingest_knowledge_url", capability(), scope="agent", url="https://example.test/doc"
    )
    assert response.status_code == 200, response.text
    engine.ingest_url.assert_awaited_with("kb_agent_named_agent", "https://example.test/doc")
    response = await call(
        registry,
        "delete_knowledge_document",
        capability(),
        scope="session",
        filename="doc.txt",
        thread_id="victim",
    )
    assert response.status_code == 200, response.text
    assert engine.delete_document.await_args.args[:2] == ("kb_sess_real_thread", "doc.txt")


@pytest.mark.asyncio
async def test_parallel_runs_do_not_share_identity(stack):
    registry, _, engine = stack
    responses = await asyncio.gather(
        *(
            call(registry, token=capability(agent=f"agent{i}", thread=f"thread{i}"), scope="session")
            for i in range(4)
        )
    )
    assert all(r.status_code == 200 for r in responses)
    assert {c.args[0] for c in engine.list_documents.await_args_list} == {
        f"kb_sess_thread{i}" for i in range(4)
    }


@pytest.mark.asyncio
async def test_internal_auth_never_falls_back_to_external_identity(stack):
    _, backend, engine = stack
    for machine_token, cap in (("wrong", capability()), ("machine-token", "forged")):
        response = await backend.get(
            "/api/knowledge/documents",
            params={"scope": "agent"},
            headers={
                "X-Internal-Token": machine_token,
                RUN_TOKEN_HEADER: cap,
                "X-Agent-ID": "victim",
            },
        )
        assert response.status_code in (401, 403)
    engine.list_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_uses_verified_workspace_before_opening_file(stack, monkeypatch):
    registry, _, _ = stack
    resolver = MagicMock(side_effect=FileNotFoundError("test upload"))
    monkeypatch.setattr(mcp_server, "_resolve_ingest_file_path", resolver)
    await call(registry, "ingest_knowledge", "forged", file_path="doc.txt", thread_id="victim")
    resolver.assert_not_called()
    await call(registry, "ingest_knowledge", capability(), file_path="doc.txt", thread_id="victim")
    resolver.assert_called_once_with("doc.txt", thread_id="real-thread")


@pytest.mark.asyncio
async def test_capability_limits_scopes_and_status_task_ownership(stack):
    registry, _, engine = stack
    response = await call(registry, token=capability(scopes=("session",)), scope="agent")
    assert response.status_code >= 400 or "403 Forbidden" in response.text
    engine.list_documents.assert_not_awaited()
    engine.get_task = AsyncMock(return_value={"collection": "kb_sess_victim"})
    response = await call(registry, "get_ingestion_status", capability(), task_id="victim-task")
    assert response.status_code >= 400 or "403 Forbidden" in response.text


def test_capability_tampering_and_expiry_fail_closed(monkeypatch):
    from cuga.backend.knowledge import run_context

    token = capability()
    assert verify_run_token(token).agent_id == "named-agent"
    with pytest.raises(ValueError):
        verify_run_token(token[:-1] + ("0" if token[-1] != "0" else "1"))
    now = run_context.time.time()
    monkeypatch.setattr(run_context.time, "time", lambda: now + run_context._TOKEN_LIFETIME + 1)
    with pytest.raises(ValueError):
        verify_run_token(token)


def test_agent_loop_propagates_selected_agent_to_graph():
    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop

    graph = MagicMock()
    loop = AgentLoop(
        thread_id="real-thread",
        langfuse_handler=None,
        graph=graph,
        tracker=MagicMock(),
        agent_id="named-agent",
    )
    loop.get_stream({"input": "list documents"})
    config = graph.astream.call_args.kwargs["config"]["configurable"]
    assert config["knowledge_agent_id"] == "named-agent"
    assert config["thread_id"] == "real-thread"


@pytest.mark.asyncio
async def test_external_oidc_identity_still_selects_named_agent(stack, monkeypatch):
    from cuga.backend.server.auth import dependencies

    _, backend, engine = stack
    monkeypatch.setattr(dependencies, "_auth_enabled", lambda: True)
    monkeypatch.setattr(
        dependencies,
        "get_current_user",
        AsyncMock(return_value=SimpleNamespace(sub="user", tenant_id="tenant", roles=["ServiceOwner"])),
    )
    response = await backend.get(
        "/api/knowledge/documents", params={"scope": "agent"}, headers={"X-Agent-ID": "named-agent"}
    )
    assert response.status_code == 200
    engine.list_documents.assert_awaited_with("kb_agent_named_agent")


@pytest.mark.asyncio
async def test_credentials_stay_outside_tool_schemas_and_request_traces():
    from cuga.backend.tools_env.registry.registry.api_registry_server import FunctionCallRequest

    request = FunctionCallRequest(
        app_name="knowledge", function_name="tool", args={}, knowledge_run_token="secret"
    )
    assert "secret" not in request.model_dump_json()
    assert "secret" not in repr(request)
    async with Client(mcp_server.mcp) as client:
        for tool in await client.list_tools():
            assert "ctx" not in tool.inputSchema.get("properties", {})
            assert "knowledge_run_token" not in tool.inputSchema.get("properties", {})


@pytest.mark.asyncio
async def test_prepared_registry_tool_uses_selected_agent_for_calls_and_prompt(stack, monkeypatch, sessions):
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers import registry as provider

    registry, _, engine = stack
    captured = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def post(self, url, *, json, **kwargs):
            captured.append(json)

            class Response:
                async def __aenter__(self):
                    response = await registry.post("/functions/call", json=json)
                    return SimpleNamespace(
                        status=response.status_code, text=AsyncMock(return_value=response.text)
                    )

                async def __aexit__(self, *args):
                    pass

            return Response()

    monkeypatch.setattr(provider.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled", False
    )
    awareness = AsyncMock(
        return_value=SimpleNamespace(
            has_knowledge=False, knowledge_block_chars=0, contract_chars=0, prompt_hash="test"
        )
    )
    monkeypatch.setattr("cuga.backend.knowledge.awareness.assemble_system_prompt_section", awareness)
    tool = provider.create_tool_from_api_dict(
        "knowledge_list_knowledge_documents",
        {"parameters": {"properties": {"scope": {"type": "string"}, "thread_id": {"type": "string"}}}},
        "knowledge",
        agent_id="named-agent",
    )
    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._task_todos_ref = []
    adapter._thread_id = "real-thread"
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._base_tool_provider.get_all_tools = AsyncMock(return_value=[tool])
    adapter._base_tool_provider.get_apps = AsyncMock(return_value=[])
    adapter._base_tool_provider.get_tools = AsyncMock(return_value=[])
    adapter._prompt_template.invoke.return_value.to_string.return_value = ""
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="list documents")],
        task_todos=None,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
        thread_id="real-thread",
    )
    node = create_prepare_tools_and_apps_node(adapter, {})
    await node(
        state,
        config={
            "configurable": {
                "knowledge_agent_id": "named-agent",
                "knowledge_user_id": "alice",
                "thread_id": "real-thread",
                "knowledge_engine": engine,
                "knowledge_provider": sessions,
                "enable_todos": False,
                "shortlisting_tool_threshold": 35,
            }
        },
    )
    assert awareness.await_args.args[1:3] == ("named-agent", "real-thread")
    result = await adapter._tools_context[tool.name](scope="agent", thread_id="victim", agent_id="victim")
    assert result == {"documents": []}
    engine.list_documents.assert_awaited_with("kb_agent_named_agent")
    run = verify_run_token(captured[0]["knowledge_run_token"])
    assert (run.agent_id, run.user_id) == ("named-agent", "alice")
    assert "knowledge_run_token" not in captured[0]["args"]
    assert current_run_token.get() is None


@pytest.mark.asyncio
async def test_e2b_stub_forwards_capability_outside_arguments(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common import CallApiHelper
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.e2b.e2b_executor import E2BExecutor
    import urllib.request

    sent = []

    def request(req, **kwargs):
        sent.append(json.loads(req.data))
        return io.BytesIO(b'{}')

    monkeypatch.setattr(urllib.request, "urlopen", request)
    tool = SimpleNamespace(
        _knowledge_allowed_scopes=("agent", "session"),
        _knowledge_default_scope="session",
        _knowledge_thread_id="real-thread",
        _knowledge_run_context=KnowledgeRunContext("named-agent", "real-thread", ("agent", "session")),
    )
    source = CallApiHelper.create_remote_call_api_code("http://registry")
    source += E2BExecutor()._serialize_knowledge_tool_stub("knowledge_list_knowledge_documents", tool)
    namespace = {}
    exec(source, namespace)
    await namespace["knowledge_list_knowledge_documents"](scope="session", thread_id="victim")
    assert sent[0]["args"] == {"scope": "session", "thread_id": "real-thread"}
    identity = verify_run_token(sent[0]["knowledge_run_token"])
    assert (identity.agent_id, identity.thread_id) == ("named-agent", "real-thread")


# --- Session ownership -------------------------------------------------------
# The ownership check used to require a tenant, which OIDC users never carry, so
# it never ran: any logged-in user could name another user's thread directly or
# through a run. These tests pin the check for both modes and pin that it stays
# off when auth is disabled.


def _login(monkeypatch):
    """Enable auth; the caller is whoever the ``X-Test-User`` header names."""
    from cuga.backend.server.auth import dependencies

    async def current_user(request):
        return SimpleNamespace(sub=request.headers["X-Test-User"], roles=None)

    monkeypatch.setattr(dependencies, "_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies, "get_current_user", current_user)


async def list_session(backend, user=None, thread="t1"):
    headers = {"X-Agent-ID": "named-agent", "X-Thread-ID": thread}
    if user:
        headers["X-Test-User"] = user
    return await backend.get("/api/knowledge/documents", params={"scope": "session"}, headers=headers)


@pytest.mark.asyncio
async def test_logged_in_user_cannot_reach_another_users_session(stack, monkeypatch):
    _, backend, engine = stack
    _login(monkeypatch)
    assert (await list_session(backend, "alice")).status_code == 200
    assert (await list_session(backend, "bob")).status_code == 403
    assert (await list_session(backend, "alice")).status_code == 200
    assert engine.list_documents.await_count == 2


@pytest.mark.asyncio
async def test_session_settings_routes_enforce_owner(stack, monkeypatch, sessions):
    _, backend, _ = stack
    _login(monkeypatch)
    sessions.claim_session("t1", "alice")
    for method in ("GET", "PATCH"):
        response = await backend.request(
            method,
            "/api/knowledge/session/settings",
            headers={"X-Agent-ID": "named-agent", "X-Thread-ID": "t1", "X-Test-User": "bob"},
            **({"json": {}} if method == "PATCH" else {}),
        )
        assert response.status_code == 403, (method, response.text)


@pytest.mark.asyncio
async def test_run_capability_carries_owner_check(stack, sessions):
    registry, _, engine = stack
    sessions.claim_session("t1", "alice")
    response = await call(registry, token=capability(thread="t1", user="bob"), scope="session")
    assert response.status_code >= 400 or "403 Forbidden" in response.text
    engine.list_documents.assert_not_awaited()
    response = await call(registry, token=capability(thread="t1", user="alice"), scope="session")
    assert response.status_code == 200, response.text
    engine.list_documents.assert_awaited_with("kb_sess_t1")


@pytest.mark.asyncio
async def test_ownerless_session_is_claimed_by_first_user(stack, monkeypatch, sessions):
    from cuga.backend.knowledge.session_provider import SessionKnowledgeState

    _, backend, _ = stack
    _login(monkeypatch)
    sessions.save_session("t1", SessionKnowledgeState(thread_id="t1"))  # pre-fix record, no owner
    assert (await list_session(backend, "alice")).status_code == 200
    assert sessions.get_session("t1").user_id == "alice"
    assert (await list_session(backend, "bob")).status_code == 403


@pytest.mark.asyncio
async def test_auth_disabled_leaves_ownership_off(stack, sessions):
    registry, backend, engine = stack
    sessions.claim_session("t1", "alice")
    assert (await list_session(backend)).status_code == 200
    response = await call(registry, token=capability(thread="t1"), scope="session")
    assert response.status_code == 200, response.text
    assert engine.list_documents.await_count == 2


def test_agent_loop_propagates_authenticated_user_to_graph():
    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop

    graph = MagicMock()
    for user, expected in (("alice", "alice"), (None, None)):
        loop = AgentLoop(
            thread_id="t1",
            langfuse_handler=None,
            graph=graph,
            tracker=MagicMock(),
            agent_id="named-agent",
            knowledge_user_id=user,
        )
        loop.get_stream({"input": "list documents"})
        assert graph.astream.call_args.kwargs["config"]["configurable"].get("knowledge_user_id") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["victim_thread", "victim.thread", " victim-thread "])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_session_collection_alias_cannot_bypass_owner(stack, sessions, monkeypatch, alias, method):
    _, backend, engine = stack
    engine._config.default_limit = 10
    engine._config.default_score_threshold = 0.0
    _login(monkeypatch)
    sessions.claim_session("victim-thread", "alice")
    response = await backend.request(
        method,
        "/api/knowledge/search" if method == "POST" else "/api/knowledge/documents",
        headers={"X-Agent-ID": "named-agent", "X-Thread-ID": alias, "X-Test-User": "bob"},
        **(
            {"params": {"scope": "session"}}
            if method == "GET"
            else {"json": {"scope": "session", "query": "secret", "filename": "private.txt"}}
        ),
    )
    assert response.status_code == 403, response.text
    engine.list_documents.assert_not_awaited()
    engine.delete_document.assert_not_awaited()
    engine.search.assert_not_called()
    assert list(sessions.list_sessions()) == ["victim-thread"]
    assert (await list_session(backend, "alice", "victim-thread")).status_code == 200


def test_persisted_aliases_keep_owner_and_conflicting_legacy_owners_fail_closed(tmp_path):
    from cuga.backend.knowledge.session_provider import PersistentSessionProvider, SessionKnowledgeState

    path = tmp_path / "sessions.json"
    provider = PersistentSessionProvider(path)
    provider.save_session("victim-thread", SessionKnowledgeState(thread_id="victim-thread", user_id="alice"))
    provider = PersistentSessionProvider(path)
    assert not provider.claim_session("victim_thread", "bob")
    assert provider.claim_session("victim_thread", "alice")
    provider.save_session("victim.thread", SessionKnowledgeState(thread_id="victim.thread", user_id="bob"))
    provider = PersistentSessionProvider(path)
    for user in ("alice", "bob"):
        assert not provider.claim_session("victim-thread", user)
        assert not provider.claim_session("victim_thread", user)


def test_clear_preserves_owned_aliases_after_restart(tmp_path):
    from cuga.backend.knowledge.session_provider import PersistentSessionProvider

    path = tmp_path / "sessions.json"
    provider = PersistentSessionProvider(path)
    for thread in ("victim-thread", "victim_thread"):
        assert provider.claim_session(thread, "alice")
        state = provider.get_session(thread)
        state.filenames = ["private.txt"]
        state.overrides = {"citations_enabled": False}
        provider.save_session(thread, state)
    provider.clear_session("victim-thread")
    provider = PersistentSessionProvider(path)
    for thread in ("victim-thread", "victim_thread"):
        state = provider.get_session(thread)
        assert state.user_id == "alice"
        assert state.filenames == []
        assert state.overrides == {}
        assert not provider.claim_session(thread, "bob")


@pytest.mark.asyncio
async def test_clear_session_knowledge_does_not_release_thread_ownership(stack, sessions, monkeypatch):
    _, backend, engine = stack
    _login(monkeypatch)
    sessions.claim_session("victim-thread", "alice")
    sessions.get_session("victim-thread").filenames = ["private.txt"]
    engine.drop_collection = AsyncMock()
    response = await backend.delete(
        "/api/knowledge/session",
        headers={"X-Agent-ID": "named-agent", "X-Thread-ID": "victim-thread", "X-Test-User": "alice"},
    )
    assert response.status_code == 200, response.text
    engine.drop_collection.assert_awaited_once_with("kb_sess_victim_thread")
    assert sessions.get_session("victim-thread").filenames == []
    assert (await list_session(backend, "bob", "victim-thread")).status_code == 403
    response = await backend.get(
        "/api/knowledge/run-context",
        headers={
            "X-Internal-Token": "machine-token",
            RUN_TOKEN_HEADER: capability(thread="victim-thread", user="bob"),
        },
    )
    assert response.status_code == 403
    assert (await list_session(backend, "alice", "victim-thread")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["agent", "session"])
async def test_upload_checks_source_thread_owner_before_resolving_file(stack, sessions, monkeypatch, scope):
    registry, _, engine = stack
    sessions.claim_session("victim-thread", "alice")
    resolver = MagicMock(side_effect=FileNotFoundError("test upload"))
    monkeypatch.setattr(mcp_server, "_resolve_ingest_file_path", resolver)
    response = await call(
        registry,
        "ingest_knowledge",
        capability(thread="victim-thread", user="bob", scopes=(scope,)),
        scope=scope,
        file_path="private.txt",
    )
    assert response.status_code >= 400 or "403 Forbidden" in response.text, response.text
    resolver.assert_not_called()
    engine._run_ingest.assert_not_called()
    await call(
        registry,
        "ingest_knowledge",
        capability(thread="victim-thread", user="alice", scopes=(scope,)),
        scope=scope,
        file_path="private.txt",
    )
    resolver.assert_called_once_with("private.txt", thread_id="victim-thread")


@pytest.mark.asyncio
async def test_run_context_cannot_fall_back_to_shared_workspace_for_authenticated_user(stack):
    _, backend, _ = stack
    response = await backend.get(
        "/api/knowledge/run-context",
        headers={
            "X-Internal-Token": "machine-token",
            RUN_TOKEN_HEADER: capability(thread=None, user="alice"),
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/run-context", "/documents?scope=session"])
async def test_authenticated_session_access_fails_closed_without_provider(stack, monkeypatch, endpoint):
    _, backend, _ = stack
    monkeypatch.setattr(backend._transport.app.state.app_state, "knowledge_provider", None)
    response = await backend.get(
        "/api/knowledge" + endpoint,
        headers={"X-Internal-Token": "machine-token", RUN_TOKEN_HEADER: capability(user="alice")},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("user", ["alice", "bob"])
async def test_prepare_authorizes_session_before_reading_previews_or_issuing_capabilities(
    stack, sessions, monkeypatch, user
):
    from fastapi import HTTPException
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
    from cuga.backend.knowledge import run_context
    from langchain_core.prompts import PromptTemplate

    _, _, engine = stack
    sessions.claim_session("victim-thread", "alice")
    doc = SimpleNamespace(filename="private.txt", chunk_count=1, preview="ALICE_PRIVATE_CONTENT")
    engine.list_documents = AsyncMock(
        side_effect=lambda coll: [doc] if coll == "kb_sess_victim_thread" else []
    )
    issue = MagicMock(wraps=run_context.issue_run_token)
    monkeypatch.setattr(run_context, "issue_run_token", issue)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled", False
    )
    tool = create_tool_from_api_dict(
        "knowledge_list_knowledge_documents", {"parameters": {"properties": {}}}, "knowledge"
    )
    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._task_todos_ref = []
    adapter._thread_id = "victim-thread"
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._base_tool_provider.get_all_tools = AsyncMock(return_value=[tool])
    adapter._base_tool_provider.get_apps = AsyncMock(return_value=[])
    adapter._base_tool_provider.get_tools = AsyncMock(return_value=[])
    adapter._prompt_template = PromptTemplate.from_template("{instructions}")
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="list documents")],
        task_todos=None,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
        thread_id="victim-thread",
    )
    config = {
        "configurable": {
            "knowledge_agent_id": "named-agent",
            "knowledge_user_id": user,
            "thread_id": "victim-thread",
            "knowledge_engine": engine,
            "knowledge_provider": sessions,
            "enable_todos": False,
            "shortlisting_tool_threshold": 35,
        }
    }
    node = create_prepare_tools_and_apps_node(adapter, {})
    if user == "bob":
        with pytest.raises(HTTPException) as exc:
            await node(state, config=config)
        assert exc.value.status_code == 403
        engine.list_documents.assert_not_awaited()
        issue.assert_not_called()
    else:
        result = await node(state, config=config)
        assert "ALICE_PRIVATE_CONTENT" in result.update["prepared_prompt"]
