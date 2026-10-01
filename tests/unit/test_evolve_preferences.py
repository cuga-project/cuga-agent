from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.evolve import preferences
from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.server.auth import require_chat_access, require_manage_access
from cuga.backend.server.auth.models import UserInfo
from cuga.backend.server.memory_routes import router
from cuga.backend.storage.facade import get_storage

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    import cuga.backend.storage.facade as facade

    monkeypatch.setattr(facade, "_local_db_path", lambda: str(tmp_path / "settings.db"))
    monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "service-a")
    monkeypatch.setenv("DYNACONF_SERVICE__TENANT_ID", "tenant-a")
    monkeypatch.setattr(preferences.settings.evolve, "enabled", False)
    get_storage().invalidate_relational_stores()
    yield
    get_storage().invalidate_relational_stores()


@pytest.mark.asyncio
async def test_precedence_and_replica_refresh(monkeypatch):
    assert not await preferences.memory_enabled("alice")
    await preferences.set_preference(user_id="admin", enabled=True, instance=True)
    assert await preferences.memory_enabled("alice")
    await preferences.set_preference(user_id="alice", enabled=False)
    assert not await preferences.memory_enabled("alice")
    assert await preferences.memory_enabled("bob")
    # Reopening storage simulates another replica observing persisted configuration.
    get_storage().invalidate_relational_stores()
    assert not await preferences.memory_enabled("alice")
    assert await preferences.memory_enabled("bob")
    await preferences.set_preference(user_id="admin", enabled=False, instance=True)
    assert not await preferences.memory_enabled("bob")
    await preferences.set_preference(user_id="admin", enabled=True, instance=True)
    assert not await preferences.memory_enabled("alice")
    monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "service-b")
    assert not await preferences.memory_enabled("bob")
    monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "service-a")
    monkeypatch.setenv("DYNACONF_SERVICE__TENANT_ID", "tenant-b")
    assert not await preferences.memory_enabled("bob")
    monkeypatch.setenv("DYNACONF_SERVICE__TENANT_ID", "tenant-a")
    await preferences.set_preference(user_id="admin", enabled=None, instance=True)
    assert not await preferences.memory_enabled("bob")
    monkeypatch.setattr(preferences.settings.evolve, "enabled", True)
    assert await preferences.memory_enabled("bob")
    assert not await preferences.memory_enabled("alice")


@pytest.mark.asyncio
async def test_service_allows_reads_but_blocks_mutations_and_agent_retrieval():
    automatic = [
        "get_guidelines",
        "get_guidelines_with_attribution",
        "store_user_facts",
        "retrieve_user_facts",
        "save_trajectory",
    ]
    with (
        patch.object(EvolveIntegration, "_get_mode", return_value="direct"),
        patch.object(
            EvolveIntegration, "_call_tool_direct", new=AsyncMock(return_value={"items": []})
        ) as transport,
        patch.object(
            EvolveIntegration,
            "_episodic_profile",
            new=AsyncMock(return_value={"id": "profile", "revision": 1}),
        ),
    ):
        for tool in automatic:
            assert await EvolveIntegration._call_tool(tool, {"user_id": "alice"}) is None
        for tool in [
            "run_retention",
            "sweep_retention",
            "delete_entity",
            "record_access",
            "start_retention_schedule",
        ]:
            with pytest.raises(RuntimeError, match="read-only"):
                await EvolveIntegration._call_tool(tool, {"user_id": "alice"})
        transport.assert_not_awaited()
        assert await EvolveIntegration.list_entities(user_id="alice") == {"items": []}
        assert transport.await_count == 1
        await preferences.set_preference(user_id="admin", enabled=True, instance=True)
        await preferences.set_episodic_preference(user_id="admin", enabled=True)
        for tool in automatic:
            await EvolveIntegration._call_tool(tool, {"user_id": "alice"})
        assert transport.await_count == 6
        await preferences.set_preference(user_id="alice", enabled=False)
        await EvolveIntegration._call_tool("store_user_facts", {"user_id": "alice"})
        assert transport.await_count == 6


def test_routes_use_authenticated_identity_and_require_admin():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_chat_access] = lambda: UserInfo(sub="alice")
    # Explicitly deny admin access to model a logged-in non-administrator.
    from fastapi import HTTPException

    def deny_admin():
        raise HTTPException(status_code=403)

    app.dependency_overrides[require_manage_access] = deny_admin
    with TestClient(app) as client:
        assert client.get("/api/memory/settings").status_code == 200
        assert client.delete("/api/memory/entities/fact-a").status_code == 403
        assert client.get("/api/manage/memory/entities").status_code == 403
        assert client.put("/api/manage/memory/settings", json={"enabled": True}).status_code == 403
        assert (
            client.put("/api/memory/settings", json={"enabled": False, "user_id": "bob"}).status_code == 422
        )
        assert client.put("/api/memory/settings", json={"enabled": False}).json()["user_enabled"] is False
        app.dependency_overrides[require_chat_access] = lambda: UserInfo(sub="bob")
        assert client.get("/api/memory/settings").json()["user_enabled"] is True
        app.dependency_overrides[require_manage_access] = lambda: UserInfo(sub="admin")
        assert (
            client.put("/api/manage/memory/settings", json={"enabled": True}).json()["instance_enabled"]
            is True
        )
        assert client.get("/api/memory/settings").json()["effective_enabled"] is True


