"""NL→Flow BENCHMARK — a labeled scorecard for "utterance → the right armed flow".

The legit benchmark behind the design's "never silently the wrong flow" claim. Reads the labeled
dataset (nl_to_flow_bench.jsonl) and scores, offline + deterministic (no LLM, no live AP):

  * router_mode_acc   — classify picks the right mode (now/cron/poll/push)
  * push_trigger_acc  — flowspec resolves the right (source, event) for push cases
  * gate_outcome_acc  — the action gate's outcome matches the label (arm / ask / decline / answer)
  * action_acc        — for ARM cases, the armed action(s) match the label
  * CORRECT_AT_ARM    — of everything we ARMED, how many were RIGHT (the anti-silent-failure metric;
                        a wrong action that arms is the ugly case — this must be 100%)

Run:  uv run python -m pytest tests/events/test_nl_to_flow_bench.py -q -s   (‑s prints the scorecard)

Complements test_flowspec_bench.py (which scores the trigger half). The LIVE arm-validity dimension
(does the armed flow actually fire?) is covered by the arm-time validity gate + gen_actions --check.
"""
import asyncio
import os as _os
_os.environ["EVENTS_VERIFY_ACTIONS"] = "0"  # deterministic tests: LLM verifier off
import json
import os
import sys

_EVENTS = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "src", "cuga", "backend", "events"))
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import classify                                          # noqa: E402
import flowspec                                          # noqa: E402
from agent_store import AgentStore                       # noqa: E402
from runtime import CugaRuntime, AgentSpec               # noqa: E402
from subscriptions import SubscriptionStore             # noqa: E402
import concierge                                          # noqa: E402
import principal as _principal_mod                        # noqa: E402
import actions as _actions                                # noqa: E402

CASES = [json.loads(_ln) for _ln in open(os.path.join(os.path.dirname(__file__), "nl_to_flow_bench.jsonl"))
         if _ln.strip()]


def _is_action_case(cx: dict) -> bool:
    """A case whose EXPECTED behavior depends on the ACTION half (arms an action, or asks/declines
    BECAUSE of one). With EVENTS_ACTIONS off these correctly degrade to a plain watcher, so their
    action-era labels no longer apply — filter them out and keep the action-independent coverage
    (router mode, push-trigger resolution, plain-watcher arms)."""
    return bool(cx.get("actions")) or cx.get("outcome") in ("ask", "decline")


if not _actions.enabled():
    CASES = [c for c in CASES if not _is_action_case(c)]


class FakeEngine:
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
        return f"exec-{len(self.calls)}"

    async def trigger_flow(self, flow_id, payload=None, **kw):
        return {"ok": True}

    async def delete_flow(self, fid):
        return True


def _gate(utt, source, event, slots):
    """Drive the concierge action gate deterministically; return (outcome, armed_actions)."""
    rt = CugaRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="cuga", prompt="x", integrations=[{"app": source, "ownership": "per-user"},
                                                                     {"app": "gmail", "ownership": "per-user"}]),
                    scope="default")
    engine = FakeEngine()
    tools = concierge.make_concierge_tools(rt, store=SubscriptionStore(":memory:"), engine=engine, users=None)
    focf = next(t for t in tools if t.name == "find_or_create_flow")
    args = {"agent": "cuga", "kind": "push", "prompt": utt, "source": source, "event": event}
    args.update(slots or {})
    concierge._principal.set(_principal_mod.DEFAULT)
    concierge._origin.set("web:local")
    reply = asyncio.run(focf.ainvoke(args))
    low = reply.lower()
    if reply.startswith("ARMED") or reply.startswith("REUSING"):
        if "branched" in reply:
            return "arm", ["branched"]
        # DIRECT trigger → action (Option A): actions arm as executor flows, not a push-flow step.
        execs = [f"{c['executor']['app']}/{c['executor']['ap_action']}"
                 for c in engine.calls if isinstance(c, dict) and "executor" in c]
        if execs:
            return "arm", execs
        armed = [f"{a['app']}/{a['ap_action']}" for a in (engine.calls[0]["actions"] or [])] if engine.calls else []
        # map ap_action back to canonical name for comparison
        armed = [x.replace("/reply_to_email", "/reply_to_email") for x in armed]
        return "arm", armed
    if "who should i send" in low or reply.rstrip().endswith("?"):
        return "ask", []
    return "decline", []


