"""CUGA's admin proxy — the bridge that keeps the Studio working after admin routes were locked.

/api/events/admin/* on the eventing service now requires X-Gateway-Token. A browser cannot hold
that secret, so CUGA forwards on the Studio's behalf, attaching the token.

THE THING THAT MATTERS: the proxy must be GATED. An ungated forwarder does not fix the
privilege-escalation hole — it moves it to a different port and hands out the token for free.
These tests pin the gate, the token attachment, and that a proxy failure reads as a failed admin
action rather than a 500 from CUGA.
"""

from __future__ import annotations

import pathlib

import pytest

SRC = pathlib.Path("src/cuga/backend/server")


# ── the gate (pinned in source: wiring it wrong is the whole risk) ─────────────────────────────
def test_the_proxy_route_is_gated_by_manage_access():
    main = (SRC / "main.py").read_text()
    i = main.index("async def events_admin_proxy")
    window = main[i - 400 : i + 400]
    assert "require_manage_access" in window, "the proxy MUST inherit the Manage UI's auth"


# ── the gate, for real ─────────────────────────────────────────────────────────────────────────
# The source assertion above is necessary and NOT sufficient, and this pair exists because that
# gap shipped. `require_manage_access` was present on the route exactly as pinned, and the
# deployed endpoint still answered 200 to an anonymous POST: with auth.enabled false the
# dependency returns None and the role check passes None straight through, so the decoration is
# inert and core attaches GATEWAY_TOKEN for whoever asked. A grep for the symbol cannot see that
# — only calling the dependency can. So: call it.
@pytest.mark.asyncio
async def test_manage_access_rejects_an_anonymous_caller_when_auth_is_on(monkeypatch):
    """With authentication ENABLED, no session means 401 — this is what shuts the proxy."""
    from fastapi import HTTPException

    from cuga.backend.server.auth import dependencies as deps

    monkeypatch.setattr(deps, "_auth_enabled", lambda: True)
    monkeypatch.setattr(deps, "_authorization_enabled", lambda: False)

    async def _no_session(_request):
        return None

    monkeypatch.setattr(deps, "get_current_user", _no_session)

    with pytest.raises(HTTPException) as e:
        await deps.require_manage_access(_Req(method="POST"))
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_manage_access_is_a_passthrough_when_auth_is_off(monkeypatch):
    """The converse, pinned deliberately: this is WHY the deployment must enable authentication.

    Not an endorsement — a warning in executable form. While auth is off this dependency admits
    anonymous callers, so the admin proxy is only as closed as `DYNACONF_AUTH__ENABLED`. If this
    test ever starts failing because the pass-through was removed, that is an IMPROVEMENT: delete
    it and keep the stricter behaviour.
    """
    from cuga.backend.server.auth import dependencies as deps

    monkeypatch.setattr(deps, "_auth_enabled", lambda: False)
    monkeypatch.setattr(deps, "_authorization_enabled", lambda: False)

    async def _no_session(_request):
        return None

    monkeypatch.setattr(deps, "get_current_user", _no_session)

    assert await deps.require_manage_access(_Req(method="POST")) is None


def test_the_proxy_is_only_mounted_when_eventing_is_enabled():
    """Vanilla CUGA must gain no new surface."""
    main = (SRC / "main.py").read_text()
    i = main.index("@app.api_route(\"/api/events/admin/{path:path}\"")
    assert "events_bridge.events_enabled()" in main[i - 900 : i], "mount must be behind the switch"


def test_the_proxy_attaches_the_gateway_token():
    br = (SRC / "events_bridge.py").read_text()
    i = br.index("async def proxy_admin")
    body = br[i : i + 2200]
    assert "X-Gateway-Token" in body
    assert "GATEWAY_TOKEN" in body


def test_the_proxy_forwards_identity_headers():
    """So an admin action is attributed to a person, not the fallback principal."""
    br = (SRC / "events_bridge.py").read_text()
    body = br[br.index("async def proxy_admin") :]
    for h in ("X-Tenant-Id", "X-Instance-Id", "X-User-Id"):
        assert h in body, f"{h} must travel with the proxied call"


# ── behaviour ──────────────────────────────────────────────────────────────────────────────────
class _Req:
    def __init__(self, method="GET", headers=None, body=b"", params=None):
        self.method = method
        self.headers = headers or {}
        self._body = body
        self.query_params = params or {}

    async def body(self):
        return self._body


