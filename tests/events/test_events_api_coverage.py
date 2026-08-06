"""Exhaustive OFFLINE coverage for the events HTTP endpoints the other suites missed.

Every test here runs with FAKE creds + ``engine=None`` (AP off) via TestClient — no real Slack/
Telegram/Discord/AP secrets, so it is safe in CI. It fills the audited gaps: dry-run, dashboard,
docs, slack/events (fake signature), synth-fire, connect/{app}(+callback), admin/oauth-apps,
admin/credential, and the NO-AP arm path at the HTTP layer.
"""

import importlib.util
import os
import sys
import tempfile

_EV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events"))
if "events" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "events", os.path.join(_EV, "__init__.py"), submodule_search_locations=[_EV]
    )
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["events"] = _pkg
    _spec.loader.exec_module(_pkg)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from events.app import register_events_routes  # noqa: E402
from events.subscriptions import SubscriptionStore  # noqa: E402
from events.runtime import DEFAULT_SCOPE  # noqa: E402

MINE = DEFAULT_SCOPE
TENANT, _, ME = MINE.split("/")


class _Runtime:
    def get_agent(self, agent, scope=""):
        return object()

    async def run(self, agent, thread_id, worker_input, scope="", deliver_to=None):
        return "42"


def _client(
    *, engine=None, users=None, oauth_store=None, identity=None, runtime=None, gateway_token=None, store=False
):
    st = SubscriptionStore(os.path.join(tempfile.mkdtemp(), "s.db")) if store else None
    app = FastAPI()
    register_events_routes(
        app,
        runtime=runtime or _Runtime(),
        store=st,
        concierge=None,
        engine=engine,
        users=users,
        oauth_store=oauth_store,
        identity=identity,
        gateway_token=gateway_token,
    )
    return TestClient(app), st


# ── /api/events/dry-run (GET + POST) ─────────────────────────────────────────────────────────────
def test_dry_run_get_previews_a_cron_with_no_side_effects():
    c, _ = _client()
    b = c.get("/api/events/dry-run", params={"text": "every 5 minutes tell me a fun fact"}).json()
    assert b["ok"] and b["mode"] == "CRON" and b["backend"] == "native"
    assert b.get("would") == "arm" and "none" in b.get("side_effects", "")  # no side effects
    assert b.get("next_fire_preview")  # preview only, not armed


def test_dry_run_post_matches_get_and_empty_text_is_400():
    c, _ = _client()
    b = c.post("/api/events/dry-run", json={"text": "when a new email arrives summarize it"}).json()
    assert b["mode"] == "PUSH" and b.get("source") == "gmail"
    assert c.get("/api/events/dry-run", params={"text": "  "}).status_code == 400


# ── /api/events/dashboard ────────────────────────────────────────────────────────────────────────
def test_dashboard_serves_nonempty_html():
    c, _ = _client()
    r = c.get("/api/events/dashboard")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html") and len(r.text) > 500


# ── /api/events/docs/{page} ──────────────────────────────────────────────────────────────────────
def test_docs_unknown_page_is_404():
    c, _ = _client()
    assert c.get("/api/events/docs/definitely-not-a-page").status_code == 404


