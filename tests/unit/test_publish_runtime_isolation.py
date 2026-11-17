"""Publishing a non-default agent must not mutate the process-wide default runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from cuga.backend.server import main, runtime_refresh
from cuga.backend.server.config_fingerprint import compute_fingerprint
from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import reset_config_db
from cuga.backend.server.manage_routes import config_routes, router

pytestmark = pytest.mark.unit


class _FakeKnowledgeEngine:
    def __init__(self):
        self._reindex_in_progress: set[str] = set()
        self._reindex_deferred: set[str] = set()

    def prepare_knowledge_update(self, knowledge_cfg: dict):
        return SimpleNamespace(knowledge_cfg=knowledge_cfg)

    def commit_knowledge_update(self, prepared) -> dict:
        return {"reindex_recommended": False, "prepared": prepared.knowledge_cfg}

    async def list_documents(self, collection: str) -> list[dict]:
        return []


def _client(app_state) -> TestClient:
    reset_config_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = app_state
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost")


def _default_runtime():
    agent = SimpleNamespace(special_instructions="default-instructions")
    return SimpleNamespace(
        knowledge_engine=_FakeKnowledgeEngine(),
        agent=agent,
        current_llm="default-llm",
        tools_include_by_app={"crm": ["get_account"]},
        config_version=1,
        tools_include_version=1,
        agent_graphs_cache={("sales-east", False): object(), ("sales-east", True): object()},
        policy_system=None,
        policy_filesystem_sync=False,
    )


@pytest.mark.asyncio
async def test_publish_rejects_unknown_agent_before_writes(monkeypatch):
    save_draft = AsyncMock()
    save_config = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": "registered-agent"}]),
    )
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", save_draft)
    monkeypatch.setattr("cuga.backend.server.config_store.save_config", save_config)
    request = SimpleNamespace(json=AsyncMock(), app=SimpleNamespace(state=SimpleNamespace(app_state=None)))

    with pytest.raises(HTTPException) as exc_info:
        await config_routes.save_manage_config_publish(request, agent_id="unknown-agent")

    assert exc_info.value.status_code == 404
    request.json.assert_not_awaited()
    save_draft.assert_not_awaited()
    save_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_passes_registry_owned_agent_id_to_policy_creation(monkeypatch):
    registry_agent_id = "".join(["registered", "-agent"])
    requested_agent_id = registry_agent_id.encode().decode()
    assert requested_agent_id == registry_agent_id
    assert requested_agent_id is not registry_agent_id

    captured = {}

    async def _capture_policy_creation(*, agent_id, draft, policies_data):
        captured["agent_id"] = agent_id
        captured["draft"] = draft
        captured["policies_data"] = policies_data

    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": registry_agent_id}]),
    )
    monkeypatch.setattr("cuga.backend.server.config_store.load_config", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", AsyncMock())
    monkeypatch.setattr("cuga.backend.server.config_store.save_config", AsyncMock(return_value="1"))
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.create_agent_policy_system",
        _capture_policy_creation,
    )
    monkeypatch.setattr(config_routes, "invalidate_agent_graph_cache", AsyncMock())
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "config": {
                    "agent": {"name": "Registered Agent"},
                    "policies": {"policies": [{"id": "policy-1"}]},
                }
            }
        ),
        app=SimpleNamespace(state=SimpleNamespace(app_state=_default_runtime())),
    )

    response = await config_routes.save_manage_config_publish(request, agent_id=requested_agent_id)

    assert response.status_code == 200
    assert captured["agent_id"] is registry_agent_id
    assert captured["draft"] is False
    assert captured["policies_data"] == [{"id": "policy-1"}]


def test_publishing_non_default_agent_does_not_mutate_singleton_runtime(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": "sales-east"}]),
    )
    state = _default_runtime()
    default_agent = state.agent
    cached_published = state.agent_graphs_cache[("sales-east", False)]
    client = _client(state)

    response = client.post(
        "/api/manage/config",
        params={"agent_id": "sales-east"},
        json={
            "config": {
                "agent": {"name": "Sales East"},
                "llm": {"provider": "openai", "model": "gpt-4o-mini"},
                "tools": [{"name": "other_app", "include": ["list_items"]}],
                "special_instructions": "east-only",
            }
        },
    )

    assert response.status_code == 200
    assert state.agent is default_agent
    assert state.agent.special_instructions == "default-instructions"
    assert state.current_llm == "default-llm"
    assert state.tools_include_by_app == {"crm": ["get_account"]}
    assert ("sales-east", False) not in state.agent_graphs_cache
    assert cached_published is not None


def test_publishing_default_agent_still_applies_to_singleton(monkeypatch):
    state = _default_runtime()
    applied = {}

    async def _capture_rebuild(app_state, config, config_version):
        applied["config"] = config
        applied["rebuilt"] = True
        app_state.current_llm = "updated-llm"
        app_state.agent.special_instructions = config.get("special_instructions")

    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.config_routes.rebuild_production_agent",
        _capture_rebuild,
    )
    client = _client(state)

    response = client.post(
        "/api/manage/config",
        params={"agent_id": "cuga-default"},
        json={
            "config": {
                "agent": {"name": "Default"},
                "special_instructions": "new-default",
            }
        },
    )

    assert response.status_code == 200
    assert applied.get("config") is not None
    assert applied.get("rebuilt") is True
    assert state.current_llm == "updated-llm"
    assert state.agent.special_instructions == "new-default"


async def _seeded_default_agent(checkpointer):
    """Build a cheap real entry graph (as test_entry_graph does) and seed one thread."""
    from unittest.mock import MagicMock, patch

    from cuga.backend.cuga_graph.entry_graph import CugaEntryGraph
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
    from cuga.backend.llm.models import LLMManager

    tool_provider = MagicMock(spec=ToolProviderInterface)
    tool_provider.initialize = AsyncMock()
    tool_provider.get_apps = AsyncMock(return_value=[])
    tool_provider.reset = MagicMock()  # rebuild_agent_from_config resets the provider
    # The constructor creates node LLMs too, so it must also run under the get_model patch.
    with patch.object(LLMManager, "get_model", return_value=MagicMock()):
        agent = CugaEntryGraph(None, tool_provider=tool_provider, supervisor_enabled=False)
        await agent.build_graph(checkpointer=checkpointer)
    cfg = {"configurable": {"thread_id": "default-thread"}}
    await agent.graph.aupdate_state(cfg, {"input": "before publish"}, as_node=agent.chat.chat_agent.name)
    return agent, cfg


async def _rebuild(state, config_version):
    from unittest.mock import MagicMock, patch

    from cuga.backend.llm.models import LLMManager
    from cuga.backend.server.manage_routes.apply import rebuild_production_agent

    with patch.object(LLMManager, "get_model", return_value=MagicMock()):
        await rebuild_production_agent(state, {}, config_version)


@pytest.mark.asyncio
async def test_default_publish_rebuild_keeps_conversations_on_shared_saver():
    """Publishing cuga-default rebuilds app_state.agent; the shared saver keeps its threads (#774)."""
    from langgraph.checkpoint.memory import MemorySaver

    saver = MemorySaver()
    agent, cfg = await _seeded_default_agent(saver)
    old_graph = agent.graph
    # The shared slot holds another agent's later publish; the rebuild must keep
    # the version its own publish saved.
    state = SimpleNamespace(agent=agent, checkpointer=saver, config_version=12)

    await _rebuild(state, 2)

    assert state.agent is not agent
    assert agent.graph is old_graph
    assert state.agent._checkpoint_config_version == "2"
    assert (await state.agent.graph.aget_state(cfg)).values["input"] == "before publish"


@pytest.fixture
async def policy_state(monkeypatch):
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
    from cuga.backend.cuga_graph.policy.models import ToolApproval
    from cuga.backend.cuga_graph.policy.storage import PolicyStorage
    from cuga.backend.storage.policy.local import LocalPolicyStore

    from cuga.config import settings

    monkeypatch.setattr(settings.secrets, "force_env", False)
    storage = PolicyStorage(backend=LocalPolicyStore(":memory:", "source_policies"), embedding_dim=2)
    storage._embedding_initialized = True
    storage._embedding_function = AsyncMock(return_value=[1.0, 0.0])
    await storage.connect()
    await storage._create_collection()
    policy = ToolApproval(
        id="approval", name="Approval", description="Protect writes", required_tools=["write"]
    )
    await storage.add_policy(policy)
    policy_system = PolicyConfigurable(storage=storage)
    policy_system.llm = MagicMock()
    await policy_system.initialize()
    monkeypatch.setattr("cuga.backend.llm.models.create_llm_from_config", lambda config: "new-llm")
    state = SimpleNamespace(
        policy_system=policy_system,
        agent=None,
        agent_id="cuga-default",
        checkpointer=object(),
        current_llm="old-llm",
        tools_include_by_app=None,
    )
    try:
        yield state
    finally:
        await storage.disconnect()


async def test_policy_snapshot_survives_live_store_clear_and_rejects_partial_build(policy_state):
    prepared = await runtime_refresh.prepare_runtime(policy_state, {})
    try:
        await policy_state.policy_system.storage.delete_policy("approval")
        policies = await prepared.policy_system.storage.list_policies(strict=True)
        assert [policy.id for policy in policies] == ["approval"]
        assert prepared.current_llm == "new-llm"
        assert policy_state.current_llm == "old-llm"
        with pytest.raises(ValueError, match="all published policies"):
            await runtime_refresh.prepare_runtime(policy_state, {"policies": {"policies": [{"type": "bad"}]}})
        assert [policy.id for policy in await prepared.policy_system.storage.list_policies(strict=True)] == [
            "approval"
        ]
    finally:
        await prepared.runtime_policy_snapshot.disconnect()


async def test_concurrent_refresh_builds_once_and_keeps_live_policy_until_swap(policy_state, monkeypatch):
    old = SimpleNamespace(aclose=AsyncMock(), policy_system=policy_state.policy_system)
    policy_state.agent = old
    policy_state.config_version = 1
    config = {"special_instructions": "new"}
    entered, finish = asyncio.Event(), asyncio.Event()
    replacement = SimpleNamespace(
        aclose=AsyncMock(), _config_fingerprint=compute_fingerprint(config), _checkpoint_config_version="2"
    )

    async def build(
        *, tool_provider, startup_config, startup_llm_cfg, startup_version, langfuse_handler, runtime_state
    ):
        entered.set()
        assert policy_state.agent is old
        assert runtime_state.policy_system is not old.policy_system
        await finish.wait()
        return replacement

    builder = AsyncMock(side_effect=build)
    monkeypatch.setattr(main, "_build_published_default_agent", builder)
    tasks = [asyncio.create_task(main._replace_default_runtime(policy_state, config, 2)) for _ in range(3)]
    await asyncio.wait_for(entered.wait(), 5)
    assert [p.id for p in await old.policy_system.storage.list_policies(strict=True)] == ["approval"]
    assert policy_state.current_llm == "old-llm"
    finish.set()
    assert await asyncio.gather(*tasks) == [replacement] * 3
    builder.assert_awaited_once()
    assert policy_state.agent is replacement
    assert policy_state.current_llm == "new-llm"
    old.aclose.assert_awaited_once()
    await builder.call_args.kwargs["runtime_state"].runtime_policy_snapshot.disconnect()


async def test_failed_graph_build_keeps_previous_runtime(policy_state, monkeypatch):
    old = SimpleNamespace(aclose=AsyncMock())
    policy_state.agent = old
    monkeypatch.setattr(
        main, "_build_published_default_agent", AsyncMock(side_effect=RuntimeError("build failed"))
    )
    with pytest.raises(RuntimeError, match="build failed"):
        await main._replace_default_runtime(policy_state, {}, 2)
    assert policy_state.agent is old
    assert policy_state.current_llm == "old-llm"
    old.aclose.assert_not_awaited()
    assert [p.id for p in await policy_state.policy_system.storage.list_policies(strict=True)] == ["approval"]


async def test_runtime_snapshot_preserves_postgres_cosine_matching(policy_state):
    from cuga.backend.storage.policy.prod import ProdPolicyStore

    policies = await policy_state.policy_system.storage.list_policies(strict=True)
    policy_state.policy_system.storage._backend = MagicMock(spec=ProdPolicyStore)
    prepared = await runtime_refresh.prepare_runtime(
        policy_state, {"policies": {"policies": [policy.model_dump(mode="json") for policy in policies]}}
    )
    try:
        await prepared.policy_system.storage.update_policy(policies[0], embedding=[2.0, 0.0])
        matches = await prepared.policy_system.storage.search_policies([1.0, 0.0])
        assert matches[0][0].id == "approval"
        assert matches[0][1] == pytest.approx(1.0)
    finally:
        await prepared.runtime_policy_snapshot.disconnect()


async def test_direct_policy_save_updates_published_config_and_future_runtime(policy_state, monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", True)
    policy_state.policy_filesystem_sync = None
    policy_state.agent = SimpleNamespace(_runtime_policy_snapshot=object(), _runtime_config={})
    monkeypatch.setattr(main, "app_state", policy_state)
    monkeypatch.setattr(main, "_resolve_policy_agent_id", AsyncMock(return_value="cuga-default"))
    data = {"enablePolicies": True, "policies": []}
    request = SimpleNamespace(headers={}, json=AsyncMock(return_value=data))
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_config",
        AsyncMock(return_value=({"special_instructions": "keep"}, 3)),
    )
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr("cuga.backend.server.config_store.update_published_config_at_version", persist)
    replace = AsyncMock()
    monkeypatch.setattr(main, "_replace_default_runtime", replace)
    response = await main.save_policies_config(request, agent_id="cuga-default", current_user=None)
    assert response.status_code == 200
    config = {"special_instructions": "keep", "policies": data}
    persist.assert_awaited_once_with(config, "cuga-default", 3)
    replace.assert_awaited_once_with(policy_state, config, 3, reread=True)


async def test_retired_runtime_closes_only_after_both_concurrent_streams_finish():
    snapshot = SimpleNamespace(disconnect=AsyncMock())
    owner = SimpleNamespace(aclose=AsyncMock(), _runtime_policy_snapshot=snapshot)
    started = [asyncio.Event(), asyncio.Event()]
    finish = [asyncio.Event(), asyncio.Event()]

    @runtime_refresh.runtime_stream
    async def stream(index):
        runtime_refresh.retain_runtime(owner)
        runtime_refresh.retain_runtime(owner)  # One stream counts once even if it reuses its graph.
        started[index].set()
        await finish[index].wait()
        yield "done"

    async def consume(index):
        return [chunk async for chunk in stream(index)]

    tasks = [asyncio.create_task(consume(index)) for index in range(2)]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=5)
        owner._runtime_retired = True
        await runtime_refresh.close_retired_runtime(owner)
        owner.aclose.assert_not_awaited()
        finish[0].set()
        assert await asyncio.wait_for(tasks[0], timeout=5) == ["done"]
        owner.aclose.assert_not_awaited()
        snapshot.disconnect.assert_not_awaited()
        finish[1].set()
        assert await asyncio.wait_for(tasks[1], timeout=5) == ["done"]
        owner.aclose.assert_awaited_once()
        snapshot.disconnect.assert_awaited_once()
    finally:
        for event in finish:
            event.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("manager_mode", [True, False])
