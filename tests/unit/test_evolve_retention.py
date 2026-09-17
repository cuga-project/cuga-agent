import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.evolve.retention import (
    DEFAULT_RETENTION_POLICY,
    DEFAULT_RETENTION_POLICY_ID,
)
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


@pytest.mark.unit
def test_default_policy_runs_on_real_filesystem_backend(client, tmp_path):
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.config.filesystem import FilesystemSettings
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.retention.execution import execute_policy

    evolve = EvolveClient(
        EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path)))
    )
    service = evolve.retention("instance-a")

    async def list_policies(**kwargs):
        return service.list_policies(include_disabled=kwargs["include_disabled"])

    async def put_policy(policy_id, name, policy, **kwargs):
        return service.put_policy(policy_id, name=name, policy=policy)

    async def run_policy(policy_id, **kwargs):
        return execute_policy(evolve, service.store, "instance-a", policy_id, dry_run=False)

    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(
            EvolveIntegration,
            "get_compliance_status",
            new=AsyncMock(return_value={"backend": "filesystem", "retention_available": True}),
        ),
        patch.object(EvolveIntegration, "list_retention_policies", new=list_policies),
        patch.object(EvolveIntegration, "put_retention_policy", new=put_policy),
        patch.object(EvolveIntegration, "run_retention", new=run_policy),
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="instance-a"),
    ):
        response = client.post("/api/manage/memory/retention/runs", json={"policy_id": "cuga-standard"})
        assert response.status_code == 200, response.text
        assert response.json()["errors"] == []
        capabilities = client.get("/api/manage/memory/retention").json()
        assert capabilities["retention_available"] is True
        assert capabilities["mark_sweep_supported"] is False
        assert capabilities["source_deletion_supported"] is False
    policy = service.get_policy("cuga-standard")["policy"]
    assert not any(rule.get("source_deleted") for rule in policy["rules"])
    assert len(policy["rules"]) == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    "path,method",
    [("candidates", "get"), ("audit", "get"), ("policies/p/mark", "post"), ("policies/p/sweep", "post")],
)
def test_filesystem_rejects_postgres_only_operations_before_calling_evolve(client, path, method):
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(
            EvolveIntegration,
            "get_compliance_status",
            new=AsyncMock(return_value={"backend": "filesystem", "retention_available": True}),
        ),
        patch.object(EvolveIntegration, "_call_structured_tool", new=AsyncMock()) as call,
    ):
        response = getattr(client, method)(f"/api/manage/memory/retention/{path}")
    assert response.status_code == 409
    assert "PostgreSQL" in response.json()["detail"]
    call.assert_not_awaited()


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
            run_id="run-a",
            namespace_id="namespace-a",
            initiated_by="admin-a",
        )

    call_tool.assert_awaited_once_with(
        "run_retention",
        {
            "policy_id": "standard",
            "dry_run": False,
            "run_id": "run-a",
            "namespace_id": "namespace-a",
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
            "reason": "Deleted because it matched a deletion rule in the retention policy.",
        }
    ]
    assert response.json()["skipped"] == [
        {
            "entity_id": "guideline-a",
            "entity_type": "guideline",
            "action": "skip",
            "outcome": "skipped",
            "reason": "The retention action was not applied.",
        }
    ]
    assert "private memory" not in response.text
    assert "user-9" not in response.text
    run_retention.assert_awaited_once_with(
        "policy-a",
        scan_limit=None,
        namespace_id="namespace-a",
        initiated_by="admin-1",
    )
    assert response.json()["errors"] == ["One or more retention operations failed."]
    assert response.json()["warnings"] == ["Evolve reported retention warnings; review the run in Evolve."]
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
        patch("cuga.backend.server.conversation_history.get_conversation_db") as get_conversation_db,
    ):
        get_conversation_db.return_value.get_thread_owners_for_agent = AsyncMock(return_value=set())
        response = client.post("/api/manage/memory/retention/runs", json={"policy_id": "policy-a"})

    assert response.status_code == 200
    assert "dry_run" not in run_retention.await_args.kwargs


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


def test_manual_run_discards_deleted_titles_and_uses_only_evolve_policy(client):
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
            "reason": "Deleted because it matched a deletion rule in the retention policy.",
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
        response = client.get("/api/manage/memory/retention")

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


@pytest.mark.parametrize("bucket", ["flagged", "deleted", "skipped"])
def test_report_projection_never_exposes_memory_labels(bucket):
    from cuga.backend.evolve.retention import project_retention_report

    result = project_retention_report(
        {
            bucket: [
                {
                    "entity_id": "memory-a",
                    "outcome": "held",
                    "title": "private title",
                    "metadata": {"display_name": "private label"},
                    "content": "private content",
                }
            ]
        }
    )
    assert result[bucket][0]["entity_id"] == "memory-a"
    assert "private" not in json.dumps(result)


def test_report_rejects_malformed_items_instead_of_silently_dropping_them():
    from pydantic import ValidationError
    from cuga.backend.evolve.retention import project_retention_report

    with pytest.raises(ValidationError):
        project_retention_report({"deleted": ["not a report item"]})


def test_invalid_provider_report_returns_safe_gateway_error():
    from cuga.backend.server.memory_routes import _retention_report_response

    with pytest.raises(HTTPException) as error:
        _retention_report_response({"deleted": [{"entity_id": {"secret": "private content"}}]})
    assert error.value.status_code == 502
    assert error.value.detail == "Evolve returned an invalid retention report"


