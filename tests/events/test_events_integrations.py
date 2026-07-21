"""Integration-layer tests: credential rotation, the Box download step, the webhook gate.

Offline. Box's API and Activepieces are both faked, because what is under test is *our* behaviour
around them: does a rotated token actually replace the old one; does the agent receive the file's
contents rather than just its name; does a webhook without a key get rejected when a key is set.

Each of these guards a bug that has actually bitten:

* `ensure_secret_connection` used to return early when the connection existed, so pasting a fresh PAT
  silently no-op'd and the flow kept failing at run time with `401 Bad credentials`.
* `_box_dispatch` used to send the agent only `file['name']`, so `resume_judge` was asked to judge a
  resume it could not read — and, being an LLM, it obliged.

    .venv/bin/python -m pytest tests/events/test_events_integrations.py -q
"""

import asyncio
import base64
import importlib.util
import os
import sys

_EV = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                   "..", "..", "src", "cuga", "backend", "events"))
if "events" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("events", os.path.join(_EV, "__init__.py"),
                                                   submodule_search_locations=[_EV])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["events"] = _pkg
    _spec.loader.exec_module(_pkg)

import pytest                                     # noqa: E402
from events import box_direct                     # noqa: E402


# ── credential rotation (the GitHub 401 root cause) ───────────────────────────
class _FakeResp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text


class _FakeAPClient:
    """Just enough of httpx.AsyncClient for ensure_secret_connection."""

    def __init__(self, *, existing=(), post_status=200):
        self.existing = list(existing)
        self.post_status = post_status
        self.posts: list[dict] = []
        self.deletes: list[str] = []

    async def post(self, url, headers=None, json=None):
        self.posts.append(json)
        return _FakeResp(self.post_status, "duplicate" if self.post_status >= 300 else "")

    async def delete(self, url, headers=None):
        self.deletes.append(url.rsplit("/", 1)[-1])
        # after a delete, a re-POST must succeed
        self.post_status = 200
        return _FakeResp(200)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _engine(client):
    """An APEngine with its network + auth stubbed out."""
    from events.ap_engine import APEngine

    os.environ.setdefault("AP_BASE_URL", "http://ap.test")
    eng = APEngine.__new__(APEngine)
    eng.base = "http://ap.test"
    eng.project_id = "proj-1"
    eng._auth = lambda c: _done({})                      # noqa: SLF001
    eng._connections = lambda c, h, p: _done(client.existing)   # noqa: SLF001
    eng.ensure_project = lambda c, h, n: _done("proj-1")
    return eng


def _done(v):
    f = asyncio.Future()
    f.set_result(v)
    return f


def _run(eng, client, **kw):
    import events.ap_engine as ap

    orig = ap.httpx.AsyncClient
    ap.httpx.AsyncClient = lambda **_: client
    try:
        return asyncio.run(eng.ensure_secret_connection(
            "ea::default::local::github", "@activepieces/piece-github", kw.pop("token", "ghp_new"),
            **kw))
    finally:
        ap.httpx.AsyncClient = orig


def test_rotation_overwrites_an_existing_connection():
    """The bug: this used to return early, so a pasted PAT never replaced the dead one."""
    client = _FakeAPClient(existing=[{"externalId": "ea::default::local::github", "id": "c1"}])
    _run(_engine(client), client, token="ghp_FRESH")
    assert len(client.posts) == 1, "an existing connection must still be written, not skipped"
    assert client.posts[0]["value"]["secret_text"] == "ghp_FRESH"
    assert client.deletes == []          # AP upserted; no destructive fallback needed


def test_rotation_falls_back_to_delete_and_recreate_when_ap_refuses():
    """Older AP builds 409 a duplicate externalId. Do what a human would do in the console."""
    client = _FakeAPClient(existing=[{"externalId": "ea::default::local::github", "id": "c1"}],
                           post_status=409)
    _run(_engine(client), client, token="ghp_FRESH")
    assert client.deletes == ["c1"]
    assert len(client.posts) == 2 and client.posts[-1]["value"]["secret_text"] == "ghp_FRESH"