def test_nl_to_flow_benchmark():
    rows = []
    for cx in CASES:
        utt = cx["utterance"]
        # 1) router mode
        mode = classify.decision(utt)["mode"].lower()
        mode_ok = mode == cx["kind"]
        # 2) push trigger resolution (flowspec)
        trig_ok = None
        if cx["kind"] == "push":
            spec = flowspec.resolve(utt)
            trig_ok = (spec.source == cx["source"]) if spec.confidence == "high" else None
        # 3) gate outcome + actions (push only; now/cron/poll are router-only here)
        out_ok, act_ok = None, None
        armed = []
        if cx["kind"] == "push":
            outcome, armed = _gate(utt, cx["source"], cx.get("event", ""), cx.get("slots"))
            out_ok = outcome == cx["outcome"]
            if cx["outcome"] == "arm":
                want = [a.replace("gmail/reply_to_email", "gmail/reply_to_email") for a in cx.get("actions", [])]
                # compare canonical app/name (ap_action send_email==name; reply/draft names differ)
                got = [a.replace("/reply_to_email", "/reply_to_email") for a in armed]
                act_ok = sorted(_canon(got)) == sorted(_canon(want))
        rows.append((cx, mode_ok, trig_ok, out_ok, act_ok, armed))

    # ── scorecard ──
    def rate(vals):
        v = [x for x in vals if x is not None]
        return (sum(1 for x in v if x) / len(v)) if v else 1.0
    mode_acc = rate([r[1] for r in rows])
    trig_acc = rate([r[2] for r in rows])
    out_acc = rate([r[3] for r in rows])
    act_acc = rate([r[4] for r in rows])
    armed_rows = [r for r in rows if r[3] is not None and r[0].get("outcome") == "arm"]
    correct_at_arm = rate([r[4] for r in armed_rows if r[4] is not None])

    print("\n── NL→Flow benchmark ─────────────────────────────")
    print(f"  cases:            {len(CASES)}")
    print(f"  router_mode_acc:  {mode_acc:.0%}")
    print(f"  push_trigger_acc: {trig_acc:.0%}")
    print(f"  gate_outcome_acc: {out_acc:.0%}")
    print(f"  action_acc:       {act_acc:.0%}")
    print(f"  CORRECT_AT_ARM:   {correct_at_arm:.0%}  (armed flows that were right — no silent-wrong)")
    for cx, m, t, o, a, armed in rows:
        bad = [n for n, v in (("mode", m), ("trig", t), ("outcome", o), ("action", a)) if v is False]
        if bad:
            print(f"    ✗ {cx['utterance'][:60]!r} → {bad} (armed={armed})")
    print("──────────────────────────────────────────────────")

    # gates: the anti-silent-failure invariants
    assert correct_at_arm == 1.0, "a flow armed with the WRONG action — silent failure"
    assert out_acc >= 0.9, f"gate outcome accuracy too low: {out_acc:.0%}"
    assert mode_acc >= 0.9, f"router mode accuracy too low: {mode_acc:.0%}"


def _canon(lst):
    # normalize ap_action → canonical app/name for comparison (send_email/reply_to_email/create_draft_reply)
    m = {"gmail/send_email": "gmail/send_email", "gmail/reply_to_email": "gmail/reply_to_email",
         "gmail/create_draft_reply": "gmail/create_draft_reply",
         "github/github_create_issue": "github/create_issue",
         "github/createCommentOnAIssue": "github/create_comment"}
    return [m.get(x, x) for x in lst]


if __name__ == "__main__":
    test_nl_to_flow_benchmark()
