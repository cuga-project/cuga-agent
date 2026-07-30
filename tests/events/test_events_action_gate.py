"""Integration test for the concierge ACTION GATE (design §3.5) — the NL→Flow action half.

Drives ``find_or_create_flow`` with a FAKE Activepieces engine that records what would be armed, so
we assert the action is validated, resolved, wired with a connection, folded into the dedup key, and
surfaced in the confirmation — without a live AP or LLM.
"""

import asyncio
import os as _os
_os.environ["EVENTS_VERIFY_ACTIONS"] = "0"  # deterministic tests: LLM verifier off
import os
import sys

_EVENTS = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "src", "cuga", "backend", "events"))
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

from agent_store import AgentStore                    # noqa: E402
from runtime import CugaRuntime, AgentSpec            # noqa: E402
from subscriptions import SubscriptionStore           # noqa: E402
import concierge                                       # noqa: E402
import principal as _principal_mod                     # noqa: E402
import actions as _actions                              # noqa: E402
import pytest                                           # noqa: E402

# ACTION half gated off by default (EVENTS_ACTIONS=0) — the gate only exists when actions are on.
pytestmark = pytest.mark.skipif(
    not _actions.enabled(), reason="ACTION half gated off (set EVENTS_ACTIONS=1 to run)")


class FakeEngine:
    """Records create_push_flow calls; pretends every connection exists."""
    kind = "activepieces"
    project_grain = "tenant"

    def __init__(self):
        self.calls = []

    async def connection_exists(self, ext, project_name=None):
        return True

    async def create_push_flow(self, **kw):
        self.calls.append(kw)
        return f"flow-{len(self.calls)}"

    async def create_branched_push_flow(self, **kw):
        self.calls.append(kw)
        return "bflow"

    async def ensure_action_executor(self, **kw):
        self.calls.append({"executor": kw})
        return f"exec-{len([c for c in self.calls if 'executor' in c])}"

    async def trigger_flow(self, flow_id, payload=None, **kw):
        self.calls.append({"fire": flow_id, "body": payload})
        return {"ok": True}

    async def delete_flow(self, fid):
        return True


class NoExecutorEngine(FakeEngine):
    """An engine that can't build an executor (e.g. the action app isn't connected)."""
    async def ensure_action_executor(self, **kw):
        raise RuntimeError("no connection")


def _mk(engine=None):
    rt = CugaRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="mailbot", prompt="triage gmail",
                              mcp_servers=["cuga-text"],
                              integrations=[{"app": "gmail", "ownership": "per-user"}],
                              channels=["telegram"]),
                    scope="default")
    store = SubscriptionStore(":memory:")
    engine = engine or FakeEngine()
    tools = concierge.make_concierge_tools(rt, store=store, engine=engine, users=None)
    focf = next(t for t in tools if t.name == "find_or_create_flow")
    return focf, store, engine


def _run(coro):
    # set the principal/origin the tool reads from contextvars
    concierge._principal.set(_principal_mod.DEFAULT)
    concierge._origin.set("web:local")
    return asyncio.run(coro)


def test_action_gate_arms_reply_action():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push", "prompt": "reply to it",
                             "source": "gmail", "event": "new_email",
                             "action": "gmail/reply_to_email"}))
    assert "ARMED" in msg and "gmail/reply_to_email" in msg
    assert engine.calls, "engine.create_push_flow was not called"
    acts = engine.calls[0]["actions"]
    assert acts and acts[0]["ap_action"] == "reply_to_email"
    assert acts[0]["params"]["message_id"] == "{{trigger.message.id}}"
    assert acts[0]["params"]["body"] == "{{step_1.body.answer}}"
    assert acts[0]["connection"]                      # gmail connection wired


def test_action_gate_send_email_to_sender():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when an email arrives, email the sender back a summary",
                             "source": "gmail", "event": "new_email",
                             "action": "gmail/send_email", "action_to": "sender"}))
    assert "ARMED" in msg
    params = engine.calls[0]["actions"][0]["params"]
    # receiver resolved to the trigger sender template (generic, from the trigger payload registry)
    assert params["receiver"] == ["{{trigger.message.from.value[0].address}}"]
    assert params["body"] == "{{step_1.body.answer}}"


def test_action_gate_unknown_action_is_rejected():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push", "prompt": "nuke it",
                             "source": "gmail", "event": "new_email",
                             "action": "gmail/delete_everything"}))
    assert "error" in msg.lower() and "unknown action" in msg
    assert not engine.calls                            # nothing armed


