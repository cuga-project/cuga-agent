import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.evolve.retention import (
    DEFAULT_RETENTION_POLICY,
    DEFAULT_RETENTION_POLICY_ID,
    find_orphaned_memory_entities,
)
from cuga.backend.server.auth import require_chat_access, require_manage_access
from cuga.backend.server.auth.models import UserInfo
from cuga.backend.server.main import app
from cuga.backend.server.memory_routes import _list_retention_inventory

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def auth_overrides():
    app.dependency_overrides[require_chat_access] = lambda: UserInfo(sub="user-1")
    app.dependency_overrides[require_manage_access] = lambda: UserInfo(sub="admin-1", roles=["ServiceAdmin"])
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.asyncio
async def test_run_retention_serializes_server_scope():
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(
            EvolveIntegration,
            "_call_tool",
            new=AsyncMock(return_value={"run_id": "run-a"}),
        ) as call_tool,
    ):
        await EvolveIntegration.run_retention(
            "standard",
            dry_run=False,
            run_id="run-a",
            namespace_id="namespace-a",
            metadata_filters={"agent_id": "agent-a"},
            initiated_by="admin-a",
        )

    call_tool.assert_awaited_once_with(
        "run_retention",
        {
            "policy_id": "standard",
            "dry_run": False,
            "run_id": "run-a",
            "namespace_id": "namespace-a",
            "metadata_filters": json.dumps({"agent_id": "agent-a"}),
            "initiated_by": "admin-a",
        },
    )


def test_manual_run_uses_server_policy_scope_and_sanitizes_report(client):
    provider_report = {
        "run_id": "provider-run",
        "dry_run": False,
        "deleted": [
            {
                "entity_id": "entity-a",
                "entity_type": "guideline",
                "action": "delete",
                "outcome": "deleted",
                "content": "private memory",
                "user_id": "user-9",
                "detail": "provider detail with private memory",
                "reason": "unused",
                "rule": "unused-guidelines",
                "session_id": "private-session",
                "source_task_id": "private-task",
            }
        ],
        "flagged": [],
        "skipped": [
            {
                "entity_id": "guideline-a",
                "entity_type": "guideline",
                "action": "skip",
                "outcome": "skipped",
                "reason": "unused",
                "rule": "unused-guidelines",
                "detail": "provider detail containing private memory",
            }
        ],
        "policy": {"secret": "provider internals"},
        "errors": ["database error containing private memory"],
        "warnings": ["warning containing user-9"],
    }
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.run_retention",
            new=AsyncMock(return_value=provider_report),
        ) as run_retention,
        patch(
            "cuga.backend.server.memory_routes._list_retention_inventory",
            new=AsyncMock(return_value=[]),
        ) as list_inventory,
        patch("cuga.backend.server.conversation_history.get_conversation_db") as get_conversation_db,
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="namespace-a"),
    ):
        get_conversation_db.return_value.get_thread_owners_for_agent = AsyncMock(return_value=set())
        response = client.post(
            "/api/manage/memory/retention/runs?agent_id=agent-a",
            json={"policy_id": "policy-a"},
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == "provider-run"
    assert "dry_run" not in response.json()
    assert response.json()["deleted"] == [
        {
            "entity_id": "entity-a",
            "entity_type": "guideline",
            "action": "delete",
            "outcome": "deleted",
            "reason": "Deleted because no use was recorded for more than 180 days.",
        }
    ]
    assert response.json()["skipped"] == [
        {
            "entity_id": "guideline-a",
            "entity_type": "guideline",
            "action": "skip",
            "outcome": "skipped",
            "reason": "No recorded last-used date was available, so this guideline was kept instead of being deleted.",
        }
    ]
    assert "private memory" not in response.text
    assert "user-9" not in response.text
    run_retention.assert_awaited_once_with(
        "policy-a",
        dry_run=False,
        scan_limit=None,
        namespace_id="namespace-a",
        initiated_by="admin-1",
    )
    assert response.json()["errors"] == ["One or more memories could not be evaluated."]
    assert response.json()["warnings"] == ["Some memories were evaluated with incomplete usage data."]
    list_inventory.assert_not_awaited()
    get_conversation_db.assert_not_called()


def test_manual_run_always_applies_retention(client):
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.run_retention",
            new=AsyncMock(return_value={"flagged": [], "deleted": [], "skipped": [], "errors": []}),
        ) as run_retention,
        patch(
            "cuga.backend.server.memory_routes._list_retention_inventory",
            new=AsyncMock(return_value=[]),
        ),
        patch("cuga.backend.server.conversation_history.get_conversation_db") as get_conversation_db,
    ):
        get_conversation_db.return_value.get_thread_owners_for_agent = AsyncMock(return_value=set())
        response = client.post("/api/manage/memory/retention/runs", json={"policy_id": "policy-a"})

    assert response.status_code == 200
    assert run_retention.await_args.kwargs["dry_run"] is False


