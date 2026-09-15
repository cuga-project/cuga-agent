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
async def test_automatic_dispatch_gated_but_management_available():
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
    ):
        for tool in automatic:
            await EvolveIntegration._call_tool(tool, {"user_id": "alice"})
        transport.assert_not_awaited()
        await EvolveIntegration.list_entities(user_id="alice")
        assert transport.await_count == 1
        await preferences.set_preference(user_id="admin", enabled=True, instance=True)
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
