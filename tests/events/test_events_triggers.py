"""The trigger registry + everything that consumes it — offline, no server, no creds.

Covers the full (app, event) surface: registry completeness/consistency, per-trigger flow
building (every AP row renders its own piece trigger + payload map), resolution semantics
(aliases, defaults, loud unknowns, direct rows), the arm-time validation gate (slots), the
registry-driven classifier, signed OAuth state, the dedup unique-index race, the subscription
event/config columns, and the direct-event dispatcher's matching + dispatch.
"""

from __future__ import annotations

import asyncio
import os
import sys

_EVENTS = os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events")
_EVENTS = os.path.abspath(_EVENTS)
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import classify            # noqa: E402
import envelope            # noqa: E402
import flows               # noqa: E402
import triggers            # noqa: E402
import direct_events       # noqa: E402
from subscriptions import Subscription, SubscriptionStore, DuplicateSubscription  # noqa: E402


# ── registry completeness / consistency ──────────────────────────────────────
def test_registry_rows_are_complete():
    """Every row carries what its backend needs — an AP row without a piece trigger or a direct
    row without a transport kind is a trigger that can never fire."""
    for t in triggers.rows():
        assert t.app and t.event and t.title, t
        if t.backend == "ap":
            assert t.piece and t.ap_trigger, f"AP row {t.key} lacks piece/trigger"
            assert t.payload, f"AP row {t.key} has no payload map — the worker would get nothing"
        else:
            assert t.direct_kind, f"direct row {t.key} lacks a transport event kind"
        for slot in t.slots:
            assert slot in triggers.SLOTS, f"{t.key} names unknown slot {slot!r}"


