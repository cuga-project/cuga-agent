"""LIVE nl_to_flow benchmark — the real thing, against a real Activepieces.

The offline `test_nl_to_flow_bench.py` stubs AP (FakeEngine) and only proves the concierge COMPILED
the right FlowSpec. This runner does what that can't: it drives the SAME cases through the real
concierge + real AP, so AP itself builds the flow and stamps every step `valid: true/false`. Then it
FIRES the synth-fireable ones (github) and reads the run status. It does NOT judge whether the output
is "correct" — it LOGS what happened, in a fixed pattern, so you can debug.

Per case it prints:
  ARM   : ARMED | REUSING | ERROR:<msg> | DECLINE/ASK   (what the concierge did)
  FLOW  : <flow_id> · steps: trigger(name)✓ → step(name)✓ … · VALID/INVALID   (AP's own verdict)
  FIRE  : SUCCEEDED | INTERNAL_ERROR | HTTP xxx | SKIPPED(<why>)               (a real run, if fireable)
  CLEAN : deleted <flow_id>

Run:  .venv/bin/python tests/events/live_nl_to_flow_bench.py
Prereqs: AP reachable (AP_BASE_URL in .env) + the relevant connections present (gmail/github/box).
Nothing is left behind — every flow it builds, it deletes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# .env → environ, then package imports (we need the real engine + concierge in-process)
_ROOT = Path(__file__).resolve().parents[2]
_env = _ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.split(" #", 1)[0].strip())
os.environ["EVENTS_VERIFY_ACTIONS"] = "0"  # deterministic: no LLM verifier
sys.path.insert(0, str(_ROOT / "src"))

from cuga.backend.events import concierge, triggers as _tr  # noqa: E402
from cuga.backend.events.ap_engine import APEngine  # noqa: E402
from cuga.backend.events.agent_store import AgentStore  # noqa: E402
from cuga.backend.events.runtime import AgentStoreRuntime, AgentSpec  # noqa: E402
from cuga.backend.events.subscriptions import SubscriptionStore  # noqa: E402
from cuga.backend.events.principal import Principal  # noqa: E402

DATASET = Path(__file__).parent / "nl_to_flow_bench.jsonl"
ADMIN = Principal(user_id="admin")  # matches ea::default::admin::<app>


async def _flow_steps(eng: APEngine, flow_id: str):
    """Return [(name, valid_bool)] for a flow's trigger + each action step, straight from AP."""
    import httpx

    async with httpx.AsyncClient(timeout=20) as c:
        hdrs = await eng._auth(c)
        d = (await c.get(f"{eng.base}/api/v1/flows/{flow_id}", headers=hdrs)).json()
    out, s = [], (d.get("version") or {}).get("trigger")
    while s:
        st = s.get("settings", {})
        name = st.get("actionName") or st.get("triggerName") or s.get("name")
        out.append((name, s.get("valid") is not False))
        s = s.get("nextAction")
    return out


async def _latest_run_status(eng: APEngine, flow_id: str) -> str:
    await asyncio.sleep(5)
    runs = await eng.list_runs(limit=10)
    for r in runs:
        if (r.get("flowId") or (r.get("flowVersion", {}) or {}).get("flowId")) == flow_id:
            return r.get("status") or "?"
    return "no-run-found"


def _mk_tools(engine):
    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(
        AgentSpec(
            name="cuga",
            prompt="triage",
            integrations=[{"app": a, "ownership": "per-user"} for a in ("gmail", "github", "box", "slack")],
        ),
        scope="default",
    )
    store = SubscriptionStore(":memory:")
    tools = concierge.make_concierge_tools(rt, store=store, engine=engine, users=None)
    return next(t for t in tools if t.name == "find_or_create_flow"), store


