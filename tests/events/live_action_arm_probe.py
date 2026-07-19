"""LIVE arming probe — arms a REAL Activepieces flow with a Gmail ACTION step, inspects it, deletes it.

This proves the arming path (ap_engine.create_push_flow(actions=…) + _action_op + flows/actions
registry) against a LIVE Activepieces — without needing a running events server, a tunnel, or a
Gmail OAuth connection. It builds a throwaway flow, fetches its JSON, asserts the Gmail action step
is present (pieceName + actionName + the resolved {{templates}}), then DELETES the flow.

Publishing may fail if Gmail isn't connected (the trigger needs gmail auth) — that's fine and
expected; we assert on the flow STRUCTURE that AP accepted, not on publish. Non-disruptive: creates
and removes exactly one flow named ea-action-probe-*.

Run:  uv run python tests/events/live_action_arm_probe.py
Env:  AP_EMAIL / AP_PASSWORD / AP_BASE_URL from .env.
"""
from __future__ import annotations

import asyncio
import sys

import httpx
from cuga.backend.events import actions as A
from cuga.backend.events import flows as F
from cuga.backend.events.ap_engine import APEngine


async def main() -> int:
    eng = APEngine()
    ok, detail = await eng.available()
    if not ok:
        print(f"[SKIP] Activepieces not reachable: {detail}")
        return 0

    # build the resolved Gmail reply action the concierge would pass to the engine
    act = A.get("gmail", "reply_to_email")
    step = {"app": "gmail", "ap_action": act.ap_action,
            "params": A.render_params(act), "connection": "", "display": "Gmail · Reply"}
    print(f"arming a throwaway flow: gmail/new_email ▸ /invoke ▸ {act.ap_action}")

    flow_id = None
    try:
        try:
            flow_id = await eng.create_push_flow(
                source="gmail", event="new_email", agent="cuga", thread_id="probe",
                prompt="reply to it", name="ea-action-probe", actions=[step])
            print(f"  published OK — flow {flow_id}")
        except Exception as e:  # publish likely fails w/o a gmail connection — structure still built
            print(f"  publish/step raised (expected without gmail connected): {str(e)[:140]}")

        # find the flow by name and inspect its steps regardless of publish state
        async with httpx.AsyncClient(timeout=30) as c:
            hdrs = await eng._auth(c)
            pid = eng.project_id
            fid = flow_id or await eng.find_flow_by_name(c, hdrs, "ea-action-probe", pid)
            if not fid:
                print("[FAIL] no flow was created — arming did not reach AP")
                return 1
            flow_id = fid
            r = await c.get(f"{eng.base}/api/v1/flows/{fid}", headers=hdrs)
            blob = r.text
            checks = {
                "gmail piece present": "@activepieces/piece-gmail" in blob,
                "reply_to_email action step present": "reply_to_email" in blob,
                "message_id templated from trigger": "trigger.message.id" in blob,
                "body templated from agent answer": "step_1.body.answer" in blob,
            }
            ok_all = all(checks.values())
            for k, v in checks.items():
                print(f"  [{'PASS' if v else 'FAIL'}] {k}")
            print(f"\nRESULT: {'PASS — real AP flow carries the Gmail action step' if ok_all else 'FAIL'}")
            return 0 if ok_all else 1
    finally:
        if flow_id:
            try:
                await eng.delete_flow(flow_id)
                print(f"  cleaned up throwaway flow {flow_id}")
            except Exception as e:  # noqa: BLE001
                print(f"  (could not delete {flow_id}: {e} — remove ea-action-probe manually)")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
