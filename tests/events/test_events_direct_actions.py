"""Direct-trigger → action (Option A): the executor render helpers + the dispatch-time runner.

Covers the two halves that let a DIRECT trigger (slack/discord/telegram) drive an AP-only action:
  * actions.executor_input / executor_body — build the reusable executor flow + its per-fire body
  * direct_events.run_action_plan          — pick the branch, substitute the answer, fire the executor
No live AP: a fake engine records trigger_flow calls.
"""

import asyncio
import os
import sys
import types

_EVENTS = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "src", "cuga", "backend", "events"))
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import actions as A                        # noqa: E402
import direct_events as D                  # noqa: E402


# ── executor_input: flow-side templates ─────────────────────────────────────────────────────────
def test_executor_input_bakes_dropdowns_templates_the_rest():
    send = A.get("gmail", "send_email")
    inp = A.executor_input(send)
    # dropdown/checkbox baked as literals (AP validates these at build time)
    assert inp["body_type"] == "plain_text"
    assert inp["draft"] is False
    # everything else reads from the webhook body
    assert inp["receiver"] == "{{trigger.body.receiver}}"
    assert inp["subject"] == "{{trigger.body.subject}}"
    assert inp["body"] == "{{trigger.body.body}}"


# ── executor_body: per-fire runtime values ──────────────────────────────────────────────────────
def test_executor_body_answer_sentinel_and_supplied():
    send = A.get("gmail", "send_email")
    body = A.executor_body(send, {"receiver": ["me@x.com"], "subject": "Hi"})
    assert body["body"] == "{{answer}}"          # answer-source → sentinel the dispatcher fills
    assert body["receiver"] == ["me@x.com"]      # supplied literal
    assert body["subject"] == "Hi"
    assert "body_type" not in body and "draft" not in body   # baked params never posted


def test_executor_body_array_wrapping_and_defaults():
    send = A.get("gmail", "send_email")
    body = A.executor_body(send, {"receiver": "solo@x.com"})   # scalar → wrapped
    assert body["receiver"] == ["solo@x.com"]
    assert body["subject"] == "Update"                          # static default when unset


# ── the dispatch-time runner ────────────────────────────────────────────────────────────────────
class RecEngine:
    def __init__(self):
        self.fired = []

    async def trigger_flow(self, flow_id, payload=None, **kw):
        self.fired.append((flow_id, payload))
        return {"ok": True}


def _sub(plan):
    return types.SimpleNamespace(id="s1", config={"action_plan": plan}, thread_id="gw:slack:C1",
                                 deliver_to=["slack"])


def _run(coro):
    return asyncio.run(coro)


def test_run_linear_executor_substitutes_answer():
    eng = RecEngine()
    plan = {"steps": [{"kind": "executor", "app": "gmail", "ap_action": "send_email",
                       "flow_id": "exec-1", "body": {"receiver": ["me@x.com"], "body": "{{answer}}"}}]}
    n = _run(D.run_action_plan(eng, _sub(plan), answer="Here is your summary.", payload={"text": "hi"}))
    assert n == 1
    fid, sent = eng.fired[0]
    assert fid == "exec-1"
    assert sent["body"] == "Here is your summary."       # sentinel replaced
    assert sent["receiver"] == ["me@x.com"]


def test_run_branch_picks_matching_condition():
    eng = RecEngine()
    urgent = {"kind": "executor", "app": "gmail", "ap_action": "reply_to_email",
              "flow_id": "exec-urgent", "body": {"body": "{{answer}}"}}
    draft = {"kind": "executor", "app": "gmail", "ap_action": "create_draft_reply",
             "flow_id": "exec-draft", "body": {"body": "{{answer}}"}}
    plan = {"branches": [
        {"when": {"field": "answer", "op": "CONTAINS", "value": "urgent"}, "step": urgent},
        {"when": None, "step": draft}]}
    # the incoming message contains "urgent" → the urgent branch fires
    _run(D.run_action_plan(eng, _sub(plan), answer="ok", payload={"text": "This is URGENT please"}))
    assert eng.fired and eng.fired[0][0] == "exec-urgent"


def test_run_branch_falls_back_when_no_match():
    eng = RecEngine()
    a = {"kind": "executor", "app": "gmail", "ap_action": "reply_to_email", "flow_id": "exec-a",
         "body": {}}
    fb = {"kind": "executor", "app": "gmail", "ap_action": "create_draft_reply", "flow_id": "exec-fb",
          "body": {}}
    plan = {"branches": [
        {"when": {"field": "answer", "op": "CONTAINS", "value": "invoice"}, "step": a},
        {"when": None, "step": fb}]}
    _run(D.run_action_plan(eng, _sub(plan), answer="ok", payload={"text": "just a hello"}))
    assert eng.fired[0][0] == "exec-fb"


def test_no_plan_is_a_noop():
    eng = RecEngine()
    n = _run(D.run_action_plan(eng, types.SimpleNamespace(id="s", config={}), answer="x", payload={}))
    assert n == 0 and not eng.fired
