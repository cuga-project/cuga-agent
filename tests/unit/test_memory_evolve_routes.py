from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from cuga.backend.server.auth.models import UserInfo
from cuga.backend.server.main import app, require_chat_access, require_manage_access

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_overrides():
    app.dependency_overrides[require_chat_access] = lambda: UserInfo(sub="user-1")
    app.dependency_overrides[require_manage_access] = lambda: UserInfo(
        sub="admin-1",
        roles=["ServiceAdmin"],
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_user_inventory_route_scopes_to_authenticated_user_and_active_agent(client):
    inventory = {
        "items": [
            {
                "id": "entity-7",
                "type": "fact",
                "created_at": "2026-07-24T12:00:00Z",
                "content_preview": "duplicate",
                "metadata": {
                    "title": "Preference",
                    "category": "preference",
                    "value": "must not leak",
                    "nested": {"secret": "must not leak"},
                },
            }
        ],
        "total": 1,
        "next_cursor": None,
        "namespace_id": "tenant-a",
    }
    with (
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.list_entities",
            new=AsyncMock(return_value=inventory),
        ) as list_entities,
        patch("cuga.backend.server.main._memory_namespace_id", return_value="tenant-a"),
    ):
        response = client.get("/api/memory/entities?entity_type=fact&limit=25")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "entity-7",
                "type": "fact",
                "created_at": "2026-07-24T12:00:00Z",
                "metadata": {"title": "Preference", "category": "preference"},
                "usage": {"count": 0, "last_used_at": None, "recent": []},
            }
        ],
        "total": 1,
        "next_cursor": None,
    }
    list_entities.assert_awaited_once_with(
        entity_types=["fact"],
        user_id="user-1",
        agent_id="cuga-default",
        session_id=None,
        metadata_filters=None,
        cursor=None,
        limit=25,
        include_content=False,
        record_access=False,
        namespace_id="tenant-a",
    )


def test_user_retention_route_returns_active_agent_policy_summary(client):
    summary = {
        "rules": [{"summary": "Guidance reviewed after 90 days", "scheduled": False}],
    }
    with patch(
        "cuga.backend.evolve.compliance_poc.get_user_retention_summary",
        new=AsyncMock(return_value=summary),
    ) as get_summary:
        response = client.get("/api/memory/retention")

    assert response.status_code == 200
    assert response.json() == summary
    get_summary.assert_awaited_once_with("cuga-default")


def test_admin_retention_route_forwards_policy_and_clock(client):
    report = {
        "run_id": "run-1",
        "dry_run": True,
        "flagged": [],
        "deleted": [],
        "skipped": [],
        "summary": (
            "Retention evaluation found 0 for review, 0 deletion matches, and "
            "0 kept because evidence was incomplete."
        ),
        "errors": [],
        "warnings": [],
        "policy": {"private": "must not leak"},
        "metadata_filters": {"user_id": "must not leak"},
    }
    policy = {"rules": [{"name": "stale", "max_age_days": 90, "action": "flag"}]}
    with (
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.run_retention",
            new=AsyncMock(return_value=report),
        ) as run_retention,
        patch("cuga.backend.server.main._memory_namespace_id", return_value="tenant-a"),
    ):
        response = client.post(
            "/api/admin/memory/retention/runs",
            json={
                "policy": policy,
                "dry_run": True,
                "as_of": "2026-07-24T12:00:00+00:00",
                "run_id": "run-1",
                "metadata_filters": {"agent_id": "other-agent", "category": "guidance"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-1",
        "dry_run": True,
        "flagged": [],
        "deleted": [],
        "skipped": [],
        "summary": (
            "Retention evaluation found 0 for review, 0 deletion matches, and "
            "0 kept because evidence was incomplete."
        ),
        "errors": [],
        "warnings": [],
    }
    run_retention.assert_awaited_once_with(
        policy,
        dry_run=True,
        as_of="2026-07-24T12:00:00+00:00",
        scan_limit=None,
        run_id="run-1",
        namespace_id="tenant-a",
        metadata_filters={"agent_id": "cuga-default", "category": "guidance"},
    )


def test_memory_routes_map_mcp_permission_errors(client):
    with patch(
        "cuga.backend.evolve.integration.EvolveIntegration.get_entity",
        new=AsyncMock(return_value={"error": "Permission denied: caller is not the owner"}),
    ):
        response = client.get("/api/memory/entities/entity-7")

    assert response.status_code == 403


def test_user_metadata_route_rejects_retention_and_ownership_fields(client):
    with patch(
        "cuga.backend.evolve.integration.EvolveIntegration.patch_entity_metadata",
        new=AsyncMock(),
    ) as patch_metadata:
        response = client.patch(
            "/api/memory/entities/entity-7/metadata",
            json={"metadata": {"legal_hold": False, "owner_id": "user-2"}},
        )

    assert response.status_code == 422
    patch_metadata.assert_not_awaited()


def test_user_access_route_uses_server_time_and_active_scope(client):
    with (
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.record_access",
            new=AsyncMock(return_value={"updated_ids": ["entity-7"]}),
        ) as record_access,
        patch("cuga.backend.server.main._memory_namespace_id", return_value="tenant-a"),
    ):
        response = client.post(
            "/api/memory/access",
            json={"entity_ids": ["entity-7"]},
        )

    assert response.status_code == 200
    record_access.assert_awaited_once_with(
        ["entity-7"],
        user_id="user-1",
        agent_id="cuga-default",
        namespace_id="tenant-a",
    )


def test_successful_forget_is_recorded_in_compliance_activity(client):
    with (
        patch(
            "cuga.backend.evolve.integration.EvolveIntegration.delete_entity",
            new=AsyncMock(return_value={"success": True}),
        ),
        patch(
            "cuga.backend.evolve.compliance_poc.record_user_request",
            new=AsyncMock(),
        ) as record_request,
    ):
        response = client.delete("/api/memory/entities/entity-7")

    assert response.status_code == 200
    record_request.assert_awaited_once_with(
        "cuga-default",
        "user-1",
        "entity-7",
        action="forget",
        status="completed",
    )


def test_admin_inventory_never_returns_memory_content(client):
    inventory = {
        "items": [
            {
                "id": "entity-7",
                "type": "fact",
                "created_at": "2026-07-24T12:00:00Z",
                "content": "private",
                "content_preview": "also private",
                "metadata": {
                    "title": "Preference",
                    "category": "preference",
                    "value": "private in metadata",
                    "nested": {"content": "private in nested metadata"},
                    "legal_hold": False,
                },
            }
        ],
        "total": 1,
        "namespace_id": "tenant-a",
    }
    with patch(
        "cuga.backend.evolve.integration.EvolveIntegration.list_entities",
        new=AsyncMock(return_value=inventory),
    ) as list_entities:
        response = client.get("/api/admin/memory/entities?include_content=true")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "entity-7",
                "type": "fact",
                "created_at": "2026-07-24T12:00:00Z",
                "metadata": {
                    "title": "Preference",
                    "category": "preference",
                    "legal_hold": False,
                },
                "usage": {"count": 0, "last_used_at": None, "recent": []},
            }
        ],
        "total": 1,
        "next_cursor": None,
    }
    assert "private" not in response.text
    list_entities.assert_awaited_once()
    assert list_entities.await_args.kwargs["agent_id"] == "cuga-default"
    assert list_entities.await_args.kwargs["include_content"] is False