def test_registry_github_is_fully_synth_fireable():
    """All 14 github triggers are WEBHOOK type → each must carry a synthetic payload shaped like
    the piece's sample output, and require the repo slot."""
    gh = triggers.events_for("github")
    assert len(gh) == 14
    for t in gh:
        assert t.fire == "synth" and t.synth, f"{t.key} not synth-fireable"
        assert "repo" in t.slots, f"{t.key} must require a repo"
        # the synthetic payload must exercise at least half the curated paths — a synth that
        # matches none of the payload map proves nothing about a real fire
        roots = {v.split("{{trigger.")[1].split(".")[0].split("}}")[0]
                 for v in t.payload.values() if "{{trigger." in v}
        hit = sum(1 for r in roots if r in t.synth)
        assert hit >= max(1, len(roots) // 2), f"{t.key}: synth covers {hit}/{len(roots)} roots"


def test_github_synths_satisfy_the_pieces_real_run_filters():
    """The AP github piece FILTERS webhook payloads, and two of its filters contradict its own
    sampleData. Both were found by reading piece-github@0.8.5's bundled run() and both produced a
    silent no-run (AP accepts the trigger; no run is ever created):

      new_release: run() = `action === "created" ? [body] : []`
                   …but its sampleData ships `action: "published"` → discarded.
      new_commit:  run() = drop unless ref startsWith "refs/heads/", then
                   `commits.filter(c => c.distinct === true)`
                   …but its sampleData omits `distinct` → zero items survive.

    A synth copied naively from sampleData therefore proves nothing. Pin the real contract."""
    rel = triggers.get("github", "new_release")
    assert rel.synth["action"] == "created", "new_release's piece run() only accepts action=created"
    com = triggers.get("github", "new_commit")
    assert com.synth["ref"].startswith("refs/heads/")
    assert com.synth.get("deleted") is False
    assert com.synth["commits"] and all(c.get("distinct") is True for c in com.synth["commits"]), \
        "new_commit's piece run() keeps only commits with distinct=true"


def test_every_github_trigger_declares_its_delivery_header():
    """One GitHub repo webhook carries every subscribed event type, so the piece disambiguates on
    the X-GitHub-Event header. A synthetic fire without it makes the piece emit NOTHING — so every
    github row must name the GitHub event it is delivered under, and /run must send it."""
    for t in triggers.events_for("github"):
        assert t.hook_event, f"{t.key} has no hook_event"
    # the two push-driven triggers share GitHub's single `push` event
    assert triggers.get("github", "new_push").hook_event == "push"
    assert triggers.get("github", "new_commit").hook_event == "push"
    assert triggers.get("github", "new_release").hook_event == "release"
    assert triggers.get("github", "new_gh_mention").hook_event == "issue_comment"
    # non-github rows don't carry one (nothing else needs a delivery header today)
    assert triggers.get("gmail", "new_email").hook_event == ""


def test_registry_event_kinds_flow_into_the_envelope():
    for t in triggers.rows():
        assert t.event in envelope.EVENT_KINDS, f"{t.event} missing from envelope.EVENT_KINDS"


def test_every_registry_event_is_unique_per_app():
    seen = set()
    for t in triggers.rows():
        assert t.key not in seen
        seen.add(t.key)


def test_exactly_one_default_per_ap_app():
    for app in ("github", "gmail", "box"):
        defaults = [t for t in triggers.events_for(app) if t.default]
        assert len(defaults) == 1, f"{app}: {[t.event for t in defaults]}"


# ── resolution semantics ─────────────────────────────────────────────────────
def test_resolution_aliases_defaults_and_loud_unknowns():
    assert triggers.get("github").event == "new_pr"                    # bare app → default
    assert triggers.get("github_pr").event == "new_pr"                 # legacy alias
    assert triggers.get("github_issue", "new_file").event == "new_issue"  # alias beats stale event
    assert triggers.get("github", "new_pull_request").event == "new_pr"   # event alias
    assert triggers.get("gmail", "new-labeled-email").event == "new_labeled_email"  # hyphens
    assert triggers.get("github", "nonsense") is None
    try:
        flows.resolve_trigger("github", "nonsense")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "known:" in str(e)


def test_every_ap_row_builds_its_own_flow():
    """The core regression the registry exists to prevent: (app, event) must select THAT event's
    piece trigger — the old code ignored `event` and armed the app default for everything."""
    for t in triggers.rows():
        if t.backend != "ap":
            continue
        f = flows.build_push_flow(agent="a", thread_id="t", prompt="p",
                                  source=t.app, event_kind=t.event)
        assert f["trigger"]["settings"]["triggerName"] == t.ap_trigger, t.key
        body = f["trigger"]["nextAction"]["settings"]["input"]["body"]
        assert body["event"]["kind"] == t.event
        payload = body["event"]["payload"]
        assert payload.pop("_raw", None) == "{{trigger}}"       # the net is always wired
        assert payload == t.payload, t.key


def test_direct_rows_render_placeholder_not_ap_piece():
    f = flows.build_push_flow(agent="a", thread_id="t", prompt="p",
                              source="box", event_kind="new_folder")
    assert f["trigger"]["settings"]["pieceName"] == "cuga-direct"


# ── the arm-time validation gate ─────────────────────────────────────────────
def test_validate_gate_asks_for_missing_slots_and_rejects_unknowns():
    t, q = triggers.validate("github", "new_star", {})
    assert t is not None and "repository" in q                  # repo missing → the slot question
    t, q = triggers.validate("github", "new_star", {"repo": "o/r"})
    assert t is not None and q == ""                            # satisfied → armable
    t, q = triggers.validate("gmail", "new_labeled_email", {})
    assert t is not None and "label" in q.lower()
    t, q = triggers.validate("github", "bogus_event", {})
    assert t is None and "unknown" in q


# ── the registry-driven classifier ───────────────────────────────────────────
# The classifier EVAL SET — one labelled utterance per registry trigger. This is the regression
# gate for NL→trigger routing: adding a trigger without a phrase that distinguishes it from every
# other trigger fails here. Every entry below was verified against the live server.
EVAL = [
    # github (14)
    ("when a new pull request opens on o/r, summarize it", ("github", "new_pr")),
    ("when a new issue is filed on o/r, triage it", ("github", "new_issue")),
    ("when my repo o/r gets a new star, post a thank-you", ("github", "new_star")),
    ("when code is pushed to main on o/r, audit the diff", ("github", "new_push")),
    ("when a new discussion opens on o/r, summarize it", ("github", "new_discussion")),
    ("when a comment is posted on a discussion in o/r, flag blockers",
     ("github", "new_discussion_comment")),
    ("when a new branch is created on o/r, check the naming convention", ("github", "new_branch")),
    ("when a collaborator is added to o/r, log their access", ("github", "new_collaborator")),
    ("when a repo label is created on o/r, announce it", ("github", "new_repo_label")),
    ("when a milestone is created on o/r, draft a plan", ("github", "new_milestone")),
    ("when a new release is published on o/r, summarize the changelog", ("github", "new_release")),
    ("when a commit lands on o/r, audit it for bugs", ("github", "new_commit")),
    ("when I'm requested to review a PR on o/r, summarize the diff",
     ("github", "new_review_request")),
    ("when someone mentions me on github in o/r, summarize the context",
     ("github", "new_gh_mention")),
    # gmail (4)
    ("when a new email arrives in my inbox, summarize it", ("gmail", "new_email")),
    ("when I label an email 'Read-later', summarize it", ("gmail", "new_labeled_email")),
    ("when an email arrives with a resume attached, judge it", ("gmail", "new_attachment")),
    ("when a new gmail label is created, announce it", ("gmail", "new_gmail_label")),
    # box (3)
    ("when a resume lands in my Box folder, judge it", ("box", "new_file")),
    ("when a new folder appears in box, index its contents", ("box", "new_folder")),
    ("when someone comments on a box file, summarize the thread", ("box", "new_box_comment")),
    # slack (8)
    ("when a message is posted in #incidents, triage it", ("slack", "new_channel_message")),
    ("when a message gets a :bug: reaction in slack, triage it", ("slack", "new_reaction")),
    ("when a reaction is removed from a task message, re-open it", ("slack", "reaction_removed")),
    ("when the team is @mentioned in a slack channel, draft an answer",
     ("slack", "new_slack_mention")),
    ("when a new channel is created, post a welcome", ("slack", "channel_created")),
    ("when a new user joins the workspace, send an onboarding brief", ("slack", "new_slack_user")),
    ("when a new custom emoji is added, announce it", ("slack", "new_emoji")),
    ("when I save a message for later, research it", ("slack", "saved_message")),
    # discord (2)
    ("when a new member joins the server on discord, greet them", ("discord", "new_member")),
    ("when someone posts in #help on discord, answer from the docs",
     ("discord", "new_channel_message")),
]


def test_classifier_eval_set_routes_every_trigger():
    """Every labelled utterance must land on its exact (app, event). A misroute here is a user
    arming the WRONG watcher — silently."""
    misrouted = [(t, want, classify.source_of(t)) for t, want in EVAL
                 if classify.source_of(t) != want]
    assert not misrouted, "\n".join(
        f"  {t!r}\n    want {w}, got {g}" for t, w, g in misrouted)


def test_classifier_eval_covers_every_ap_and_channel_trigger():
    """The eval set must not silently fall behind the registry — a new trigger needs a labelled
    utterance proving it is reachable from natural language."""
    covered = {want for _, want in EVAL}
    expected = {t.key for t in triggers.rows()
                if t.app not in ("webhook", "telegram")}     # webhook needs no arming; telegram
    missing = expected - covered                             # is a content-filter on converse
    assert not missing, f"registry triggers with no eval utterance: {sorted(missing)}"


def test_classifier_poll_vs_cron_new_items():
    """The proven misroute (feed_watcher armed CRON 3/3): an interval that emits only on NEW
    content is a POLL."""
    assert classify.classify(
        "check https://hnrss.org/frontpage every 15 minutes and tell me only about new items") == "POLL"
    assert classify.classify(
        "every 5 minutes check for new MoE papers and tell me only if there are new ones") == "POLL"
    # a plain schedule stays CRON; a plain PR watcher stays PUSH
    assert classify.classify("every day at 9am send me the price of bitcoin") == "CRON"
    assert classify.classify("when a new PR opens on o/r, summarize it") == "PUSH"


def test_gmail_and_github_label_triggers_do_not_collide():
    """`new_repo_label` (github) once matched on a bare "label … created", which swallowed
    "when a new gmail label is created" and sent the user to GitHub asking for a repo."""
    assert classify.source_of("when a new gmail label is created, announce it") == \
        ("gmail", "new_gmail_label")
    assert classify.source_of("when a repo label is created on o/r, announce it") == \
        ("github", "new_repo_label")


def test_classifier_delivery_clause_is_not_a_gmail_source():
    """'…email me the fit' is a DELIVERY clause — box stays the source."""
    assert classify.source_of("when a resume lands in my Box folder, email me the fit") == \
        ("box", "new_file")


# ── signed OAuth state ───────────────────────────────────────────────────────
def test_oauth_state_signature_roundtrip_tamper_and_expiry(monkeypatch):
    import oauth
    monkeypatch.setenv("GATEWAY_TOKEN", "test-key")
    st = oauth.encode_state(scope="t/·/u", app="gmail")
    d = oauth.decode_state(st)
    assert d.get("scope") == "t/·/u" and d.get("app") == "gmail"
    # tamper with the payload → hard reject
    payload, sig = st.rsplit(".", 1)
    assert oauth.decode_state(payload[:-2] + "xx." + sig) == {}
    # a plain-base64 (unsigned, legacy/attacker) state → hard reject
    import base64
    import json as _json
    legacy = base64.urlsafe_b64encode(_json.dumps({"scope": "attacker/·/x"}).encode()).decode()
    assert oauth.decode_state(legacy) == {}
    # expiry
    monkeypatch.setattr(oauth, "STATE_MAX_AGE_SECS", -1)
    assert oauth.decode_state(oauth.encode_state(scope="t/·/u")) == {}


# ── dedup race: the DB is the referee ────────────────────────────────────────
def test_dedup_unique_index_turns_the_race_into_reuse():
    st = SubscriptionStore(":memory:")
    st.upsert(Subscription(id="a1", mode="PUSH", target_agent="x", dedup_key="k1"))
    try:
        st.upsert(Subscription(id="a2", mode="PUSH", target_agent="x", dedup_key="k1"))
        raise AssertionError("expected DuplicateSubscription")
    except DuplicateSubscription as e:
        assert e.dedup_key == "k1"
    # same id → update, not a duplicate; different key → fine; deleted rows don't block
    st.upsert(Subscription(id="a1", mode="PUSH", target_agent="y", dedup_key="k1"))
    st.upsert(Subscription(id="a3", mode="PUSH", target_agent="x", dedup_key="k2"))
    st.delete("a3")
    st.upsert(Subscription(id="a4", mode="PUSH", target_agent="x", dedup_key="k2"))


def test_subscription_event_and_config_roundtrip():
    st = SubscriptionStore(":memory:")
    st.upsert(Subscription(id="s1", mode="PUSH", target_agent="repo_watcher",
                           source_connector="github", event="new_star",
                           config={"repo": "o/r"}, dedup_key="k9"))
    got = st.get("s1")
    assert got.event == "new_star" and got.config == {"repo": "o/r"}


# ── the direct-event dispatcher ──────────────────────────────────────────────
def _watcher(id="w1", app="slack", event="new_reaction", config=None, agent="incident_triage"):
    return Subscription(id=id, mode="PUSH", target_agent=agent, source_connector=app,
                        ap_flow_id=None, event=event, config=config or {},
                        thread_id="gw:slack:C1", prompt="triage it", dedup_key=id)


def test_direct_kind_mapping():
    assert direct_events.kind_for("slack", "reaction_added") == "new_reaction"
    assert direct_events.kind_for("slack", "team_join") == "new_slack_user"
    assert direct_events.kind_for("discord", "GUILD_MEMBER_ADD") == "new_member"
    assert direct_events.kind_for("slack", "some_unknown_event") == ""


def test_direct_match_applies_config_filters():
    st = SubscriptionStore(":memory:")
    st.upsert(_watcher("w1", config={"emoji": "bug"}))
    st.upsert(_watcher("w2", config={"emoji": "bookmark", "channel": "C9"}, agent="research_compass"))
    st.upsert(_watcher("w3", event="new_channel_message", config={"pattern": "https?://"},
                       app="telegram"))
    # emoji filter
    assert [s.id for s in direct_events.match(st, "slack", "new_reaction", emoji="bug")] == ["w1"]
    # channel + emoji must BOTH hold
    assert direct_events.match(st, "slack", "new_reaction", emoji="bookmark", channel="C1") == []
    assert [s.id for s in direct_events.match(st, "slack", "new_reaction",
                                              emoji="bookmark", channel="C9")] == ["w2"]
    # pattern filter (a URL-bearing telegram message)
    assert [s.id for s in direct_events.match(st, "telegram", "new_channel_message",
                                              text="see https://x.com")] == ["w3"]
    assert direct_events.match(st, "telegram", "new_channel_message", text="no link here") == []
    # AP-armed rows are never direct-dispatched
    st.upsert(_watcher("w4", config={}, agent="a"))
    row = st.get("w4")
    row.ap_flow_id = "apflow"
    st.upsert(row)
    assert all(s.id != "w4" for s in direct_events.match(st, "slack", "new_reaction", emoji="x"))


def test_direct_dispatch_posts_the_invoke_envelope(monkeypatch):
    posted = []

    class _Resp:
        status_code = 200

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append((url, json))
            return _Resp()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", lambda *a, **k: _C())
    subs = [_watcher("w1", config={"emoji": "bug"})]
    n = asyncio.run(direct_events.dispatch_all(
        subs, app="slack", event="new_reaction", payload={"reaction": "bug", "user": "U1"}))
    assert n == 1
    url, body = posted[0]
    assert url.endswith("/invoke")
    assert body["agent"] == "incident_triage" and body["deliver"] is True
    assert body["event"]["kind"] == "new_reaction"
    assert body["event"]["payload"]["reaction"] == "bug"
    assert body["source"]["thread_id"] == "gw:slack:C1"      # delivery → where it was armed from
