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

from tests.fixtures.async_only_saver import AsyncOnlySaver

pytestmark = pytest.mark.unit

RESUME = {
    "action_id": "approve",
    "confirmed": True,
    "response_type": "confirmation",
    "timestamp": "2026-10-08T00:00:00Z",
    "user_id": "user-a",  # Client input cannot replace the authenticated subject.
}


@pytest.fixture
async def runtime(monkeypatch, request):
    # Indirect param: the checkpointer class (MemorySaver by default, AsyncOnlySaver to prove async-only access).
    saver_cls = getattr(request, "param", MemorySaver)
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.server import main, run_routes

    for attr in ["_EVENT_STREAM", "_DEFAULT_USER_ID", "_ASSERT_THREAD_ACCESS", "_RUNTIME_AGENT_ID"]:
        monkeypatch.setattr(run_routes, attr, getattr(run_routes, attr))
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
    graph = builder.compile(checkpointer=saver_cls())
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

        async def conversation(thread="owned", user="user-a", agent="cuga-default"):
            # Web conversations are server-issued (#774): create one before streaming on it.
            await db.create_conversation(thread, user, agent)

        yield SimpleNamespace(
            client=client,
            conversation=conversation,
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
    assert response.status_code == 404, response.text
    assert "USER_A_PRIVATE" not in response.text
    assert event.is_set() == before
    assert not runtime.seen
    runtime.clear.assert_not_called()
    runtime.drop.assert_not_called()


async def test_owner_followup_state_and_controls_use_bound_agent(runtime):
    await runtime.conversation(agent="sales")
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
    assert other.status_code == 404
    wrong_agent = await runtime.client.post(
        "/stream", headers=runtime.headers(agent="other"), json={"query": "attack"}
    )
    assert wrong_agent.status_code == 404


async def test_owner_can_resume_scoped_checkpoint(runtime):
    await runtime.conversation()
    response = await runtime.client.post("/stream", headers=runtime.headers(), json={"query": "pause"})
    assert response.status_code == 200, response.text
    denied = await runtime.client.post("/stream", headers=runtime.headers("user-b"), json=RESUME)
    assert denied.status_code == 404
    resumed = await runtime.client.post("/stream", headers=runtime.headers(), json=RESUME)
    assert resumed.status_code == 200, resumed.text
    assert runtime.seen and runtime.seen[-1][0:2] == ("user-a", "pause")
    state = await runtime.client.get("/api/agent/state", headers=runtime.headers())
    assert state.json()["state"]["url"] == "1"


@pytest.mark.parametrize("runtime", [AsyncOnlySaver], indirect=True)
async def test_stream_and_state_endpoint_use_async_state_api(runtime):
    """event_stream's state loads and /api/agent/state work on a saver that rejects sync calls."""
    await runtime.conversation()
    await runtime.conversation("paused")
    for query in ["first", "followup"]:
        response = await runtime.client.post("/stream", headers=runtime.headers(), json={"query": query})
        assert response.status_code == 200, response.text
        assert "Error" not in response.text
    state = await runtime.client.get("/api/agent/state", headers=runtime.headers())
    assert state.status_code == 200, state.text
    assert state.json()["state"]["input"] == "followup"
    # url "2" means the follow-up turn loaded the first turn's checkpoint instead of
    # silently falling back to a fresh state.
    assert state.json()["state"]["url"] == "2"

    # Seed a turn first: AgentLoop needs a prior message to report an interrupt cleanly.
    paused = runtime.headers(thread="paused")
    for query in ["first", "pause"]:
        response = await runtime.client.post("/stream", headers=paused, json={"query": query})
        assert response.status_code == 200, response.text
        assert "Error" not in response.text
    resumed = await runtime.client.post("/stream", headers=paused, json=RESUME)
    assert resumed.status_code == 200, resumed.text
    assert "Error" not in resumed.text
    assert runtime.seen[-1][0:2] == ("user-a", "pause")
    state = await runtime.client.get("/api/agent/state", headers=paused)
    assert state.json()["state"]["url"] == "2"


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
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path,method", [("/stream", "POST"), ("/api/agent/state", "GET"), ("/stop", "POST"), ("/reset", "POST")]
)
@pytest.mark.parametrize("storage_error", [RuntimeError, PermissionError])
async def test_storage_failure_fails_closed(runtime, monkeypatch, path, method, storage_error):
    monkeypatch.setattr(runtime.db, "claim_thread", AsyncMock(side_effect=storage_error("database down")))
    response = await runtime.client.request(method, path, headers=runtime.headers(), json={"query": "hello"})
    assert response.status_code == 404, response.text
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
    run_routes.build_run_router(
        event_stream=runtime.main.event_stream,
        default_user_id="default_user",
        assert_thread_access=runtime.main._assert_thread_access,
        runtime_agent_id=lambda: runtime.main.app_state.agent_id,
    )
    runtime.app.add_api_route("/run", run_routes.run_sync, methods=["POST"])
    denied = await runtime.client.post(
        "/run", headers=runtime.headers("user-b"), json={"query": "hello", "thread_id": "owned"}
    )
    assert denied.status_code == 404, denied.text
    forward.assert_not_awaited()
    get_supervisor.assert_not_awaited()
    owner = await runtime.client.post(
        "/run", headers=runtime.headers(), json={"query": "hello", "thread_id": "owned"}
    )
    assert owner.status_code == 200, owner.text
    if dispatch == "supervisor":
        kwargs = supervisor.invoke.call_args.kwargs
        assert kwargs["thread_id"] == "owned"
        assert kwargs["user_id"] == "user-a"
        assert kwargs["checkpoint_thread_id"] == runtime.main._checkpoint_thread_id(
            "owned", "user-a", "cuga-default", "run"
        )
    else:
        forward.assert_awaited_once()


