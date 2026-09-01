from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

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


def test_disabled_feature_returns_not_found_without_calling_evolve(client):
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=False,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.list_entities",
            new=AsyncMock(),
        ) as list_entities,
    ):
        response = client.get("/api/memory/entities")

    assert response.status_code == 404
    list_entities.assert_not_awaited()


def test_user_inventory_is_scoped_and_projected(client):
    inventory = {
        "items": [
            {
                "id": "entity-7",
                "type": "fact",
                "created_at": "2026-07-24T12:00:00Z",
                "content_preview": "private preview",
                "metadata": {
                    "title": "Preference",
                    "category": "preference",
                    "value": "private value",
                    "session_id": "thread-a",
                },
            }
        ],
        "total": 1,
        "next_cursor": None,
    }
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.list_entities",
            new=AsyncMock(return_value=inventory),
        ) as list_entities,
        patch(
            "cuga.backend.server.memory_routes.get_memory_usage_summaries",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "cuga.backend.server.memory_routes.get_available_conversation_thread_ids",
            new=AsyncMock(return_value={"thread-a"}),
        ),
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="namespace-a"),
    ):
        response = client.get("/api/memory/entities?agent_id=agent-a&entity_type=fact&limit=25")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "entity-7",
                "type": "fact",
                "created_at": "2026-07-24T12:00:00Z",
                "metadata": {"title": "Preference", "category": "preference"},
                "usage": {"count": 0, "last_used_at": None, "recent": []},
                "source_thread_id": "thread-a",
                "source_available": True,
            }
        ],
        "total": 1,
        "next_cursor": None,
    }
    assert "private" not in response.text
    list_entities.assert_awaited_once_with(
        entity_types=["fact"],
        user_id="user-1",
        agent_id="agent-a",
        session_id=None,
        metadata_filters=None,
        cursor=None,
        limit=25,
        include_content=False,
        namespace_id="namespace-a",
    )


def test_user_source_prefers_verified_thread_id_and_hides_unavailable_source(client):
    inventory = {
        "items": [
            {
                "id": "entity-available",
                "type": "fact",
                "metadata": {"thread_id": "thread-a", "session_id": "session-a"},
            },
            {
                "id": "entity-unavailable",
                "type": "fact",
                "metadata": {"session_id": "unknown-session"},
            },
        ],
        "total": 2,
    }
    with (
        patch("cuga.backend.server.memory_routes.EvolveIntegration.is_enabled", return_value=True),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.list_entities",
            new=AsyncMock(return_value=inventory),
        ),
        patch(
            "cuga.backend.server.memory_routes.get_memory_usage_summaries",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "cuga.backend.server.memory_routes.get_available_conversation_thread_ids",
            new=AsyncMock(return_value={"thread-a", "session-a"}),
        ),
    ):
        response = client.get("/api/memory/entities?agent_id=agent-a")

    assert response.status_code == 200
    available, unavailable = response.json()["items"]
    assert available["source_thread_id"] == "thread-a"
    assert available["source_available"] is True
    assert unavailable["source_thread_id"] is None
    assert unavailable["source_available"] is False


def test_admin_inventory_never_returns_content_or_conversation_labels(client):
    inventory = {
        "items": [
            {
                "id": "entity-7",
                "type": "fact",
                "created_at": "2026-07-24T12:00:00Z",
                "content": "private content",
                "metadata": {"title": "Preference", "legal_hold": False},
            }
        ],
        "total": 1,
    }
    usage = {
        "entity-7": {
            "count": 2,
            "last_used_at": "2026-07-25T12:00:00Z",
            "recent": [],
        }
    }
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.list_entities",
            new=AsyncMock(return_value=inventory),
        ) as list_entities,
        patch(
            "cuga.backend.server.memory_routes.get_memory_usage_summaries",
            new=AsyncMock(return_value=usage),
        ) as summaries,
    ):
        response = client.get("/api/manage/memory/entities?agent_id=agent-a")

    assert response.status_code == 200
    assert "private content" not in response.text
    assert response.json()["items"][0]["usage"]["recent"] == []
    assert list_entities.await_args.kwargs["include_content"] is False
    assert summaries.await_args.kwargs["include_recent"] is False


def test_user_metadata_rejects_scope_and_retention_fields(client):
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.patch_entity_metadata",
            new=AsyncMock(),
        ) as patch_metadata,
    ):
        response = client.patch(
            "/api/memory/entities/entity-7/metadata",
            json={"metadata": {"owner_id": "user-2", "legal_hold": False}},
        )

    assert response.status_code == 422
    patch_metadata.assert_not_awaited()


def test_permission_errors_are_mapped_without_exposing_provider_details(client):
    with (
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.server.memory_routes.EvolveIntegration.get_entity",
            new=AsyncMock(return_value={"error": "Permission denied: owner user-99"}),
        ),
    ):
        response = client.get("/api/memory/entities/entity-7")

    assert response.status_code == 403
    assert response.json() == {"detail": "Memory access denied"}
    assert "user-99" not in response.text


def test_metadata_filters_cannot_override_server_scope(client):
    with patch(
        "cuga.backend.server.memory_routes.EvolveIntegration.is_enabled",
        return_value=True,
    ):
        response = client.get('/api/memory/entities?metadata_filters={"user_id":"user-2"}')

    assert response.status_code == 422