# ── /api/events/slack/events — the public seam, tested with a FAKE signature ─────────────────────
def test_slack_url_verification_echoes_the_challenge_without_a_signature():
    c, _ = _client()
    r = c.post("/api/events/slack/events", json={"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.text == "abc123"


def test_slack_event_with_a_bad_signature_is_401(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shh")  # secret present → signature is enforced
    c, _ = _client()
    r = c.post(
        "/api/events/slack/events",
        headers={"X-Slack-Signature": "v0=deadbeef", "X-Slack-Request-Timestamp": "1"},
        json={"event": {"type": "message", "text": "hi"}},
    )
    assert r.status_code == 401


# ── /api/events/synth-fire — debug seam: gates + happy dispatch ──────────────────────────────────
def test_synth_fire_disabled_by_env_is_403(monkeypatch):
    monkeypatch.setenv("EVENTS_DEBUG_RUN", "0")
    c, _ = _client(gateway_token="gw")
    assert (
        c.post(
            "/api/events/synth-fire",
            headers={"X-Gateway-Token": "gw"},
            json={"source": "github", "event": "new_pr"},
        ).status_code
        == 403
    )


def test_synth_fire_requires_the_gateway_token_401():
    c, _ = _client(gateway_token="gw")
    assert c.post("/api/events/synth-fire", json={"source": "github"}).status_code == 401


def test_synth_fire_unknown_source_is_404():
    c, _ = _client(gateway_token="gw")
    r = c.post("/api/events/synth-fire", headers={"X-Gateway-Token": "gw"}, json={"source": "not-a-real-app"})
    assert r.status_code == 404


def test_synth_fire_happy_path_posts_to_invoke_with_the_trigger_event(monkeypatch):
    import httpx

    posted = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "answer": "done"}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _C())
    c, _ = _client(gateway_token="gw")
    r = c.post(
        "/api/events/synth-fire",
        headers={"X-Gateway-Token": "gw"},
        json={"source": "github", "event": "new_pr", "payload": {"number": 7}},
    )
    assert r.status_code == 200, r.text
    assert posted and posted[0]["event"]["kind"] == "new_pr"


# ── /api/events/connect/{app} (+ callback) — OAuth begin + the state-tamper guard ────────────────
def test_connect_unknown_app_is_404():
    c, _ = _client()
    assert c.get("/api/events/connect/myspace").status_code == 404


def test_connect_token_app_reports_kind_token():
    c, _ = _client()
    b = c.get("/api/events/connect/telegram").json()
    assert b["ok"] and b["kind"] == "token"


def test_connect_oauth_app_not_configured_is_501(monkeypatch):
    for k in list(os.environ):
        if k.startswith("EVENTS_OAUTH_BOX_"):
            monkeypatch.delenv(k, raising=False)
    c, _ = _client()
    assert c.get("/api/events/connect/box").status_code == 501


def test_connect_oauth_app_configured_redirects_302(monkeypatch):
    monkeypatch.setenv("EVENTS_OAUTH_BOX_CLIENT_ID", "cid")
    monkeypatch.setenv("EVENTS_OAUTH_BOX_CLIENT_SECRET", "csec")
    monkeypatch.setenv("EVENTS_PUBLIC_URL", "https://example.test")
    c, _ = _client()
    r = c.get("/api/events/connect/box", follow_redirects=False)
    assert r.status_code == 302 and "location" in r.headers


def test_connect_callback_with_a_tampered_state_is_rejected():
    c, _ = _client()
    r = c.get("/api/events/connect/box/callback", params={"code": "x", "state": "forged-not-hmac-signed"})
    assert r.status_code in (400, 401)  # must NOT fall back to a header principal


# ── admin/oauth-apps — role gate + round-trip ────────────────────────────────────────────────────
def _admin_users(role="admin"):
    from events.users import UserStore

    u = UserStore(":memory:")
    u.add(ME, roles=[role], tenant=TENANT)
    return u


def test_admin_oauth_apps_requires_admin_403():
    from events.oauth import OAuthAppStore

    c, _ = _client(users=_admin_users("user"), oauth_store=OAuthAppStore(":memory:"))
    assert c.get("/api/events/admin/oauth-apps").status_code == 403
    assert (
        c.post(
            "/api/events/admin/oauth-apps", json={"app": "box", "client_id": "a", "client_secret": "b"}
        ).status_code
        == 403
    )


def test_admin_oauth_apps_set_then_list():
    from events.oauth import OAuthAppStore

    c, _ = _client(users=_admin_users("admin"), oauth_store=OAuthAppStore(":memory:"))
    r = c.post(
        "/api/events/admin/oauth-apps", json={"app": "box", "client_id": "cid", "client_secret": "csec"}
    )
    assert r.status_code == 200, r.text
    apps = c.get("/api/events/admin/oauth-apps").json()
    assert any(a.get("app") == "box" for a in apps.get("apps", []))


# ── admin/credential — role gate + whitelist guard ───────────────────────────────────────────────
def test_admin_credential_requires_admin_403():
    c, _ = _client(users=_admin_users("user"))
    assert (
        c.post("/api/events/admin/credential", json={"key": "SLACK_BOT_TOKEN", "value": "x"}).status_code
        == 403
    )


def test_admin_credential_rejects_a_key_not_in_the_whitelist_400():
    c, _ = _client(users=_admin_users("admin"))
    r = c.post("/api/events/admin/credential", json={"key": "NOT_A_WHITELISTED_KEY", "value": "x"})
    assert r.status_code == 400


# ── the NO-AP path at the HTTP layer (engine=None) ───────────────────────────────────────────────
def _concierge_client(engine=None):
    from events.agent_store import AgentStore
    from events.runtime import AgentStoreRuntime, AgentSpec
    from events.concierge import Concierge
    from events.principal import DEFAULT as _DEFAULT_PRINCIPAL

    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"))
    # agent_scope (<tenant>/<instance>) is where lookups happen; a bare "default" never matches and
    # sends the concierge down its live-LLM fallback. See test_arming_hitl._client.
    rt.upsert_agent(AgentSpec(name="cuga", prompt="c", integrations=[]), scope=_DEFAULT_PRINCIPAL.agent_scope)
    store = SubscriptionStore(":memory:")
    cg = Concierge(rt, store=store, engine=engine)
    app = FastAPI()
    register_events_routes(app, runtime=rt, store=store, concierge=cg, engine=engine, gateway_token="")
    return TestClient(app), store


def test_native_cron_arms_over_http_with_no_ap(monkeypatch):
    """HITL: a slash command PROPOSES; only an explicit "yes" arms."""
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _concierge_client(engine=None)
    r = c.post("/api/concierge", json={"text": "/cron every 2 minutes tell me a fun fact"})
    assert r.status_code == 200, r.text
    assert r.json().get("state") == "confirm"
    assert store.list() == [], "nothing may be armed before the human confirms"
    r2 = c.post("/api/concierge", json={"text": "yes"})
    assert r2.status_code == 200, r2.text
    assert r2.json().get("state") == "armed"
    subs = store.list()
    assert len(subs) == 1 and subs[0].backend == "native" and subs[0].ap_flow_id is None


def test_gmail_push_declines_over_http_with_no_ap():
    c, store = _concierge_client(engine=None)
    r = c.post("/api/concierge", json={"text": "/push when a new gmail arrives, summarize it"})
    assert r.status_code == 200
    assert r.json().get("state") == "confirm"  # proposed, not armed
    r2 = c.post("/api/concierge", json={"text": "yes"})
    low = (r2.json().get("answer") or r2.json().get("reply") or "").lower()
    assert "activepieces" in low or "make up" in low  # honest decline, nothing armed
    assert store.list() == []