@pytest.mark.asyncio
async def test_storage_failure_disables_automatic_memory():
    with patch.object(preferences, "get_preferences", new=AsyncMock(side_effect=RuntimeError("offline"))):
        assert not await preferences.memory_enabled("alice")


@pytest.mark.asyncio
async def test_disabled_service_browsing_does_not_register_retention_policy():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_manage_access] = lambda: UserInfo(sub="admin")
    app.dependency_overrides[require_chat_access] = lambda: UserInfo(sub="alice")
    with (
        TestClient(app) as client,
        patch.object(
            EvolveIntegration, "list_entities", new=AsyncMock(return_value={"items": [], "total": 0})
        ),
        patch.object(EvolveIntegration, "list_retention_policies", new=AsyncMock(return_value={"items": []})),
        patch.object(EvolveIntegration, "put_retention_policy", new=AsyncMock()) as create,
    ):
        assert client.get("/api/memory/entities").status_code == 200
        from cuga.backend.server.memory_routes import _retention_policies

        assert await _retention_policies() == []
        create.assert_not_awaited()
        assert client.post("/api/manage/retention/runs", json={"policy_id": "p"}).status_code == 403


@pytest.mark.asyncio
async def test_episodic_setting_is_shared_scoped_and_preserved(monkeypatch):
    await preferences.set_preference(user_id="admin", enabled=True, instance=True)
    assert (await preferences.get_preferences("alice"))["episodic_enabled"]
    await preferences.set_episodic_preference(user_id="admin", enabled=False)
    get_storage().invalidate_relational_stores()
    assert not (await preferences.get_preferences("bob"))["episodic_enabled"]
    await preferences.set_preference(user_id="admin", enabled=False, instance=True)
    assert not (await preferences.get_preferences("bob"))["episodic_enabled"]
    assert not await preferences.memory_enabled("bob")
    monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "service-b")
    assert (await preferences.get_preferences("bob"))["episodic_enabled"]
    monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "service-a")
    monkeypatch.setenv("DYNACONF_SERVICE__TENANT_ID", "tenant-b")
    assert (await preferences.get_preferences("bob"))["episodic_enabled"]


@pytest.mark.asyncio
async def test_semantic_only_skips_guidelines_and_processing_but_keeps_facts():
    await preferences.set_episodic_preference(user_id="admin", enabled=False)
    await preferences.set_preference(user_id="admin", enabled=True, instance=True)
    with (
        patch.object(EvolveIntegration, "_get_mode", return_value="direct"),
        patch.object(EvolveIntegration, "_call_tool_direct", new=AsyncMock(return_value={})) as transport,
        patch.object(
            EvolveIntegration,
            "_episodic_profile",
            new=AsyncMock(return_value={"id": "profile", "revision": 3}),
        ) as profile,
    ):
        for tool in ["save_trajectory", "get_guidelines", "get_guidelines_with_attribution"]:
            await EvolveIntegration._call_tool(tool, {"user_id": "alice"})
        transport.assert_not_awaited()
        profile.assert_not_awaited()
        for tool in ["store_user_facts", "retrieve_user_facts"]:
            await EvolveIntegration._call_tool(tool, {"user_id": "alice"})
        assert transport.await_count == 2
        await preferences.set_episodic_preference(user_id="admin", enabled=True)
        await EvolveIntegration._call_tool("save_trajectory", {"user_id": "alice"})
        transport.assert_awaited_with(
            "save_trajectory",
            {
                "user_id": "alice",
                "namespace_id": "service-a",
                "processing_profile": "profile",
                "profile_revision": 3,
            },
        )
        await preferences.set_preference(user_id="alice", enabled=False)
        await EvolveIntegration._call_tool("save_trajectory", {"user_id": "alice"})
        assert transport.await_count == 3
        await preferences.set_episodic_preference(user_id="admin", enabled=False)
        await EvolveIntegration._call_tool("get_guidelines", {"user_id": "bob"})
        assert transport.await_count == 3


def test_episodic_setting_requires_admin_and_enabled_service():
    from fastapi import HTTPException

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_chat_access] = lambda: UserInfo(sub="alice")

    def deny_admin():
        raise HTTPException(403)

    app.dependency_overrides[require_manage_access] = deny_admin
    with TestClient(app) as client:
        endpoint = "/api/manage/memory/settings/episodic"
        assert client.put(endpoint, json={"enabled": True}).status_code == 403
        assert (
            client.put("/api/memory/settings", json={"enabled": True, "episodic_enabled": True}).status_code
            == 422
        )
        app.dependency_overrides[require_manage_access] = lambda: UserInfo(sub="admin")
        assert client.put(endpoint, json={"enabled": True}).status_code == 403
        client.put("/api/manage/memory/settings", json={"enabled": True})
        assert client.put(endpoint, json={"enabled": True}).json()["episodic_enabled"]
        assert client.get("/api/memory/settings").json()["episodic_enabled"]
        assert client.put(endpoint, json={"enabled": None}).status_code == 422


