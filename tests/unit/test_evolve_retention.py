import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.evolve.retention import DEFAULT_RETENTION_POLICY, find_orphaned_memory_entities
from cuga.backend.server.auth import require_chat_access, require_manage_access
from cuga.backend.server.auth.models import UserInfo
from cuga.backend.server.main import app

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
            DEFAULT_RETENTION_POLICY,
            dry_run=False,
            run_id="run-a",
            namespace_id="namespace-a",
            metadata_filters={"agent_id": "agent-a"},
        )

    call_tool.assert_awaited_once_with(
        "run_retention",
        {
            "policy": json.dumps(DEFAULT_RETENTION_POLICY),
            "dry_run": False,
            "run_id": "run-a",
            "namespace_id": "namespace-a",
            "metadata_filters": json.dumps({"agent_id": "agent-a"}),
        },
    )


def test_manual_run_uses_server_policy_scope_and_sanitizes_report(client):
    provider_report = {
        "run_id": "provider-run",
        "dry_run": False,
        "deleted": [
            {
                "entity_id": "entity-a",
                "action": "delete",
                "outcome": "deleted",
                "content": "private memory",
                "user_id": "user-9",
                "detail": "provider detail with private memory",
                "reason": "provider reason with user-9",
                "session_id": "private-session",
                "source_task_id": "private-task",
            }
        ],
        "flagged": [],
        "skipped": [],
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
            "cuga.backend.evolve.retention_store.save_retention_run",
            new=AsyncMock(),
        ) as save_run,
        patch(
            "cuga.backend.server.memory_routes._list_retention_inventory",
            new=AsyncMock(return_value=[]),
        ),
        patch("cuga.backend.server.conversation_history.get_conversation_db") as get_conversation_db,
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="namespace-a"),
        patch("cuga.backend.server.memory_routes.uuid.uuid4", return_value="run-a"),
    ):
        get_conversation_db.return_value.get_thread_owners_for_agent = AsyncMock(return_value=set())
        response = client.post(
            "/api/manage/memory/retention/runs?agent_id=agent-a",
            json={},
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-a"
    assert "dry_run" not in response.json()
    assert response.json()["deleted"] == [{"entity_id": "entity-a", "action": "delete", "outcome": "deleted"}]
    assert "private memory" not in response.text
    assert "user-9" not in response.text
    run_retention.assert_awaited_once_with(
        DEFAULT_RETENTION_POLICY,
        dry_run=False,
        as_of=None,
        scan_limit=None,
        run_id="run-a",
        namespace_id="namespace-a",
        metadata_filters={"agent_id": "agent-a"},
    )
    assert save_run.await_args.kwargs["actor_id"] == "admin-1"
    assert save_run.await_args.kwargs["agent_id"] == "agent-a"
    persisted_report = json.dumps(save_run.await_args.kwargs["report"])
    assert "private memory" not in persisted_report
    assert "user-9" not in persisted_report
    assert "private-session" not in persisted_report
    assert "private-task" not in persisted_report
    assert save_run.await_args.kwargs["report"]["error_count"] == 1
    assert save_run.await_args.kwargs["report"]["warning_count"] == 1
    assert response.json()["errors"] == ["One or more memories could not be evaluated."]
    assert response.json()["warnings"] == ["Some memories were evaluated with incomplete usage data."]


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
            "cuga.backend.evolve.retention_store.save_retention_run",
            new=AsyncMock(),
        ),
        patch(
            "cuga.backend.server.memory_routes._list_retention_inventory",
            new=AsyncMock(return_value=[]),
        ),
        patch("cuga.backend.server.conversation_history.get_conversation_db") as get_conversation_db,
    ):
        get_conversation_db.return_value.get_thread_owners_for_agent = AsyncMock(return_value=set())
        response = client.post("/api/manage/memory/retention/runs", json={})

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
            json={"dry_run": True},
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


def test_manual_run_deletes_orphaned_memories_and_keeps_a_safe_title(client):
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
            new=AsyncMock(return_value={"flagged": [], "deleted": [], "skipped": [], "errors": []}),
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.delete_entity",
            new=AsyncMock(return_value={"success": True}),
        ) as delete_entity,
        patch(
            "cuga.backend.server.memory_routes._list_retention_inventory",
            new=AsyncMock(return_value=[orphan]),
        ),
        patch("cuga.backend.server.conversation_history.get_conversation_db") as get_conversation_db,
        patch(
            "cuga.backend.evolve.retention_store.save_retention_run",
            new=AsyncMock(),
        ),
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="namespace-a"),
    ):
        get_conversation_db.return_value.get_thread_owners_for_agent = AsyncMock(return_value=set())
        response = client.post(
            "/api/manage/memory/retention/runs?agent_id=agent-a",
            json={"as_of": "2026-09-03T00:00:00Z"},
        )

    assert response.status_code == 200
    assert response.json()["deleted"] == [
        {
            "entity_id": "orphan-a",
            "entity_type": "fact",
            "action": "delete",
            "outcome": "deleted",
            "title": "Orphaned preference",
        }
    ]
    assert "private memory content" not in response.text
    delete_entity.assert_awaited_once_with(
        "orphan-a",
        agent_id="agent-a",
        namespace_id="namespace-a",
    )


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
    assert response.json()["rules"][-1] == {
        "name": "orphaned-conversations",
        "entity_type": "memory",
        "action": "delete",
        "max_age_days": 7,
        "description": "Delete memories whose source conversation remains unavailable after 7 days",
    }


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
