"""The TRIGGER REGISTRY — the single source of truth for every (integration, event) we can watch.

Everything that used to be scattered across five files now derives from this table:

  * ``flows.SOURCE_TRIGGER`` / ``flows.PUSH_PAYLOAD``  → generated shims over :data:`TRIGGERS`
  * ``ap_engine.create_push_flow``                     → resolves (app, event) here, not per-app
  * ``classify._SOURCES``                              → built from each trigger's ``phrases``
  * the concierge's arm-time validation + slot prompts → :func:`validate`
  * ``envelope.EVENT_KINDS``                           → :func:`event_kinds` (union of rows + legacy)
  * docs (examples feasibility) and the parametrized tests → iterate :func:`rows`

One row = one trigger. Adding a trigger is adding ONE entry here (+ an agent that declares it).
An unknown (app, event) now FAILS LOUDLY at build time — the old silent ``(source, "new_item")``
fallback armed a flow that could never publish, and is gone.

Keep this module dependency-free (stdlib only): ``envelope`` and ``classify`` import it.

Field notes
-----------
``backend``   "ap"     → an Activepieces piece trigger (flow armed in AP; AP resolves the connection)
              "direct" → CUGA receives the event itself (Slack Events API / Discord Gateway / the
                         box_direct poller). No AP connection involved.
``payload``   AP rows: the curated ``{{trigger.<path>}}`` field map wired into the /invoke body.
              Paths follow the PIECE'S OWN sample output (fetched from the live AP catalog) — the
              raw webhook shape for github (``{{trigger.pull_request.title}}``, NOT a top-level
              ``title``). ``_raw`` is always added by ap_engine as the safety net.
              direct rows: the event fields the dispatcher lifts into the payload (None = whole event).
``slots``     config the trigger needs from the user, e.g. ("repo",). See :data:`SLOTS` for the
              question the concierge asks when one is missing.
``synth``     a sample payload for the debug ``POST /subscriptions/{id}/run`` — shaped like the
              piece's real output, so a synthetic fire exercises the same curated paths a real
              event would. Only meaningful for WEBHOOK-type AP triggers (github).
``fire``      how the trigger can be verified end-to-end:
              "synth"  — machine-fireable via /run (github webhook triggers)
              "real"   — fireable with a real, local action we control (box upload, slack message)
              "manual" — needs a real external event (a real email); arm-verified only
``default``   the app's default trigger — used when a caller names only the source (legacy path).
"""

from __future__ import annotations

from dataclasses import dataclass


# ── slots: per-trigger config the user must (or may) supply ─────────────────────────────────────
@dataclass(frozen=True)
class Slot:
    name: str
    question: str                       # what the concierge asks when the slot is missing
    required: bool = True


SLOTS = {
    "repo": Slot("repo", "Which repository (owner/repo) should I watch?"),
    "label": Slot("label", "Which Gmail label should I watch (as shown in Gmail)?"),
    "channel": Slot("channel", "Which channel should I watch?", required=False),
    "emoji": Slot("emoji", "Which emoji reaction should trigger this (e.g. bug, bookmark)?",
                  required=False),
    "folder": Slot("folder", "Which Box folder id should I watch?", required=False),
    "pattern": Slot("pattern", "What text/URL pattern should the message match?", required=False),
}


@dataclass(frozen=True)
class Trigger:
    app: str                            # integration / connection app ("github", "gmail", …)
    event: str                          # OUR canonical event kind (rides in envelope.event.kind)
    title: str                          # human name (mirrors the AP display name)
    backend: str = "ap"                 # "ap" | "direct"
    piece: str = ""                     # AP piece key (ap backend)
    ap_trigger: str = ""                # AP trigger name (ap backend)
    direct_kind: str = ""               # transport event type (direct backend)
    payload: dict | None = None         # curated field map (see module docstring)
    slots: tuple = ()                   # Slot names this trigger needs
    phrases: tuple = ()                 # regex fragments for the deterministic classifier
    synth: dict | None = None           # synthetic /run payload (webhook triggers only)
    # The provider's webhook EVENT NAME for this trigger (github's X-GitHub-Event header). One repo
    # webhook carries many event types, so the piece disambiguates on this header — a synthetic fire
    # without it makes the piece emit NOTHING (proven: new_release/new_commit produced no AP run).
    hook_event: str = ""
    fire: str = "manual"                # "synth" | "real" | "manual"
    default: bool = False               # the app's default trigger
    notes: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.app, self.event)