def test_admin_entity_update_returns_sanitized_active_agent_projection(client):
    entity = {
        "id": "entity-7",
        "type": "fact",
        "created_at": "2026-07-24T12:00:00Z",
        "content": "private",
        "content_preview": "also private",
        "metadata": {
            "title": "Preference",
            "value": "private in metadata",
            "retention_rule": "stale-guidelines",
        },
    }
    with patch(
        "cuga.backend.evolve.integration.EvolveIntegration.patch_entity_metadata",
        new=AsyncMock(return_value=entity),
    ) as patch_metadata:
        response = client.patch(
            "/api/admin/memory/entities/entity-7/metadata",
            json={"metadata": {"legal_hold": True}},
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "entity-7",
        "type": "fact",
        "created_at": "2026-07-24T12:00:00Z",
        "metadata": {
            "title": "Preference",
            "retention_rule": "stale-guidelines",
        },
        "usage": {"count": 0, "last_used_at": None, "recent": []},
    }
    assert "private" not in response.text
    patch_metadata.assert_awaited_once_with(
        "entity-7",
        {"legal_hold": True},
        agent_id="cuga-default",
        namespace_id=None,
    )


def test_admin_entity_update_rejects_scope_and_content_fields(client):
    with patch(
        "cuga.backend.evolve.integration.EvolveIntegration.patch_entity_metadata",
        new=AsyncMock(),
    ) as patch_metadata:
        response = client.patch(
            "/api/admin/memory/entities/entity-7/metadata",
            json={
                "metadata": {
                    "agent_id": "other-agent",
                    "user_id": "other-user",
                    "value": "private",
                }
            },
        )

    assert response.status_code == 422
    patch_metadata.assert_not_awaited()


def test_automation_route_rejects_invalid_schedule(client):
    response = client.patch(
        "/api/admin/memory/automation",
        json={"retention_frequency": "Whenever", "retention_time": "25:90"},
    )

    assert response.status_code == 422


def test_scheduled_run_reports_evolve_unavailability(client):
    with patch(
        "cuga.backend.evolve.compliance_poc.run_simulated_schedule",
        new=AsyncMock(side_effect=RuntimeError("Evolve retention service is unavailable")),
    ):
        response = client.post(
            "/api/admin/memory/scheduled-runs",
            json={
                "policy": {"rules": [{"name": "stale", "max_age_days": 90}]},
                "dry_run": True,
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Evolve retention service is unavailable"


def test_poc_routes_require_manage_access_and_forward_active_namespace(client, monkeypatch):
    monkeypatch.setenv("CUGA_COMPLIANCE_POC_SEED_ENABLED", "1")
    with (
        patch("cuga.backend.server.main._memory_namespace_id", return_value="tenant-a"),
        patch(
            "cuga.backend.evolve.compliance_poc.bootstrap", new=AsyncMock(return_value={"memory_count": 36})
        ) as bootstrap,
    ):
        response = client.post("/api/admin/memory/poc/bootstrap")

    assert response.status_code == 200
    bootstrap.assert_awaited_once()
    assert bootstrap.await_args.args[0] == "cuga-default"
    assert bootstrap.await_args.args[2] == "tenant-a"

    app.dependency_overrides[require_manage_access] = lambda: (_ for _ in ()).throw(
        HTTPException(status_code=403)
    )
    denied = client.get("/api/admin/memory/activity")
    assert denied.status_code == 403
