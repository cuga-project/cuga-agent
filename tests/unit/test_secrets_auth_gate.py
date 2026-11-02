"""Regression tests for Change 1 — the secrets API fail-closed gate.

GHSA-r2pq-mqwg-m575 (9.1) and GHSA-4cj6-vfwp-8pq7 (7.5).

Each test fails against the unfixed tree:
  * the gate tests fail because the router used ``require_auth``, which returns
    None (pass-through) for every caller when ``auth.enabled=false``;
  * the proxy tests run the gate behind uvicorn's ProxyHeadersMiddleware, which
    rewrites the client address from X-Forwarded-For for trusted peers;
  * the origin and Host tests fail because the unfixed tree has no
    same-machine-but-untrusted-browser check at all;
  * GHSA-4cj6-vfwp-8pq7 itself is closed by deleting ``/api/secrets/resolve``
    (no test needed here — its absence is what test_secrets_routes.py, if any,
    would exercise via a 404).
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cuga.backend.server.auth.dependencies import require_secrets_access

# AGENTS.md: every test in this module is a fast, isolated unit test.
pytestmark = pytest.mark.unit


def _request(host, headers=None):
    client = SimpleNamespace(host=host) if host else None
    return SimpleNamespace(client=client, headers={"host": "127.0.0.1:7860", **(headers or {})})


@pytest.mark.asyncio
async def test_remote_caller_refused_on_default_settings():
    # Default settings ship auth.enabled=false. A remote (non-loopback) caller
    # must be refused rather than passed through.
    with pytest.raises(HTTPException) as exc:
        await require_secrets_access(_request("203.0.113.9"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_no_client_refused():
    with pytest.raises(HTTPException):
        await require_secrets_access(_request(None))


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
async def test_local_caller_allowed(host):
    # Same-machine callers keep working without a login (Decision 1 / Option A).
    assert await require_secrets_access(_request(host)) is None


def _proxied_client(peer, forwarded_allow_ips):
    # The gate behind uvicorn's ProxyHeadersMiddleware, which `cuga start` runs by
    # default. It rewrites request.client from X-Forwarded-For when the peer is in
    # FORWARDED_ALLOW_IPS (default "127.0.0.1").
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app = FastAPI()

    @app.get("/gated", dependencies=[Depends(require_secrets_access)])
    def gated():
        return {}

    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=forwarded_allow_ips)
    return TestClient(wrapped, client=(peer, 50000), base_url="http://127.0.0.1:7860")


@pytest.mark.parametrize("forwarded_allow_ips", ["127.0.0.1", "*"])
def test_spoofed_forwarded_for_from_remote_peer_refused(forwarded_allow_ips):
    # With FORWARDED_ALLOW_IPS=*, uvicorn turns this remote peer into 127.0.0.1.
    client = _proxied_client("203.0.113.9", forwarded_allow_ips)
    assert client.get("/gated", headers={"x-forwarded-for": "127.0.0.1"}).status_code == 401


@pytest.mark.parametrize(
    ("header", "value"),
    [("x-forwarded-for", "127.0.0.1"), ("x-real-ip", "127.0.0.1"), ("forwarded", "for=127.0.0.1")],
)
def test_loopback_peer_with_forwarding_header_refused(header, value):
    # A local reverse proxy forwards remote traffic from the loopback address.
    client = _proxied_client("127.0.0.1", "127.0.0.1")
    assert client.get("/gated", headers={header: value}).status_code == 401


def test_direct_loopback_caller_allowed_behind_proxy_middleware():
    assert _proxied_client("127.0.0.1", "127.0.0.1").get("/gated").status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("host_header", ["evil.example", "evil.example:7860", "10.0.0.5:7860", ""])
async def test_local_caller_with_untrusted_host_refused(host_header):
    # DNS rebinding: the attack page is same-origin with this server after the
    # rebind, so the browser sends no Origin header, but Host names the attacker's domain.
    with pytest.raises(HTTPException) as exc:
        await require_secrets_access(_request("127.0.0.1", headers={"host": host_header}))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("host_header", ["localhost", "localhost:7860", "127.0.0.1:9999", "[::1]:7860"])
async def test_local_caller_with_loopback_host_allowed(host_header):
    assert await require_secrets_access(_request("127.0.0.1", headers={"host": host_header})) is None


@pytest.mark.asyncio
async def test_local_caller_with_no_origin_header_allowed():
    # Non-browser local callers (curl, scripts, the CLI) never send an Origin
    # header and must be unaffected by the browser-origin check.
    assert await require_secrets_access(_request("127.0.0.1", headers={})) is None


@pytest.mark.asyncio
async def test_local_caller_with_trusted_origin_allowed():
    request = _request("127.0.0.1", headers={"origin": "http://localhost:7860"})
    assert await require_secrets_access(request) is None


@pytest.mark.asyncio
async def test_local_caller_with_untrusted_origin_refused():
    # A malicious web page open in the operator's browser also connects from
    # the loopback address; only the Origin allowlist distinguishes it from
    # this app's own local UI.
    request = _request("127.0.0.1", headers={"origin": "https://evil.example"})
    with pytest.raises(HTTPException) as exc:
        await require_secrets_access(request)
    assert exc.value.status_code == 401


def test_secrets_router_wiring_refuses_remote_caller():
    # F2: exercise the real router, not the gate function in isolation. TestClient
    # connects from a non-loopback host ("testclient"), so on default settings
    # (auth off) the wired dependency must refuse with 401. If the route's
    # dependency is reverted to require_auth, auth-off pass-through would let this
    # reach the handler instead — so this fails against the unfixed wiring.
    from fastapi.testclient import TestClient
    from cuga.backend.server import main

    client = TestClient(main.app)
    assert client.get("/api/secrets").status_code == 401


def test_manage_router_refuses_remote_caller():
    # Q2 / reviewer note: /api/manage carried the same hole (unauth remote caller
    # could repoint an LLM api_base and have a stored key sent to an attacker host).
    # The router now uses the same local-or-manage gate, so a remote caller is
    # refused on default settings.
    from fastapi.testclient import TestClient
    from cuga.backend.server import main

    client = TestClient(main.app)
    assert client.get("/api/manage/config").status_code == 401


@pytest.mark.asyncio
async def test_origin_on_configured_ui_port_allowed(monkeypatch):
    # The UI port comes from server_ports.demo, which operators can change.
    from cuga.config import settings

    monkeypatch.setattr(settings.server_ports, "demo", 8100)
    request = _request("127.0.0.1", headers={"origin": "http://localhost:8100"})
    assert await require_secrets_access(request) is None

    request = _request("127.0.0.1", headers={"origin": "http://localhost:7860"})
    with pytest.raises(HTTPException):
        await require_secrets_access(request)


@pytest.mark.asyncio
async def test_local_exemption_off_refuses_every_caller_when_auth_is_off(monkeypatch):
    # auth.allow_local_admin=false with auth off: no caller can authenticate, so the
    # protected APIs are closed to everyone, local callers included.
    from cuga.config import settings

    monkeypatch.setattr(settings.auth, "allow_local_admin", False)
    with pytest.raises(HTTPException) as exc:
        await require_secrets_access(_request("127.0.0.1"))
    assert exc.value.status_code == 401


# --- D2: authorization tracks authentication. There is no longer an
#     authorization_enabled switch, so the unsafe "auth on, authz off" state (every
#     logged-in user, including chat-only/no-role, reaching manage+secrets) cannot be
#     configured, and a stale flag from an older release is ignored. ---


def test_authorization_tracks_authentication_and_ignores_stale_flag(monkeypatch):
    from cuga.backend.server.auth import dependencies as deps
    from cuga.config import settings

    # A stale authorization_enabled=false left over from an older release must not
    # re-open the hole, and must not crash.
    monkeypatch.setattr(settings.auth, "authorization_enabled", False, raising=False)

    monkeypatch.setattr(deps, "_auth_enabled", lambda: True)
    assert deps._authorization_enabled() is True  # auth on  -> authz on

    monkeypatch.setattr(deps, "_auth_enabled", lambda: False)
    assert deps._authorization_enabled() is False  # auth off -> authz off


def test_auth_config_never_reports_auth_on_authz_off(monkeypatch):
    # /api/auth/config must never show enabled=true with authorization_enabled=false.
    from cuga.backend.server.auth import dependencies as deps

    monkeypatch.setattr(deps, "_auth_enabled", lambda: True)
    assert deps._auth_enabled() == deps._authorization_enabled()


def test_manage_role_enforced_whenever_auth_is_on(monkeypatch):
    from cuga.backend.server.auth import dependencies as deps
    from cuga.backend.server.auth.models import UserInfo

    monkeypatch.setattr(deps, "_auth_enabled", lambda: True)  # => authorization on
    # A chat-only / no-role user has no manage access...
    assert deps.has_manage_access(UserInfo(sub="u", roles=["ServiceUser"])) is False
    assert deps.has_manage_access(UserInfo(sub="u", roles=[])) is False
    # ...a manage-role user does.
    assert deps.has_manage_access(UserInfo(sub="u", roles=["ServiceOwner"])) is True


# --- Deny paths with auth on, end to end through the real routers (Sovereign Core
#     review, "Missing deny-path tests"). The caller is a direct loopback client, so
#     these also show that the local exemption does not apply when auth is on. ---

_GATED_ROUTES = ["/api/secrets", "/api/manage/config"]


def _loopback_client():
    from fastapi.testclient import TestClient
    from cuga.backend.server import main

    return TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost")


@pytest.mark.parametrize("path", _GATED_ROUTES)
def test_auth_on_no_token_refused(monkeypatch, path):
    from cuga.backend.server.auth import dependencies as deps

    monkeypatch.setattr(deps, "_auth_enabled", lambda: True)
    assert _loopback_client().get(path).status_code == 401


@pytest.mark.parametrize("path", _GATED_ROUTES)
def test_auth_on_invalid_token_refused(monkeypatch, path):
    from cuga.backend.server.auth import dependencies as deps

    class _RejectingValidator:
        jwks_uri = "https://issuer.example/jwks"

        def validate_and_decode(self, token):
            raise ValueError("bad signature")

    async def _validator_for_token(token):
        return _RejectingValidator()

    monkeypatch.setattr(deps, "_auth_enabled", lambda: True)
    monkeypatch.setattr(deps, "_get_validator_for_token", _validator_for_token)
    response = _loopback_client().get(path, headers={"Authorization": "Bearer not-a-valid-token"})
    assert response.status_code == 401


@pytest.mark.parametrize("path", _GATED_ROUTES)
def test_auth_on_chat_only_role_forbidden(monkeypatch, path):
    from cuga.backend.server.auth import dependencies as deps
    from cuga.backend.server.auth.models import UserInfo

    async def _chat_user(request):
        return UserInfo(sub="chat-user", roles=["ServiceUser"])

    monkeypatch.setattr(deps, "_auth_enabled", lambda: True)
    monkeypatch.setattr(deps, "get_current_user", _chat_user)
    assert _loopback_client().get(path).status_code == 403


@pytest.mark.parametrize("path", _GATED_ROUTES)
def test_auth_on_manage_role_passes_gate(monkeypatch, path):
    # Positive control for the two deny tests above: the same request with a manage
    # role gets past the gate.
    from cuga.backend.server.auth import dependencies as deps
    from cuga.backend.server.auth.models import UserInfo

    async def _admin_user(request):
        return UserInfo(sub="admin", roles=["ServiceAdmin"])

    monkeypatch.setattr(deps, "_auth_enabled", lambda: True)
    monkeypatch.setattr(deps, "get_current_user", _admin_user)
    assert _loopback_client().get(path).status_code not in (401, 403)