def _t(*a, **kw) -> Trigger:
    return Trigger(*a, **kw)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# GitHub — 14 triggers, all WEBHOOK type (⇒ all synthetically fireable via /run), all need `repo`.
# Payload paths follow the piece's sampleData (the raw GitHub webhook shape).
# ─────────────────────────────────────────────────────────────────────────────────────────────────
_GH = dict(app="github", backend="ap", piece="github", slots=("repo",), fire="synth")

_GITHUB = [
    _t(event="new_pr", title="New Pull Request", ap_trigger="trigger_pull_request", hook_event="pull_request", default=True,
       payload={"action": "{{trigger.action}}", "title": "{{trigger.pull_request.title}}",
                "body": "{{trigger.pull_request.body}}", "url": "{{trigger.pull_request.html_url}}",
                "author": "{{trigger.pull_request.user.login}}",
                "repo": "{{trigger.repository.full_name}}",
                "changed_files": "{{trigger.pull_request.changed_files}}",
                "additions": "{{trigger.pull_request.additions}}",
                "deletions": "{{trigger.pull_request.deletions}}"},
       phrases=(r"\bpull requests?\b", r"\bPRs?\b", r"\bnew pr\b"),
       synth={"action": "opened",
              "pull_request": {"title": "Add retry with backoff to the download client",
                               "body": "Wraps the download call in an exponential-backoff retry.",
                               "html_url": "https://github.com/o/r/pull/999",
                               "user": {"login": "tester"},
                               "changed_files": 3, "additions": 84, "deletions": 12},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_issue", title="New Issue", ap_trigger="trigger_issues", hook_event="issues",
       payload={"action": "{{trigger.action}}", "title": "{{trigger.issue.title}}",
                "body": "{{trigger.issue.body}}", "url": "{{trigger.issue.html_url}}",
                "author": "{{trigger.issue.user.login}}",
                "labels": "{{trigger.issue.labels}}",
                "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bissues?\b",),
       synth={"action": "opened",
              "issue": {"title": "Login page 500s on submit",
                        "body": "Users get a 500 when submitting the login form. Blocking.",
                        "html_url": "https://github.com/o/r/issues/501",
                        "user": {"login": "tester"}, "labels": []},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_star", title="New Star", ap_trigger="trigger_star", hook_event="star",
       payload={"action": "{{trigger.action}}", "starred_at": "{{trigger.starred_at}}",
                "by": "{{trigger.sender.login}}", "repo": "{{trigger.repository.full_name}}",
                "stars": "{{trigger.repository.stargazers_count}}"},
       phrases=(r"\bstars?\b.{0,20}\brepo\b", r"\brepo\b.{0,30}\bstar", r"\bnew star\b"),
       synth={"action": "created", "starred_at": "2026-07-13T11:18:55Z",
              "sender": {"login": "stargazer"},
              "repository": {"full_name": "o/r", "stargazers_count": 42}}, **_GH),
    _t(event="new_push", title="Push", ap_trigger="trigger_push", hook_event="push",
       payload={"ref": "{{trigger.ref}}", "before": "{{trigger.before}}",
                "after": "{{trigger.after}}", "commits": "{{trigger.commits}}",
                "pusher": "{{trigger.pusher.name}}", "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bpush(ed|es)?\b.{0,20}\b(main|master|branch|repo)\b", r"\bcode is pushed\b"),
       synth={"ref": "refs/heads/main", "before": "abc123", "after": "def456",
              "commits": [{"id": "def456", "message": "tighten retry loop",
                           "author": {"name": "Tester", "email": "t@example.com"},
                           "added": [], "modified": ["client.py"], "removed": []}],
              "pusher": {"name": "tester"}, "repository": {"full_name": "o/r"},
              "sender": {"login": "tester"}}, **_GH),
    _t(event="new_discussion", title="New Discussion", ap_trigger="trigger_discussion", hook_event="discussion",
       payload={"action": "{{trigger.action}}", "title": "{{trigger.discussion.title}}",
                "body": "{{trigger.discussion.body}}", "url": "{{trigger.discussion.html_url}}",
                "author": "{{trigger.discussion.user.login}}",
                "category": "{{trigger.discussion.category.name}}",
                "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bdiscussions?\b",),
       synth={"action": "created",
              "discussion": {"title": "Should we support Python 3.13?",
                             "body": "3.13 is out — what breaks?",
                             "html_url": "https://github.com/o/r/discussions/7",
                             "user": {"login": "tester"}, "category": {"name": "Ideas"}},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_discussion_comment", title="New Comment Posted",
       ap_trigger="trigger_discussion_comment", hook_event="discussion_comment",
       payload={"action": "{{trigger.action}}", "comment": "{{trigger.comment.body}}",
                "url": "{{trigger.comment.html_url}}", "author": "{{trigger.comment.user.login}}",
                "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bcomment(s|ed)?\b.{0,30}\b(discussion|issue|posted)\b",),
       synth={"action": "created",
              "comment": {"body": "This blocks the release — the fix must land first.",
                          "html_url": "https://github.com/o/r/discussions/7#comment-1",
                          "user": {"login": "tester"}},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_branch", title="New Branch", ap_trigger="new_branch", hook_event="create",
       payload={"branch": "{{trigger.ref}}", "ref_type": "{{trigger.ref_type}}",
                "by": "{{trigger.sender.login}}", "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bbranch(es)?\b.{0,20}\b(created?|new)\b", r"\bnew branch\b"),
       synth={"ref": "feature/new-design", "ref_type": "branch", "master_branch": "main",
              "sender": {"login": "tester"}, "repository": {"full_name": "o/r"}}, **_GH),
    _t(event="new_collaborator", title="New Collaborator", ap_trigger="new_collaborator", hook_event="member",
       payload={"action": "{{trigger.action}}", "member": "{{trigger.member.login}}",
                "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bcollaborators?\b",),
       synth={"action": "added", "member": {"login": "octocat"},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_repo_label", title="New Label", ap_trigger="new_label", hook_event="label",
       payload={"action": "{{trigger.action}}", "label": "{{trigger.label.name}}",
                "color": "{{trigger.label.color}}", "repo": "{{trigger.repository.full_name}}"},
       # repo/github context REQUIRED: a bare "label … created" also matches "when a new gmail
       # label is created", which belongs to gmail/new_gmail_label.
       phrases=(r"\b(repo|repository|github)\b.{0,25}\blabel\b",
                r"\blabel\b.{0,20}\b(repo|repository|github)\b"),
       synth={"action": "created", "label": {"name": "needs-triage", "color": "f29513"},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_milestone", title="New Milestone", ap_trigger="new_milestone", hook_event="milestone",
       payload={"action": "{{trigger.action}}", "title": "{{trigger.milestone.title}}",
                "description": "{{trigger.milestone.description}}",
                "due_on": "{{trigger.milestone.due_on}}",
                "url": "{{trigger.milestone.html_url}}",
                "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bmilestones?\b",),
       synth={"action": "created",
              "milestone": {"title": "v1.0", "description": "First stable release",
                            "due_on": "2026-09-01T00:00:00Z",
                            "html_url": "https://github.com/o/r/milestones/1",
                            "creator": {"login": "tester"}, "number": 1, "state": "open"},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_release", title="New Release", ap_trigger="new_release", hook_event="release",
       payload={"action": "{{trigger.action}}", "tag": "{{trigger.release.tag_name}}",
                "name": "{{trigger.release.name}}", "notes": "{{trigger.release.body}}",
                "url": "{{trigger.release.html_url}}", "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\breleases?\b", r"\bchangelog\b"),
       # The piece's run() is: action === "created" ? [body] : []  — its sampleData says
       # "published", which the trigger itself would DISCARD. Verified in piece-github@0.8.5.
       synth={"action": "created",
              "release": {"id": 1, "tag_name": "v1.4.0", "name": "v1.4.0",
                          "body": "### Added\n- retry with backoff\n### Fixed\n- 401 refresh",
                          "url": "https://api.github.com/repos/o/r/releases/1",
                          "html_url": "https://github.com/o/r/releases/v1.4.0",
                          "target_commitish": "main", "draft": False, "prerelease": False,
                          "author": {"login": "tester"}},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_commit", title="New Commit", ap_trigger="new_commit", hook_event="push",
       payload={"ref": "{{trigger.ref}}", "commits": "{{trigger.commits}}",
                "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bcommits?\b",),
       # The piece's run() drops the event unless ref starts with refs/heads/ AND keeps only
       # commits with distinct === true — its sampleData omits `distinct`, so the sample as-shipped
       # yields ZERO items. Verified in piece-github@0.8.5.
       synth={"ref": "refs/heads/main", "deleted": False,
              "commits": [{"id": "def456", "message": "Add new feature", "distinct": True,
                           "timestamp": "2026-07-13T12:34:56Z",
                           "url": "https://github.com/o/r/commit/def456"}],
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_review_request", title="New Review Request", ap_trigger="new_review_request", hook_event="pull_request",
       payload={"action": "{{trigger.action}}", "title": "{{trigger.pull_request.title}}",
                "url": "{{trigger.pull_request.html_url}}",
                "author": "{{trigger.pull_request.user.login}}",
                "reviewer": "{{trigger.requested_reviewer.login}}",
                "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\breview(s|ed)?\b.{0,25}\b(request|assigned|PR)\b", r"\brequested to review\b"),
       synth={"action": "review_requested",
              "pull_request": {"title": "Amazing new feature",
                               "html_url": "https://github.com/o/r/pull/1347",
                               "user": {"login": "author"}},
              "requested_reviewer": {"login": "tester"},
              "repository": {"full_name": "o/r"}, "sender": {"login": "tester"}}, **_GH),
    _t(event="new_gh_mention", title="New Mention", ap_trigger="new_mention", hook_event="issue_comment",
       payload={"action": "{{trigger.action}}", "comment": "{{trigger.comment.body}}",
                "by": "{{trigger.comment.user.login}}", "repo": "{{trigger.repository.full_name}}"},
       phrases=(r"\bmention(s|ed)?\b.{0,25}\b(github|repo|me)\b",),
       synth={"action": "created",
              "comment": {"body": "@MENTION_LOGIN please review this.",
                          "user": {"login": "author"},
                          "created_at": "2026-07-13T20:09:31Z"},
              "repository": {"full_name": "o/r"}, "sender": {"login": "author"}}, **_GH),
]

# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Gmail — 4 triggers, all POLLING (AP will not run them out of band ⇒ fire is manual: a real email).
# ─────────────────────────────────────────────────────────────────────────────────────────────────
_GM = dict(app="gmail", backend="ap", piece="gmail", fire="manual")

_GMAIL = [
    _t(event="new_email", title="New Email", ap_trigger="gmail_new_email_received", default=True,
       payload={"subject": "{{trigger.message.subject}}",
                "from": "{{trigger.message.from.value[0].address}}",
                "body": "{{trigger.message.text}}"},
       # inbound-context required: a bare "email" is usually a DELIVERY clause ("…email me the
       # fit"), which must not make gmail the watched source.
       phrases=(r"\bgmail\b", r"\binbox\b", r"\b(new|an) e-?mails?\b",
                r"\be-?mail\b.{0,20}\b(arrives?|received|lands|comes)\b"), **_GM),
    _t(event="new_labeled_email", title="New Labeled Email", ap_trigger="new_labeled_email",
       slots=("label",),
       payload={"subject": "{{trigger.message.subject}}",
                "from": "{{trigger.message.from.value[0].address}}",
                "body": "{{trigger.message.text}}"},
       phrases=(r"\blabel(ed)?\b.{0,30}\be-?mail\b", r"\be-?mail\b.{0,30}\blabel\b",
                r"\bI label\b"),
       notes="the `label` prop is an AP dropdown — we pass the label as given; verify the first "
             "real fire matches the label you meant (Gmail system labels are UPPERCASE ids)", **_GM),
    _t(event="new_attachment", title="New Attachment", ap_trigger="new_attachment",
       payload={"subject": "{{trigger.message.subject}}",
                "from": "{{trigger.message.from.value[0].address}}",
                "attachment": "{{trigger.attachment.filename}}",
                "body": "{{trigger.message.text}}"},
       phrases=(r"\battach(ment|ed)s?\b",
                r"\be-?mail\b.{0,30}\b(resume|pdf|attach)",  # email-context FIRST — a bare
                # "resume … email me" is usually a Box/other watcher that DELIVERS by email
                ), **_GM),
    _t(event="new_gmail_label", title="New Label created", ap_trigger="new_label",
       payload={"label": "{{trigger.name}}", "id": "{{trigger.id}}"},
       phrases=(r"\bgmail label\b.{0,25}\bcreated\b", r"\bnew gmail label\b"), **_GM),
]

# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Box — the direct poller (EVENTS_BOX_BACKEND=direct, the default operating mode). `new_file` is the
# proven resume path; folder/comment are handled by the same poller. AP-piece rows exist for the
# non-direct mode and reuse the same events.
# ─────────────────────────────────────────────────────────────────────────────────────────────────
_BOX = [
    _t(app="box", event="new_file", title="New File", backend="ap", piece="box",
       ap_trigger="new_file", default=True, fire="real", slots=("folder",),
       payload={"name": "{{trigger.name}}", "id": "{{trigger.id}}"},
       phrases=(r"\bbox\b",),
       notes="direct mode arms a schedule→/api/events/box/poll watcher instead of an AP box trigger"),
    _t(app="box", event="new_folder", title="New Folder", backend="direct",
       direct_kind="new_folder", fire="real", slots=("folder",),
       payload={"name": None, "id": None, "created_at": None},
       # folder-CREATION verbs only: "a resume lands in my Box folder" is a new_FILE utterance
       # that merely mentions a folder — it must not route here.
       phrases=(r"\b(new|a) folder\b.{0,25}\b(appears?|created|added)\b",
                r"\bfolder is (created|added)\b")),
    _t(app="box", event="new_box_comment", title="New Comment", backend="direct",
       direct_kind="new_comment", fire="real", slots=("folder",),
       payload={"message": None, "file": None, "by": None, "created_at": None},
       phrases=(r"\bcomments?\b.{0,25}\bbox\b", r"\bbox\b.{0,25}\bcomments?\b")),
]

# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Slack — DIRECT backend. CUGA already receives the Events API; these rows are matched by the
# direct-event dispatcher (direct_events.py) against the event `type` Slack posts. Activation
# needs the Slack app subscribed to each event (documented in setup/SLACK.md).
# ─────────────────────────────────────────────────────────────────────────────────────────────────
_SL = dict(app="slack", backend="direct", fire="real")

_SLACK = [
    _t(event="new_channel_message", title="New Message Posted to Channel", direct_kind="message",
       slots=("channel", "pattern"),
       payload={"text": None, "channel": None, "user": None, "ts": None},
       # an explicit #channel is REQUIRED. A looser "message … in <word>" also matched "when a
       # message gets a :bug: reaction in slack", stealing the reaction trigger.
       phrases=(r"\b(message|posts?)\b.{0,30}\bin #\w+", r"\bposts? in #\w+",
                r"\bposted (to|in) (the )?#\w+"),
       notes="a channel-scoped watcher; converse (the chat bot) still answers unmatched messages",
       **_SL),
    _t(event="new_reaction", title="New Reaction", direct_kind="reaction_added",
       slots=("emoji", "channel"),
       payload={"reaction": None, "user": None, "item": None},
       phrases=(r"\bmessage\b.{0,30}\breaction\b", r"\breaction\b.{0,20}\b(is )?added\b",
                r":\w+:\s*reaction", r"\breacts?\b.{0,20}:\w+:", r"\breactions?\b"), **_SL),
    _t(event="reaction_removed", title="Reaction Removed", direct_kind="reaction_removed",
       slots=("emoji", "channel"),
       payload={"reaction": None, "user": None, "item": None},
       phrases=(r"\breaction\b.{0,20}\bremoved\b",), **_SL),
    _t(event="new_slack_mention", title="New Mention in Channel", direct_kind="app_mention",
       payload={"text": None, "channel": None, "user": None, "ts": None},
       phrases=(r"@?mention(s|ed)?\b.{0,25}\b(slack|channel|team)\b",
                r"\b(slack|team)\b.{0,25}@?mention"), **_SL),
    _t(event="channel_created", title="Channel created", direct_kind="channel_created",
       payload={"channel": None},
       phrases=(r"\bchannel\b.{0,20}\b(created|new)\b", r"\bnew channel\b"), **_SL),
    _t(event="new_slack_user", title="New User", direct_kind="team_join",
       payload={"user": None},
       phrases=(r"\b(user|teammate|member)\b.{0,25}\bjoins?\b.{0,25}\b(workspace|slack|team)\b",
                r"\bnew (user|teammate)\b"), **_SL),
    _t(event="new_emoji", title="New Team Custom Emoji", direct_kind="emoji_changed",
       payload={"name": None, "value": None, "subtype": None},
       phrases=(r"\bcustom emoji\b", r"\bemoji\b.{0,20}\badded\b"), **_SL),
    _t(event="saved_message", title="New Saved Message", direct_kind="star_added",
       payload={"item": None, "user": None},
       phrases=(r"\bsaved? (a )?message\b", r"\bsave\b.{0,15}\bfor later\b"), **_SL),
]

# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Discord — DIRECT (Gateway). new_member needs the privileged GUILD_MEMBERS intent, which is
# OPT-IN via EVENTS_DISCORD_MEMBERS_INTENT=1 (an unapproved privileged intent closes the whole
# gateway with 4014 — so it must never be default-on).
# ─────────────────────────────────────────────────────────────────────────────────────────────────
_DISCORD = [
    _t(app="discord", event="new_member", title="New Member", backend="direct",
       direct_kind="GUILD_MEMBER_ADD", fire="real",
       payload={"user": None, "joined_at": None, "guild_id": None},
       phrases=(r"\bmember\b.{0,20}\bjoins?\b.{0,25}\b(server|discord)\b",
                r"\b(server|discord)\b.{0,30}\bnew member\b"),
       notes="requires EVENTS_DISCORD_MEMBERS_INTENT=1 AND the Server Members intent enabled in "
             "the Discord dev portal — otherwise the gateway rejects the connection (4014)"),
    _t(app="discord", event="new_channel_message", title="New message (channel-scoped)",
       backend="direct", direct_kind="MESSAGE_CREATE", fire="real", slots=("channel", "pattern"),
       payload={"content": None, "channel_id": None, "author": None},
       phrases=(r"\bposts? in #\w+.{0,30}\bdiscord\b", r"\bdiscord\b.{0,30}\bposts? in #\w+",
                r"\bsomeone posts in #\w+")),
]

# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Telegram — the bot's message stream (already flowing through AP → /invoke). A content-filter
# watcher matches messages (e.g. carrying a URL) before the concierge converse path.
# ─────────────────────────────────────────────────────────────────────────────────────────────────
_TELEGRAM = [
    _t(app="telegram", event="new_channel_message", title="New Update (content-filtered)",
       backend="direct", direct_kind="message", fire="real", slots=("pattern",),
       payload={"text": None, "chat_id": None, "user": None},
       phrases=(r"\b(send|forward)\b.{0,20}\b(the bot|telegram)\b.{0,20}\b(link|url)\b",
                r"\btelegram\b.{0,40}\b(link|url)\b")),
]

# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Webhook — the generic inbound endpoint (no AP, no piece). Kept in the registry so docs/tests
# enumerate it with everything else; arming is not needed (the endpoint is always live).
# ─────────────────────────────────────────────────────────────────────────────────────────────────
_WEBHOOK = [
    _t(app="webhook", event="inbound", title="Generic inbound webhook (pinned or routed)",
       backend="direct", direct_kind="http_post", fire="synth", default=True,
       payload=None, phrases=(r"\bwebhook\b",),
       notes="POST /api/events/hook/<name>; ?agent= pins, ?route=1 routes via the concierge"),
]


TRIGGERS: dict[tuple[str, str], Trigger] = {
    t.key: t for t in (_GITHUB + _GMAIL + _BOX + _SLACK + _DISCORD + _TELEGRAM + _WEBHOOK)
}

# legacy source aliases (the classifier/LLM historically said github_pr / github_issue)
SOURCE_ALIASES = {
    "github_pr": ("github", "new_pr"),
    "github_issue": ("github", "new_issue"),
    "github": ("github", ""),          # bare app → its default trigger
}

# legacy event-kind aliases (older flows/LLM output used these; keep them resolving)
EVENT_ALIASES = {
    ("github", "new_pull_request"): "new_pr",
    ("github", "pull_request"): "new_pr",
    ("gmail", "new_email_received"): "new_email",
    ("box", "new_comment"): "new_box_comment",
    ("github", "new_label"): "new_repo_label",
    ("gmail", "new_label"): "new_gmail_label",
    ("slack", "new_reaction_added"): "new_reaction",
    ("slack", "new_mention"): "new_slack_mention",
}


def rows() -> list[Trigger]:
    return list(TRIGGERS.values())


def get(app: str, event: str = "") -> Trigger | None:
    """Look up a trigger; resolves source/event aliases and the app default. None if unknown.

    A source ALIAS (``github_pr``/``github_issue``) keeps its baked-in event even when the caller
    passes a non-matching one — legacy callers pass ``source="github_pr", event="new_file"`` (the
    old signature's default) and historically the event was ignored entirely for known sources."""
    app = (app or "").lower().strip()
    event = (event or "").lower().strip().replace("-", "_")
    alias_event = ""
    if app in SOURCE_ALIASES:
        app, alias_event = SOURCE_ALIASES[app]
    event = EVENT_ALIASES.get((app, event), event)
    if event and (app, event) in TRIGGERS:
        return TRIGGERS[(app, event)]
    if alias_event:                       # the alias's own event wins over an unresolvable one
        return TRIGGERS.get((app, alias_event))
    if not event:
        return next((t for t in TRIGGERS.values() if t.app == app and t.default), None)
    return None


def apps() -> list[str]:
    return sorted({t.app for t in TRIGGERS.values()})


def events_for(app: str) -> list[Trigger]:
    return [t for t in TRIGGERS.values() if t.app == app]


def event_kinds() -> tuple[str, ...]:
    """All canonical event kinds — feeds envelope.EVENT_KINDS (plus its legacy/base kinds)."""
    return tuple(sorted({t.event for t in TRIGGERS.values()}))


def validate(app: str, event: str, config: dict | None = None) -> tuple[Trigger | None, str]:
    """The arm-time validation gate: (trigger, "") when armable, (None|trigger, problem) otherwise.

    The concierge calls this BEFORE building anything — an unknown trigger or a missing required
    slot comes back as a question/error instead of a broken AP flow."""
    t = get(app, event)
    if t is None:
        known = ", ".join(x.event for x in events_for((app or "").lower())) or "none"
        return None, (f"unknown trigger '{app}/{event}' — known events for {app}: {known}")
    cfg = config or {}
    for slot_name in t.slots:
        slot = SLOTS[slot_name]
        if slot.required and not cfg.get(slot_name):
            return t, slot.question
    return t, ""


def prompt_vocabulary() -> str:
    """A compact app→events vocabulary for the concierge system prompt (generated, never drifts)."""
    parts = []
    for app in apps():
        evs = []
        for t in sorted(events_for(app), key=lambda x: (not x.default, x.event)):
            mark = "*" if t.default else ""
            # ONLY required slots are advertised. Rendering optional ones as "(needs folder)" made
            # the LLM stop and ask for a Box folder id instead of arming the watcher.
            req = [n for n in t.slots if SLOTS[n].required]
            slot = f"(needs {'/'.join(req)})" if req else ""
            evs.append(f"{t.event}{mark}{slot}")
        parts.append(f"{app}: {', '.join(evs)}")
    return " · ".join(parts)


def classifier_sources() -> list[tuple[str, tuple[str, str]]]:
    """(regex, (app, event)) pairs for the deterministic classifier, most-specific first.

    Ordering matters: 'when a PR gets a review request' must hit new_review_request before the
    generic new_pr phrase. We sort by phrase length (longer = more specific) so narrower patterns
    win, and keep app defaults last within an app."""
    out: list[tuple[str, tuple[str, str], bool]] = []
    for t in TRIGGERS.values():
        for ph in t.phrases:
            out.append((ph, (t.app, t.event), t.default))
    # NON-default (more specific) triggers first — "an email with an attachment" must hit
    # new_attachment before the default new_email phrase gets a look; length desc within a group.
    out.sort(key=lambda p: (p[2], -len(p[0])))
    return [(ph, se) for ph, se, _ in out]