async def test_replacement_loads_published_manager_tools_before_build(
    policy_state, monkeypatch, manager_mode
):
    import httpx
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers import combined
    from cuga.backend.tools_env.registry.utils import api_utils
    from cuga.config import settings

    monkeypatch.setenv("CUGA_MANAGER_MODE", str(manager_mode).lower())
    monkeypatch.setattr(settings.advanced_features, "registry", True)
    monkeypatch.setattr(combined.tracker, "apps", [])
    monkeypatch.setattr(api_utils, "get_registry_base_url", lambda: "http://registry.test")
    requests = []

    def registry_request(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(registry_request))
    monkeypatch.setattr("cuga.backend.server.manage_routes.apply.httpx.AsyncClient", lambda: client)

    async def applications(agent_id):
        # The registry keeps cached MCP connections until /reload reads the published config.
        if manager_mode and not requests:
            return []
        return [SimpleNamespace(name="filesystem", url="http://registry.test/filesystem", type="mcp")]

    monkeypatch.setattr(combined, "get_apps", applications)
    policy_state.agent = SimpleNamespace(aclose=AsyncMock())

    async def build(
        *, tool_provider, startup_config, startup_llm_cfg, startup_version, langfuse_handler, runtime_state
    ):
        await tool_provider.initialize()
        apps = await tool_provider.get_apps()
        assert [app.name for app in apps] == ["filesystem"]
        assert tool_provider.get_include_by_app() == ({"filesystem": ["read_file"]}, 2)
        return SimpleNamespace(
            aclose=AsyncMock(), _runtime_policy_snapshot=runtime_state.runtime_policy_snapshot
        )

    monkeypatch.setattr(main, "_build_published_default_agent", build)
    replacement = await main._replace_default_runtime(
        policy_state, {"tools": [{"name": "filesystem", "include": ["read_file"]}]}, 2
    )
    assert requests == ([("POST", "/reload")] if manager_mode else [])
    await replacement._runtime_policy_snapshot.disconnect()
    await client.aclose()
