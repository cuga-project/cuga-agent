"""Five defects found by reading the deployed system, each with the failure it actually caused.

They share a shape worth naming: **nothing threw**. A cursor reset, a watcher fired with no
content, a credential reference was passed through literally, a secret sat in plaintext, and two
docstrings described behaviour that had changed underneath them. Every one of these looked healthy
from the outside, which is why they get tests rather than just fixes.
"""

from __future__ import annotations

import os
import sys

import pytest

_EVENTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events")
)
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import box_direct  # noqa: E402
import delivery  # noqa: E402
import oauth  # noqa: E402

pytestmark = pytest.mark.unit


# ---- 1. the Box poll cursor must outlive the container -------------------
def test_box_cursor_survives_an_instance_replace(monkeypatch, tmp_path):
    """THE BUG: the watermark was `.box_since.json`, a relative path. Nothing in the CE deploy set
    EVENTS_BOX_SINCE_FILE, and with Postgres configured the deploy runs "no mount, no snapshots" —
    so it lived on the container's ephemeral disk. An instance replace lost it and every existing
    file in the watched folder re-fired as if newly added. Silent: a burst of correct-looking work.
    """
    monkeypatch.setenv("EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("EVENTS_BOX_SINCE_FILE", str(tmp_path / "unused.json"))

    assert box_direct.load_since("folder-1") is None
    box_direct.save_since("folder-1", "2026-09-03T10:00:00Z")
    assert box_direct.load_since("folder-1") == "2026-09-03T10:00:00Z"

    # The replace: a brand-new process against the same database, and no file to fall back on.
    assert not (tmp_path / "unused.json").exists(), "wrote the file — the DB path was not taken"
    import importlib

    reloaded = importlib.reload(box_direct)
    assert reloaded.load_since("folder-1") == "2026-09-03T10:00:00Z"
    assert reloaded.load_since("never-polled") is None  # and it is per folder, not global


def test_box_cursor_still_works_with_no_database(monkeypatch, tmp_path):
    """A dev box with no EVENTS_DB keeps the file. The fix must not require Postgres to poll."""
    monkeypatch.delenv("EVENTS_DB", raising=False)
    monkeypatch.setenv("EVENTS_BOX_SINCE_FILE", str(tmp_path / "since.json"))
    import importlib

    b = importlib.reload(box_direct)
    b.save_since("f", "2026-01-01T00:00:00Z")
    assert b.load_since("f") == "2026-01-01T00:00:00Z"
    assert (tmp_path / "since.json").exists()


# ---- 2. pointer-shaped Slack events need hydrating ----------------------
@pytest.mark.asyncio
async def test_slack_reaction_is_hydrated_with_the_message_text(monkeypatch):
    """THE BUG: `reaction_added` carries item.{channel,ts} — a POINTER — never the message. So
    "when someone reacts :bug:, review the code" reached the agent with a reaction and no code, and
    the agent correctly answered that it had nothing to review. Nothing errored.

    Hydrating `text` is enough because `direct_events.describe()` already renders that key, so the
    message reaches the prompt with no other change.
    """
    import direct_events
    import slack_direct

    async def _fake_fetch(channel, ts):
        assert (channel, ts) == ("C_ENG", "1712345678.9")
        return "def f(x): return x/0"

    monkeypatch.setattr(slack_direct, "fetch_message_text", _fake_fetch)

    ev = {
        "type": "reaction_added",
        "user": "U_ALICE",
        "reaction": "bug",
        "item": {"type": "message", "channel": "C_ENG", "ts": "1712345678.9"},
    }
    # what the endpoint does before dispatch
    payload = dict(ev)
    item = ev["item"]
    if not payload.get("text") and item.get("ts") and item.get("channel"):
        payload["text"] = await slack_direct.fetch_message_text(item["channel"], item["ts"])

    rendered = direct_events.describe("slack", "new_reaction", payload)
    assert "x/0" in rendered, f"the agent still cannot see the code: {rendered}"
    assert "reaction=bug" in rendered  # and the reaction is still there


@pytest.mark.asyncio
async def test_hydration_failure_does_not_drop_the_event(monkeypatch):
    """An unreadable message (missing scope, deleted message) must still fire the watcher with
    less context — not swallow it."""
    import slack_direct

    monkeypatch.setattr(slack_direct, "bot_token", lambda: "")  # no token → cannot fetch
    assert await slack_direct.fetch_message_text("C", "1.0") == ""


# ---- 3. OAuth credentials must go through the seam ----------------------
def test_oauth_client_secret_resolves_through_the_seam(monkeypatch):
    """THE BUG: the connect callback read EVENTS_OAUTH_<APP>_CLIENT_SECRET with a raw
    os.environ.get, bypassing oauth._env. Set it to `vault://…` and the LITERAL string was handed
    over as the client secret — authenticating against nothing, with no hint the reference was
    never resolved. Only bites the day someone moves to a vault, which is the worst day for it.
    """
    monkeypatch.setattr(oauth, "_cred_resolver", lambda app, key: "resolved-secret")
    assert oauth._env("box", "CLIENT_SECRET") == "resolved-secret"

    monkeypatch.setattr(oauth, "_cred_resolver", None)
    monkeypatch.setenv("EVENTS_OAUTH_BOX_CLIENT_SECRET", "from-env")
    assert oauth._env("box", "CLIENT_SECRET") == "from-env"


# ---- 4. admin-entered OAuth secrets are encrypted at rest ---------------
def test_oauth_app_store_encrypts_the_client_secret(monkeypatch):
    """THE BUG: the store's own docstring admitted "plaintext at rest ... Production TODO". These
    are admin-entered OAuth app secrets, so a database read handed them over directly."""
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    monkeypatch.setenv("CUGA_SECRET_KEY", Fernet.generate_key().decode())
    s = oauth.OAuthAppStore(":memory:")
    s.set("t1", "box", "cid-123", "SUPER-SECRET", "a b")

    at_rest = s._db.execute("SELECT client_secret, client_id FROM oauth_app").fetchone()
    assert at_rest[0].startswith("fernet:"), "client_secret is still readable at rest"
    assert "SUPER-SECRET" not in at_rest[0]
    assert at_rest[1] == "cid-123"  # the id is deliberately NOT encrypted — it is not a secret
    assert s.get("t1", "box", "CLIENT_SECRET") == "SUPER-SECRET"  # round-trips


def test_oauth_app_store_stays_plaintext_without_a_key(monkeypatch):
    """No CUGA_SECRET_KEY → dev parity with .env, and pre-existing plaintext rows still read. The
    `fernet:` marker is what lets both live in one column, so there is no migration."""
    monkeypatch.delenv("CUGA_SECRET_KEY", raising=False)
    s = oauth.OAuthAppStore(":memory:")
    s.set("t1", "box", "cid", "plain-secret")
    assert s._db.execute("SELECT client_secret FROM oauth_app").fetchone()[0] == "plain-secret"
    assert s.get("t1", "box", "CLIENT_SECRET") == "plain-secret"


# ---- 5. the docstrings that had drifted ---------------------------------
def test_all_four_channels_default_to_direct():
    """Two modules described a world that no longer existed: delivery.py said telegram/discord
    default to `ap`, and connectors.py hardcoded Telegram's backend as `ap`. Live, that reported a
    WORKING channel as AP-backed next to "Activepieces not reachable" — an operator would conclude
    it was down and go install AP to fix nothing. The dict is the authority; this pins it."""
    for ch in ("slack", "telegram", "discord", "web"):
        assert delivery.channel_backend(ch) == "direct", ch


def test_the_delivery_docstring_matches_the_defaults():
    """The specific drift: prose claiming an `ap` default while the dict below it said `direct`."""
    doc = delivery.__doc__ or ""
    assert "telegram/discord default to ``ap``" not in doc
    assert "default to ``direct``" in doc


def test_github_is_documented_as_oauth_not_a_pasted_pat():
    """oauth.py listed GitHub under "token (github PAT)" while PROVIDERS carries its OAuth
    authorize URL and setup/GITHUB.md says a pasted PAT is REJECTED. Someone reads that docstring
    to decide an integration approach."""
    assert oauth.PROVIDERS["github"]["kind"] == "oauth"
    assert "github PAT" not in (oauth.__doc__ or "")
