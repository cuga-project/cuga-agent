"""The NL→Flow BENCHMARK — utterance → expected FlowSpec, scored in CI.

This is the measuring stick for the deterministic half of NL→Flow (`flowspec.resolve`). Every case
is hand-labelled. The gates, strongest first:

  1. **ZERO wrong-at-high** — a HIGH-confidence spec that disagrees with the label is a user
     silently arming the WRONG watcher: the one unforgivable failure. Falling back to `ambiguous`
     (the LLM path) is always acceptable; being confidently wrong never is.
  2. **Every push trigger has a proven happy path** — at least one strict case per registry
     trigger resolves HIGH with the right (source, event).
  3. **Ask-when-incomplete** — a case whose right answer is a question must produce `spec.ask`
     (never an armable spec with a missing required slot, never a guess).
  4. **Slot extraction** — labelled config values must be extracted verbatim.

Case shape: (utterance, want) — want keys: kind · source · event · config (subset) ·
ask (True = the correct output is a question) · strict (True = MUST resolve high; False = a freer
paraphrase where high-correct and ambiguous are both acceptable, wrong-at-high never is).

The scorecard prints on every run: fast-path rate is INFORMATIONAL (paraphrases legitimately fall
to the LLM); correctness is GATED.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src", "cuga", "backend", "events"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src", "cuga", "backend"))

from events import flowspec, triggers  # noqa: E402


def _c(utterance, kind="push", source="", event="", config=None, ask=False, strict=True):
    return (utterance, {"kind": kind, "source": source, "event": event,
                        "config": config or {}, "ask": ask, "strict": strict})


R = {"repo": "acme/api"}
BENCH = [
    # ── github (14) — strict canonical + free paraphrase; repo slot required ────────────────────
    _c("when a new pull request opens on acme/api, summarize it", source="github", event="new_pr", config=R),
    _c("review new PRs on acme/api as they arrive and post a verdict",
       source="github", event="new_pr", config=R, strict=False),
    _c("when a PR is opened, review it", source="github", event="new_pr", ask=True),
    _c("when a new issue is filed on acme/api, triage it", source="github", event="new_issue", config=R),
    _c("as soon as an issue lands on acme/api, triage it by severity",
       source="github", event="new_issue", config=R, strict=False),
    _c("when my repo acme/api gets a new star, post a thank-you", source="github", event="new_star", config=R),
    _c("when code is pushed to main on acme/api, audit the diff", source="github", event="new_push", config=R),
    _c("when a new discussion opens on acme/api, summarize it", source="github", event="new_discussion", config=R),
    _c("when a comment is posted on a discussion in acme/api, flag blockers",
       source="github", event="new_discussion_comment", config=R),
    _c("when a new branch is created on acme/api, check the naming convention",
       source="github", event="new_branch", config=R),
    _c("when a collaborator is added to acme/api, log their access",
       source="github", event="new_collaborator", config=R),
    _c("when a repo label is created on acme/api, announce it", source="github", event="new_repo_label", config=R),
    _c("when a milestone is created on acme/api, draft a plan", source="github", event="new_milestone", config=R),
    _c("when a new release is published on acme/api, summarize the changelog",
       source="github", event="new_release", config=R),
    _c("when a commit lands on acme/api, audit it for bugs", source="github", event="new_commit", config=R),
    _c("when I'm requested to review a PR on acme/api, summarize the diff",
       source="github", event="new_review_request", config=R),
    _c("when someone mentions me on github in acme/api, summarize the context",
       source="github", event="new_gh_mention", config=R),
    # ── gmail (4) ────────────────────────────────────────────────────────────────────────────────
    _c("when a new email arrives in my inbox, summarize it", source="gmail", event="new_email"),
    _c("when I label an email 'Read-later', summarize it",
       source="gmail", event="new_labeled_email", config={"label": "Read-later"}),
    _c("when I label an email, summarize it", source="gmail", event="new_labeled_email", ask=True),
    _c("when an email arrives with a resume attached, judge it", source="gmail", event="new_attachment"),
    _c("when a new gmail label is created, announce it", source="gmail", event="new_gmail_label"),
    # ── box (3) ──────────────────────────────────────────────────────────────────────────────────
    _c("when a resume lands in my Box folder, judge it", source="box", event="new_file"),
    _c("when a new folder appears in box, index its contents", source="box", event="new_folder"),
    _c("when someone comments on a box file, summarize the thread", source="box", event="new_box_comment"),
    # ── slack (8) ────────────────────────────────────────────────────────────────────────────────
    _c("when a message is posted in #incidents, triage it",
       source="slack", event="new_channel_message", config={"channel": "incidents"}),
    _c("when a message gets a :bug: reaction in slack, triage it",
       source="slack", event="new_reaction", config={"emoji": "bug"}),
    _c("when a reaction is removed from a task message, re-open it", source="slack", event="reaction_removed"),
    _c("when the team is @mentioned in a slack channel, draft an answer",
       source="slack", event="new_slack_mention"),
    _c("when a new channel is created, post a welcome", source="slack", event="channel_created"),
    _c("when a new user joins the workspace, send an onboarding brief", source="slack", event="new_slack_user"),
    _c("when a new custom emoji is added, announce it", source="slack", event="new_emoji"),
    _c("when I save a message for later, research it", source="slack", event="saved_message"),
    # ── discord (2) · telegram · webhook (fast-path-excluded sources may resolve high) ──────────
    _c("when a new member joins the server on discord, greet them", source="discord", event="new_member"),
    _c("when someone posts in #help on discord, answer from the docs",
       source="discord", event="new_channel_message", config={"channel": "help"}),
    _c("when someone sends the bot a link on telegram, summarize it",
       source="telegram", event="new_channel_message", strict=False),
    _c("when a webhook fires, triage the payload", source="webhook", event="inbound", strict=False),
    # ── new pieces: google calendar / pinterest / youtube (ap) + discord reaction (direct) ──────
    _c("when a new event is added to my google calendar, brief me",
       source="google_calendar", event="new_event", strict=False),
    _c("when a calendar event is updated, tell me what changed",
       source="google_calendar", event="new_or_updated_event", strict=False),
    _c("when a meeting ends on my calendar, email me the follow-ups",
       source="google_calendar", event="event_ends", strict=False),
    _c("when there's a new pin on my pinterest board, share it",
       source="pinterest", event="new_pin", strict=False),
    _c("when a new board is created on pinterest, announce it",
       source="pinterest", event="new_board", strict=False),
    _c("when I get a new pinterest follower, thank them",
       source="pinterest", event="new_follower", strict=False),
    _c("when my youtube channel posts a new video, share it",
       source="youtube", event="new_video", strict=False),
    _c("when a new item appears in the rss feed https://example.com/feed.xml, summarize it",
       source="rss", event="new_item", strict=False),
    _c("when someone reacts in discord with :tada:, thank them",
       source="discord", event="new_reaction", strict=False),
    # ── schedules — deterministic cadence, LLM picks the agent (never fast-pathed) ──────────────
    _c("every morning at 9 send me the weather", kind="cron"),
    _c("every weekday at 8am send me a market brief", kind="cron"),
    _c("watch bitcoin every 2 minutes and ping me on any move", kind="poll"),
    _c("check the feed every hour and tell me only if something is new", kind="poll"),
    # ── negatives — answer-now, must NEVER become a flow ─────────────────────────────────────────
    _c("what is the current price of bitcoin?", kind="now"),
    _c("summarize the open PRs on acme/api", kind="now"),
    _c("who has starred acme/api recently?", kind="now"),
    _c("what's the weather in Yorktown?", kind="now"),
    # ── genuine ambiguity — multiple apps hit, none named: the right answer is NOT high ─────────
    _c("when something changes in my repo or my inbox, tell me", strict=False),
    _c("when anything new happens, give me a digest", kind="push", strict=False),
]


def _score():
    rows = []
    for utter, want in BENCH:
        spec = flowspec.resolve(utter)
        rows.append((utter, want, spec))
    return rows


def test_bench_zero_wrong_at_high():
    """THE gate. A high-confidence resolution that disagrees with the label = the wrong watcher
    armed silently. Zero tolerance — every other gate is negotiable, this one is not."""
    bad = []
    for utter, want, spec in _score():
        if spec.confidence != "high":
            continue
        if want["kind"] != "push" or not want["source"]:
            bad.append((utter, "resolved high but the label says "
                        f"kind={want['kind']!r}", spec))
        elif (spec.source, spec.event) != (want["source"], want["event"]):
            bad.append((utter, f"high but wrong trigger: got {spec.source}/{spec.event}, "
                        f"want {want['source']}/{want['event']}", spec))
    assert not bad, "WRONG-AT-HIGH (the unforgivable failure):\n" + "\n".join(
        f"  {u!r}: {why}" for u, why, _ in bad)


def test_bench_every_strict_case_resolves_high_and_right():
    """Strict cases are the per-trigger happy path: canonical phrasing MUST fast-path."""
    bad = []
    for utter, want, spec in _score():
        if not want["strict"] or want["kind"] != "push" or not want["source"]:
            continue
        if spec.confidence != "high":
            bad.append((utter, f"expected high, got {spec.confidence}"))
        elif (spec.source, spec.event) != (want["source"], want["event"]):
            bad.append((utter, f"got {spec.source}/{spec.event}"))
        elif want["ask"] and not spec.ask:
            bad.append((utter, "expected a question, got an armable spec"))
        elif not want["ask"] and spec.ask:
            bad.append((utter, f"unexpected question: {spec.ask!r}"))
    assert not bad, "\n".join(f"  {u!r}: {why}" for u, why in bad)


def test_bench_kind_classification():
    """cron/poll labels must classify correctly, and a NOW case must never be intercepted
    (classify's kind heuristic may loosely read a NOW as push — the LLM path tolerates that;
    what must NEVER happen is the fast path acting on it, i.e. confidence == high)."""
    bad = [(u, want["kind"], spec.kind) for u, want, spec in _score()
           if want["strict"] and want["kind"] in ("cron", "poll") and spec.kind != want["kind"]]
    bad += [(u, "now — never intercepted", f"high ({spec.source}/{spec.event})")
            for u, want, spec in _score()
            if want["kind"] == "now" and spec.confidence == "high"]
    assert not bad, "\n".join(f"  {u!r}: want {w}, got {g}" for u, w, g in bad)


def test_bench_slot_extraction():
    """Labelled config values must come out verbatim — a mangled slot is a garbage AP trigger.
    Gated only where the fast path ACTS (high confidence); an ambiguous case's slots ride to the
    LLM as language, not as config."""
    bad = []
    for utter, want, spec in _score():
        if spec.confidence != "high":
            continue
        for k, v in want["config"].items():
            if spec.config.get(k) != v:
                bad.append((utter, k, v, spec.config.get(k)))
    assert not bad, "\n".join(f"  {u!r}: {k}: want {v!r}, got {g!r}" for u, k, v, g in bad)


def test_bench_covers_every_push_trigger():
    """The bench must keep pace with the registry — a new trigger needs bench cases."""
    covered = {(w["source"], w["event"]) for _, w in BENCH if w["source"]}
    missing = [t.key for t in triggers.rows() if t.key not in covered]
    assert not missing, f"triggers with no bench case: {missing}"


def test_bench_scorecard():
    """Informational — prints the numbers (run pytest -s to see). Gated only on a floor so a
    regression that silently kills the fast path fails loudly."""
    rows = _score()
    push = [(w, s) for _, w, s in rows if w["kind"] == "push" and w["source"]]
    high = [(w, s) for w, s in push if s.confidence == "high"]
    right = [(w, s) for w, s in high if (s.source, s.event) == (w["source"], w["event"])]
    asks = [(w, s) for _, w, s in rows if w["ask"]]
    print(f"\nNL→Flow bench: {len(rows)} cases · push {len(push)} · "
          f"fast-path {len(high)}/{len(push)} ({100 * len(high) // max(len(push), 1)}%) · "
          f"correct-at-high {len(right)}/{len(high)} · ask-cases {len(asks)}")
    assert len(right) == len(high)                       # redundant with gate 1, cheap to assert
    assert len(high) >= int(0.7 * len(push)), "fast-path rate collapsed below 70%"


# ── ask-till-legit: park → fill → armable ─────────────────────────────────────────────────────
def test_fill_completes_a_parked_spec_with_a_bare_answer():
    spec = flowspec.resolve("when a PR is opened, review it")
    assert spec.ask and spec.missing == "repo"
    done = flowspec.fill(spec, "acme/api")
    assert done is not None and done.armable and done.config["repo"] == "acme/api"


def test_fill_rejects_a_topic_change():
    """A reply that reads as a new request must NOT be crammed into the slot."""
    spec = flowspec.resolve("when a PR is opened, review it")
    assert flowspec.fill(spec, "actually, what is the weather in Yorktown today?") is None
    assert flowspec.fill(spec, "every morning send me a digest") is None


def test_fill_validates_the_answer_shape():
    """'repo' must look like owner/repo — a stray word is re-asked, not armed."""
    spec = flowspec.resolve("when a PR is opened, review it")
    assert flowspec.fill(spec, "pachyderm") is None      # not owner/repo → not an answer


def test_fill_takes_a_quoted_label():
    spec = flowspec.resolve("when I label an email, summarize it")
    assert spec.ask and spec.missing == "label"
    done = flowspec.fill(spec, "'Read-later'")
    assert done is not None and done.armable and done.config["label"] == "Read-later"


def test_park_and_pending_roundtrip_with_ttl():
    spec = flowspec.resolve("when a PR is opened, review it")
    flowspec.park("t1", spec, "when a PR is opened, review it")
    got = flowspec.pending_for("t1")
    assert got is not None and got[0].missing == "repo"
    assert flowspec.pending_for("t1") is None            # popped — fill or lose
