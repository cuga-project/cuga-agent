"""OAuth user-token store — the piece Activepieces used to own.

``oauth.exchange_code`` returned tokens and nothing stored them ("AP then stores/refreshes").
This store is the replacement, and it is the hard prerequisite for Gmail/Calendar/Outlook, whose
tokens expire hourly and can only be renewed with a refresh token.

The tests below concentrate on the failures that are SILENT — a dropped rotated refresh token
does not break today, it breaks at the next refresh, hours later, as "please reconnect".
"""

from __future__ import annotations

import time

import pytest

from cuga.backend.events.oauth_tokens import REFRESH_SKEW, TokenStore


@pytest.fixture
def store():
    return TokenStore(":memory:")


def test_nothing_stored_reads_as_empty(store):
    assert store.get("t", "gmail") == {}
    assert store.connected("t", "gmail") is False


def test_save_then_get_round_trips(store):
    store.save("t", "gmail", "u1", {"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
    g = store.get("t", "gmail", "u1")
    assert g["access_token"] == "at" and g["refresh_token"] == "rt"
    assert g["expires_at"] > time.time()


def test_expires_in_becomes_an_absolute_deadline(store):
    """A relative TTL is meaningless once it has been sitting in a database."""
    before = time.time()
    store.save("t", "gmail", "", {"access_token": "at", "expires_in": 100})
    assert before + 99 <= store.get("t", "gmail")["expires_at"] <= before + 101


def test_a_missing_expires_in_defaults_to_an_hour(store):
    store.save("t", "gmail", "", {"access_token": "at"})
    assert store.get("t", "gmail")["expires_at"] > time.time() + 3000


def test_a_junk_expires_in_does_not_explode(store):
    store.save("t", "gmail", "", {"access_token": "at", "expires_in": "soon"})
    assert store.get("t", "gmail")["expires_at"] > time.time()


def test_users_are_isolated(store):
    store.save("t", "gmail", "u1", {"access_token": "a1"})
    store.save("t", "gmail", "u2", {"access_token": "a2"})
    assert store.get("t", "gmail", "u1")["access_token"] == "a1"
    assert store.get("t", "gmail", "u2")["access_token"] == "a2"


def test_tenants_are_isolated(store):
    store.save("t1", "gmail", "u", {"access_token": "a1"})
    store.save("t2", "gmail", "u", {"access_token": "a2"})
    assert store.get("t1", "gmail", "u")["access_token"] == "a1"
    assert store.get("t2", "gmail", "u")["access_token"] == "a2"


def test_forget_removes_the_grant(store):
    store.save("t", "gmail", "u", {"access_token": "a", "refresh_token": "r"})
    store.forget("t", "gmail", "u")
    assert store.get("t", "gmail", "u") == {}


def test_connected_is_true_on_a_refresh_token_even_when_expired(store):
    """An expired access token with a refresh token is still a working connection."""
    store.save("t", "gmail", "u", {"access_token": "a", "refresh_token": "r", "expires_in": -10})
    assert store.connected("t", "gmail", "u") is True


def test_connected_is_false_when_expired_with_no_refresh_token(store):
    store.save("t", "gmail", "u", {"access_token": "a", "expires_in": -10})
    assert store.connected("t", "gmail", "u") is False


# ── THE one that bites: rotated refresh tokens ─────────────────────────────────────────────────
def test_a_refresh_response_without_a_refresh_token_keeps_the_old_one(store):
    """Most providers omit refresh_token on refresh. Dropping it would end the grant at the NEXT
    refresh — hours later, looking like an unrelated bug."""
    store.save("t", "gmail", "u", {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600})
    store.save("t", "gmail", "u", {"access_token": "a2", "expires_in": 3600})  # no refresh_token
    g = store.get("t", "gmail", "u")
    assert g["access_token"] == "a2"
    assert g["refresh_token"] == "r1", "the existing refresh token must survive"


def test_a_rotated_refresh_token_replaces_the_old_one(store):
    """Google/Microsoft may hand back a NEW one — keeping the old would break the next refresh."""
    store.save("t", "gmail", "u", {"access_token": "a1", "refresh_token": "r1"})
    store.save("t", "gmail", "u", {"access_token": "a2", "refresh_token": "r2"})
    assert store.get("t", "gmail", "u")["refresh_token"] == "r2"


# ── access_token(): refresh-before-use ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_live_token_is_returned_without_refreshing(store, monkeypatch):
    store.save("t", "gmail", "u", {"access_token": "live", "refresh_token": "r", "expires_in": 3600})
    called = []
    monkeypatch.setattr(store, "refresh", lambda *a, **k: called.append(1))
    assert await store.access_token("t", "gmail", "u") == "live"
    assert called == []


@pytest.mark.asyncio
async def test_a_token_inside_the_skew_window_is_refreshed_early(store, monkeypatch):
    """Refreshing only after expiry makes every token's death a user-visible failure first."""
    store.save("t", "gmail", "u", {"access_token": "old", "refresh_token": "r",
                                   "expires_in": REFRESH_SKEW / 2})

    async def _fake(tenant, app, user_id=""):
        store.save(tenant, app, user_id, {"access_token": "new", "expires_in": 3600})
        return store.get(tenant, app, user_id)

    monkeypatch.setattr(store, "refresh", _fake)
    assert await store.access_token("t", "gmail", "u") == "new"


@pytest.mark.asyncio
async def test_expired_with_no_refresh_token_returns_empty(store):
    store.save("t", "gmail", "u", {"access_token": "old", "expires_in": -10})
    assert await store.access_token("t", "gmail", "u") == ""


@pytest.mark.asyncio
async def test_no_grant_at_all_returns_empty(store):
    assert await store.access_token("t", "gmail", "nobody") == ""


# ── refresh() over the wire ────────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p, self.text = status, payload or {}, "body"

    def json(self):
        return self._p


def _stub(monkeypatch, resp, seen=None):
    import cuga.backend.events.oauth_tokens as mod

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, headers=None):
            if seen is not None:
                seen.append((url, dict(data or {})))
            if isinstance(resp, Exception):
                raise resp
            return resp

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _C)
    return mod