def test_manual_run_rejects_removed_preview_option(client):
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.run_retention",
            new=AsyncMock(),
        ) as run_retention,
    ):
        response = client.post(
            "/api/manage/memory/retention/runs",
            json={"policy_id": "policy-a", "dry_run": True},
        )

    assert response.status_code == 422
    run_retention.assert_not_awaited()


def test_orphan_detection_resolves_direct_and_derived_conversations():
    old = "2026-08-01T00:00:00Z"
    entities = [
        {
            "id": "trajectory-a",
            "type": "trajectory",
            "created_at": old,
            "metadata": {"task_id": "task-a", "session_id": "thread-b"},
        },
        {
            "id": "direct",
            "type": "fact",
            "created_at": old,
            "metadata": {"thread_id": "thread-a", "user_id": "user-1"},
        },
        {
            "id": "derived",
            "type": "guideline",
            "created_at": old,
            "metadata": {"source_task_id": "task-a", "user_id": "user-1"},
        },
        {
            "id": "wrong-owner",
            "type": "fact",
            "created_at": old,
            "metadata": {"thread_id": "thread-a", "user_id": "user-2"},
        },
        {"id": "no-source", "type": "policy", "created_at": old, "metadata": {}},
        {
            "id": "fresh",
            "type": "fact",
            "created_at": "2026-09-01T00:00:00Z",
            "metadata": {},
        },
        {
            "id": "held",
            "type": "fact",
            "created_at": old,
            "metadata": {"legal_hold": True},
        },
    ]

    orphaned = find_orphaned_memory_entities(
        entities,
        {("thread-a", "user-1"), ("thread-b", "user-1")},
        now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )

    assert [item["id"] for item in orphaned] == ["wrong-owner", "no-source"]


def test_manual_run_discards_deleted_titles_and_uses_only_evolve_policy(client):
    orphan = {
        "id": "orphan-a",
        "type": "fact",
        "content": "private memory content",
        "created_at": "2026-01-01T00:00:00Z",
        "metadata": {"title": "Orphaned preference", "user_id": "user-1"},
    }
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.run_retention",
            new=AsyncMock(
                return_value={
                    "flagged": [],
                    "deleted": [
                        {
                            "entity_id": "orphan-a",
                            "entity_type": "fact",
                            "action": "delete",
                            "outcome": "deleted",
                            "title": "Orphaned preference",
                            "reason": "orphaned_conversation",
                            "rule": "orphaned-conversations",
                        }
                    ],
                    "skipped": [],
                    "errors": [],
                }
            ),
        ) as run_retention,
        patch(
            "cuga.backend.server.memory_routes._list_retention_inventory",
            new=AsyncMock(return_value=[orphan]),
        ),
        patch(
            "cuga.backend.server.memory_routes._retention_policies",
            new=AsyncMock(return_value=[]),
        ),
        patch("cuga.backend.server.conversation_history.get_conversation_db") as get_conversation_db,
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="namespace-a"),
    ):
        get_conversation_db.return_value.get_thread_owners_for_agent = AsyncMock(return_value=set())
        response = client.post(
            "/api/manage/memory/retention/runs?agent_id=agent-a",
            json={"policy_id": DEFAULT_RETENTION_POLICY_ID},
        )

    assert response.status_code == 200
    assert response.json()["deleted"] == [
        {
            "entity_id": "orphan-a",
            "entity_type": "fact",
            "action": "delete",
            "outcome": "deleted",
            "reason": "Deleted because the memory was more than 7 days old and its source conversation was unavailable.",
        }
    ]
    assert "private memory content" not in response.text
    assert "additional_matches" not in run_retention.await_args.kwargs
    assert "metadata_filters" not in run_retention.await_args.kwargs
    assert "Orphaned preference" not in response.text


