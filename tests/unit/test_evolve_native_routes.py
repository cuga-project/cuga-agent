"""Exercise CUGA's transport boundary against the actual Evolve native router."""

from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from cuga.backend.evolve import http_worker
from cuga.backend.server import evolve_native_routes as gateway
from cuga.backend.server import memory_routes
from cuga.backend.server.auth.models import UserInfo

pytestmark = pytest.mark.unit


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    pytest.importorskip("altk_evolve.frontend.api.memory")
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.config.filesystem import FilesystemSettings
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.frontend.mcp import mcp_server
    from altk_evolve.retention import scheduler

    evolve = EvolveClient(
        EvolveConfig(
            backend="filesystem",
            settings=FilesystemSettings(data_dir=str(tmp_path)),
            retention_scheduler_enabled=False,
        )
    )
    evolve.ensure_namespace("instance-a")
    monkeypatch.setenv("CUGA_EVOLVE_API_TOKEN", "private-token")
    monkeypatch.setattr(mcp_server, "get_client", lambda: evolve)
    owners = []

    @contextmanager
    def runtime(client):
        owners.append(client)
        yield
        owners.remove(client)

    monkeypatch.setattr(scheduler, "retention_runtime", runtime)
    monkeypatch.setattr(gateway, "bundled_api_token", lambda: "private-token")
    monkeypatch.setattr(gateway, "get_service_instance_id", lambda: "instance-a")
    from cuga.backend.evolve import preferences

    monkeypatch.setattr(preferences, "get_preferences", AsyncMock(return_value={"instance_enabled": True}))
    monkeypatch.setattr(gateway.EvolveIntegration, "is_enabled", lambda: True)
    monkeypatch.setattr(gateway, "require_chat_access", AsyncMock(return_value=UserInfo(sub="alice")))
    monkeypatch.setattr(gateway, "require_manage_access", AsyncMock(return_value=UserInfo(sub="admin")))
    provision_default = memory_routes._retention_policies
    monkeypatch.setattr(memory_routes, "_retention_policies", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        memory_routes,
        "_require_durable_retention",
        AsyncMock(side_effect=HTTPException(409, "Requires PostgreSQL")),
    )
    app = FastAPI()
    app.include_router(memory_routes.router)
    sent = []
    with TestClient(http_worker.create_app()) as worker:

        class Response:
            def __init__(self, result):
                self.result = result
                self.status = result.status_code

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def json(self):
                return self.result.json()

        class Session:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def request(self, method, url, **kwargs):
                kwargs.pop("allow_redirects")
                sent.append((method, url, kwargs))
                return Response(worker.request(method, url.removeprefix("http://127.0.0.1:8201"), **kwargs))

        monkeypatch.setattr(gateway.aiohttp, "ClientSession", Session)
        yield SimpleNamespace(
            client=TestClient(app),
            worker=worker,
            evolve=evolve,
            sent=sent,
            owners=owners,
            provision_default=provision_default,
        )
    assert owners == []


