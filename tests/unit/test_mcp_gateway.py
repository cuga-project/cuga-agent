"""MCP gateway instance entries: URL parsing, config, API-key token exchange, discovery.

A local HTTP server plays the platform IAM and the gateway.
"""

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from cuga.backend.server.managed_mcp import tools_to_registry_yaml
from cuga.backend.tools_env.registry import mcp_gateway
from cuga.backend.tools_env.registry.config.config_loader import Auth, _create_service_config
from cuga.backend.tools_env.registry.mcp_manager.adapter import apply_authentication

pytestmark = pytest.mark.unit

INSTANCE = "7f3c9a10-5d2e-4b8a-9c61-0e4f2a7b8d93"


def _jwt(exp: float, n: int) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp), 'n': n})}.sig"


class FakePlatform:
    """IAM token exchange and the gateway's /tools, with call counters."""

    def __init__(self, token_ttl: float = 3600):
        self.token_ttl = token_ttl
        self.exchanges = 0
        self.apikeys: list[str] = []
        self.accepted: set[str] = set()
        self.reject_first_tool_call = False
        self.tools_calls: list[str] = []
        self.iam_status = 200
        platform = self

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
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path != f"/api/2.0/services/{INSTANCE}/apikeys/token":
                    return self._send(404, {"message": "not found"})
                if platform.iam_status != 200:
                    return self._send(platform.iam_status, {"message": "Key secret is not valid"})
                platform.exchanges += 1
                platform.apikeys.append(body.get("apikey"))
                token = _jwt(time.time() + platform.token_ttl, platform.exchanges)
                platform.accepted.add(token)
                return self._send(200, {"token": token})

            def do_GET(self):
                auth = self.headers.get("Authorization", "")
                platform.tools_calls.append(auth)
                token = auth.removeprefix("Bearer ")
                if platform.reject_first_tool_call and len(platform.tools_calls) == 1:
                    platform.accepted.discard(token)
                if token not in platform.accepted:
                    return self._send(401, {"detail": "Invalid token"})
                return self._send(
                    200,
                    [
                        {"name": "demo-time-get-time", "description": "Time", "gatewaySlug": "demo-time"},
                        {"name": "demo-time-add", "description": "Add", "gatewaySlug": "demo-time"},
                        {"name": "notes-search", "description": "Search", "gateway_slug": "notes"},
                        {"name": "old-tool", "gatewaySlug": "notes", "enabled": False},
                    ],
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def platform(monkeypatch):
    p = FakePlatform()
    monkeypatch.setattr(
        mcp_gateway,
        "gateway_settings",
        lambda: {"iam_url": p.url, "iam_verify": True, "gateway_verify": True},
    )
    yield p
    p.close()


# --- URL ---------------------------------------------------------------------------------


def test_parse_instance_url():
    inst = mcp_gateway.parse_instance_url(f" https://gw.example.com/?instance={INSTANCE} ")
    assert inst.instance_id == INSTANCE
    assert inst.base_url == "https://gw.example.com"
    assert inst.mcp_url == "https://gw.example.com/mcp/"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "gw.example.com/?instance=abc",
        "ftp://gw.example.com/?instance=abc",
        "https://gw.example.com/",
        "https://gw.example.com/?instance=",
        "https://gw.example.com/?instance=../admin",
        "https://gw.example.com/?instance=a%20b",
    ],
)
def test_parse_instance_url_rejects(url):
    with pytest.raises(ValueError):
        mcp_gateway.parse_instance_url(url)


# --- config ------------------------------------------------------------------------------


def _manage_entry(**over):
    entry = {
        "name": "tools_gw",
        "type": "mcp",
        "url": f"https://gw.example.com/?instance={INSTANCE}",
        "transport": "http",
        "description": "Gateway tools",
        "auth": {"type": "account_iam_apikey", "value": "db://gw-key"},
        "gateway": {"instance_url": f"https://gw.example.com/?instance={INSTANCE}", "run_as": "service"},
        "include": ["demo-time-add"],
    }
    entry.update(over)
    return entry


def test_service_config_of_a_gateway_entry():
    cfg = _create_service_config("tools_gw", _manage_entry(transport="sse"), is_mcp_server=True)
    assert cfg.url == "https://gw.example.com/mcp/"
    assert cfg.transport == "http"
    assert cfg.gateway == {
        "instance_url": f"https://gw.example.com/?instance={INSTANCE}",
        "instance_id": INSTANCE,
        "run_as": "service",
    }
    assert cfg.auth.type == "account_iam_apikey"
    assert cfg.include == ["demo-time-add"]


def test_service_config_rejects_a_gateway_entry_without_instance():
    entry = _manage_entry(url="https://gw.example.com/", gateway={"run_as": "service"})
    with pytest.raises(ValueError, match="tools_gw"):
        _create_service_config("tools_gw", entry, is_mcp_server=True)


