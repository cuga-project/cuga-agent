"""MCP gateway tools that run as the signed-in user.

A local HTTP server plays the platform IAM (user token exchange) and the registry.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aiohttp
import pytest
from cryptography.fernet import Fernet

from cuga.backend.server.auth import gateway_tokens
from cuga.backend.tools_env.registry import mcp_gateway
from cuga.backend.tools_env.registry.config.config_loader import _create_service_config
from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.utils.api_utils import post_registry_call

pytestmark = pytest.mark.unit

INSTANCE = "7f3c9a10-5d2e-4b8a-9c61-0e4f2a7b8d93"
URL = f"https://gw.example.com/?instance={INSTANCE}"


class FakeServer:
    def __init__(self):
        self.exchanges = 0
        self.iam_status, self.iam_message = 200, ""
        self.registry_headers: list[str | None] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path.startswith(f"/api/2.0/services/{INSTANCE}/token"):
                    if server.iam_status != 200:
                        return self._send(server.iam_status, {"message": server.iam_message})
                    server.exchanges += 1
                    return self._send(200, {"token": f"instance-token-{server.exchanges}"})
                if self.path.startswith("/functions/call"):
                    token = self.headers.get(mcp_gateway.USER_TOKEN_HEADER)
                    server.registry_headers.append(token)
                    if not token:
                        return self._send(401, mcp_gateway.user_token_required_body(INSTANCE))
                    return self._send(200, {"ok": True, "token_seen": token})
                return self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def fake(monkeypatch):
    f = FakeServer()
    monkeypatch.setattr(
        mcp_gateway,
        "gateway_settings",
        lambda: {"iam_url": f.url, "iam_verify": True, "gateway_verify": True},
    )
    monkeypatch.setenv("CUGA_SECRET_KEY", Fernet.generate_key().decode())
    gateway_tokens._cache.clear()
    yield f
    f.server.shutdown()


@pytest.fixture
def no_provider():
    """Run the test in a context without a provider."""
    token = gateway_tokens._provider.set(None)
    yield
    gateway_tokens._provider.reset(token)


# --- cookie ------------------------------------------------------------------------------


def test_idp_cookie_round_trip_is_bound_to_the_session(fake):
    cookie = gateway_tokens.encode_idp_cookie("idp-token", "session-a")
    assert cookie and "idp-token" not in cookie
    assert gateway_tokens.decode_idp_cookie(cookie, "session-a") == "idp-token"
    assert gateway_tokens.decode_idp_cookie(cookie, "session-b") is None
    assert gateway_tokens.decode_idp_cookie("not-a-cookie", "session-a") is None


def test_idp_cookie_expires(fake):
    cookie = gateway_tokens.encode_idp_cookie("idp-token", "session-a")
    time.sleep(2.1)  # Fernet counts age in whole seconds
    assert gateway_tokens.decode_idp_cookie(cookie, "session-a", max_age=1) is None


def test_no_cookie_without_an_encryption_key(monkeypatch):
    monkeypatch.delenv("CUGA_SECRET_KEY", raising=False)
    assert gateway_tokens.encode_idp_cookie("idp-token", "session-a") is None
    assert not gateway_tokens.user_tokens_available()


def test_provider_from_request_needs_the_matching_session(fake):
    class Req:
        def __init__(self, cookies):
            self.cookies = cookies

    class User:
        sub = "user-1"

    cookie = gateway_tokens.encode_idp_cookie("idp-token", "session-a")
    good = Req({"cuga_session": "session-a", gateway_tokens.IDP_COOKIE_NAME: cookie})
    stolen = Req({"cuga_session": "session-b", gateway_tokens.IDP_COOKIE_NAME: cookie})
    assert gateway_tokens.provider_from_request(good, User()).sub == "user-1"
    assert gateway_tokens.provider_from_request(stolen, User()) is None
    assert gateway_tokens.provider_from_request(good, None) is None
    assert "idp-token" not in repr(gateway_tokens.provider_from_request(good, User()))


# --- exchange ----------------------------------------------------------------------------


async def test_exchange_is_cached_per_user_and_instance(fake):
    alice = gateway_tokens.GatewayTokenProvider("alice", "idp-a")
    assert await alice.token(INSTANCE) == "instance-token-1"
    assert await alice.token(INSTANCE) == "instance-token-1"
    bob = gateway_tokens.GatewayTokenProvider("bob", "idp-b")
    assert await bob.token(INSTANCE) == "instance-token-2"
    assert fake.exchanges == 2


@pytest.mark.parametrize(
    "status,message,expected",
    [
        (400, "No role assigned to the user for the scope", "no role on MCP gateway instance"),
        (400, "Invalid JWT [8] Audience is invalid", "same workspace"),
        (401, "jwt expired", "Sign in again"),
        (500, "boom", "answered 500"),
    ],
)
async def test_exchange_errors_are_messages_for_the_user(fake, status, message, expected):
    fake.iam_status, fake.iam_message = status, message
    with pytest.raises(gateway_tokens.GatewayAccessError, match=expected):
        await gateway_tokens.GatewayTokenProvider("alice", "idp-a").token(INSTANCE)


# --- agent -> registry -------------------------------------------------------------------


async def test_a_user_mode_call_is_retried_with_the_instance_token(fake):
    gateway_tokens.set_provider(gateway_tokens.GatewayTokenProvider("alice", "idp-a"))
    async with aiohttp.ClientSession() as session:
        status, text = await post_registry_call(session, f"{fake.url}/functions/call", {"app_name": "x"}, 10)
    assert status == 200
    assert json.loads(text)["token_seen"] == "instance-token-1"
    assert fake.registry_headers == [None, "instance-token-1"]


async def test_a_user_mode_call_without_a_user_is_refused(fake, no_provider):
    async with aiohttp.ClientSession() as session:
        status, text = await post_registry_call(session, f"{fake.url}/functions/call", {"app_name": "x"}, 10)
    assert status == 403
    assert "no signed-in user" in text
    assert fake.exchanges == 0


async def test_a_user_without_a_role_gets_the_message(fake):
    fake.iam_status, fake.iam_message = 400, "No role assigned"
    gateway_tokens.set_provider(gateway_tokens.GatewayTokenProvider("carol", "idp-c"))
    async with aiohttp.ClientSession() as session:
        status, text = await post_registry_call(session, f"{fake.url}/functions/call", {"app_name": "x"}, 10)
    assert status == 403 and "no role on MCP gateway instance" in text
    assert fake.registry_headers == [None]


async def test_other_errors_are_not_retried(fake):
    assert await gateway_tokens.retry_headers(500, "{}") is None
    assert await gateway_tokens.retry_headers(401, '{"detail": "Invalid token"}') is None
    assert await gateway_tokens.retry_headers(401, "not json") is None


# --- registry ----------------------------------------------------------------------------


def _config(run_as):
    return _create_service_config(
        "team_tools",
        {
            "url": URL,
            "gateway": {"run_as": run_as},
            "auth": {"type": "account_iam_apikey", "value": "db://k"},
        },
        is_mcp_server=True,
    )


def test_run_as_is_normalized():
    assert _config("user").gateway["run_as"] == "user"
    assert _config("service").gateway["run_as"] == "service"
    assert _config("anything").gateway["run_as"] == "service"
    assert mcp_gateway.user_mode_instance(_config("user")) == INSTANCE
    assert mcp_gateway.user_mode_instance(_config("service")) is None


def test_user_mode_call_transport_uses_the_call_token():
    mgr = MCPManager({"team_tools": _config("user")})
    with pytest.raises(Exception, match="runs as the signed-in user"):
        mgr._call_transport("team_tools")
    reset = mcp_gateway.CALL_USER_TOKEN.set("instance-token-9")
    try:
        transport = mgr._call_transport("team_tools")
    finally:
        mcp_gateway.CALL_USER_TOKEN.reset(reset)
    assert transport.url == "https://gw.example.com/mcp/"
    assert transport.headers["Authorization"] == "Bearer instance-token-9"


def test_service_mode_call_transport_is_the_stored_one():
    mgr = MCPManager({"team_tools": _config("service")})
    mgr.mcp_transports["team_tools"] = stored = object()
    assert mgr._call_transport("team_tools") is stored


# --- user mode without an API key --------------------------------------------------------


async def test_user_mode_entry_serves_saved_tool_definitions_without_a_credential():
    """No auth, no listing: the registry registers the saved tools and calls carry the user's token."""
    config = _create_service_config(
        "team_tools",
        {
            "url": URL,
            "gateway": {
                "run_as": "user",
                "tools": [
                    {
                        "name": "demo-time-add",
                        "description": "Add two numbers.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                        },
                    }
                ],
            },
            "include": ["demo-time-add"],
        },
        is_mcp_server=True,
    )
    assert config.auth is None
    mgr = MCPManager({"team_tools": config})
    assert await mgr._initialize_single_fastmcp_server("team_tools", config)
    assert mgr.server_by_tool == {"team_tools_demo_time_add": "team_tools"}
    assert mgr.original_tool_name_by_sanitized["team_tools_demo_time_add"] == "demo-time-add"
    params = mgr.tools_by_server["team_tools"][0]["function"]["parameters"]
    assert set(params.get("properties", {})) == {"a", "b"}
    schema = mgr.schemas["team_tools"]["tools"][0]
    assert schema["inputSchema"] == config.gateway["tools"][0]["inputSchema"]
    assert schema["outputSchema"] == {}
    assert mgr.mcp_transports["team_tools"] is None  # never used: each call builds its own


