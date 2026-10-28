"""A provider's 401 on 'List models' must not become CUGA's own 401 (#950).

The frontend sends any 401 from CUGA's API to the login page, so a wrong provider key looked
like an expired CUGA session.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit


class _Provider(BaseHTTPRequestHandler):
    status = 401
    body = {"error": {"message": "Invalid API Key", "type": "invalid_request_error"}}

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = json.dumps(self.body).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def provider(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"

    async def fake_draft(agent_id):
        return {"llm": {"provider": "litellm", "url": url, "api_key": "fake-provider-key"}}

    from cuga.config import settings

    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", fake_draft)
    old = settings.get("secrets.force_env")
    settings.set("secrets.force_env", False)
    yield _Provider
    settings.set("secrets.force_env", old)
    _Provider.status = 401
    server.shutdown()


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_provider_key_is_a_502_without_echoing_body(provider, status):
    from cuga.backend.server.manage_routes.llm_routes import list_llm_models

    provider.status = status
    with pytest.raises(HTTPException) as e:
        await list_llm_models(request=None, disable_ssl=False, agent_id="cuga-default")
    assert e.value.status_code == 502
    assert "rejected the configured API key" in e.value.detail
    assert f"HTTP {status}" in e.value.detail
    assert "Invalid API Key" not in e.value.detail


async def test_other_provider_errors_are_a_502_too(provider):
    from cuga.backend.server.manage_routes.llm_routes import list_llm_models

    provider.status = 404
    with pytest.raises(HTTPException) as e:
        await list_llm_models(request=None, disable_ssl=False, agent_id="cuga-default")
    assert e.value.status_code == 502 and "HTTP 404" in e.value.detail


async def test_the_key_is_not_logged(provider):
    from loguru import logger

    from cuga.backend.server.manage_routes.llm_routes import list_llm_models

    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        with pytest.raises(HTTPException):
            await list_llm_models(request=None, disable_ssl=False, agent_id="cuga-default")
    finally:
        logger.remove(sink)
    assert not any("fake-pro" in line for line in lines)