def test_worker_requires_service_credential_and_scope(boundary):
    assert boundary.worker.get("/private/manage/retention/policies").status_code == 401
    assert (
        boundary.worker.get(
            "/private/manage/retention/policies", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert (
        boundary.worker.get(
            "/private/manage/retention/policies", headers={"Authorization": "Bearer private-token"}
        ).status_code
        == 400
    )


def test_one_client_and_scheduler_serve_both_transports(boundary):
    assert boundary.owners == [boundary.evolve]
    assert boundary.worker.app.state.evolve_client is boundary.evolve
    # Records made through the native router are immediately visible to the MCP
    # singleton client: no second filesystem client or local shadow database.
    response = boundary.client.post("/api/manage/retention/policies", json={"policy_id": "p"})
    assert response.status_code == 201, response.text
    assert boundary.evolve.retention("instance-a").get_policy("p")["policy_id"] == "p"
    assert boundary.evolve.retention("instance-other").list_policies()["items"] == []


@pytest.mark.parametrize("path", ["/api/memory/access", "/api/manage/retention/policies"])
def test_feature_disabled_never_calls_worker(boundary, monkeypatch, path):
    from cuga.backend.evolve import preferences

    monkeypatch.setattr(preferences, "get_preferences", AsyncMock(return_value={"instance_enabled": False}))
    assert boundary.client.post(path, json={}).status_code == 403
    assert boundary.sent == []


def test_missing_identity_and_chat_permissions_fail_closed(boundary, monkeypatch):
    monkeypatch.setattr(gateway, "require_chat_access", AsyncMock(return_value=None))
    assert boundary.client.post("/api/memory/access", json={"entity_ids": ["x"]}).status_code == 401
    monkeypatch.setattr(
        gateway, "require_chat_access", AsyncMock(side_effect=HTTPException(403, "No chat access"))
    )
    assert boundary.client.post("/api/memory/access", json={"entity_ids": ["x"]}).status_code == 403
    assert boundary.sent == []


def test_management_permission_is_separate_from_chat(boundary, monkeypatch):
    monkeypatch.setattr(
        gateway, "require_manage_access", AsyncMock(side_effect=HTTPException(403, "No manage access"))
    )
    assert boundary.client.get("/api/manage/retention/policies").status_code == 403
    gateway.require_chat_access.assert_not_called()
    assert boundary.sent == []


@pytest.mark.parametrize(
    "params", ["namespace_id=other", "user_id=bob", "can_manage=true", "agent_id=unknown-agent"]
)
def test_request_cannot_override_scope(boundary, params):
    response = boundary.client.post(f"/api/memory/access?{params}", json={"entity_ids": ["x"]})
    assert response.status_code in {404, 422}
    assert boundary.sent == []


def test_headers_cannot_override_trusted_scope(boundary):
    response = boundary.client.post(
        "/api/memory/access",
        json={"entity_ids": ["x"]},
        headers={
            "Authorization": "Bearer caller",
            "X-Cuga-Memory-Scope": '{"user_id":"bob","namespace_id":"other","can_manage":true}',
        },
    )
    assert response.status_code == 200, response.text
    headers = boundary.sent[-1][2]["headers"]
    assert headers["Authorization"] == "Bearer private-token"
    assert json.loads(headers["X-Cuga-Memory-Scope"]) == dict(
        namespace_id="instance-a", user_id="alice", agent_id="cuga-default", can_manage=False
    )


def test_native_dry_run_default_and_explicit_execution(boundary):
    service = boundary.evolve.retention("instance-a")
    service.put_policy("p", name="P", policy={"rules": []})
    preview = boundary.client.post("/api/manage/retention/runs", json={"policy_id": "p"})
    assert preview.status_code == 200, preview.text
    assert boundary.sent[-1][2]["json"] == {"policy_id": "p"}
    assert preview.json()["dry_run"] is True
    execute = boundary.client.post("/api/manage/retention/runs", json={"policy_id": "p", "dry_run": False})
    assert execute.status_code == 200, execute.text
    assert boundary.sent[-1][2]["json"]["dry_run"] is False
    assert service.get_run(execute.json()["run_id"])["report"]["dry_run"] is False


def test_durable_operations_preserve_backend_gate(boundary):
    for path in ("candidates", "audit"):
        assert boundary.client.get(f"/api/manage/retention/{path}").status_code == 409
    for phase in ("mark", "sweep"):
        assert boundary.client.post(f"/api/manage/retention/policies/p/{phase}").status_code == 409
    assert boundary.sent == []


def test_unreviewed_routes_are_not_exposed(boundary):
    for path in ("deleted-sources", "secrets"):
        assert boundary.client.post(f"/api/manage/retention/{path}", json={}).status_code == 404
    assert boundary.sent == []


def test_native_errors_do_not_expose_service_details(boundary):
    response = boundary.client.get("/api/manage/retention/policies/secret-database-name")
    assert response.status_code == 404
    assert response.json() == {"detail": "Memory not found"}


def test_external_mode_keeps_mcp_transport(boundary, monkeypatch):
    monkeypatch.setattr(gateway, "bundled_api_token", lambda: None)
    call = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(memory_routes.EvolveIntegration, "delete_entity", call)
    boundary.client.app.dependency_overrides[memory_routes.require_chat_access] = lambda: UserInfo(
        sub="alice"
    )
    response = boundary.client.delete("/api/memory/entities/x")
    assert response.status_code == 200
    call.assert_awaited_once()
    assert boundary.sent == []
    assert boundary.client.get("/api/manage/retention/jobs").status_code == 503


def test_personal_mutations_cannot_cross_user_agent_or_namespace(boundary):
    from altk_evolve.schema.core import Entity

    evolve = boundary.evolve
    targets = []
    for namespace, user, agent in (
        ("instance-a", "bob", "cuga-default"),
        ("instance-a", "alice", "other-agent"),
        ("other-instance", "alice", "cuga-default"),
    ):
        evolve.ensure_namespace(namespace)
        evolve.update_entities(
            namespace,
            [
                Entity(
                    type="fact",
                    content="private",
                    metadata={"user_id": user, "owner_id": user, "agent_id": agent},
                )
            ],
            enable_conflict_resolution=False,
        )
        entity = evolve.scan_entities(
            namespace, filters={"metadata.user_id": user, "metadata.agent_id": agent}
        )[0]
        targets.append((namespace, entity.id))
        response = boundary.client.patch(
            f"/api/memory/entities/{entity.id}/metadata", json={"metadata": {"title": "stolen"}}
        )
        assert response.status_code in {403, 404}, response.text
        response = boundary.client.delete(f"/api/memory/entities/{entity.id}")
        assert response.status_code in {403, 404}, response.text
    for namespace, entity_id in targets:
        assert evolve.scan_entities(namespace, filters={"id": entity_id})[0].metadata.get("title") is None


def test_schedule_contract_scopes_jobs_and_namespace(boundary):
    service = boundary.evolve.retention("instance-a")
    service.put_policy("p", name="P", policy={"rules": []})
    response = boundary.client.put(
        "/api/manage/retention/schedules/nightly?agent_id=cuga-default",
        json={
            "definition": {
                "policy_id": "p",
                "spec": {"schedule": "0 0 * * *", "suspend": True},
                "agent_id": "cuga-default",
                "dry_run": False,
            },
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    definition = service.get_schedule("nightly")["definition"]
    assert definition["dry_run"] is False
    assert definition["agent_id"] == "cuga-default"
    # Native scopes reject a schedule aimed at another agent, even for managers.
    response = boundary.client.put(
        "/api/manage/retention/schedules/cross?agent_id=cuga-default",
        json={
            "definition": {"policy_id": "p", "spec": {"schedule": "0 0 * * *"}, "agent_id": None},
            "expected_revision": 0,
        },
    )
    assert response.status_code == 403, response.text
    assert boundary.client.get("/api/manage/retention/jobs?agent_id=cuga-default").json() == {"items": []}
    assert boundary.evolve.retention("other-instance").list_schedules() == {"items": []}


def test_manual_external_matches_are_not_accepted(boundary):
    response = boundary.client.post(
        "/api/manage/retention/runs",
        json={"policy_id": "p", "dry_run": False, "additional_matches": [{"entity_id": "other"}]},
    )
    assert response.status_code == 422
    assert boundary.sent == []


@pytest.mark.parametrize("limit", ["-1", "0", "1001", "invalid"])
def test_candidate_and_audit_limit_is_bounded(boundary, monkeypatch, limit):
    monkeypatch.setattr(memory_routes, "_require_durable_retention", AsyncMock())
    assert boundary.client.get(f"/api/manage/retention/candidates?limit={limit}").status_code == 422
    assert boundary.sent == []


def test_missing_namespace_cannot_fall_back_to_global_storage(boundary, monkeypatch):
    monkeypatch.setattr(gateway, "get_service_instance_id", lambda: None)
    assert boundary.client.get("/api/manage/retention/policies").status_code == 503
    assert boundary.sent == []


def test_cross_agent_jobs_and_runs_are_hidden(boundary, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from cuga.backend.server import agent_registry, config_store

    monkeypatch.setattr(agent_registry, "is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        config_store, "list_agents_with_configs", AsyncMock(return_value=[{"agent_id": "other-agent"}])
    )
    service = boundary.evolve.retention("instance-a", agent_id="other-agent")
    service.put_policy("p", name="P", policy={"rules": []})
    service.put_schedule(
        "other",
        {"policy_id": "p", "agent_id": "other-agent", "spec": {"schedule": "* * * * *"}},
        expected_revision=0,
        initiated_by="admin",
    )
    job_id = service.store.dispatch("instance-a", "other", datetime.now(timezone.utc) + timedelta(minutes=2))
    assert job_id
    result = service.run("p", dry_run=True)
    for path in (f"jobs/{job_id}", f"runs/{result['run_id']}", "schedules/other"):
        assert boundary.client.get(f"/api/manage/retention/{path}?agent_id=cuga-default").status_code == 404
        assert boundary.client.get(f"/api/manage/retention/{path}?agent_id=other-agent").status_code == 200
    assert boundary.client.get("/api/manage/retention/jobs?agent_id=cuga-default").json() == {"items": []}
    assert (
        boundary.client.post(f"/api/manage/retention/jobs/{job_id}/cancel?agent_id=cuga-default").status_code
        == 404
    )


@pytest.mark.parametrize("definition", [[], "invalid", 42])
def test_invalid_native_definition_is_422_not_500(boundary, definition):
    response = boundary.client.put("/api/manage/retention/schedules/x", json={"definition": definition})
    assert response.status_code == 422


def test_default_mark_provisions_policy_first(boundary, monkeypatch):
    monkeypatch.setattr(memory_routes, "_require_durable_retention", AsyncMock())
    boundary.client.post("/api/manage/retention/policies/cuga-standard/mark")
    memory_routes._retention_policies.assert_awaited_once()


def test_native_patch_preserves_cuga_enrichment(boundary, monkeypatch):
    from altk_evolve.schema.core import Entity
    from fastapi.responses import JSONResponse

    boundary.evolve.update_entities(
        "instance-a",
        [
            Entity(
                type="fact",
                content="hello",
                metadata={"user_id": "alice", "owner_id": "alice", "agent_id": "cuga-default"},
            )
        ],
        enable_conflict_resolution=False,
    )
    entity = boundary.evolve.scan_entities("instance-a")[0]
    enriched = {
        "id": entity.id,
        "source_thread_id": "conversation-1",
        "source_available": True,
        "usage": {"count": 2},
    }
    detail = AsyncMock(return_value=JSONResponse(enriched))
    monkeypatch.setattr(memory_routes, "get_user_memory_entity", detail)
    response = boundary.client.patch(
        f"/api/memory/entities/{entity.id}/metadata", json={"metadata": {"title": "updated"}}
    )
    assert response.json() == enriched
    assert boundary.evolve.scan_entities("instance-a")[0].metadata["title"] == "updated"
    detail.assert_awaited_once_with(entity.id, "cuga-default", UserInfo(sub="alice"))


def test_only_native_retention_contract_is_exposed(boundary):
    for resource in ("policies", "runs", "schedules", "audit"):
        assert boundary.client.get(f"/api/manage/memory/retention/{resource}").status_code == 404
    assert (
        boundary.client.post(
            "/api/manage/retention/runs", json={"policy_id": "p", "scan_limit": 10}
        ).status_code
        == 422
    )


def test_native_default_policy_provisioning_uses_shared_backend(boundary, monkeypatch):
    service = boundary.evolve.retention("instance-a")

    async def list_policies(**kwargs):
        return service.list_policies(include_disabled=kwargs["include_disabled"])

    async def put_policy(policy_id, name, policy, **kwargs):
        return service.put_policy(policy_id, name=name, policy=policy)

    monkeypatch.setattr(memory_routes, "_retention_policies", boundary.provision_default)
    monkeypatch.setattr(memory_routes, "_namespace_id", lambda: "instance-a")
    monkeypatch.setattr(gateway.EvolveIntegration, "list_retention_policies", list_policies)
    monkeypatch.setattr(gateway.EvolveIntegration, "put_retention_policy", put_policy)
    monkeypatch.setattr(
        gateway.EvolveIntegration,
        "get_compliance_status",
        AsyncMock(return_value={"backend": "filesystem", "retention_available": True}),
    )
    response = boundary.client.get("/api/manage/retention/policies")
    assert response.status_code == 200, response.text
    policy = service.get_policy("cuga-standard")["policy"]
    assert len(policy["rules"]) == 3
    assert not any(rule.get("source_deleted") for rule in policy["rules"])
    assert (
        boundary.client.post("/api/manage/retention/runs", json={"policy_id": "cuga-standard"}).status_code
        == 200
    )
    assert len(service.list_policies()["items"]) == 1


def test_management_without_identity_is_rejected(boundary, monkeypatch):
    monkeypatch.setattr(gateway, "require_manage_access", AsyncMock(return_value=None))
    assert boundary.client.get("/api/manage/retention/policies").status_code == 401
    assert boundary.sent == []


def test_disabled_service_can_read_native_retention(boundary, monkeypatch):
    from cuga.backend.evolve import preferences

    monkeypatch.setattr(preferences, "get_preferences", AsyncMock(return_value={"instance_enabled": False}))
    assert boundary.client.get("/api/manage/retention/policies").status_code == 200
    assert len(boundary.sent) == 1


def test_bundled_service_settings_bypass_native_identity_boundary(boundary):
    assert boundary.client.get("/api/manage/memory/settings").status_code == 200
    assert boundary.sent == []