async def run():
    eng = APEngine()
    ok, why = await eng.available()
    print(f"AP: {'reachable' if ok else 'UNREACHABLE — ' + why} ({eng.base})\n")
    if not ok:
        return
    cases = [json.loads(ln) for ln in DATASET.read_text().splitlines() if ln.strip()]
    tally = {"armed": 0, "valid": 0, "fired": 0, "succeeded": 0, "declined_or_ask": 0, "error": 0}

    for cx in cases:
        if cx.get("kind") == "now":  # NOW answers, arms nothing
            continue
        focf, store = _mk_tools(eng)  # fresh store per case (no dedup carryover)
        concierge._principal.set(ADMIN)
        concierge._origin.set("web:local")
        args = {
            "agent": "cuga",
            "kind": cx["kind"],
            "prompt": cx["utterance"],
            "source": cx.get("source", ""),
            "event": cx.get("event", ""),
        }
        args.update(cx.get("slots") or {})
        if cx["kind"] in ("cron", "poll"):  # cadence isn't in the label — parse it from the text
            from cuga.backend.events import classify

            cad = classify.cadence_of(cx["utterance"])
            if cad.get("cron"):
                args["cron"] = cad["cron"]
            elif cad.get("interval_seconds"):
                args["every_minutes"] = max(1, int(cad["interval_seconds"]) // 60)
        print(f"━━ [{cx.get('utterance')[:66]}]  (expect: {cx.get('outcome')})")
        try:
            reply = str(await focf.ainvoke(args))
        except Exception as e:  # noqa: BLE001
            print(f"   ARM   : EXCEPTION {e}\n")
            tally["error"] += 1
            continue

        armed = reply.startswith("ARMED")
        if not armed:
            kind = (
                "REUSING"
                if reply.startswith("REUSING")
                else "ERROR"
                if reply.lower().startswith("error") or "wouldn't arm" in reply
                else "DECLINE/ASK"
            )
            print(f"   ARM   : {kind} — {reply[:110]}\n")
            tally["declined_or_ask" if kind == "DECLINE/ASK" else "error"] += 1
            continue
        tally["armed"] += 1
        print(f"   ARM   : ARMED — {reply[:100]}")

        # collect the flow id(s): AP-push/branched → sub.ap_flow_id; direct → executor flow ids
        subs = store.list()
        sub = subs[-1] if subs else None
        flow_ids = []
        if sub and sub.ap_flow_id:
            flow_ids.append(("watcher", sub.ap_flow_id))
        plan = (getattr(sub, "config", None) or {}).get("action_plan") if sub else None
        if plan:
            for st in plan.get("steps") or []:
                if st.get("flow_id"):
                    flow_ids.append(("executor", st["flow_id"]))
            for b in plan.get("branches") or []:
                if (b.get("step") or {}).get("flow_id"):
                    flow_ids.append(("executor", b["step"]["flow_id"]))

        all_valid = True
        for label, fid in flow_ids:
            try:
                steps = await _flow_steps(eng, fid)
                chain = " → ".join(f"{n}{'✓' if v else '✗'}" for n, v in steps)
                valid = all(v for _, v in steps)
                all_valid = all_valid and valid
                print(f"   FLOW  : {label} {fid} · {chain} · {'VALID' if valid else 'INVALID'}")
            except Exception as e:  # noqa: BLE001
                print(f"   FLOW  : {label} {fid} · could not fetch ({e})")
                all_valid = False
        if flow_ids and all_valid:
            tally["valid"] += 1

        # FIRE — only synth-fireable triggers (github webhooks). Others: log why not.
        src, ev = cx.get("source", ""), cx.get("event", "")
        trow = _tr.get(src, ev)
        watcher_fid = next((f for lbl, f in flow_ids if lbl == "watcher"), None)
        if trow and trow.fire == "synth" and trow.synth and watcher_fid:
            hdrs = {"X-GitHub-Event": trow.hook_event} if trow.hook_event else None
            fired, msg = await eng.trigger_flow(watcher_fid, trow.synth, headers=hdrs)
            status = await _latest_run_status(eng, watcher_fid) if fired else "not-fired"
            print(f"   FIRE  : {status}  ({msg})")
            tally["fired"] += 1
            if status == "SUCCEEDED":
                tally["succeeded"] += 1
        else:
            why = (
                "poll trigger — only a real event fires it"
                if trow and trow.fire == "manual"
                else "direct/real trigger — needs a real event"
                if trow
                else "no synth payload"
            )
            print(f"   FIRE  : SKIPPED ({why})")

        # CLEAN — delete every flow we built
        for _lbl, fid in flow_ids:
            try:
                await eng.delete_flow(fid)
            except Exception:  # noqa: BLE001
                pass
        print(f"   CLEAN : deleted {len(flow_ids)} flow(s)\n")

    print("═══ SUMMARY ═══")
    print(f"  armed:            {tally['armed']}")
    print(f"  built VALID in AP:{tally['valid']}   (AP stamped every step valid)")
    print(f"  fired (synth):    {tally['fired']}   → SUCCEEDED: {tally['succeeded']}")
    print(f"  declined/asked:   {tally['declined_or_ask']}  (expected for archive/delete/label/no-recipient)")
    print(f"  errors:           {tally['error']}")


if __name__ == "__main__":
    asyncio.run(run())