@pytest.mark.asyncio
async def test_episodic_uses_released_processing_manager(tmp_path, monkeypatch):
    pytest.importorskip("altk_evolve")
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.config.filesystem import FilesystemSettings
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.frontend.mcp import mcp_server as server
    from altk_evolve.llm.guidelines import guidelines, consistency_guidelines
    from altk_evolve.llm.conflict_resolution import conflict_resolution
    from altk_evolve.schema.conflict_resolution import EntityUpdate
    from altk_evolve.schema.guidelines import Guideline, GuidelineGenerationResult
    from langchain_core.messages import HumanMessage

    client = EvolveClient(
        config=EvolveConfig(
            backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path / "evolve"))
        )
    )
    monkeypatch.setattr(server, "get_client", lambda: client)
    monkeypatch.setattr(server, "_initialized_namespaces", set())
    monkeypatch.setattr(preferences.settings.evolve, "save_on_success", True)
    monkeypatch.setattr(EvolveIntegration, "_get_mode", lambda: "direct")
    calls = []

    def generated(method):
        calls.append(method)
        return [
            GuidelineGenerationResult(
                task_description="handoff",
                guidelines=[
                    Guideline(
                        content=f"{method}: start with impact",
                        category="strategy",
                        rationale="clarity",
                        trigger="handoff",
                    )
                ],
            )
        ]

    monkeypatch.setattr(guidelines, "generate_guidelines", lambda *a, **kw: generated("standard"))
    monkeypatch.setattr(
        consistency_guidelines,
        "generate_consistency_guidelines_fast",
        lambda *a, **kw: generated("consistency"),
    )
    monkeypatch.setattr(
        conflict_resolution,
        "resolve_conflicts",
        lambda old, new, **kwargs: [
            EntityUpdate(id=e.id, type=e.type, content=e.content, metadata=e.metadata, event="ADD")
            for e in new
        ],
    )

    async def transport(tool, args):
        return getattr(server, tool)(**args)

    monkeypatch.setattr(EvolveIntegration, "_call_tool_direct", transport)
    await preferences.set_preference(user_id="admin", enabled=True, instance=True)
    await preferences.set_episodic_preference(user_id="admin", enabled=True)
    try:
        await EvolveIntegration.save_trajectory(
            [HumanMessage(content="Start with impact")],
            "task-a",
            True,
            user_id="alice",
            namespace_id="service-a",
            session_id="conversation-a",
            agent_id="agent-a",
        )
        assert calls == ["standard", "consistency"]
        entities = client.search_entities("service-a", filters={"type": "guideline"}, limit=10)
        assert len(entities) == 2
        for entity in entities:
            assert entity.metadata["user_id"] == "alice"
            assert entity.metadata["agent_id"] == "agent-a"
            assert entity.metadata["session_id"] == "conversation-a"
        profile = await EvolveIntegration._episodic_profile()
        assert profile["revision"] == 1
        # A second replica reuses the profile without creating a new revision.
        assert (await EvolveIntegration._episodic_profile())["revision"] == 1
        monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "service-b")
        await preferences.set_preference(user_id="admin", enabled=True, instance=True)
        assert (await EvolveIntegration._episodic_profile())["id"] != profile["id"]
    finally:
        client.backend.close()


@pytest.mark.asyncio
async def test_profile_creation_race_reuses_winning_revision():
    calls = []

    async def tool(name, args):
        calls.append((name, args))
        if len(calls) == 1:
            raise RuntimeError("not found")
        if name == "set_processing_profile":
            assert args["expected_revision"] == 0
            raise RuntimeError("another replica already created it")
        return {"id": args["profile_id"], "revision": 1}

    with patch.object(EvolveIntegration, "_call_tool", new=tool):
        assert (await EvolveIntegration._episodic_profile())["revision"] == 1
    assert [name for name, _ in calls] == [
        "get_processing_profile",
        "set_processing_profile",
        "get_processing_profile",
    ]


@pytest.mark.asyncio
async def test_profile_failure_never_falls_back_to_legacy_generation():
    from langchain_core.messages import HumanMessage

    await preferences.set_preference(user_id="admin", enabled=True, instance=True)
    await preferences.set_episodic_preference(user_id="admin", enabled=True)
    with (
        patch.object(preferences.settings.evolve, "save_on_success", True),
        patch.object(EvolveIntegration, "_get_mode", return_value="direct"),
        patch.object(
            EvolveIntegration, "_call_tool_direct", new=AsyncMock(side_effect=RuntimeError("unavailable"))
        ) as transport,
    ):
        await EvolveIntegration.save_trajectory(
            [HumanMessage(content="hello")], "task", True, user_id="alice"
        )
        assert all(call.args[0] != "save_trajectory" for call in transport.await_args_list)
