"""Runtime ownership contracts using signed JWTs, SQLite, and a real LangGraph."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

pytestmark = pytest.mark.unit

RESUME = {
    "action_id": "approve",
    "confirmed": True,
    "response_type": "confirmation",
    "timestamp": "2026-10-08T00:00:00Z",
    "user_id": "user-a",  # Client input cannot replace the authenticated subject.
}


@pytest.fixture
async def runtime(monkeypatch):
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.server import main
    from cuga.backend.server.auth import dependencies as auth
    from cuga.backend.server.auth.jwt_validator import JWTValidator
    from cuga.backend.server.conversation_history import ConversationHistoryDB

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    validator = JWTValidator("https://test.invalid/jwks", issuer="https://test.invalid")
    monkeypatch.setattr(
        validator._client, "get_signing_key_from_jwt", lambda _: SimpleNamespace(key=private_key.public_key())
    )
    monkeypatch.setattr(auth, "_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "_authorization_enabled", lambda: True)
    monkeypatch.setattr(auth, "_get_validator_for_token", AsyncMock(return_value=validator))

    def headers(user="user-a", thread="owned", agent=None):
        token = jwt.encode(
            {
                "sub": user,
                "roles": ["ServiceUser"],
                "iss": "https://test.invalid",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS256",
        )
        result = {"Authorization": f"Bearer {token}"}
        if thread is not None:
            result["X-Thread-ID"] = thread
        if agent is not None:
            result["X-Agent-ID"] = agent
        return result

    seen = []

    def node(state: AgentState):
        if state.input == "pause":
            interrupt("approve")
        seen.append((state.user_id, state.input, state.service_scope.copy()))
        return {
            "url": str(int(state.url or "0") + 1),
            "final_answer": state.input,
            "messages": [AIMessage(content=state.input)],
        }

    builder = StateGraph(AgentState)
    builder.add_node("FinalAnswerAgent", node)
    builder.add_edge(START, "FinalAnswerAgent")
    builder.add_edge("FinalAnswerAgent", END)
    graph = builder.compile(checkpointer=MemorySaver())
    agent = SimpleNamespace(graph=graph, policy_system=None, chat=None)
    db = ConversationHistoryDB()
    monkeypatch.setattr(main, "get_conversation_db", lambda: db)
    monkeypatch.setattr(
        main,
        "app_state",
        SimpleNamespace(
            agent=agent,
            agent_id="cuga-default",
            stop_events={},
            current_llm=None,
            output_format=None,
            knowledge_provider=None,
        ),
    )
    monkeypatch.setattr(main, "_resolve_stream_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(main.agent_registry, "is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(main.events_bridge, "forwards_to_events", lambda *args: False)
    monkeypatch.setattr(main, "_knowledge_enabled_for_app_state", lambda _: False)
    monkeypatch.setattr(main, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main, "_dispatch_slash_for_stream", AsyncMock(return_value=None))
    monkeypatch.setattr(main.settings.evolve, "enabled", False)
    monkeypatch.setattr(main.settings.advanced_features, "mode", "api")
    monkeypatch.setattr(main.settings.advanced_features, "langfuse_tracing", False)
    from cuga.backend import agent_spawn
    from cuga.backend.knowledge import sources

    clear = Mock()
    drop = Mock()
    monkeypatch.setattr(agent_spawn, "clear_runtime_caches", clear)
    monkeypatch.setattr(sources, "drop_ledger", drop)
    app = FastAPI()
    # Real auth dependency and JWT signature validation; only JWKS network lookup is stubbed.
    for path, handler, method in [
        ("/stream", main.stream, "POST"),
        ("/api/agent/state", main.get_agent_state, "GET"),
        ("/stop", main.stop, "POST"),
        ("/reset", main.reset_agent_state, "POST"),
    ]:
        app.add_api_route(path, handler, methods=[method])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(
            client=client,
            headers=headers,
            db=db,
            graph=graph,
            seen=seen,
            main=main,
            app=app,
            clear=clear,
            drop=drop,
        )


@pytest.mark.parametrize(
    "path,method,body",
    [
        ("/stream", "POST", {"query": "USER_B_INPUT", "user_id": "user-a"}),
        ("/stream", "POST", RESUME),
        ("/api/agent/state", "GET", None),
        ("/stop", "POST", {}),
        ("/reset", "POST", {}),
    ],
)
async def test_authenticated_foreign_runtime_access_is_denied(runtime, path, method, body):
    await runtime.db.claim_thread("owned", "user-a", "cuga-default")
    event = asyncio.Event()
    if path == "/reset":
        event.set()
    runtime.main.app_state.stop_events["owned"] = event
    before = event.is_set()
    response = await runtime.client.request(method, path, headers=runtime.headers("user-b"), json=body)
    assert response.status_code == 403, response.text
    assert "USER_A_PRIVATE" not in response.text
    assert event.is_set() == before
    assert not runtime.seen
    runtime.clear.assert_not_called()
    runtime.drop.assert_not_called()


async def test_owner_followup_state_and_controls_use_bound_agent(runtime):
    for query in ["USER_A_PRIVATE", "followup"]:
        response = await runtime.client.post(
            "/stream",
            headers={**runtime.headers(agent="sales"), "X-Disable-History": "true"},
            json={"query": query},
        )
        assert response.status_code == 200, response.text
        assert "Error" not in response.text
    state = await runtime.client.get("/api/agent/state", headers=runtime.headers())
    assert state.status_code == 200, state.text
    assert state.json()["state"]["input"] == "followup"
    assert state.json()["state"]["url"] == "2"
    assert [item[0] for item in runtime.seen] == ["user-a", "user-a"]
    assert all(item[2]["agent_id"] == "sales" for item in runtime.seen)
    assert (await runtime.client.post("/stop", headers=runtime.headers())).status_code == 200
    assert runtime.main.app_state.stop_events["owned"].is_set()
    assert (await runtime.client.post("/reset", headers=runtime.headers())).status_code == 200
    assert not runtime.main.app_state.stop_events["owned"].is_set()
    runtime.clear.assert_called_with("owned")
    runtime.drop.assert_called_once_with("owned")
    # History-disabled runs still have durable ownership.
    assert await runtime.db.get_thread_history("owned") == []
    other = await runtime.client.post("/stream", headers=runtime.headers("user-b"), json={"query": "attack"})
    assert other.status_code == 403
    wrong_agent = await runtime.client.post(
        "/stream", headers=runtime.headers(agent="other"), json={"query": "attack"}
    )
    assert wrong_agent.status_code == 403


async def test_owner_can_resume_scoped_checkpoint(runtime):
    response = await runtime.client.post("/stream", headers=runtime.headers(), json={"query": "pause"})
    assert response.status_code == 200, response.text
    denied = await runtime.client.post("/stream", headers=runtime.headers("user-b"), json=RESUME)
    assert denied.status_code == 403
    resumed = await runtime.client.post("/stream", headers=runtime.headers(), json=RESUME)
    assert resumed.status_code == 200, resumed.text
    assert runtime.seen and runtime.seen[-1][0:2] == ("user-a", "pause")
    state = await runtime.client.get("/api/agent/state", headers=runtime.headers())
    assert state.json()["state"]["url"] == "1"


@pytest.mark.parametrize("path", ["/stop", "/reset"])
async def test_unscoped_controls_do_not_mutate_any_thread(runtime, path):
    event = asyncio.Event()
    if path == "/reset":
        event.set()
    runtime.main.app_state.stop_events["other"] = event
    before = event.is_set()
    response = await runtime.client.post(path, headers=runtime.headers(thread=None), json={})
    assert response.status_code == 400
    assert event.is_set() == before
    runtime.clear.assert_not_called()
    runtime.drop.assert_not_called()


@pytest.mark.parametrize(
    "path,method", [("/stop", "POST"), ("/reset", "POST"), ("/api/agent/state?thread_id=owned", "GET")]
)
async def test_query_or_body_thread_does_not_bypass_ownership(runtime, path, method):
    await runtime.db.claim_thread("owned", "user-a", "cuga-default")
    response = await runtime.client.request(
        method, path, headers=runtime.headers("user-b", thread=None), json={"thread_id": "owned"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path,method", [("/stream", "POST"), ("/api/agent/state", "GET"), ("/stop", "POST"), ("/reset", "POST")]
)
@pytest.mark.parametrize("storage_error", [RuntimeError, PermissionError])
async def test_storage_failure_fails_closed(runtime, monkeypatch, path, method, storage_error):
    monkeypatch.setattr(runtime.db, "claim_thread", AsyncMock(side_effect=storage_error("database down")))
    response = await runtime.client.request(method, path, headers=runtime.headers(), json={"query": "hello"})
    assert response.status_code == 503, response.text
    assert not runtime.seen
    assert not runtime.main.app_state.stop_events
    runtime.clear.assert_not_called()
    runtime.drop.assert_not_called()


async def test_no_or_invalid_auth_cannot_claim_thread(runtime):
    for headers in [{}, {"Authorization": "Bearer invalid", "X-Thread-ID": "owned"}]:
        response = await runtime.client.post("/stream", headers=headers, json={"query": "hello"})
        assert response.status_code == 401
    store = runtime.db._get_store()
    await runtime.db._ensure_schema()
    assert await store.fetchall("SELECT * FROM runtime_thread_owners") == []


async def test_first_owner_and_agent_claims_are_atomic_across_sqlite_connections(tmp_path):
    from cuga.backend.server.conversation_history import ConversationHistoryDB
    from cuga.backend.storage.relational.local import LocalRelationalStore

    stores = [LocalRelationalStore(str(tmp_path / "owners.db")) for _ in range(2)]
    dbs = [ConversationHistoryDB(), ConversationHistoryDB()]
    for db, store in zip(dbs, stores):
        db._get_store = lambda store=store: store
        await db._ensure_schema()
    try:
        results = await asyncio.gather(
            *(dbs[i % 2].claim_thread("race", f"user-{i}", "agent") for i in range(20)),
            return_exceptions=True,
        )
        assert results.count("agent") == 1
        assert sum(isinstance(result, PermissionError) for result in results) == 19
        await dbs[0].claim_thread("agent-race", "owner")
        agents = await asyncio.gather(
            dbs[0].claim_thread("agent-race", "owner", "agent-a"),
            dbs[1].claim_thread("agent-race", "owner", "agent-b"),
            return_exceptions=True,
        )
        assert sum(isinstance(result, PermissionError) for result in agents) == 1
        winner = next(result for result in agents if isinstance(result, str))
        await stores[0].close()
        assert await dbs[0].claim_thread("agent-race", "owner", winner) == winner
    finally:
        for store in stores:
            await store.close()


@pytest.mark.parametrize("kind", ["history", "events", "ambiguous"])
async def test_legacy_records_cannot_be_claimed_by_foreign_user(kind):
    from cuga.backend.server.conversation_history import ConversationHistoryDB

    db = ConversationHistoryDB()
    if kind != "events":
        assert await db.save_conversation("agent", "legacy", 1, "user-a", [])
    if kind != "history":
        assert await db.save_stream_events("agent", "legacy", "user-a", [])
    if kind == "ambiguous":
        assert await db.save_conversation("agent", "legacy", 1, "user-b", [])
    with pytest.raises(PermissionError):
        await db.claim_thread("legacy", "user-b", "agent")
    if kind != "ambiguous":
        assert await db.claim_thread("legacy", "user-a", "agent") == "agent"
        assert await db.delete_thread("agent", "legacy", "user-a")
        with pytest.raises(PermissionError):
            await db.claim_thread("legacy", "user-b", "agent")


def test_checkpoint_identity_separates_all_scope_components(monkeypatch):
    from cuga.backend.server import thread_scope

    monkeypatch.setattr(thread_scope, "get_tenant_id", lambda: "tenant-a")
    monkeypatch.setattr(thread_scope, "get_service_instance_id", lambda: "service-a")
    key = thread_scope.checkpoint_thread_id("thread", "user", "agent")
    assert key != "thread"
    assert key == thread_scope.checkpoint_thread_id("thread", "user", "agent")
    assert (
        len(
            {
                key,
                thread_scope.checkpoint_thread_id("other", "user", "agent"),
                thread_scope.checkpoint_thread_id("thread", "other", "agent"),
                thread_scope.checkpoint_thread_id("thread", "user", "other"),
            }
        )
        == 4
    )
    monkeypatch.setattr(thread_scope, "get_tenant_id", lambda: "tenant-b")
    assert key != thread_scope.checkpoint_thread_id("thread", "user", "agent")
    monkeypatch.setattr(thread_scope, "get_tenant_id", lambda: "tenant-a")
    monkeypatch.setattr(thread_scope, "get_service_instance_id", lambda: "service-b")
    assert key != thread_scope.checkpoint_thread_id("thread", "user", "agent")


@pytest.mark.parametrize("dispatch", ["events", "supervisor"])
async def test_run_dispatch_rejects_foreign_owner_before_side_effects(runtime, monkeypatch, dispatch):
    from cuga.backend.server import run_routes

    await runtime.db.claim_thread("owned", "user-a", "cuga-default")
    monkeypatch.setattr(run_routes, "_run_auth_failure", AsyncMock(return_value=None))
    forward = AsyncMock(return_value="armed")
    supervisor = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(answer="done")))
    get_supervisor = AsyncMock(return_value=supervisor)
    monkeypatch.setattr(run_routes.events_bridge, "forwards_to_events", lambda *_: dispatch == "events")
    monkeypatch.setattr(run_routes.events_bridge, "forward_slash_to_events", forward)
    monkeypatch.setattr(run_routes, "_get_supervisor", get_supervisor)
    monkeypatch.setattr(run_routes, "_release_supervisor", AsyncMock())
    runtime.app.add_api_route("/run", run_routes.run_sync, methods=["POST"])
    denied = await runtime.client.post(
        "/run", headers=runtime.headers("user-b"), json={"query": "hello", "thread_id": "owned"}
    )
    assert denied.status_code == 403, denied.text
    forward.assert_not_awaited()
    get_supervisor.assert_not_awaited()
    owner = await runtime.client.post(
        "/run", headers=runtime.headers(), json={"query": "hello", "thread_id": "owned"}
    )
    assert owner.status_code == 200, owner.text
    if dispatch == "supervisor":
        kwargs = supervisor.invoke.call_args.kwargs
        assert kwargs["thread_id"] == "owned"
        assert kwargs["checkpoint_thread_id"] == runtime.main._checkpoint_thread_id(
            "owned", "user-a", "cuga-default"
        )
    else:
        forward.assert_awaited_once()


async def test_protocol_runner_checks_owner_before_reading_pending_action(runtime):
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner
    from cuga.backend.server import conversation_history
    from unittest.mock import patch

    await runtime.db.claim_thread("owned", "user-a", "cuga-default")
    graph = Mock()
    stream = Mock()
    runner = SimpleAgentRunner(
        SimpleNamespace(agent=SimpleNamespace(graph=graph), agent_id="cuga-default"),
        stream,
        caller_user_id="user-b",
    )
    with patch.object(conversation_history, "get_conversation_db", return_value=runtime.db):
        events = [event async for event in runner.run("attack", context_id="owned")]
    assert events[-1].name == "error"
    graph.get_state.assert_not_called()
    stream.assert_not_called()


async def test_scoped_spawn_runtime_tracks_and_cancels_by_logical_thread(monkeypatch):
    from cuga.backend.agent_spawn import runtime as spawn
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    config = {"configurable": {"thread_id": "opaque-checkpoint", "logical_thread_id": "logical"}}
    rt = spawn.SpawnAgentRuntime([], parent_config=config)
    child, workspace = rt._resolve_thread_ids(share_workspace=True)
    assert workspace == "logical" and child != workspace
    started = asyncio.Event()

    async def hang(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(rt, "execute", hang)
    try:
        fid = await rt.execute_async("task")
        await started.wait()
        tasks = spawn.pending_spawn_tasks("logical")
        assert len(tasks) == 1
        assert spawn.pending_spawn_tasks("opaque-checkpoint") == []
        tools = create_spawn_tools(spawn.thread_spawn_futures("logical"), parent_config=config)
        get_result = next(tool for tool in tools if tool.name == "get_agent_result")
        assert "[SpawnTimeout]" in await get_result.coroutine(future_id=fid, timeout=0.01)
        await asyncio.gather(*tasks, return_exceptions=True)
        assert tasks[0].cancelled()
        assert spawn.pending_spawn_tasks("logical") == []
    finally:
        spawn.clear_runtime_caches("logical")


async def test_prepare_tools_read_logical_workspace_and_session_knowledge(monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    from langchain_core.messages import HumanMessage
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import prepare_node
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.tools.runtime_tools import RuntimeBackends
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import thread_workspace_root

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(prepare_node.settings.policy, "enabled", False)
    monkeypatch.setattr(prepare_node.settings.evolve, "enabled", False)
    monkeypatch.setattr(prepare_node.settings.agent_spawn, "enabled", False)
    monkeypatch.setattr(prepare_node, "resolve_runtime_backends", lambda *_: RuntimeBackends("host", "none"))
    scope = Mock(return_value=([], None))
    monkeypatch.setattr(prepare_node, "_get_knowledge_tool_scope_context", scope)
    root = thread_workspace_root("logical")
    root.mkdir(parents=True, exist_ok=True)
    (root / "uploaded.txt").write_text("USER_A_UPLOAD")
    adapter = MagicMock()
    adapter._task_todos_ref = []
    adapter._tools_context = {}
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._thread_id = "logical"
    adapter._weak_schema_tool_names = frozenset()
    adapter._base_tool_provider = DirectLangChainToolsProvider(tools=[])
    adapter._prompt_template.invoke.return_value.to_string.return_value = ""
    state = SimpleNamespace(
        thread_id="logical",
        chat_messages=[HumanMessage(content="task")],
        task_todos=None,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
    )
    node = prepare_node.create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
    await node(
        state,
        config={
            "configurable": {
                "thread_id": "opaque-checkpoint",
                "logical_thread_id": "logical",
                "workspace_thread_id": "logical",
                "enable_todos": False,
            }
        },
    )
    assert await adapter._tools_context["read_file"](path="uploaded.txt") == "USER_A_UPLOAD"
    await adapter._tools_context["write_file"](path="generated.txt", content="OWNER_OUTPUT")
    assert (root / "generated.txt").read_text() == "OWNER_OUTPUT"
    assert scope.call_args.args[1] == "logical"
    assert not thread_workspace_root("opaque-checkpoint").exists()


async def test_supervisor_sdk_keeps_logical_resources_when_checkpoint_is_scoped(monkeypatch):
    from cuga import sdk
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse
    from langchain_core.runnables import RunnableConfig

    observed = []

    def pause(state: CugaSupervisorState, config: RunnableConfig):
        interrupt("approve")
        observed.append(
            (
                state.thread_id,
                config["configurable"]["logical_thread_id"],
                config["configurable"]["workspace_thread_id"],
            )
        )
        return {"final_answer": "done"}

    builder = StateGraph(CugaSupervisorState)
    builder.add_node("pause", pause)
    builder.add_edge(START, "pause")
    builder.add_edge("pause", END)
    supervisor = sdk.CugaSupervisor.__new__(sdk.CugaSupervisor)
    supervisor._compiled_graph = builder.compile(checkpointer=MemorySaver())
    supervisor._auto_load_policies = False
    supervisor._reset_policy_storage = False
    supervisor._policy_system = None
    supervisor._callbacks = None
    supervisor._cuga_lite_max_steps = None
    monkeypatch.setattr(sdk, "init_openlit", lambda: None)
    first = await supervisor.invoke("hello", thread_id="logical", checkpoint_thread_id="scoped")
    assert first.thread_id == "logical"
    assert supervisor.graph.get_state({"configurable": {"thread_id": "scoped"}}).next
    assert not supervisor.graph.get_state({"configurable": {"thread_id": "logical"}}).values
    resumed = await supervisor.invoke(
        None, thread_id="logical", checkpoint_thread_id="scoped", action_response=ActionResponse(**RESUME)
    )
    assert resumed.answer == "done"
    assert observed == [("logical", "logical", "logical")]


async def test_supervisor_protocol_claims_before_construction_and_scopes_owner_run(runtime, monkeypatch):
    from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner
    from cuga.backend.server import conversation_history

    await runtime.db.claim_thread("owned", "user-a", "cuga-default")
    supervisor = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(answer="done", error=None)))
    runner = SupervisorAgentRunner(
        SimpleNamespace(agent_id="cuga-default"), "unused.yaml", caller_user_id="user-b"
    )
    construct = AsyncMock(return_value=supervisor)
    monkeypatch.setattr(runner, "_ensure_supervisor", construct)
    monkeypatch.setattr(conversation_history, "get_conversation_db", lambda: runtime.db)
    denied = [event async for event in runner.run("attack", "owned")]
    assert denied[-1].name == "error"
    construct.assert_not_awaited()
    supervisor.invoke.assert_not_awaited()
    owner = SupervisorAgentRunner(
        SimpleNamespace(agent_id="cuga-default"), "unused.yaml", caller_user_id="user-a"
    )
    monkeypatch.setattr(owner, "_ensure_supervisor", construct)
    events = [event async for event in owner.run("hello", "owned")]
    assert events[-1].data == {"text": "done"}
    supervisor.invoke.assert_awaited_once_with(
        "hello",
        thread_id="owned",
        checkpoint_thread_id=runtime.main._checkpoint_thread_id("owned", "user-a", "cuga-default"),
    )