def test_action_is_part_of_dedup_identity():
    focf, store, engine = _mk()
    a = {"agent": "cuga", "kind": "push", "prompt": "reply to it", "source": "gmail",
         "event": "new_email", "action": "gmail/reply_to_email"}
    m1 = _run(focf.ainvoke(dict(a)))
    m2 = _run(focf.ainvoke(dict(a)))                   # same identity → reuse
    assert "ARMED" in m1 and "REUSING" in m2
    # a DIFFERENT action on the same trigger is a NEW flow, not a dedup collision
    b = dict(a)
    b["action"] = "gmail/create_draft_reply"
    b["prompt"] = "draft a reply"
    m3 = _run(focf.ainvoke(b))
    assert "ARMED" in m3
    assert len([c for c in engine.calls]) == 2         # two real arms (reply, draft); m2 reused


def test_onramp_extracts_action_when_none_passed():
    # THE WIRE FIX: the deterministic paths call find_or_create_flow WITHOUT `action`; the gate must
    # extract it from the utterance so a chat "reply to the sender" actually arms an action flow.
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when I get an email, reply to the sender acknowledging it",
                             "source": "gmail", "event": "new_email"}))   # NO action= passed
    assert "ARMED" in msg and "gmail/reply_to_email" in msg
    assert engine.calls[0]["actions"][0]["ap_action"] == "reply_to_email"


def test_onramp_plain_watcher_when_no_action_verb():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when I get an email, summarize it and message me",
                             "source": "gmail", "event": "new_email"}))
    assert "ARMED" in msg and "reply_to_email" not in msg and "send_email" not in msg
    assert not engine.calls[0].get("actions")          # plain watcher, no action step


def test_cross_piece_box_trigger_gmail_send_action():
    # generic, not overfit to gmail: a BOX trigger driving a GMAIL send action
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when a file lands in Box, email me its name at me@x.com",
                             "source": "box", "event": "new_file", "folder": "0"}))
    assert "ARMED" in msg and "gmail/send_email" in msg
    act = engine.calls[0]["actions"][0]
    assert act["app"] == "gmail" and act["ap_action"] == "send_email"


def test_cross_piece_reply_rejected_needs_same_app():
    # reply/draft key off the firing message → a Box trigger can't drive gmail/reply_to_email
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when a file lands in Box, reply to the sender",
                             "source": "box", "event": "new_file", "folder": "0",
                             "action": "gmail/reply_to_email"}))
    assert "needs a gmail trigger" in msg and "send_email" in msg
    assert not engine.calls


def test_direct_trigger_gmail_action_arms_via_executor():
    # slack is a DIRECT trigger (no AP flow). Option A: the gmail action runs via a reusable EXECUTOR
    # flow CUGA fires after the agent answers. The watcher arms WITH an action_plan stashed on config.
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when a message is posted in #alerts, email me a summary at me@x.com",
                             "source": "slack", "event": "new_channel_message",
                             "channel": "#alerts", "action": "gmail/send_email",
                             "action_to": "me@x.com"}))
    assert "ARMED direct watcher" in msg and "executor" in msg
    # an executor flow was ensured for the gmail action
    assert any("executor" in c and c["executor"]["ap_action"] == "send_email" for c in engine.calls)
    # the plan is stashed on the subscription for the dispatcher to run
    sub = store.find_by_dedup_key(next(iter([s.dedup_key for s in store.list()])))
    plan = (sub.config or {}).get("action_plan")
    assert plan and plan["steps"][0]["kind"] == "executor"
    assert plan["steps"][0]["body"]["receiver"] == ["me@x.com"]
    assert plan["steps"][0]["body"]["body"] == "{{answer}}"      # answer sentinel for the dispatcher


def test_direct_trigger_gates_on_action_app_not_source():
    # A DIRECT trigger (slack) has NO AP connection — the connect-gate must check the ACTION app
    # (gmail), not the connectionless source. Regression for the live 2026-07-20 false-negative where
    # slack→gmail wrongly demanded "connect slack" even with Gmail connected.
    rt = CugaRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="cuga", prompt="x",
                              integrations=[{"app": "slack", "ownership": "per-user"},
                                            {"app": "gmail", "ownership": "per-user"}]),
                    scope="default")

    class SlackUnconnected(FakeEngine):
        async def connection_exists(self, ext, project_name=None):
            return "slack" not in ext            # slack NOT connected; gmail IS

    store = SubscriptionStore(":memory:")
    tools = concierge.make_concierge_tools(rt, store=store, engine=SlackUnconnected(), users=None)
    focf = next(t for t in tools if t.name == "find_or_create_flow")
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when a message is posted in #alerts, email me a summary at me@x.com",
                             "source": "slack", "event": "new_channel_message", "channel": "#alerts",
                             "action": "gmail/send_email", "action_to": "me@x.com"}))
    # gated on gmail (connected) → arms; must NOT demand connecting slack
    assert "ARMED direct watcher" in msg and "executor" in msg, msg
    assert "CONNECT NEEDED" not in msg