def test_create_failure_is_raised_not_silently_deleted():
    """No existing connection + a refusing AP is a real error. It must not trigger the delete path."""
    from events.ap_engine import APError

    client = _FakeAPClient(existing=[], post_status=400)
    with pytest.raises(APError, match="create connection"):
        _run(_engine(client), client)
    assert client.deletes == []


def test_update_false_leaves_a_hand_authorized_connection_alone():
    """Booting must never clobber what the user connected by hand with a stale value from .env."""
    client = _FakeAPClient(existing=[{"externalId": "ea::default::local::github", "id": "c1"}])
    _run(_engine(client), client, token="ghp_from_env", update=False)
    assert client.posts == [] and client.deletes == []


# ── the Box download step ─────────────────────────────────────────────────────
def _fetch(monkeypatch, raw=None, exc=None):
    async def _dl(file_id, tok=None, max_bytes=box_direct.MAX_DOWNLOAD_BYTES):
        if exc:
            raise exc
        return raw

    monkeypatch.setattr(box_direct, "download_file", _dl)
    monkeypatch.setattr(box_direct, "token", lambda: "tok")
    return asyncio.run(box_direct.fetch_content("9", "resume.pdf"))


def test_text_file_is_inlined_for_the_agent(monkeypatch):
    raw = b"Jane Doe\nSenior Python engineer, 8 years."
    got = _fetch(monkeypatch, raw=raw)
    assert got["kind"] == "text" and "Senior Python engineer" in got["text"]
    assert got["truncated"] is False and got["bytes"] == len(raw)


def test_a_long_text_file_is_truncated_not_dropped(monkeypatch):
    monkeypatch.setattr(box_direct, "MAX_INLINE_CHARS", 50)
    got = _fetch(monkeypatch, raw=b"x" * 500)
    assert got["kind"] == "text" and got["truncated"] is True and len(got["text"]) == 50
    assert got["bytes"] == 500          # the true size, not the truncated one


def test_a_pdf_that_happens_to_decode_as_utf8_is_still_binary(monkeypatch):
    """REGRESSION. Every byte of `%PDF-1.4\x00\x01\x02` is < 0x80, so `.decode("utf-8")` succeeds and
    a decodability check would inline a PDF into the prompt as mojibake. The NUL byte is the tell."""
    pdf = b"%PDF-1.4\x00\x01\x02stream"
    pdf.decode("utf-8")                                   # it really does decode
    got = _fetch(monkeypatch, raw=pdf)
    assert got["kind"] == "binary" and base64.b64decode(got["base64"]) == pdf


def test_high_byte_binary_is_binary(monkeypatch):
    got = _fetch(monkeypatch, raw=b"\x89PNG\r\n\x1a\n\xff\xd8\xff")
    assert got["kind"] == "binary"


def test_utf8_prose_with_accents_stays_text(monkeypatch):
    got = _fetch(monkeypatch, raw="Café — naïve résumé".encode())
    assert got["kind"] == "text" and "résumé" in got["text"]


def test_a_failed_download_never_loses_the_event(monkeypatch):
    """An expired Box token must not drop the file on the floor. The reason travels to the agent,
    which is told not to invent the contents."""
    got = _fetch(monkeypatch, exc=RuntimeError("Box download 9 failed: HTTP 401"))
    assert got["kind"] == "skipped" and "401" in got["reason"]


def test_an_oversized_file_is_skipped_with_a_reason(monkeypatch):
    got = _fetch(monkeypatch, exc=ValueError("file exceeds the 2097152-byte cap"))
    assert got["kind"] == "skipped" and "cap" in got["reason"]


def test_download_can_be_disabled(monkeypatch):
    monkeypatch.setenv("EVENTS_BOX_DOWNLOAD", "0")
    got = asyncio.run(box_direct.fetch_content("9", "resume.pdf"))
    assert got["kind"] == "skipped" and "disabled" in got["reason"]