@pytest.mark.asyncio
async def test_unconfigured_eventing_reports_503_not_a_crash(monkeypatch):
    from cuga.backend.server import events_bridge as eb

    monkeypatch.setenv("EVENTS_API_URL", "")
    status, body = await eb.proxy_admin(_Req(), "users")
    assert status == 503 and body["ok"] is False


@pytest.mark.asyncio
async def test_an_unreachable_events_service_is_a_502_not_a_500(monkeypatch):
    """A proxy failure must read as a failed admin action, not as CUGA itself breaking."""
    from cuga.backend.server import events_bridge as eb

    monkeypatch.setenv("EVENTS_API_URL", "http://127.0.0.1:9")  # nothing listens
    monkeypatch.setenv("GATEWAY_TOKEN", "t")
    status, body = await eb.proxy_admin(_Req(), "users")
    assert status == 502 and body["ok"] is False
    assert "eventing service" in body["error"]


@pytest.mark.asyncio
async def test_it_sends_the_token_and_the_right_url(monkeypatch):
    import httpx

    from cuga.backend.server import events_bridge as eb

    monkeypatch.setenv("EVENTS_API_URL", "http://events.local")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-123")
    seen = {}

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, content=None, headers=None, params=None):
            seen.update(method=method, url=url, headers=headers or {})

            class _R:
                status_code = 200

                def json(self):
                    return {"ok": True}

            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _C)
    status, body = await eb.proxy_admin(_Req("POST", {"X-User-Id": "kate"}, b"{}"), "users")
    assert status == 200 and body == {"ok": True}
    assert seen["url"] == "http://events.local/api/events/admin/users"
    assert seen["headers"]["X-Gateway-Token"] == "tok-123"
    assert seen["headers"]["X-User-Id"] == "kate"


@pytest.mark.asyncio
async def test_the_verified_principal_overrides_a_self_asserted_user_id(monkeypatch):
    """An authenticated caller must not be able to act as someone else by setting a header.

    The events side builds its admin principal FROM X-User-Id and then checks that principal's
    roles, so whoever controls the header controls the authorization. Requiring a gateway token
    on those routes closed the anonymous hole but not this one: the route already depended on
    `require_manage_access`, and then discarded the user it verified and forwarded the caller's
    own header instead. Pin the override.
    """
    import httpx

    from cuga.backend.server import events_bridge as eb

    monkeypatch.setenv("EVENTS_API_URL", "http://events.local")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-123")
    seen = {}

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, content=None, headers=None, params=None):
            seen.update(headers=headers or {})

            class _R:
                status_code = 200

                def json(self):
                    return {"ok": True}

            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _C)

    class _User:
        sub = "real-person"
        email = "real@example.com"

    # The caller claims to be the bootstrap admin; the session says otherwise.
    await eb.proxy_admin(_Req("POST", {"X-User-Id": "admin"}, b"{}"), "users", _User())
    assert seen["headers"]["X-User-Id"] == "real-person", "the verified principal must win"


@pytest.mark.asyncio
async def test_the_header_still_travels_when_authentication_is_off(monkeypatch):
    """With no session there is nothing to verify, so the existing behaviour is preserved."""
    import httpx

    from cuga.backend.server import events_bridge as eb

    monkeypatch.setenv("EVENTS_API_URL", "http://events.local")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-123")
    seen = {}

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, content=None, headers=None, params=None):
            seen.update(headers=headers or {})

            class _R:
                status_code = 200

                def json(self):
                    return {"ok": True}

            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _C)
    await eb.proxy_admin(_Req("POST", {"X-User-Id": "kate"}, b"{}"), "users", None)
    assert seen["headers"]["X-User-Id"] == "kate"


# ── the SPA must actually route admin calls to CUGA ────────────────────────────────────────────
def test_the_spa_routes_admin_paths_to_core_not_the_events_origin():
    api = pathlib.Path("src/frontend_workspaces/frontend/src/api.ts").read_text()
    assert 'CORE_ONLY_PATHS = ["/api/events/admin"]' in api
    assert "!CORE_ONLY_PATHS.some" in api, "the events-origin test must exclude admin paths"
