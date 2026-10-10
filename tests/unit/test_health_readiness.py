"""Unit tests for /health/readiness and /health endpoints, and Helm probes.

Covers issue #928:
- Readiness returns HTTP 503 when mandatory subsystems are starting or failed.
- Readiness returns HTTP 200 when all mandatory subsystems are ready.
- Disabled and optional subsystems do not block overall readiness.
- Subsystem-level diagnostics and errors are preserved in the response body.
- Helm readiness probe targets /health/readiness while liveness targets /health.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from cuga.backend.server.health import (
    build_subsystem_status,
    evaluate_readiness,
    make_readiness_response,
)

pytestmark = pytest.mark.unit


class FakeAppState:
    """Lightweight test double for AppState subsystem management."""

    def __init__(self):
        self.subsystem_statuses: Dict[str, Dict[str, Any]] = {}

    def set_subsystem_status(
        self,
        name: str,
        state: str,
        message: str = "",
        details: dict | None = None,
        required: bool | None = None,
    ) -> None:
        self.subsystem_statuses[name] = build_subsystem_status(
            state=state,
            message=message,
            details=details,
            required=required,
        )

    def get_subsystem_status(self, name: str) -> Dict[str, Any]:
        return self.subsystem_statuses.get(
            name,
            {
                "state": "unknown",
                "message": "",
                "required": False,
                "details": {},
            },
        )

    def get_subsystem_statuses(self) -> Dict[str, Dict[str, Any]]:
        return {name: status.copy() for name, status in self.subsystem_statuses.items()}


def _create_app(app_state: FakeAppState) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "subsystems": app_state.get_subsystem_statuses()})

    @app.get("/health/readiness")
    async def readiness(subsystem: str | None = Query(None)):
        return make_readiness_response(
            subsystem=subsystem,
            statuses=app_state.get_subsystem_statuses(),
            get_subsystem_status_fn=app_state.get_subsystem_status,
        )

    return app


def test_build_subsystem_status_defaults():
    # Enabled subsystems default to required=True
    status_ready = build_subsystem_status("ready", "Ready to serve")
    assert status_ready["state"] == "ready"
    assert status_ready["required"] is True
    assert status_ready["message"] == "Ready to serve"
    assert status_ready["details"] == {}

    # Disabled subsystems default to required=False
    status_disabled = build_subsystem_status("disabled", "Disabled in config")
    assert status_disabled["state"] == "disabled"
    assert status_disabled["required"] is False

    # Explicit required override is respected
    status_optional = build_subsystem_status("ready", required=False)
    assert status_optional["required"] is False

    # Details are preserved
    details = {"error": "Connection refused", "code": 111}
    status_failed = build_subsystem_status("failed", "Init failed", details=details)
    assert status_failed["state"] == "failed"
    assert status_failed["details"] == details


def test_evaluate_readiness_scenarios():
    # Empty statuses -> ready
    assert evaluate_readiness({}) == ("ready", True)

    # All required subsystems ready -> ready
    statuses = {
        "policy": build_subsystem_status("ready"),
        "knowledge": build_subsystem_status("ready"),
    }
    assert evaluate_readiness(statuses) == ("ready", True)

    # Disabled subsystem does not block readiness
    statuses = {
        "policy": build_subsystem_status("ready"),
        "knowledge": build_subsystem_status("disabled"),
    }
    assert evaluate_readiness(statuses) == ("ready", True)

    # Optional subsystem in failed/starting state does not block readiness
    statuses = {
        "policy": build_subsystem_status("ready"),
        "analytics": build_subsystem_status("failed", required=False),
    }
    assert evaluate_readiness(statuses) == ("ready", True)

    # Required subsystem starting -> starting, not ready
    statuses = {
        "policy": build_subsystem_status("ready"),
        "knowledge": build_subsystem_status("starting"),
    }
    assert evaluate_readiness(statuses) == ("starting", False)

    # Required subsystem failed -> degraded, not ready
    statuses = {
        "policy": build_subsystem_status("failed", details={"error": "db down"}),
        "knowledge": build_subsystem_status("ready"),
    }
    assert evaluate_readiness(statuses) == ("degraded", False)

    # One failed and one starting -> degraded takes precedence
    statuses = {
        "policy": build_subsystem_status("failed"),
        "knowledge": build_subsystem_status("starting"),
    }
    assert evaluate_readiness(statuses) == ("degraded", False)


def test_readiness_endpoint_all_ready():
    state = FakeAppState()
    state.set_subsystem_status("policy", "ready", "Policy subsystem ready")
    state.set_subsystem_status("knowledge", "ready", "Knowledge subsystem ready")
    client = TestClient(_create_app(state))

    resp = client.get("/health/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["ready"] is True
    assert "policy" in data["subsystems"]
    assert "knowledge" in data["subsystems"]


def test_readiness_endpoint_subsystem_starting_returns_503():
    state = FakeAppState()
    state.set_subsystem_status("policy", "ready", "Policy subsystem ready")
    state.set_subsystem_status("knowledge", "starting", "Initializing embeddings")
    client = TestClient(_create_app(state))

    resp = client.get("/health/readiness")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "starting"
    assert data["ready"] is False
    assert data["subsystems"]["knowledge"]["state"] == "starting"


def test_readiness_endpoint_subsystem_failed_returns_503_preserving_diagnostics():
    state = FakeAppState()
    error_details = {"error": "sqlite3.OperationalError: unable to open database file"}
    state.set_subsystem_status(
        "policy",
        "failed",
        "Policy subsystem failed to initialize",
        details=error_details,
    )
    state.set_subsystem_status("knowledge", "ready", "Knowledge subsystem ready")
    client = TestClient(_create_app(state))

    resp = client.get("/health/readiness")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["ready"] is False
    assert data["subsystems"]["policy"]["state"] == "failed"
    assert data["subsystems"]["policy"]["details"] == error_details


def test_readiness_query_parameter_by_subsystem():
    state = FakeAppState()
    state.set_subsystem_status("policy", "ready", "Policy subsystem ready")
    state.set_subsystem_status("knowledge", "starting", "Warming up embeddings")
    client = TestClient(_create_app(state))

    # Ready subsystem -> 200
    resp_policy = client.get("/health/readiness?subsystem=policy")
    assert resp_policy.status_code == 200
    data_policy = resp_policy.json()
    assert data_policy["subsystem"] == "policy"
    assert data_policy["ready"] is True
    assert data_policy["status"] == "ready"

    # Starting subsystem -> 503
    resp_knowledge = client.get("/health/readiness?subsystem=knowledge")
    assert resp_knowledge.status_code == 503
    data_knowledge = resp_knowledge.json()
    assert data_knowledge["subsystem"] == "knowledge"
    assert data_knowledge["ready"] is False
    assert data_knowledge["status"] == "starting"

    # Unknown subsystem -> 503
    resp_unknown = client.get("/health/readiness?subsystem=nonexistent")
    assert resp_unknown.status_code == 503
    data_unknown = resp_unknown.json()
    assert data_unknown["ready"] is False
    assert data_unknown["status"] == "unknown"


def test_liveness_endpoint_stays_200_even_when_subsystems_fail():
    state = FakeAppState()
    state.set_subsystem_status("policy", "failed", "Policy failed", {"error": "disk error"})
    client = TestClient(_create_app(state))

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["subsystems"]["policy"]["state"] == "failed"


def test_helm_deployment_probe_paths():
    deployment_path = Path(__file__).resolve().parents[2] / "deployment/helm/cuga/templates/deployment.yaml"
    assert deployment_path.exists()
    content = deployment_path.read_text()

    # Verify readiness probe explicitly targets /health/readiness
    readiness_match = re.search(r"readinessProbe:\s*\n\s*httpGet:\s*\n\s*path:\s*([^\s]+)", content)
    assert readiness_match is not None, "readinessProbe httpGet path not found"
    assert readiness_match.group(1) == "/health/readiness"

    # Verify liveness probe explicitly targets /health
    liveness_match = re.search(r"livenessProbe:\s*\n\s*httpGet:\s*\n\s*path:\s*([^\s]+)", content)
    assert liveness_match is not None, "livenessProbe httpGet path not found"
    assert liveness_match.group(1) == "/health"

    # Verify startup probe explicitly targets /health
    startup_match = re.search(r"startupProbe:\s*\n\s*httpGet:\s*\n\s*path:\s*([^\s]+)", content)
    assert startup_match is not None, "startupProbe httpGet path not found"
    assert startup_match.group(1) == "/health"
