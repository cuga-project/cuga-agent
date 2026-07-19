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

    async def delete_flow(self, fid):
        return True


def _mk():
    rt = CugaRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="mailbot", prompt="triage gmail",
                              mcp_servers=["cuga-text"],
                              integrations=[{"app": "gmail", "ownership": "per-user"}],
                              channels=["telegram"]),
                    scope="default")
    store = SubscriptionStore(":memory:")
    engine = FakeEngine()
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


def test_multi_action_arms_two_native_steps():
    focf, store, engine = _mk()
    msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push",
                             "prompt": "when an email arrives, email me a summary at me@x.com and reply to the sender",
                             "source": "gmail", "event": "new_email"}))
    assert "ARMED" in msg
    acts = engine.calls[0]["actions"]
    assert [a["ap_action"] for a in acts] == ["send_email", "reply_to_email"]


def test_custom_api_call_actions_are_gated():
    # archive/mark_read/delete are custom_api_call-backed → deferred (not armed), honest message
    for utt in ("when I get an email, archive it",
                "when I get an email, mark it as read",
                "when I get an email, delete it"):
        focf, store, engine = _mk()
        msg = _run(focf.ainvoke({"agent": "cuga", "kind": "push", "prompt": utt,
                                 "source": "gmail", "event": "new_email"}))
        assert "raw Gmail API call" in msg and "reply" in msg
        assert not engine.calls                          # nothing armed


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