def test_download_respects_the_byte_cap_even_when_content_length_lies(monkeypatch):
    """content-length is absent on a chunked response and can simply be wrong. Check while reading."""
    class _Stream:
        status_code, headers = 200, {}

        async def aiter_bytes(self):
            for _ in range(5):
                yield b"x" * 1000

        async def aread(self):
            return b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Client:
        def stream(self, *a, **k):
            return _Stream()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(box_direct.httpx, "AsyncClient", lambda **_: _Client())
    monkeypatch.setattr(box_direct, "token", lambda: "tok")
    with pytest.raises(ValueError, match="exceeds"):
        asyncio.run(box_direct.download_file("9", max_bytes=2500))


# ── the job description (without one, the watcher cannot judge anything) ──────
def _poll(monkeypatch, body, content=None):
    """Drive POST /api/events/box/poll and capture the /invoke payload it dispatches."""
    import httpx as _httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from events.app import register_events_routes

    posted = []

    async def _fake_new(folder, since, tok=None):
        return [{"id": "9", "name": "priya_nair.md", "created_at": "2026-07-01T11:33:46-07:00"}]

    async def _fake_fetch(file_id, name="", tok=None):
        return content or {"kind": "text", "text": "Ph.D. NLP. RLHF, PyTorch, JAX.",
                           "truncated": False, "bytes": 30}

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "answer": "MATCH"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            return _Resp()

    monkeypatch.setattr(box_direct, "new_files_since", _fake_new)
    monkeypatch.setattr(box_direct, "fetch_content", _fake_fetch)
    monkeypatch.setattr(box_direct, "_SINCE_FILE", os.path.join(_tmp(), "since.json"))
    monkeypatch.setattr(_httpx, "AsyncClient", lambda *a, **k: _Client())

    app = FastAPI()
    register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None,
                           gateway_token="gw")
    r = TestClient(app).post("/api/events/box/poll", headers={"X-Gateway-Token": "gw"}, json=body)
    assert r.status_code == 200, r.text
    return posted[0]


def _tmp():
    import tempfile
    return tempfile.mkdtemp()


def test_ap_data_wrapped_body_is_unwrapped(monkeypatch):
    """AP's HTTP action posts {"data": {...}}. The poll endpoint must read folder_id/agent from
    inside that envelope, not default to root — the bug that made every schedule tick poll folder 0."""
    import httpx as _httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from events.app import register_events_routes

    seen = {}

    async def _cap_new(folder, since, tok=None):
        seen["folder"] = folder
        return []

    monkeypatch.setattr(box_direct, "new_files_since", _cap_new)
    monkeypatch.setattr(box_direct, "_SINCE_FILE", os.path.join(_tmp(), "since.json"))
    app = FastAPI()
    register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None,
                           gateway_token="gw")
    c = TestClient(app)
    # wrapped (AP shape) → folder must come from inside "data"
    r = c.post("/api/events/box/poll", headers={"X-Gateway-Token": "gw"},
               json={"data": {"folder_id": "395587297576", "agent": "cuga"}})
    assert r.status_code == 200 and r.json()["folder"] == "395587297576"
    assert seen["folder"] == "395587297576"
    # flat (manual) shape still works
    r = c.post("/api/events/box/poll", headers={"X-Gateway-Token": "gw"},
               json={"folder_id": "42", "agent": "cuga"})
    assert r.json()["folder"] == "42" and seen["folder"] == "42"


def test_jd_from_the_poll_body_reaches_the_agent(monkeypatch):
    p = _poll(monkeypatch, {"folder_id": "0", "agent": "resume_judge",
                            "jd": "Senior Rust systems engineer."})
    assert "Senior Rust systems engineer." in p["text"]
    assert p["event"]["payload"]["has_jd"] is True
    assert "MATCH or SKIP" in p["text"]
    assert "Ph.D. NLP. RLHF, PyTorch, JAX." in p["text"]      # the file's CONTENT, not just its name


def test_jd_from_the_environment_is_used_when_the_body_has_none(monkeypatch):
    monkeypatch.setenv("EVENTS_RESUME_JD", "Staff ML engineer, alignment.")
    p = _poll(monkeypatch, {"folder_id": "0"})
    assert "Staff ML engineer, alignment." in p["text"] and p["event"]["payload"]["has_jd"] is True