async def test_protocol_runner_checks_owner_before_reading_pending_action(runtime):
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner
    from cuga.backend.server import conversation_history
    from unittest.mock import patch

    await runtime.db.claim_thread("owned", "user-a", "cuga-default")
    graph = Mock()
    graph.aget_state = AsyncMock()
    stream = Mock()
    runner = SimpleAgentRunner(
        SimpleNamespace(agent=SimpleNamespace(graph=graph), agent_id="cuga-default"),
        stream,
        caller_user_id="user-b",
    )
    with patch.object(conversation_history, "get_conversation_db", return_value=runtime.db):
        events = [event async for event in runner.run("attack", context_id="owned")]
    assert events[-1].name == "error"
    graph.aget_state.assert_not_called()
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
                state.user_id,
                state.service_scope["tenant_id"],
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
    first = await supervisor.invoke(
        "hello", thread_id="logical", checkpoint_thread_id="scoped", user_id="user-a", tenant_id="tenant-a"
    )
    assert first.thread_id == "logical"
    assert (await supervisor.graph.aget_state({"configurable": {"thread_id": "scoped"}})).next
    assert not (await supervisor.graph.aget_state({"configurable": {"thread_id": "logical"}})).values
    resumed = await supervisor.invoke(
        None, thread_id="logical", checkpoint_thread_id="scoped", action_response=ActionResponse(**RESUME)
    )
    assert resumed.answer == "done"
    assert observed == [("logical", "user-a", "tenant-a", "logical", "logical")]


@pytest.mark.parametrize("context_id", ["owned", " owned "])
async def test_supervisor_protocol_claims_before_construction_and_scopes_owner_run(
    runtime, monkeypatch, context_id
):
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
    events = [event async for event in owner.run("hello", context_id)]
    assert events[-1].data == {"text": "done"}
    supervisor.invoke.assert_awaited_once_with(
        "hello",
        thread_id="owned",
        checkpoint_thread_id=runtime.main._checkpoint_thread_id("owned", "user-a", "cuga-default", "a2a"),
    )


