"""GitHub direct — one signed webhook serving all 14 triggers, no Activepieces.

GitHub was the largest AP dependency in the registry (14 of 42 triggers). What makes replacing it
tractable is that GitHub POSTs *every* event to ONE url and names the kind in ``X-GitHub-Event``,
so this is a signature check plus a dispatch table.

These tests pin the three things that fail quietly:
  * the signature gate must FAIL CLOSED — an unset secret is an open agent-execution endpoint,
  * the event map must not GUESS — an unmapped delivery is ignored, never fired at a random watcher,
  * a watcher armed for one repo must not fire on another.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from cuga.backend.events import github_direct as gh


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in (
        "GITHUB_WEBHOOK_SECRET",
        "GITHUB_TOKEN",
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_INSTALLATION_ID",
        "EVENTS_ALLOW_UNAUTHENTICATED",
    ):
        monkeypatch.setenv(k, "")
    gh._inst_cache.update({"token": "", "expires_at": 0.0})


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── the signature gate ──────────────────────────────────────────────────────────────────────────
def test_no_secret_refuses():
    """The whole point. 'Enforce if configured' would leave this endpoint open."""
    ok, why = gh.verify_signature({}, b"{}")
    assert ok is False and "GITHUB_WEBHOOK_SECRET" in why


def test_no_secret_can_be_opened_deliberately_for_local_dev(monkeypatch):
    monkeypatch.setenv("EVENTS_ALLOW_UNAUTHENTICATED", "1")
    ok, why = gh.verify_signature({}, b"{}")
    assert ok is True and "EVENTS_ALLOW_UNAUTHENTICATED" in why


def test_a_correct_signature_passes(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cr3t")
    body = b'{"action":"opened"}'
    ok, _ = gh.verify_signature({"x-hub-signature-256": _sig("s3cr3t", body)}, body)
    assert ok is True


def test_a_wrong_signature_is_refused(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cr3t")
    body = b'{"action":"opened"}'
    ok, why = gh.verify_signature({"x-hub-signature-256": _sig("WRONG", body)}, body)
    assert ok is False and "mismatch" in why


def test_a_tampered_body_is_refused(monkeypatch):
    """The signature covers the body — changing it after signing must not verify."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cr3t")
    good = _sig("s3cr3t", b'{"action":"opened"}')
    ok, _ = gh.verify_signature({"x-hub-signature-256": good}, b'{"action":"closed"}')
    assert ok is False


def test_a_missing_signature_header_is_refused(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cr3t")
    ok, why = gh.verify_signature({}, b"{}")
    assert ok is False and "missing" in why


def test_signature_header_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cr3t")
    body = b"{}"
    ok, _ = gh.verify_signature({"X-Hub-Signature-256": _sig("s3cr3t", body)}, body)
    assert ok is True


# ── the event map ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "gh_event,action,expected",
    [
        ("pull_request", "opened", "new_pr"),
        ("pull_request", "review_requested", "new_review_request"),
        ("issues", "opened", "new_issue"),
        ("push", None, "new_push"),
        ("release", "published", "new_release"),
        ("star", "created", "new_star"),
        ("member", "added", "new_collaborator"),
        ("discussion", "created", "new_discussion"),
    ],
)
def test_maps_github_vocabulary_to_registry_events(gh_event, action, expected):
    payload = {"action": action} if action else {}
    assert gh.event_of({"x-github-event": gh_event}, payload) == expected


def test_an_unmapped_action_is_ignored_not_guessed():
    """pull_request/closed is a real delivery nobody armed — it must map to nothing."""
    assert gh.event_of({"x-github-event": "pull_request"}, {"action": "closed"}) == ""


def test_an_unknown_event_is_ignored():
    assert gh.event_of({"x-github-event": "deployment_status"}, {}) == ""


def test_no_event_header_is_ignored():
    assert gh.event_of({}, {"action": "opened"}) == ""


def test_create_is_split_by_ref_type():
    """GitHub sends one 'create' for branches AND tags — only the branch is a trigger we have."""
    assert gh.event_of({"x-github-event": "create"}, {"ref_type": "branch"}) == "new_branch"
    assert gh.event_of({"x-github-event": "create"}, {"ref_type": "tag"}) == ""


