from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.server.auth import require_chat_access, require_manage_access
from cuga.backend.server.auth.models import UserInfo
from cuga.backend.server.main import app

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def auth_overrides(monkeypatch):
    monkeypatch.setattr(
        EvolveIntegration,
        "get_compliance_status",
        AsyncMock(return_value={"backend": "postgres", "retention_available": True}),
    )
    app.dependency_overrides[require_chat_access] = lambda: UserInfo(sub="user-1")
    app.dependency_overrides[require_manage_access] = lambda: UserInfo(sub="admin-1", roles=["ServiceAdmin"])
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_retention_capabilities_report_evolve_schedule_management(client):
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.get_compliance_status",
            new=AsyncMock(return_value={"backend": "postgres", "retention_available": True}),
        ),
    ):
        response = client.get("/api/manage/retention")

    assert response.status_code == 200
    assert response.json()["retention_available"] is True
    assert response.json()["scheduling_supported"] is True
    assert response.json()["schedule"]["state"] == "managed_by_evolve"
    assert (
        next(rule for rule in response.json()["rules"] if rule["name"] == "orphaned-conversations")[
            "source_deleted"
        ]
        is True
    )


def test_compliance_status_does_not_expose_provider_details(client):
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.get_compliance_status",
            new=AsyncMock(
                return_value={
                    "healthy": True,
                    "backend": "postgres",
                    "retention_available": True,
                    "connection_string": "private",
                    "plugins": [
                        {
                            "name": "access-stamp",
                            "enabled": True,
                            "healthy": True,
                            "config": {"private": True},
                        }
                    ],
                }
            ),
        ),
    ):
        response = client.get("/api/manage/memory/compliance/status")

    assert response.status_code == 200
    assert response.json()["scheduling_supported"] is True
    assert "connection_string" not in response.text
    assert "config" not in response.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_collection_transport_cannot_override_instance_namespace(monkeypatch):
    monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "instance-a")
    with (
        patch.object(EvolveIntegration, "_get_mode", return_value="direct"),
        patch.object(
            EvolveIntegration, "_call_tool_direct", new=AsyncMock(return_value={"items": []})
        ) as call,
    ):
        await EvolveIntegration._call_tool("list_retention_candidates", {"namespace_id": "other-tenant"})
        call.assert_awaited_once_with("list_retention_candidates", {"namespace_id": "instance-a"})
        monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "")
        with pytest.raises(ValueError, match="service instance ID"):
            await EvolveIntegration._call_tool(
                "sweep_retention", {"namespace_id": "other-tenant", "policy_id": "p"}
            )


def test_schedule_preview_uses_evolve_without_saving(client):
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(EvolveIntegration, "_call_structured_tool", new=AsyncMock()) as call,
    ):
        response = client.post(
            "/api/manage/retention/schedules/preview",
            json={"spec": {"schedule": "0 2 * * *", "timeZone": "America/New_York", "suspend": True}},
        )
    assert response.status_code == 200
    from datetime import datetime
    from zoneinfo import ZoneInfo

    assert len(response.json()["next_runs"]) == 5
    assert all(
        datetime.fromisoformat(value).astimezone(ZoneInfo("America/New_York")).hour == 2
        for value in response.json()["next_runs"]
    )
    assert response.json()["suspended"] is True
    call.assert_not_awaited()


@pytest.mark.parametrize("spec", [{"schedule": "bad"}, {"schedule": "@daily", "timeZone": "+03:00"}])
def test_schedule_preview_rejects_invalid_timing(client, spec):
    with patch.object(EvolveIntegration, "is_enabled", return_value=True):
        response = client.post("/api/manage/retention/schedules/preview", json={"spec": spec})
    assert response.status_code == 422