@pytest.mark.asyncio
async def test_refresh_sends_the_grant_and_persists(store, monkeypatch):
    store.save("t", "gmail", "u", {"access_token": "old", "refresh_token": "r1", "expires_in": -1})
    seen = []
    _stub(monkeypatch, _Resp(200, {"access_token": "new", "expires_in": 3600}), seen)
    out = await store.refresh("t", "gmail", "u")
    assert out["access_token"] == "new"
    assert seen[0][1]["grant_type"] == "refresh_token"
    assert seen[0][1]["refresh_token"] == "r1"
    assert store.get("t", "gmail", "u")["access_token"] == "new"


@pytest.mark.asyncio
async def test_a_refresh_http_error_returns_empty_and_keeps_the_old_grant(store, monkeypatch):
    """A revoked grant must not also destroy what we have — the user may re-consent."""
    store.save("t", "gmail", "u", {"access_token": "old", "refresh_token": "r1", "expires_in": -1})
    _stub(monkeypatch, _Resp(400, {}))
    assert await store.refresh("t", "gmail", "u") == {}
    assert store.get("t", "gmail", "u")["refresh_token"] == "r1"


@pytest.mark.asyncio
async def test_a_network_error_during_refresh_is_swallowed(store, monkeypatch):
    store.save("t", "gmail", "u", {"access_token": "old", "refresh_token": "r1", "expires_in": -1})
    _stub(monkeypatch, RuntimeError("dns"))
    assert await store.refresh("t", "gmail", "u") == {}


@pytest.mark.asyncio
async def test_refresh_without_a_refresh_token_is_a_no_op(store, monkeypatch):
    store.save("t", "gmail", "u", {"access_token": "old", "expires_in": -1})
    seen = []
    _stub(monkeypatch, _Resp(200, {"access_token": "x"}), seen)
    assert await store.refresh("t", "gmail", "u") == {}
    assert seen == [], "must not call the provider without a refresh token"


# ── the background renewal pass ────────────────────────────────────────────────────────────────
def test_due_lists_only_renewable_grants(store):
    store.save("t", "gmail", "soon", {"access_token": "a", "refresh_token": "r", "expires_in": 60})
    store.save("t", "gmail", "later", {"access_token": "a", "refresh_token": "r", "expires_in": 99999})
    store.save("t", "gmail", "orphan", {"access_token": "a", "expires_in": 60})  # no refresh token
    due = {u for _, _, u in store.due(within=600)}
    assert due == {"soon"}, "only grants that are close AND renewable"


@pytest.mark.asyncio
async def test_renew_due_counts_successes_and_survives_failures(store, monkeypatch):
    store.save("t", "gmail", "u1", {"access_token": "a", "refresh_token": "r", "expires_in": 60})
    store.save("t", "gmail", "u2", {"access_token": "a", "refresh_token": "r", "expires_in": 60})

    async def _flaky(tenant, app, user_id=""):
        if user_id == "u2":
            raise RuntimeError("boom")
        return {"access_token": "new"}

    monkeypatch.setattr(store, "refresh", _flaky)
    assert await store.renew_due() == 1  # u1 renewed, u2 blew up and was contained


# ── encryption at rest ─────────────────────────────────────────────────────────────────────────
def test_tokens_are_encrypted_at_rest_when_a_key_is_set(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("CUGA_SECRET_KEY", Fernet.generate_key().decode())
    s = TokenStore(":memory:")
    s.save("t", "gmail", "u", {"access_token": "super-secret", "refresh_token": "also-secret"})
    raw = s._db.execute("SELECT access_token,refresh_token FROM oauth_token").fetchone()
    assert "super-secret" not in raw[0] and raw[0].startswith("fernet:")
    assert "also-secret" not in raw[1]
    # ...and still reads back correctly
    assert s.get("t", "gmail", "u")["access_token"] == "super-secret"
