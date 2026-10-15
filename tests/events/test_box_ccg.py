"""Box Client Credentials Grant — the thing that removes the 60-minute dev token.

CCG is the right auth for a folder watcher because it has NO refresh token: the access token is
minted from static config (client id/secret + the subject it acts as), so an expiry is a re-mint
rather than a rotation. These tests pin the parts that fail SILENTLY — a mint that quietly returns
"" looks exactly like "Box was never configured", and the poll just reports no new files forever.
"""

from __future__ import annotations

import time

import pytest

from cuga.backend.events import box_direct


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Every Box variable absent by default, and the mint cache emptied between tests."""
    for k in (
        "EVENTS_BOX_TOKEN",
        "BOX_DEV_TOKEN",
        "BOX_CLIENT_ID",
        "BOX_CLIENT_SECRET",
        "BOX_ENTERPRISE_ID",
        "BOX_USER_ID",
    ):
        monkeypatch.setenv(k, "")
    box_direct._ccg_cache.update({"token": "", "expires_at": 0.0})


def _ccg(monkeypatch, **over):
    monkeypatch.setenv("BOX_CLIENT_ID", over.get("cid", "cid-1"))
    monkeypatch.setenv("BOX_CLIENT_SECRET", over.get("csec", "csec-1"))
    if "user" in over:
        monkeypatch.setenv("BOX_USER_ID", over["user"])
    else:
        monkeypatch.setenv("BOX_ENTERPRISE_ID", over.get("ent", "ent-1"))


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = "body"

    def json(self):
        return self._payload


def _stub_post(monkeypatch, resp, seen=None):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            if seen is not None:
                seen.append((url, dict(data or {})))
            if isinstance(resp, Exception):
                raise resp
            return resp

    monkeypatch.setattr(box_direct.httpx, "AsyncClient", _Client)


# ── configured() — the status endpoints depend on this ──────────────────────────────────────────
def test_configured_is_false_with_nothing_set():
    assert box_direct.configured() is False


def test_configured_true_on_a_static_token(monkeypatch):
    monkeypatch.setenv("BOX_DEV_TOKEN", "dev-abc")
    assert box_direct.configured() is True


def test_configured_true_on_ccg_creds_alone(monkeypatch):
    """The regression this guards: status used to call token(), which is empty under CCG, so a
    working deployment reported 'not_connected'."""
    _ccg(monkeypatch)
    assert box_direct.token() == ""
    assert box_direct.configured() is True


def test_client_creds_without_a_subject_are_not_enough(monkeypatch):
    monkeypatch.setenv("BOX_CLIENT_ID", "cid-1")
    monkeypatch.setenv("BOX_CLIENT_SECRET", "csec-1")
    assert box_direct._ccg_config() == {}
    assert box_direct.configured() is False


# ── access_token() ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_static_token_wins_and_never_mints(monkeypatch):
    """An existing dev-token setup must keep working with no network call at all."""
    monkeypatch.setenv("BOX_DEV_TOKEN", "dev-abc")
    _ccg(monkeypatch)
    seen = []
    _stub_post(monkeypatch, _Resp(200, {"access_token": "minted", "expires_in": 3600}), seen)
    assert await box_direct.access_token() == "dev-abc"
    assert seen == [], "a static token must short-circuit the mint"


@pytest.mark.asyncio
async def test_mints_and_sends_the_right_grant(monkeypatch):
    _ccg(monkeypatch)
    seen = []
    _stub_post(monkeypatch, _Resp(200, {"access_token": "tok-1", "expires_in": 3600}), seen)
    assert await box_direct.access_token() == "tok-1"
    url, body = seen[0]
    assert url == box_direct.OAUTH_TOKEN_URL
    assert body["grant_type"] == "client_credentials"
    assert body["box_subject_type"] == "enterprise"
    assert body["box_subject_id"] == "ent-1"


@pytest.mark.asyncio
async def test_user_subject_overrides_enterprise(monkeypatch):
    _ccg(monkeypatch, user="u-9")
    monkeypatch.setenv("BOX_ENTERPRISE_ID", "ent-1")
    seen = []
    _stub_post(monkeypatch, _Resp(200, {"access_token": "tok-u", "expires_in": 3600}), seen)
    await box_direct.access_token()
    assert seen[0][1]["box_subject_type"] == "user"
    assert seen[0][1]["box_subject_id"] == "u-9"


@pytest.mark.asyncio
async def test_second_call_uses_the_cache(monkeypatch):
    _ccg(monkeypatch)
    seen = []
    _stub_post(monkeypatch, _Resp(200, {"access_token": "tok-1", "expires_in": 3600}), seen)
    await box_direct.access_token()
    await box_direct.access_token()
    assert len(seen) == 1, "a cached, unexpired token must not be re-minted"


@pytest.mark.asyncio
async def test_expiry_triggers_a_re_mint(monkeypatch):
    _ccg(monkeypatch)
    seen = []
    _stub_post(monkeypatch, _Resp(200, {"access_token": "tok-1", "expires_in": 3600}), seen)
    await box_direct.access_token()
    box_direct._ccg_cache["expires_at"] = time.time() - 1  # pretend it aged out
    await box_direct.access_token()
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_the_cache_expires_early_by_the_skew(monkeypatch):
    """A token that dies mid-request is the failure this skew exists to prevent."""
    _ccg(monkeypatch)
    _stub_post(monkeypatch, _Resp(200, {"access_token": "tok-1", "expires_in": 3600}))
    before = time.time()
    await box_direct.access_token()
    remaining = box_direct._ccg_cache["expires_at"] - before
    assert remaining > 0
    assert remaining <= 3600 - box_direct._CCG_SKEW + 1


@pytest.mark.asyncio
async def test_a_failed_mint_returns_empty_not_an_exception(monkeypatch):
    """A Box outage must not kill the scheduler loop — the tick reports no token and moves on."""
    _ccg(monkeypatch)
    _stub_post(monkeypatch, _Resp(401, {}))
    assert await box_direct.access_token() == ""


@pytest.mark.asyncio
async def test_a_network_error_returns_empty_not_an_exception(monkeypatch):
    _ccg(monkeypatch)
    _stub_post(monkeypatch, RuntimeError("dns"))
    assert await box_direct.access_token() == ""


@pytest.mark.asyncio
async def test_a_200_with_no_access_token_is_still_a_failure(monkeypatch):
    _ccg(monkeypatch)
    _stub_post(monkeypatch, _Resp(200, {"expires_in": 3600}))
    assert await box_direct.access_token() == ""


@pytest.mark.asyncio
async def test_unconfigured_returns_empty_without_calling_out(monkeypatch):
    seen = []
    _stub_post(monkeypatch, _Resp(200, {"access_token": "x"}), seen)
    assert await box_direct.access_token() == ""
    assert seen == []