def test_registry_yaml_keeps_the_gateway_block():
    data = tools_to_registry_yaml([_manage_entry()])
    entry = data["mcpServers"]["tools_gw"]
    assert entry["gateway"]["run_as"] == "service"
    assert entry["auth"] == {"type": "account_iam_apikey", "value": "db://gw-key"}
    assert entry["include"] == ["demo-time-add"]
    # The YAML round trip gives the same service config.
    cfg = _create_service_config("tools_gw", entry, is_mcp_server=True)
    assert cfg.url == "https://gw.example.com/mcp/" and cfg.transport == "http"


def test_the_api_key_is_never_sent_as_a_static_header():
    headers, query = {}, {}
    apply_authentication(Auth(type="account_iam_apikey", value="plain-fake-apikey"), headers, query)
    assert headers == {} and query == {}


# --- token exchange ----------------------------------------------------------------------


def _client(platform, auth):
    return httpx.Client(base_url=platform.url, auth=auth)


def test_exchanges_once_and_reuses_the_token(platform):
    auth = mcp_gateway.AccountIAMApiKeyAuth(platform.url, INSTANCE, "fake-apikey")
    with _client(platform, auth) as c:
        assert c.get("/tools").status_code == 200
        assert c.get("/tools").status_code == 200
    assert platform.exchanges == 1
    assert platform.apikeys == ["fake-apikey"]
    assert "fake-apikey" not in repr(auth)


def test_renews_after_a_401(platform):
    platform.reject_first_tool_call = True
    auth = mcp_gateway.AccountIAMApiKeyAuth(platform.url, INSTANCE, "fake-apikey")
    with _client(platform, auth) as c:
        assert c.get("/tools").status_code == 200
    assert platform.exchanges == 2
    assert platform.tools_calls[0] != platform.tools_calls[1]


def test_renews_a_token_close_to_expiry(platform):
    platform.token_ttl = mcp_gateway.RENEW_BEFORE_SECONDS - 10
    auth = mcp_gateway.AccountIAMApiKeyAuth(platform.url, INSTANCE, "fake-apikey")
    with _client(platform, auth) as c:
        c.get("/tools")
        c.get("/tools")
    assert platform.exchanges == 2


async def test_async_flow_exchanges_once(platform):
    auth = mcp_gateway.AccountIAMApiKeyAuth(platform.url, INSTANCE, "fake-apikey")
    async with httpx.AsyncClient(base_url=platform.url, auth=auth) as c:
        r1, r2 = (await c.get("/tools")), (await c.get("/tools"))
    assert (r1.status_code, r2.status_code) == (200, 200)
    assert platform.exchanges == 1


def test_iam_refusal_is_a_clear_error(platform):
    platform.iam_status = 400
    auth = mcp_gateway.AccountIAMApiKeyAuth(platform.url, INSTANCE, "fake-apikey")
    with pytest.raises(mcp_gateway.GatewayError, match="refused the instance API key"):
        with _client(platform, auth) as c:
            c.get("/tools")


def test_no_iam_url_is_a_clear_error():
    with pytest.raises(mcp_gateway.GatewayError, match="iam_proxy_url"):
        mcp_gateway.AccountIAMApiKeyAuth("", INSTANCE, "fake-apikey")


# --- discovery ---------------------------------------------------------------------------