def test_direct_trigger_action_declines_when_executor_unbuildable():
    # if the executor can't be built (action app not connected), DON'T arm a watcher that silently
    # drops the action — decline loudly.
    focf, store, engine = _mk(engine=NoExecutorEngine())
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when a message is posted in #alerts, email me a summary at me@x.com",
                             "source": "slack", "event": "new_channel_message",
                             "channel": "#alerts", "action": "gmail/send_email",
                             "action_to": "me@x.com"}))
    assert "couldn't set up the executor" in msg
    assert not store.list()                            # nothing armed


def test_github_create_issue_cross_piece():
    # cross-app: a GMAIL trigger files a GITHUB issue (create_issue is not same-app).
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when an email arrives, file a github issue summarizing it",
                             "source": "gmail", "event": "new_email",
                             "action": "github/create_issue", "repo": "o/r"}))
    assert "ARMED" in msg and "github/create_issue" in msg
    act = engine.calls[0]["actions"][0]
    assert act["ap_action"] == "github_create_issue"
    assert act["params"]["repository"] == "o/r"
    assert act["params"]["description"] == "{{step_1.body.answer}}"     # agent answer → issue body


def test_github_create_comment_same_app():
    # same-app: a GITHUB PR trigger comments on THAT pr — issue_number from the firing event.
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when a PR opens, review it and comment on the PR",
                             "source": "github", "event": "new_pr",
                             "action": "github/create_comment", "repo": "o/r"}))
    assert "ARMED" in msg
    act = engine.calls[0]["actions"][0]
    assert act["ap_action"] == "createCommentOnAIssue"
    assert act["params"]["issue_number"] == "{{trigger.pull_request.number}}"
    assert act["params"]["comment"] == "{{step_1.body.answer}}"
    assert act["params"]["repository"] == "o/r"


def test_github_comment_rejected_on_non_github_trigger():
    # create_comment is same-app — a gmail trigger can't drive it (no PR/issue to comment on).
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when an email arrives, comment on the issue",
                             "source": "gmail", "event": "new_email",
                             "action": "github/create_comment", "repo": "o/r"}))
    assert "needs a github trigger" in msg
    assert not engine.calls


def test_github_create_issue_asks_for_repo():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when an email arrives, file a github issue",
                             "source": "gmail", "event": "new_email", "action": "github/create_issue"}))
    assert "which repository" in msg.lower()
    assert not engine.calls


def test_multi_action_arms_two_native_steps():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when an email arrives, email me a summary at me@x.com and reply to the sender",
                             "source": "gmail", "event": "new_email"}))
    assert "ARMED" in msg
    acts = engine.calls[0]["actions"]
    assert [a["ap_action"] for a in acts] == ["send_email", "reply_to_email"]


def test_branched_flow_arms():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when an email arrives, if it mentions urgent reply to the sender, otherwise draft a reply",
                             "source": "gmail", "event": "new_email"}))
    assert "ARMED" in msg and "branched" in msg
    branches = engine.calls[0]["branches"]               # create_branched_push_flow was called
    assert branches[0]["actions"][0]["ap_action"] == "reply_to_email"
    assert branches[1]["actions"][0]["ap_action"] == "create_draft_reply"
    assert branches[1]["when"] is None                   # fallback
    # the condition points at the EMAIL body, not the agent answer
    assert "trigger.message" in str(branches[0]["when"]["field"])




def test_subject_and_cc_from_nl():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when a PR opens, email me at me@x.com with subject 'PR alert' cc boss@x.com",
                             "source": "github", "event": "new_pr", "repo": "o/r"}))
    assert "ARMED" in msg
    params = engine.calls[0]["actions"][0]["params"]
    assert params["subject"] == "PR alert" and params["cc"] == ["boss@x.com"]


def test_send_email_without_recipient_asks():
    focf, store, engine = _mk()
    # github source (no sender field the same way) + send_email + no action_to → must ask
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when a PR opens email me", "source": "github",
                             "event": "new_pr", "action": "gmail/send_email",
                             "action_to": "me", "repo": "psf/requests"}))
    assert "address" in msg.lower() or "who should i" in msg.lower()
    assert not engine.calls
    # recipient ask-till-legit: the original utterance is PARKED for this thread so the next
    # message (an address) completes it (completion itself runs in Concierge.run, tested live)
    key = _principal_mod.DEFAULT.thread("web:local")
    assert concierge._pending_recipient.get(key) == "when a PR opens email me"