@pytest.mark.parametrize("operation", ["start", "stop", "delete", "get", "list", "put"])
def test_schedule_routes_forward_service_scope_and_revision(client, operation):
    tool = {"get": "get", "list": "list", "put": "put"}.get(operation, operation)
    tool = f"{tool}_retention_schedule" + ("s" if operation == "list" else "")
    with (
        patch.object(
            EvolveIntegration, "_call_structured_tool", new=AsyncMock(return_value={"revision": 4})
        ) as call,
        patch("cuga.backend.server.memory_routes._namespace_id", return_value="service-a"),
    ):
        path = "/api/manage/memory/retention/schedules"
        if operation != "list":
            path += "/nightly"
        if operation in {"start", "stop"}:
            response = client.post(path + "/" + operation, json={"expected_revision": 3})
        elif operation == "put":
            response = client.put(
                path, json={"policy_id": "custom", "spec": {"schedule": "@daily"}, "expected_revision": 3}
            )
        elif operation == "delete":
            response = client.delete(path + "?expected_revision=3")
        else:
            response = client.get(path)
    assert response.status_code == 200
    args = call.await_args.args
    assert args[0] == tool
    assert args[1]["namespace_id"] == "service-a"
    assert "user_id" not in args[1]
    if operation in {"put", "start", "stop"}:
        assert args[1]["initiated_by"] == "admin-1"
        assert args[1]["expected_revision"] == 3
    if operation == "put":
        assert args[1]["definition"]["agent_id"] is None
        assert args[1]["definition"]["dry_run"] is False


def test_schedule_conflict_requires_refresh(client):
    with patch.object(
        EvolveIntegration,
        "_call_structured_tool",
        new=AsyncMock(return_value={"error": "Schedule revision conflict"}),
    ):
        response = client.post(
            "/api/manage/memory/retention/schedules/nightly/start", json={"expected_revision": 1}
        )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "override",
    [
        {"namespace_id": "other"},
        {"user_id": "other"},
        {"agent_id": "other"},
        {"initiated_by": "other"},
        {"dry_run": True},
    ],
)
def test_schedule_body_cannot_override_service_scope_or_execution(client, override):
    response = client.put(
        "/api/manage/memory/retention/schedules/nightly",
        json={"policy_id": "custom", "spec": {"schedule": "@daily"}, **override},
    )
    assert response.status_code == 422


def test_schedule_preview_uses_evolve_without_saving(client):
    with (
        patch.object(EvolveIntegration, "is_enabled", return_value=True),
        patch.object(EvolveIntegration, "_call_structured_tool", new=AsyncMock()) as call,
    ):
        response = client.post(
            "/api/manage/memory/retention/schedules/preview",
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
        response = client.post("/api/manage/memory/retention/schedules/preview", json={"spec": spec})
    assert response.status_code == 422


def test_schedule_changes_require_management_access(client):
    def denied():
        raise HTTPException(status_code=403)

    app.dependency_overrides[require_manage_access] = denied
    with patch.object(EvolveIntegration, "_call_structured_tool", new=AsyncMock()) as call:
        response = client.post(
            "/api/manage/memory/retention/schedules/nightly/start", json={"expected_revision": 1}
        )
    assert response.status_code == 403
    call.assert_not_awaited()


def test_schedule_routes_with_real_evolve_catalog_are_revisioned_and_isolated(client, tmp_path):
    from types import SimpleNamespace

    from altk_evolve.retention.schedule_store import ScheduleStore
    from altk_evolve.retention.service import RetentionError, RetentionService

    evolve = SimpleNamespace(
        config=SimpleNamespace(backend="filesystem"), backend=SimpleNamespace(data_dir=tmp_path)
    )
    store = ScheduleStore(evolve, sqlite_path=tmp_path / "schedules.sqlite")
    for namespace in ("service-a", "service-b"):
        store.put_policy(
            namespace_id=namespace,
            policy_id="custom",
            name="Custom",
            description=None,
            enabled=True,
            policy={"rules": [{"name": "old", "max_age_days": 7, "action": "delete"}]},
        )

    async def dispatch(tool, args):
        args = dict(args)
        service = RetentionService(evolve, args.pop("namespace_id"), store=store)
        operation = tool.replace("_retention_", "_")
        try:
            if operation == "put_schedule":
                return service.put_schedule(args.pop("schedule_id"), args.pop("definition"), **args)
            return getattr(service, operation)(**args)
        except RetentionError as error:
            return error.payload()

    path = "/api/manage/memory/retention/schedules/nightly"
    with patch.object(EvolveIntegration, "_call_structured_tool", new=dispatch):
        with patch("cuga.backend.server.memory_routes._namespace_id", return_value="service-a"):
            created = client.put(
                path, json={"policy_id": "custom", "spec": {"schedule": "@daily", "suspend": True}}
            )
            assert created.status_code == 200
            assert created.json()["revision"] == 1
            assert created.json()["definition"]["dry_run"] is False
            started = client.post(path + "/start", json={"expected_revision": 1})
            assert started.status_code == 200
            assert started.json()["revision"] == 2
            assert client.post(path + "/stop", json={"expected_revision": 1}).status_code == 409
            assert len(client.get(path).json()["next_runs"]) == 5
        with patch("cuga.backend.server.memory_routes._namespace_id", return_value="service-b"):
            assert client.get(path).status_code == 404
            assert client.get("/api/manage/memory/retention/schedules").json()["items"] == []
            assert client.delete(path + "?expected_revision=2").status_code == 404
        with patch("cuga.backend.server.memory_routes._namespace_id", return_value="service-a"):
            stopped = client.post(path + "/stop", json={"expected_revision": 2})
            assert stopped.status_code == 200
            assert client.get(path).json()["next_runs"] == []
            assert client.delete(path + "?expected_revision=3").json()["deleted"] is True