@pytest.mark.parametrize("path,method", [("/stop", "POST"), ("/reset", "POST"), ("/api/agent/state", "GET")])
async def test_read_and_controls_do_not_claim_unused_id(runtime, path, method):
    response = await runtime.client.request(method, path, headers=runtime.headers("user-b"), json={})
    assert response.status_code == 200, response.text
    await runtime.db._ensure_schema()
    assert await runtime.db._get_store().fetchall("SELECT * FROM runtime_thread_owners") == []
    await runtime.conversation()
    response = await runtime.client.post("/stream", headers=runtime.headers(), json={"query": "hello"})
    assert response.status_code == 200, response.text
    assert runtime.seen[-1][0] == "user-a"


@pytest.mark.parametrize("first", ["stream", "state", "reset"])
async def test_disabled_registry_normalizes_agent_on_all_routes(runtime, monkeypatch, first):
    monkeypatch.setattr(runtime.main.agent_registry, "is_agent_registry_enabled", lambda: False)
    await runtime.conversation()
    headers = runtime.headers(agent="sales")
    if first == "state":
        assert (await runtime.client.get("/api/agent/state", headers=headers)).status_code == 200
    elif first == "reset":
        assert (await runtime.client.post("/reset", headers=headers)).status_code == 200
    response = await runtime.client.post("/stream", headers=headers, json={"query": "hello"})
    assert response.status_code == 200, response.text
    assert "Error" not in response.text
    assert runtime.seen[-1][2]["agent_id"] == "cuga-default"
    state = await runtime.client.get("/api/agent/state", headers=headers)
    assert state.status_code == 200, state.text
    assert state.json()["state"]["input"] == "hello"
    for path in ["/stop", "/reset"]:
        response = await runtime.client.post(path, headers=headers)
        assert response.status_code == 200, response.text


async def test_whitespace_thread_uses_same_owner_checkpoint_and_stop_event(runtime):
    await runtime.conversation()
    response = await runtime.client.post(
        "/stream", headers=runtime.headers(thread=" owned "), json={"query": "hello"}
    )
    assert response.status_code == 200, response.text
    response = await runtime.client.post(
        "/stop", headers=runtime.headers("user-b", thread=None), json={"thread_id": " owned "}
    )
    assert response.status_code == 404, response.text
    response = await runtime.client.post(
        "/stop", headers=runtime.headers(thread=None), json={"thread_id": " owned "}
    )
    assert response.status_code == 200, response.text
    assert runtime.main.app_state.stop_events["owned"].is_set()
    assert " owned " not in runtime.main.app_state.stop_events
    state = await runtime.client.get("/api/agent/state", headers=runtime.headers())
    assert state.json()["state"]["input"] == "hello"
    rows = await runtime.db._get_store().fetchall("SELECT thread_id FROM runtime_thread_owners")
    assert [row["thread_id"] for row in rows] == ["owned"]