@pytest.mark.unit
def test_admin_can_list_evolve_owned_retention_policies(client):
    custom_policy = {
        "policy_id": "strict",
        "name": "Strict retention",
        "description": "Short-lived records",
        "enabled": True,
        "policy": {"rules": [{"name": "old", "max_age_days": 30, "action": "delete"}]},
    }
    default_policy = {
        "policy_id": DEFAULT_RETENTION_POLICY_ID,
        "name": "Standard retention",
        "description": "Default lifecycle policy",
        "enabled": True,
        "policy": DEFAULT_RETENTION_POLICY,
    }
    with (
        patch("cuga.backend.server.memory_routes.EvolveIntegration.is_enabled", return_value=True),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.list_retention_policies",
            new=AsyncMock(return_value={"items": [custom_policy]}),
        ) as list_policies,
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.put_retention_policy",
            new=AsyncMock(return_value=default_policy),
        ) as put_policy,
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="namespace-a"),
    ):
        response = client.get("/api/manage/memory/retention/policies")

    assert response.status_code == 200
    assert [item["policy_id"] for item in response.json()["items"]] == ["strict", DEFAULT_RETENTION_POLICY_ID]
    assert response.json()["items"][0]["rules"] == [{"name": "old", "max_age_days": 30, "action": "delete"}]
    list_policies.assert_awaited_once_with(namespace_id="namespace-a", include_disabled=True)
    assert put_policy.await_args.args[:2] == (DEFAULT_RETENTION_POLICY_ID, "Standard retention")
    assert put_policy.await_args.kwargs["namespace_id"] == "namespace-a"


@pytest.mark.unit
def test_admin_run_history_is_read_from_evolve_and_sanitized(client):
    with (
        patch("cuga.backend.server.memory_routes.EvolveIntegration.is_enabled", return_value=True),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.list_retention_runs",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "run_id": "run-a",
                            "policy_id": "strict",
                            "initiated_by": "admin-1",
                            "status": "completed",
                            "created_at": "2026-09-09T12:00:00Z",
                            "report": {
                                "run_id": "run-a",
                                "policy_id": "strict",
                                "deleted": [],
                                "flagged": [],
                                "skipped": [],
                                "errors": [],
                                "warnings": [],
                                "policy": {"private": True},
                            },
                        }
                    ]
                }
            ),
        ) as list_runs,
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="namespace-a"),
    ):
        response = client.get("/api/manage/memory/retention/runs?agent_id=agent-a&limit=20")

    assert response.status_code == 200
    assert response.json()["items"][0]["policy_id"] == "strict"
    assert "private" not in response.text
    list_runs.assert_awaited_once_with(
        namespace_id="namespace-a",
        limit=20,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orphan_inventory_fails_closed_when_scan_limit_truncates_provenance():
    with (
        patch.object(
            EvolveIntegration,
            "list_entities",
            new=AsyncMock(
                return_value={
                    "items": [{"id": "one", "type": "guideline"}],
                    "next_cursor": "more",
                }
            ),
        ),
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="namespace-a"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _list_retention_inventory(agent_id="agent-a", scan_limit=1)

    assert exc_info.value.status_code == 409


def test_retention_capabilities_report_scheduling_as_unsupported(client):
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.get_compliance_status",
            new=AsyncMock(return_value={"retention_available": True}),
        ),
    ):
        response = client.get("/api/manage/memory/retention")

    assert response.status_code == 200
    assert response.json()["retention_available"] is True
    assert response.json()["scheduling_supported"] is False
    assert response.json()["schedule"]["state"] == "unavailable"
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
    assert response.json()["scheduling_supported"] is False
    assert "connection_string" not in response.text
    assert "config" not in response.text


@pytest.mark.unit
@pytest.mark.parametrize("phase", ["mark", "sweep"])
def test_collection_operations_use_instance_and_authenticated_admin(client, phase):
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(
            EvolveIntegration, "_call_structured_tool", new=AsyncMock(return_value={"run_id": "r"})
        ) as call,
        patch("cuga.backend.server.memory_routes._retention_policies", new=AsyncMock(return_value=[])),
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="instance-a"),
    ):
        response = client.post(f"/api/manage/memory/retention/policies/p/{phase}?agent_id=irrelevant")
    assert response.status_code == 200
    call.assert_awaited_once_with(
        f"{phase}_retention", {"namespace_id": "instance-a", "policy_id": "p", "initiated_by": "admin-1"}
    )


@pytest.mark.unit
@pytest.mark.parametrize("resource", ["candidates", "audit"])
def test_collection_inventory_is_service_scoped(client, resource):
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(
            EvolveIntegration, "_call_structured_tool", new=AsyncMock(return_value={"items": []})
        ) as call,
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="instance-a"),
    ):
        response = client.get(f"/api/manage/memory/retention/{resource}")
    assert response.status_code == 200
    call.assert_awaited_once_with(f"list_retention_{resource}", {"namespace_id": "instance-a", "limit": 1000})


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