async def test_discover_groups_tools_by_server(platform):
    result = await mcp_gateway.discover(f"{platform.url}/?instance={INSTANCE}", "fake-apikey")
    assert result["instance_id"] == INSTANCE
    assert result["mcp_url"] == f"{platform.url}/mcp/"
    assert result["servers"] == [
        {
            "name": "demo-time",
            "tools": [
                {
                    "name": "demo-time-add",
                    "description": "Add",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "demo-time-get-time",
                    "description": "Time",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ],
        },
        {
            "name": "notes",
            "tools": [
                {
                    "name": "notes-search",
                    "description": "Search",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ],
        },
    ]


async def test_discover_needs_a_key(platform):
    with pytest.raises(mcp_gateway.GatewayError, match="API key"):
        await mcp_gateway.discover(f"{platform.url}/?instance={INSTANCE}", "")


async def test_discover_reports_a_refused_key(platform):
    platform.iam_status = 400
    with pytest.raises(mcp_gateway.GatewayError, match="refused the instance API key"):
        await mcp_gateway.discover(f"{platform.url}/?instance={INSTANCE}", "fake-apikey")


async def test_discover_route_accepts_only_saved_secret_refs():
    from fastapi import HTTPException

    from cuga.backend.server.manage_routes.gateway_routes import GatewayDiscoverRequest, discover_gateway

    with pytest.raises(HTTPException) as e:
        await discover_gateway(
            GatewayDiscoverRequest(
                url=f"https://gw.example.com/?instance={INSTANCE}", api_key_ref="OPENAI_API_KEY"
            )
        )
    assert e.value.status_code == 400


@pytest.mark.parametrize(
    "run_as,error_kind,status",
    [
        ("user", "url", 400),
        ("user", "access", 403),
        ("user", "gateway", 502),
        ("service", "url", 400),
        ("service", "gateway", 502),
    ],
)
async def test_discover_route_keeps_exception_details_out_of_responses(
    monkeypatch, run_as, error_kind, status
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from cuga.backend.server.auth import gateway_tokens
    from cuga.backend.server.manage_routes.gateway_routes import GatewayDiscoverRequest, discover_gateway

    errors = {
        "url": ValueError,
        "access": gateway_tokens.GatewayAccessError,
        "gateway": mcp_gateway.GatewayError,
    }
    error = errors[error_kind]("private upstream detail")
    provider = SimpleNamespace(token=AsyncMock(return_value="instance-token"))
    monkeypatch.setattr(gateway_tokens, "provider_from_request", lambda request, user: provider)
    if run_as == "user" and error_kind == "url":

        def invalid_url(url):
            raise error

        monkeypatch.setattr(mcp_gateway, "parse_instance_url", invalid_url)
    elif error_kind == "access":
        provider.token.side_effect = error
    else:
        monkeypatch.setattr(mcp_gateway, "discover", AsyncMock(side_effect=error))

    with pytest.raises(HTTPException) as raised:
        await discover_gateway(
            GatewayDiscoverRequest(url=f"https://gw.example.com/?instance={INSTANCE}", run_as=run_as),
            request=SimpleNamespace(),
            user=SimpleNamespace(),
        )

    assert raised.value.status_code == status
    assert "private upstream detail" not in raised.value.detail
    assert "(ref " in raised.value.detail


def test_agent_include_filter_accepts_raw_mcp_tool_names():
    """Manage stores the gateway's raw names ("demo-time-add"); the agent's tools carry the
    sanitized, prefixed names ("team_tools_demo_time_add"). Both filters must keep the tool."""
    from types import SimpleNamespace

    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import CombinedToolProvider

    tools = [SimpleNamespace(name=n) for n in ("team_tools_demo_time_add", "team_tools_demo_time_echo")]
    kept = CombinedToolProvider._filter_tools_by_include(None, tools, "team_tools", ["demo-time-add"])
    assert [t.name for t in kept] == ["team_tools_demo_time_add"]
    kept = CombinedToolProvider._filter_tools_by_include(
        None, tools, "team_tools", ["team_tools_demo_time_echo"]
    )
    assert [t.name for t in kept] == ["team_tools_demo_time_echo"]


@pytest.mark.parametrize(
    "gw_ca, iam_ca, env, expected",
    [
        # An explicit gateway bundle wins.
        ("/etc/gw-ca.pem", "/etc/platform.pem", {}, "/etc/gw-ca.pem"),
        # Otherwise the platform bundle that the IAM proxy uses (set by cuga-operator on Sovereign).
        ("", "/etc/platform.pem", {"SSL_CERT_FILE": "env"}, "/etc/platform.pem"),
        # Otherwise SSL_CERT_FILE, then REQUESTS_CA_BUNDLE, when the file exists.
        ("", "", {"SSL_CERT_FILE": "env"}, "env"),
        ("", "", {"REQUESTS_CA_BUNDLE": "env"}, "env"),
        ("", "", {"SSL_CERT_FILE": "/no/such/file.pem"}, True),
        # Nothing set: httpx's default store.
        ("", "", {}, True),
    ],
)
def test_gateway_tls_falls_back_to_the_platform_bundle(monkeypatch, tmp_path, gw_ca, iam_ca, env, expected):
    from cuga.config import settings

    bundle = tmp_path / "ca.pem"
    bundle.write_text("pem")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, str(bundle) if value == "env" else value)
    monkeypatch.setattr(settings.mcp_gateway, "ca_bundle", gw_ca, raising=False)
    monkeypatch.setattr(settings.mcp_gateway, "skip_verify", False, raising=False)
    monkeypatch.setattr(settings.auth, "iam_proxy_ca_bundle", iam_ca, raising=False)

    verify = mcp_gateway.gateway_settings()["gateway_verify"]

    assert verify == (str(bundle) if expected == "env" else expected)


def test_gateway_skip_verify_still_wins(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.mcp_gateway, "skip_verify", True, raising=False)
    monkeypatch.setattr(settings.auth, "iam_proxy_ca_bundle", "/etc/platform.pem", raising=False)

    assert mcp_gateway.gateway_settings()["gateway_verify"] is False