async def test_generator_storage_failure_yields_error_event(runtime, monkeypatch):
    claim = runtime.db.claim_thread
    attempts = 0

    async def fail_second(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("database down")
        return await claim(*args, **kwargs)

    monkeypatch.setattr(runtime.db, "claim_thread", fail_second)
    await runtime.conversation()
    response = await runtime.client.post("/stream", headers=runtime.headers(), json={"query": "hello"})
    assert response.status_code == 200, response.text
    assert "event: Error" in response.text
    assert "Conversation not found" in response.text
    assert not runtime.seen


async def test_workspace_reads_do_not_create_owner_rows(runtime, monkeypatch):
    main = runtime.main
    runtime.app.add_api_route("/api/workspace/tree", main.get_workspace_tree, methods=["GET"])
    monkeypatch.setattr(main, "workspace_tree_is_native_backed", lambda: False)
    monkeypatch.setattr(main, "workspace_tree_is_sandbox_backed", lambda: False)
    tree = Mock(return_value=[])
    monkeypatch.setattr(main, "fetch_host_workspace_tree", tree)
    for thread in ["fresh-1", "fresh-2"]:
        response = await runtime.client.get(
            "/api/workspace/tree", params={"thread_id": thread}, headers=runtime.headers()
        )
        assert response.status_code == 200, response.text
    assert await runtime.db._get_store().fetchall("SELECT * FROM runtime_thread_owners") == []
    await runtime.db.claim_thread("owned", "user-a", "cuga-default")
    tree.reset_mock()
    response = await runtime.client.get(
        "/api/workspace/tree", params={"thread_id": "owned"}, headers=runtime.headers("user-b")
    )
    # Another user's thread looks exactly like an unknown one: an empty tree.
    assert response.status_code == 200
    tree.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
async def test_check_only_enforces_existing_ownership_without_binding_agent(runtime, legacy):
    if legacy:
        await runtime.db.save_conversation("sales", "legacy", 1, "user-a", [])
        thread = "legacy"
    else:
        await runtime.db.claim_thread("unbound", "user-a")
        thread = "unbound"
    with pytest.raises(PermissionError):
        await runtime.db.claim_thread(thread, "user-b", create=False)
    await runtime.db.claim_thread(thread, "user-a", "sales", create=False)
    rows = await runtime.db._get_store().fetchall("SELECT agent_id FROM runtime_thread_owners")
    assert rows == [] if legacy else rows[0]["agent_id"] == ""


@pytest.mark.parametrize(
    "cfg,logical,workspace",
    [
        ({"thread_id": "legacy"}, "legacy", "legacy"),
        ({"thread_id": "hash", "logical_thread_id": "chat"}, "chat", "chat"),
        (
            {"thread_id": "hash", "logical_thread_id": "child", "workspace_thread_id": "parent"},
            "child",
            "parent",
        ),
        ({}, "fallback", "fallback"),
    ],
)
def test_logical_and_workspace_thread_helpers(cfg, logical, workspace):
    from cuga.backend.cuga_graph.utils.thread_ids import logical_thread_id, workspace_thread_id

    assert logical_thread_id(cfg, "fallback") == logical
    assert workspace_thread_id(cfg, "fallback") == workspace


async def test_extension_auth_and_validation_precede_ownership(runtime):
    runtime.app.add_api_route("/extension/agent_query", runtime.main.extension_agent_query, methods=["POST"])
    unauthenticated = await runtime.client.post(
        "/extension/agent_query", json={"query": "hello", "request_id": "owned"}
    )
    assert unauthenticated.status_code == 401
    invalid = await runtime.client.post(
        "/extension/agent_query", headers=runtime.headers(), json={"request_id": "owned"}
    )
    assert invalid.status_code == 400
    await runtime.db._ensure_schema()
    assert await runtime.db._get_store().fetchall("SELECT * FROM runtime_thread_owners") == []
    response = await runtime.client.post(
        "/extension/agent_query", headers=runtime.headers(), json={"query": "hello", "request_id": " owned "}
    )
    assert response.status_code == 200, response.text
    assert runtime.seen[-1][0] == "user-a"
    denied = await runtime.client.post(
        "/extension/agent_query",
        headers=runtime.headers("user-b"),
        json={"query": "attack", "request_id": "owned"},
    )
    assert denied.status_code == 404


async def test_unknown_workspace_read_does_not_touch_new_foreign_resources(runtime, monkeypatch):
    main = runtime.main
    runtime.app.add_api_route("/api/workspace/file", main.get_workspace_file, methods=["GET"])
    original = runtime.db.claim_thread

    async def concurrent_claim(thread, user, *args, **kwargs):
        result = await original(thread, user, *args, **kwargs)
        if kwargs.get("create") is False and result is None:
            # The upload wins after the access check's database snapshot.
            await original(thread, "user-b")
        return result

    monkeypatch.setattr(runtime.db, "claim_thread", concurrent_claim)
    native = Mock(return_value=True)
    monkeypatch.setattr(main, "workspace_tree_is_native_backed", native)
    read = Mock(return_value="USER_B_PRIVATE")
    monkeypatch.setattr(main, "native_workspace_text_preview", read)
    response = await runtime.client.get(
        "/api/workspace/file", params={"thread_id": "fresh", "path": "secret.txt"}, headers=runtime.headers()
    )
    assert response.status_code == 404, response.text
    assert "USER_B_PRIVATE" not in response.text
    native.assert_not_called()
    read.assert_not_called()


async def test_unbound_upload_owner_can_read_workspace(runtime, monkeypatch):
    main = runtime.main
    await runtime.db.claim_thread("owned", "user-a")
    runtime.app.add_api_route("/api/workspace/file", main.get_workspace_file, methods=["GET"])
    monkeypatch.setattr(main, "workspace_tree_is_native_backed", lambda: True)
    monkeypatch.setattr(main, "native_workspace_text_preview", lambda *_: "my upload")
    response = await runtime.client.get(
        "/api/workspace/file", params={"thread_id": "owned", "path": "file.txt"}, headers=runtime.headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == "my upload"


async def test_simple_protocol_resumes_canonical_context(runtime, monkeypatch):
    from cuga.backend.server import conversation_history
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    await runtime.db.claim_thread("owned", "user-a", "cuga-default")
    graph = Mock()
    graph.aget_state = AsyncMock()
    graph.aget_state.return_value = SimpleNamespace(
        next=("WaitForResponse",),
        values={"hitl_action": {"action_id": "approve", "type": "confirmation", "description": "Approve?"}},
    )
    captured = []

    async def stream(**kwargs):
        captured.append(kwargs)
        yield 'event: Answer\ndata: {"data":"done"}\n\n'

    runner = SimpleAgentRunner(
        SimpleNamespace(agent=SimpleNamespace(graph=graph), agent_id="cuga-default"),
        stream,
        caller_user_id="user-a",
    )
    monkeypatch.setattr(conversation_history, "get_conversation_db", lambda: runtime.db)
    events = [event async for event in runner.run("yes", context_id=" owned ")]
    assert events[-1].name == "final_answer"
    assert captured[0]["thread_id"] == "owned"
    assert captured[0]["resume"].action_id == "approve"
    assert captured[0]["resume"].confirmed
    graph.aget_state.assert_awaited_once_with(
        {
            "configurable": {
                "thread_id": runtime.main._checkpoint_thread_id("owned", "user-a", "cuga-default", "a2a")
            }
        }
    )
    graph.get_state.assert_not_called()


async def test_run_injected_dependencies_do_not_import_main(runtime, monkeypatch):
    import builtins
    from cuga.backend.server import run_routes

    claim = AsyncMock(return_value="cuga-default")

    async def stream(*args, **kwargs):
        yield 'event: Answer\ndata: {"data":"done"}\n\n'

    run_routes.build_run_router(
        event_stream=stream,
        default_user_id="default_user",
        assert_thread_access=claim,
        runtime_agent_id=lambda: "sales",
    )
    monkeypatch.setattr(run_routes, "_run_auth_failure", AsyncMock(return_value=None))
    monkeypatch.setattr(run_routes, "_get_supervisor", AsyncMock(return_value=None))
    runtime.app.add_api_route("/run", run_routes.run_sync, methods=["POST"])
    original_import = builtins.__import__

    def no_main_import(name, *args, **kwargs):
        if name == "cuga.backend.server.main":
            raise AssertionError("run_routes must work without importing main")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_main_import)
    response = await runtime.client.post(
        "/run", headers=runtime.headers(), json={"query": "hello", "thread_id": " owned "}
    )
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "done"
    assert response.json()["thread_id"] == "owned"
    claim.assert_awaited_once_with("owned", "user-a", "sales")