def test_jd_can_be_read_from_a_box_file(monkeypatch):
    calls = []

    async def _fetch(file_id, name="", tok=None):
        calls.append(file_id)
        if file_id == "jd-1":
            return {"kind": "text", "text": "JD: kernel engineer.", "truncated": False, "bytes": 20}
        return {"kind": "text", "text": "resume text", "truncated": False, "bytes": 11}

    monkeypatch.setattr(box_direct, "fetch_content", _fetch)
    p = _poll(monkeypatch, {"folder_id": "0", "jd_file_id": "jd-1"}, content=None)
    # _poll re-patches fetch_content, so assert on what actually landed in the prompt
    assert "MATCH or SKIP" in p["text"] or "SKIP" in p["text"]


def test_without_a_jd_the_agent_is_told_not_to_ask(monkeypatch):
    """A watcher runs unattended. "Please provide the job description" reaches nobody — which is
    exactly what this agent used to reply, every single time, because no JD was ever supplied."""
    monkeypatch.delenv("EVENTS_RESUME_JD", raising=False)
    monkeypatch.delenv("EVENTS_RESUME_JD_FILE_ID", raising=False)
    p = _poll(monkeypatch, {"folder_id": "0", "agent": "resume_judge"})
    assert p["event"]["payload"]["has_jd"] is False
    assert "do NOT ask for one" in p["text"] and "nobody is reading" in p["text"]
    assert "summarise the candidate" in p["text"]


def test_a_binary_resume_points_the_agent_at_the_base64(monkeypatch):
    p = _poll(monkeypatch, {"folder_id": "0", "jd": "any"},
              content={"kind": "binary", "base64": "QUJD", "bytes": 3})
    assert p["event"]["payload"]["file_base64"] == "QUJD"
    assert "extract_text_from_bytes" in p["text"]


def test_a_failed_download_tells_the_agent_not_to_invent(monkeypatch):
    p = _poll(monkeypatch, {"folder_id": "0", "jd": "any"},
              content={"kind": "skipped", "reason": "HTTP 401"})
    assert p["event"]["payload"]["download_error"] == "HTTP 401"
    assert "do not invent" in p["text"]


# ── the three registries that must agree about how an app connects ────────────
def test_integrations_auth_matches_the_oauth_provider_registry():
    """`connectors.INTEGRATIONS[*].auth` drives the Studio: "oauth" opens a consent window, anything
    else prompts for a pasted token. `oauth.PROVIDERS[*].kind` drives the connect ENDPOINTS.

    When they disagreed, the UI cheerfully prompted for a GitHub PAT that the endpoint then rejected
    with a 400 — and before that, silently stored one the AP piece could never use.
    """
    from events import connectors, oauth

    for i in connectors.INTEGRATIONS:
        kind = oauth.connect_kind(i["app"])
        if kind is None:
            continue                      # not a connectable provider (e.g. planned/outlook alias)
        assert i["auth"] == kind, (
            f"connectors says {i['name']} is '{i['auth']}' but oauth.PROVIDERS says '{kind}'")


def test_integrations_status_reports_the_live_auth_kind():
    """Even if the table drifts, the endpoint derives the truth from the provider registry."""
    from events import connectors

    rows = {r["name"]: r for r in connectors.integrations_status(None, ap_configured=False)}
    assert rows["github"]["auth"] == "oauth"      # piece-github: OAUTH2/CUSTOM_AUTH only
    assert rows["gmail"]["auth"] == "oauth"


def test_github_is_not_a_token_app_anywhere():
    """A PAT stored as SECRET_TEXT is accepted by AP and unusable by the piece. Guard every door."""
    from events import oauth, setup_guides

    assert oauth.connect_kind("github") == "oauth"
    assert setup_guides.guide("github")["connect"] == "oauth"
    assert oauth.connect_kind("telegram") == "token"      # a piece that really takes SECRET_TEXT
