"""GET /api/manage/llm/models must not relay the provider's HTTP status (see #950).

A provider 401 relayed verbatim is indistinguishable, for the UI, from an expired CUGA
session and sends the user to the login page. Every upstream HTTP error maps to 502.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth, require_manage_access
from cuga.backend.server.manage_routes import llm_routes, router

API_KEY = "sk-test-0123456789abcdefghij"
URL = "/api/manage/llm/models"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    # settings.secrets.force_env = true ships by default: the key then comes from the environment
    # and no draft config is loaded.
    monkeypatch.setenv("OPENAI_API_KEY", API_KEY)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[require_manage_access] = lambda: None
    return TestClient(app)


def _mock_provider(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(llm_routes.httpx, "AsyncClient", factory)


@pytest.mark.unit
@pytest.mark.parametrize("upstream_status", [401, 403])
def test_upstream_auth_rejection_maps_to_502_without_echoing_body(client, monkeypatch, upstream_status):
    body = {"error": {"message": f"Incorrect API key provided: {API_KEY}. See https://example.test/keys"}}
    _mock_provider(monkeypatch, lambda request: httpx.Response(upstream_status, json=body))

    resp = client.get(URL)

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "rejected the configured API key" in detail
    assert str(upstream_status) in detail
    assert API_KEY not in detail
    assert "Incorrect API key" not in detail


@pytest.mark.unit
@pytest.mark.parametrize("upstream_status", [400, 404, 429, 500, 503])
def test_other_upstream_http_errors_map_to_502_with_status_and_message(client, monkeypatch, upstream_status):
    _mock_provider(
        monkeypatch,
        lambda request: httpx.Response(upstream_status, json={"error": {"message": "rate limit exceeded"}}),
    )

    resp = client.get(URL)

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert f"HTTP {upstream_status}" in detail
    assert "rate limit exceeded" in detail


@pytest.mark.unit
def test_upstream_message_is_redacted_single_line_and_truncated(client, monkeypatch):
    noisy = f"line1\r\nline2 {API_KEY} " + "x" * 2000
    _mock_provider(monkeypatch, lambda request: httpx.Response(500, text=noisy))

    resp = client.get(URL)

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert API_KEY not in detail
    assert "[REDACTED]" in detail
    assert "\r" not in detail and "\n" not in detail
    assert len(detail) < 450


@pytest.mark.unit
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, content=b""),
        httpx.Response(502, content=b"\xff\xfe\x00binary"),
        httpx.Response(503, text="<html><body>Bad gateway</body></html>"),
        httpx.Response(500, json={"error": {"code": 1}}),
        httpx.Response(500, json=["not", "an", "object"]),
    ],
    ids=["empty", "non-utf8", "html", "error-without-message", "json-array"],
)
def test_unusual_upstream_error_bodies_still_map_to_502(client, monkeypatch, response):
    _mock_provider(monkeypatch, lambda request: response)

    resp = client.get(URL)

    assert resp.status_code == 502
    assert "Models fetch failed" in resp.json()["detail"]


@pytest.mark.unit
def test_connection_error_maps_to_502(client, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    _mock_provider(monkeypatch, handler)

    resp = client.get(URL)

    assert resp.status_code == 502
    assert "connection refused" in resp.json()["detail"]


@pytest.mark.unit
def test_malformed_success_payload_maps_to_502(client, monkeypatch):
    _mock_provider(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"name": "no-id"}]}))

    resp = client.get(URL)

    assert resp.status_code == 502


@pytest.mark.unit
def test_success_returns_sorted_models_and_sends_bearer_key(client, monkeypatch):
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "gpt-b"}, {"id": "gpt-a"}]})

    _mock_provider(monkeypatch, handler)

    resp = client.get(URL)

    assert resp.status_code == 200
    assert resp.json() == {"models": ["gpt-a", "gpt-b"]}
    assert seen["authorization"] == f"Bearer {API_KEY}"


@pytest.mark.unit
def test_missing_api_key_still_returns_400(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _mock_provider(monkeypatch, lambda request: pytest.fail("provider must not be called without a key"))

    resp = client.get(URL)

    assert resp.status_code == 400


@pytest.mark.unit
def test_upstream_error_log_does_not_contain_body_or_key(client, monkeypatch):
    from loguru import logger

    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    try:
        _mock_provider(
            monkeypatch,
            lambda request: httpx.Response(401, json={"error": {"message": f"bad key {API_KEY}"}}),
        )
        client.get(URL)
    finally:
        logger.remove(sink_id)

    joined = "\n".join(records)
    assert "HTTP 401" in joined
    assert API_KEY not in joined
    assert "bad key" not in joined