def test_every_mapped_event_exists_in_the_registry():
    """A typo here would arm a watcher that can never match. Pin the map to the registry."""
    from cuga.backend.events import triggers as T

    known = {t.event for t in T.rows() if t.app == "github"}
    for target in set(gh._MAP.values()) | {"new_branch"}:
        assert target in known, f"{target} is not a github trigger in the registry"


# ── repo + mentions ─────────────────────────────────────────────────────────────────────────────
def test_repo_is_pulled_from_the_payload():
    assert gh.repo_of({"repository": {"full_name": "octo/demo"}}) == "octo/demo"
    assert gh.repo_of({}) == ""


def test_mentions_finds_the_login_in_a_comment_body():
    p = {"comment": {"body": "cc @octocat please look"}}
    assert gh.mentions(p, "octocat") is True
    assert gh.mentions(p, "someone-else") is False


def test_mentions_is_false_without_a_login():
    assert gh.mentions({"comment": {"body": "@octocat"}}, "") is False


# ── the repo filter — cross-repo firing is the bug this prevents ────────────────────────────────
def test_a_watcher_armed_for_one_repo_ignores_another():
    """Every repo the App is installed in POSTs to the SAME url, so without this filter a watcher
    on octo/one would fire on octo/two."""
    from cuga.backend.events import direct_events

    assert direct_events._cfg_match({"repo": "octo/one"}, repo="octo/one") is True
    assert direct_events._cfg_match({"repo": "octo/one"}, repo="octo/two") is False
    # case-insensitive: GitHub treats these as the same repository
    assert direct_events._cfg_match({"repo": "Octo/One"}, repo="octo/one") is True
    # no repo configured = watch them all, unchanged from before
    assert direct_events._cfg_match({}, repo="octo/anything") is True


# ── summaries ───────────────────────────────────────────────────────────────────────────────────
def test_summary_is_a_line_not_a_payload_dump():
    s = gh.summarize(
        "pull_request",
        {
            "repository": {"full_name": "octo/demo"},
            "sender": {"login": "kate"},
            "pull_request": {"number": 7, "title": "Fix the poller", "html_url": "u"},
        },
    )
    assert "octo/demo" in s and "#7" in s and "Fix the poller" in s and "kate" in s
    assert len(s) < 300


def test_summary_never_raises_on_a_sparse_payload():
    for ev in ("pull_request", "issues", "push", "release", "create", "discussion", "wat"):
        assert isinstance(gh.summarize(ev, {}), str)


# ── auth ────────────────────────────────────────────────────────────────────────────────────────
def test_configured_false_with_nothing_set():
    assert gh.configured() is False


def test_a_pat_is_enough(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    assert gh.configured() is True


@pytest.mark.asyncio
async def test_a_pat_short_circuits_minting(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    assert await gh.access_token() == "ghp_x"


@pytest.mark.asyncio
async def test_unconfigured_returns_empty(monkeypatch):
    assert await gh.access_token() == ""


def test_a_pem_with_escaped_newlines_is_repaired(monkeypatch):
    """Pasting a PEM into an env var mangles the newlines — a correct key would otherwise be
    rejected by the signer."""
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv(
        "GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\\nabc\\n-----END RSA PRIVATE KEY-----"
    )
    cfg = gh._app_config()
    assert "\\n" not in cfg["private_key"] and "\n" in cfg["private_key"]


def test_app_config_needs_both_id_and_key(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    assert gh._app_config() == {}
    assert gh.configured() is False


def test_the_map_covers_a_realistic_delivery():
    """End-to-end shape check with a payload the way GitHub actually sends it."""
    body = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": "octo/demo"},
            "sender": {"login": "kate"},
            "pull_request": {"number": 1, "title": "t", "html_url": "u"},
        }
    )
    payload = json.loads(body)
    hdrs = {"x-github-event": "pull_request"}
    assert gh.event_of(hdrs, payload) == "new_pr"
    assert gh.repo_of(payload) == "octo/demo"