def test_saved_tools_are_ignored_in_service_mode():
    assert mcp_gateway.stored_tools(_config("service")) is None


async def test_discover_with_the_callers_own_token(fake, monkeypatch):
    seen = {}

    import httpx as _httpx

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return _httpx.Response(
            200,
            json=[{"name": "demo-time-add", "gatewaySlug": "demo-time", "inputSchema": {"type": "object"}}],
        )

    real = _httpx.AsyncClient

    class Client(real):
        def __init__(self, *a, **kw):
            kw["transport"] = _httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(mcp_gateway.httpx, "AsyncClient", Client)
    result = await mcp_gateway.discover(URL, bearer_token="admin-instance-token")
    assert seen["auth"] == "Bearer admin-instance-token"
    assert result["servers"][0]["tools"][0] == {
        "name": "demo-time-add",
        "description": "",
        "inputSchema": {"type": "object"},
    }
    assert fake.exchanges == 0  # no API key exchange


async def test_discover_route_in_user_mode_needs_a_sign_in():
    from fastapi import HTTPException

    from cuga.backend.server.manage_routes.gateway_routes import GatewayDiscoverRequest, discover_gateway

    class Req:
        cookies = {}

    with pytest.raises(HTTPException) as e:
        await discover_gateway(GatewayDiscoverRequest(url=URL, run_as="user"), request=Req(), user=None)
    assert e.value.status_code == 400 and "Sign in" in e.value.detail
