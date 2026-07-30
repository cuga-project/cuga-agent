#!/usr/bin/env python3
"""Live FIRE harness — an utterance goes in, a real answer comes out.

    make test-fire                          # everything (~9 min)
    make test-fire ARGS="--only now"        # one phase
    GITHUB_TEST_REPO=owner/repo make test-fire

Every other harness stops at *armed*: it proves a flow was created, wired to the right trigger and
the right sink. None of them proves the flow ever runs. A subscription can point at an Activepieces
flow that does not exist, or at one that exists and throws on every tick, and every existing test
still reports green.

This harness closes that gap. For each case it types a sentence, waits for the trigger to actually
fire, and reads back the answer the agent produced — from the run log for AP-owned flows, from the
channel itself for direct sinks, from the response body for the synchronous surfaces.

The verdict vocabulary is deliberately not PASS/FAIL:

    FIRED     armed → the trigger fired → an answer came back. The only fully green outcome.
    ARMED     the flow exists and is enabled, but nothing fired inside the budget. Not a failure
              on its own — a schedule may simply not have come round — but it is not proof either.
    NOFIRE    we deliberately did not fire it, with a reason. Firing a GitHub push means mutating
              somebody's repository; that needs consent, not a test flag.
    FAIL      it should have fired and did not, or it fired and produced an error.
    SKIP      the surface is not configured.

Cadence: cron/poll cases arm a ONE-MINUTE schedule so a real tick lands inside the budget. That is a
real Activepieces schedule trigger (`{"minutes": 1}`), not a simulated one, and the flow is deleted
afterwards. Verified by hand before this file existed: armed at t=0, `SUCCEEDED` by t≈60s, answer
"Bitcoin (BTC) is currently priced at $63,795.00 USD".
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_e2e import (  # noqa: E402  — one definition of the transport, shared
    BASE, connect_needed_app, env, flow_alive, gw_headers, http, srv,
)
from steps import step  # noqa: E402

RUN = hashlib.sha1(str(time.time()).encode()).hexdigest()[:6]
FIRE_BUDGET = int(os.environ.get("FIRE_BUDGET_SECS", "900"))
TICK_WAIT = int(os.environ.get("FIRE_TICK_WAIT_SECS", "180"))   # how long to wait for one schedule tick
T0 = time.time()

FIRED, ARMED, NOFIRE, FAIL, SKIP = "FIRED", "ARMED", "NOFIRE", "FAIL", "SKIP"
ICON = {FIRED: "\033[32m✓\033[0m", ARMED: "\033[33m◐\033[0m", NOFIRE: "\033[90m·\033[0m",
        FAIL: "\033[31m✗\033[0m", SKIP: "\033[90m–\033[0m"}


def left() -> float:
    return FIRE_BUDGET - (time.time() - T0)


class Report:
    def __init__(self):
        self.rows = []

    def add(self, *, case, verdict, utterance, channel, integration, trigger, response="", why=""):
        self.rows.append(dict(case=case, verdict=verdict, utterance=utterance, channel=channel,
                              integration=integration, trigger=trigger, response=response, why=why))
        line = f"   {ICON[verdict]} {case:<28} {trigger:<8} {channel or '—':<9}"
        detail = response or why
        print(f"{line} {detail[:74]}", flush=True)
        return verdict

    def summary(self) -> int:
        c = {k: sum(1 for r in self.rows if r["verdict"] == k) for k in ICON}
        print("\n" + "═" * 76)
        print("  FIRE REPORT — did the flow actually run and answer?")
        print("═" * 76)
        w = max((len(r["case"]) for r in self.rows), default=10)
        print(f"  {'case':<{w}}  {'trigger':<8} {'channel':<9} {'integration':<11} verdict")
        print("  " + "─" * (w + 42))
        for r in self.rows:
            print(f"  {r['case']:<{w}}  {r['trigger']:<8} {r['channel'] or '—':<9} "
                  f"{r['integration'] or '—':<11} {ICON[r['verdict']]} {r['verdict']}")
            if r["utterance"]:
                print(f"  {'':<{w}}  utterance: “{r['utterance'][:64]}”")
            if r["response"]:
                print(f"  {'':<{w}}  response : {r['response'][:64]}")
            if r["why"]:
                print(f"  {'':<{w}}  \033[90m{r['why'][:70]}\033[0m")
        print("\n  " + " · ".join(f"{c[k]} {k.lower()}" for k in ICON if c[k]))
        bad = c[FAIL]
        verdict = "\033[31mFAIL\033[0m" if bad else "\033[32mPASS\033[0m"
        print(f"\n  RESULT: {verdict}   ({c[FIRED]} of {len(self.rows)} cases fired end-to-end)")
        return 1 if bad else 0


# ── the two ways a flow can prove it ran ──────────────────────────────────────
def wait_for_run(sub_id: str, timeout: int) -> tuple[str, str]:
    """Poll the run log until this subscription's flow produces a FINISHED run.

    This is the general fire-detector: it works for cron, poll and push alike, because every armed
    Activepieces flow calls /invoke and Activepieces records the result. Returns (status, answer).
    """
    deadline = time.time() + min(timeout, max(left() - 30, 0))
    seen_running = False
    while time.time() < deadline:
        code, body = srv("GET", "/api/events/runs", timeout=30)
        if code != 200:
            time.sleep(10)
            continue
        mine = [r for r in (body or {}).get("runs", []) if r.get("subscription_id") == sub_id]
        done = [r for r in mine if r.get("status") not in ("RUNNING", "PAUSED", None)]
        if mine and not done:
            seen_running = True
        if done:
            rid = done[0]["id"]
            code, det = srv("GET", f"/api/events/runs/{rid}", timeout=30)
            status = (det.get("run") or {}).get("status", done[0].get("status", "?"))
            answer = det.get("answer") or ""
            if det.get("error"):
                return status, f"ERROR: {det['error']}"
            return status, answer
        time.sleep(10)
    return ("RUNNING" if seen_running else "NONE"), ""


def slack_newest(chan: str, tok: str, after_ts: float) -> str:
    """The newest bot message in a Slack channel posted after `after_ts`. Direct-sink evidence."""
    sh = {"Authorization": f"Bearer {tok}"}
    _, hist = http("GET", f"https://slack.com/api/conversations.history?channel={chan}&limit=20",
                   headers=sh, timeout=20)
    for m in (hist.get("messages") or []):
        if float(m.get("ts", 0)) > after_ts and (m.get("bot_id") or m.get("app_id")):
            return (m.get("text") or "").replace("\n", " ")[:200]
    return ""


def has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s or "")


# ── arming ────────────────────────────────────────────────────────────────────
def subs() -> list[dict]:
    _, b = srv("GET", "/api/events/subscriptions", timeout=30)
    return (b or {}).get("subscriptions", [])


def arm(utterance: str, thread: str) -> tuple[dict | None, str]:
    """Send the utterance through the concierge; return the subscription it created (if any)."""
    before = {s["id"] for s in subs()}
    code, body = srv("POST", "/api/concierge", {"text": utterance, "thread_id": thread}, timeout=240)
    reply = (body or {}).get("reply") or (body or {}).get("error") or f"HTTP {code}"
    if code != 200:
        return None, reply
    new = [s for s in subs() if s["id"] not in before]
    return (new[0] if new else None), reply


def cleanup(created: list[str]):
    for sid in created:
        srv("DELETE", f"/api/events/subscriptions/{sid}", timeout=60)
    if created:
        print(f"\n  cleaned up {len(created)} subscription(s) this run created")


# ── phases ────────────────────────────────────────────────────────────────────
def phase_now(r: Report):
    """The synchronous surfaces: the answer IS the response body. Nothing to wait for."""
    print("\n\033[1m[NOW]\033[0m  utterance → /invoke → answer, in one request")
    for case, agent, utter in [
        ("now/pricebot", "pricebot", "what is the current price of bitcoin?"),
        ("now/geobot", "geobot", "what is the capital of Peru?"),
        ("now/weatherbot", "weatherbot", "what is the weather in Tokyo right now?"),
    ]:
        code, body = srv("POST", "/invoke", {
            "agent": agent, "text": utter,
            "source": {"type": "time", "name": "cron", "thread_id": f"web:fire-{RUN}"},
            "event": {"kind": "runonce", "payload": {}}}, headers=gw_headers(), timeout=180)
        ans = (body or {}).get("answer", "")
        mcp = ((body or {}).get("meta") or {}).get("mcp") or []
        ok = code == 200 and body.get("ok") and bool(ans.strip())
        v = r.add(case=case, verdict=FIRED if ok else FAIL, utterance=utter, channel="web",
                  integration="", trigger="NOW", response=ans.replace("\n", " ")[:160],
                  why="" if ok else f"HTTP {code} {json.dumps(body)[:90]}")
        step(phase="fire", surface="web", actor="you", utterance=utter, channel="web", trigger="NOW",
             action=f"ask {agent} directly on /invoke (no channel, no concierge)",
             expect="a substantive answer, produced by a real MCP tool",
             got=(ans.replace("\n", " ")[:180] + (f"  [mcp: {', '.join(mcp)}]" if mcp else "")),
             ok=(v == FIRED))


def phase_channel(r: Report):
    """A message typed in Slack must come back as a reply in that same Slack thread."""
    print("\n\033[1m[CHANNEL]\033[0m  type in Slack → the bot replies in-thread")
    tok, secret = env("SLACK_BOT_TOKEN"), env("SLACK_SIGNING_SECRET")
    utter = "what is the current price of bitcoin?"
    if not tok:
        return r.add(case="channel/slack", verdict=SKIP, utterance=utter, channel="slack",
                     integration="", trigger="NOW", why="SLACK_BOT_TOKEN not set")
    chan = env("SLACK_TEST_CHANNEL")
    sh = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=utf-8"}
    if not chan:
        _, lst = http("GET", "https://slack.com/api/conversations.list?types=public_channel&limit=200",
                      headers=sh, timeout=20)
        member = [c for c in lst.get("channels", []) if c.get("is_member")]
        if not member:
            return r.add(case="channel/slack", verdict=SKIP, utterance=utter, channel="slack",
                         integration="", trigger="NOW",
                         why="bot is in no channel — /invite it, or set SLACK_TEST_CHANNEL")
        chan = member[0]["id"]

    _, posted = http("POST", "https://slack.com/api/chat.postMessage",
                     {"channel": chan, "text": f"[fire-{RUN}] {utter}"}, sh, timeout=20)
    if not posted.get("ok"):
        return r.add(case="channel/slack", verdict=SKIP, utterance=utter, channel="slack",
                     integration="", trigger="NOW", why=f"chat.postMessage: {posted.get('error')}")
    ts = posted["ts"]
    step(phase="fire", surface="slack", actor="you", utterance=utter, channel="slack", trigger="NOW",
         action=f"type it into the Slack channel {chan}",
         expect="Slack's Events API notifies CUGA", got=f"posted, ts={ts}", ok=None)

    ev = {"type": "event_callback",
          "event": {"type": "message", "text": utter, "channel": chan, "user": "U0FIRE", "ts": ts}}
    raw = json.dumps(ev)
    hdrs = {"Content-Type": "application/json"}
    if secret:
        stamp = str(int(time.time()))
        hdrs["X-Slack-Request-Timestamp"] = stamp
        hdrs["X-Slack-Signature"] = "v0=" + hmac.new(
            secret.encode(), f"v0:{stamp}:{raw}".encode(), hashlib.sha256).hexdigest()
    code, _ = http("POST", BASE + "/api/events/slack/events", raw_body=raw, headers=hdrs, timeout=30)

    reply = ""
    deadline = time.time() + min(150, max(left() - 30, 0))
    while time.time() < deadline and not reply:
        time.sleep(8)
        _, thr = http("GET", f"https://slack.com/api/conversations.replies?channel={chan}&ts={ts}",
                      headers=sh, timeout=20)
        for m in (thr.get("messages") or [])[1:]:
            if m.get("bot_id") or m.get("app_id"):
                reply = (m.get("text") or "").replace("\n", " ")[:200]
                break
    ok = bool(reply) and has_digit(reply)
    r.add(case="channel/slack", verdict=FIRED if ok else FAIL, utterance=utter, channel="slack",
          integration="", trigger="NOW", response=reply,
          why="" if ok else f"no bot reply in the thread within 150s (ack was HTTP {code})")
    step(phase="fire", surface="slack", actor="the bot", utterance=utter, channel="slack",
         trigger="NOW", action="reply in the thread under your message",
         expect="a price, delivered back into the same Slack thread",
         got=reply or "(no reply appeared in the thread)", ok=ok)


def _schedule_case(r: Report, created: list, *, case, utter, thread, channel, trigger):
    """Arm a ONE-MINUTE schedule, wait for a real tick, read the answer out of the run log."""
    sub, reply = arm(utter, thread)
    if sub is None:
        app = connect_needed_app(reply)
        v = NOFIRE if app else FAIL
        return r.add(case=case, verdict=v, utterance=utter, channel=channel, integration="",
                     trigger=trigger,
                     why=(f"concierge asked you to connect '{app}' — nothing to fire" if app
                          else f"no subscription armed: {reply[:90]}"))
    created.append(sub["id"])
    # Report the mode the concierge ACTUALLY armed, not the one this case is named after. They can
    # differ — "every 15 minutes, only new items" is a known case that arms CRON while reading as a
    # poll — and a trigger column that prints the label we hoped for is worse than no column at all.
    armed_mode = sub.get("mode") or "?"
    if armed_mode != trigger:
        trigger = f"{armed_mode} (asked for {trigger})"
    alive, detail = flow_alive(sub)
    step(phase="fire", surface=channel, actor="you", utterance=utter, channel=channel,
         trigger=trigger, action="say this in chat, then wait for the schedule to come round",
         expect="a real Activepieces flow, enabled, on a 1-minute schedule",
         got=(f"armed {sub['mode']} flow {detail}" if alive else f"NOT ARMED — {detail}"),
         ok=alive)
    if not alive:
        return r.add(case=case, verdict=FAIL, utterance=utter, channel=channel, integration="",
                     trigger=trigger, why=detail)

    print(f"     waiting up to {TICK_WAIT}s for a tick…", flush=True)
    status, answer = wait_for_run(sub["id"], TICK_WAIT)
    if status.startswith("ERROR") or answer.startswith("ERROR"):
        v = FAIL
        why = answer or status
    elif status == "SUCCEEDED" and answer.strip():
        v, why = FIRED, ""
    elif status == "SUCCEEDED":
        v, why = FAIL, "the flow ran and succeeded, but produced no answer"
    elif status == "RUNNING":
        v, why = ARMED, f"the flow fired but was still running after {TICK_WAIT}s"
    else:
        v, why = ARMED, f"enabled, but no run appeared within {TICK_WAIT}s"
    r.add(case=case, verdict=v, utterance=utter, channel=channel, integration="", trigger=trigger,
          response=answer.replace("\n", " ")[:160], why=why)
    step(phase="fire", surface=channel, actor="the flow", utterance=utter, channel=channel,
         trigger=trigger, action="fire on its schedule and run the agent",
         expect="a finished run whose answer is a real, tool-derived response",
         got=(answer.replace("\n", " ")[:180] or f"run status={status}"), ok=(v == FIRED),
         note=why)
    return v


def phase_cron(r: Report, created: list):
    print("\n\033[1m[CRON]\033[0m  arm a 1-minute schedule, wait for a real tick")
    _schedule_case(r, created, case="cron/pricebot", trigger="CRON", channel="web",
                   utter="every minute send me the price of bitcoin",
                   thread=f"web:fire-cron-{RUN}")


def phase_poll(r: Report, created: list):
    print("\n\033[1m[POLL]\033[0m  arm a 1-minute watch, wait for a real tick")
    _schedule_case(r, created, case="poll/weatherbot", trigger="POLL", channel="web",
                   utter="check the weather in Tokyo every minute and ping me if it changes",
                   thread=f"web:fire-poll-{RUN}")


def phase_webhook(r: Report):
    """Synchronous: the hook runs the agent and returns its answer in the response."""
    print("\n\033[1m[WEBHOOK]\033[0m  POST a payload → agent triages it → answer in the response")
    payload = {"alert": "HighCPU", "service": "checkout-api", "value": 97, "threshold": 85}
    utter = "(an external system POSTs a monitoring alert)"
    key = env("EVENTS_WEBHOOK_KEY")
    q = f"?agent=incident_triage&key={key}" if key else "?agent=incident_triage"
    code, body = srv("POST", f"/api/events/hook/fire-{RUN}{q}", payload, timeout=240)
    ans = (body or {}).get("answer", "") or ""
    ok = code == 200 and (body or {}).get("ok") and bool(ans.strip())
    r.add(case="webhook/incident_triage", verdict=FIRED if ok else FAIL, utterance=utter,
          channel="web", integration="webhook", trigger="WEBHOOK",
          response=ans.replace("\n", " ")[:160],
          why="" if ok else f"HTTP {code} {json.dumps(body)[:90]}")
    step(phase="fire", surface="webhook", actor="an external system", utterance=utter,
         channel="", integration="webhook", trigger="WEBHOOK",
         action=f"POST {json.dumps(payload)} to /api/events/hook/fire-{RUN}",
         expect="the agent triages the alert and the answer rides back in the response",
         got=ans.replace("\n", " ")[:180] or f"HTTP {code}", ok=ok)


def phase_box(r: Report):
    """The direct Box poller: a real folder listing, a real dispatch per new file."""
    print("\n\033[1m[POLL · box]\033[0m  poll the Box folder → dispatch the watcher agent per file")
    utter = "(a resume lands in the watched Box folder)"
    if env("EVENTS_BOX_BACKEND") != "direct":
        return r.add(case="poll/box", verdict=SKIP, utterance=utter, channel="", integration="box",
                     trigger="POLL", why="EVENTS_BOX_BACKEND != direct — Box uses the AP push trigger")
    if not (env("BOX_DEV_TOKEN") or env("EVENTS_BOX_TOKEN")):
        return r.add(case="poll/box", verdict=SKIP, utterance=utter, channel="", integration="box",
                     trigger="POLL", why="no Box token")
    # since=epoch replays every file in the folder; the server watermark is NOT advanced.
    code, body = srv("POST", "/api/events/box/poll",
                     {"folder_id": env("BOX_FOLDER_ID", "0"), "since": "1970-01-01T00:00:00-00:00",
                      "agent": "resume_judge"},
                     headers=gw_headers(), timeout=300)
    n = len((body or {}).get("processed", []))
    if code == 502:
        # Worth being loud about: for the direct backend, /api/events/integrations reports box as
        # "connected" whenever BOX_DEV_TOKEN is a non-empty STRING (app.py:370) — it never calls Box.
        # A dev token expires after an hour, so "connected" and "works" routinely disagree. Only
        # firing the poller tells them apart, which is the whole reason this harness exists.
        v, why = FAIL, (f"Box rejected the token: {(body or {}).get('error', '')[:70]} — note that "
                        f"/api/events/integrations still reports box 'connected', because for the "
                        f"direct backend that only means BOX_DEV_TOKEN is a non-empty string.")
    elif code != 200:
        v, why = FAIL, f"HTTP {code} {json.dumps(body)[:80]}"
    elif n == 0:
        v, why = NOFIRE, "the folder is empty — nothing to fire the watcher on. Drop a file in it."
    else:
        v, why = FIRED, ""
    files = ", ".join(f["name"] for f in (body or {}).get("processed", [])[:3])
    r.add(case="poll/box", verdict=v, utterance=utter, channel="", integration="box", trigger="POLL",
          response=(f"dispatched resume_judge on {n} file(s): {files}" if n else ""), why=why)
    step(phase="fire", surface="box", actor="the poller", utterance=utter, integration="box",
         trigger="POLL", action="list the Box folder and run resume_judge on every new file",
         expect="one agent dispatch per file, and a watermark to resume from",
         got=(f"{n} file(s): {files}" if n else why), ok=(v == FIRED), note=why)


def phase_push_github(r: Report, created: list):
    """Arm a real GitHub watcher. Firing it means pushing to somebody's repo — we don't."""
    print("\n\033[1m[PUSH · github]\033[0m  arm the watcher; do NOT mutate the repo to fire it")
    repo = env("GITHUB_TEST_REPO")
    utter = f"watch the repo {repo or 'owner/repo'} for new pull requests and summarize each one"
    if not repo:
        return r.add(case="push/github", verdict=SKIP, utterance=utter, channel="web",
                     integration="github", trigger="PUSH",
                     why="GITHUB_TEST_REPO unset — refusing to guess a repository to arm against")
    # The concierge intermittently replies "Which repository?" even though the utterance names it —
    # the repo never reaches find_or_create_flow's `prompt`. Retry on a fresh thread rather than
    # score an LLM coin-flip as a broken integration. Two identical asks IS a finding, so it fails.
    sub = reply = None
    for attempt in range(2):
        sub, reply = arm(utter, f"web:fire-gh-{RUN}-{attempt}")
        if sub is not None or "which rep" not in (reply or "").lower():
            break
        print(f"     concierge dropped the repo from the utterance; retrying ({attempt + 1}/2)",
              flush=True)
    if sub is None:
        app = connect_needed_app(reply)
        asked = "which rep" in (reply or "").lower()
        return r.add(case="push/github", verdict=(NOFIRE if app else FAIL), utterance=utter,
                     channel="web", integration="github", trigger="PUSH",
                     why=(f"concierge asked you to connect '{app}'" if app else
                          ("the concierge asked 'which repository?' twice, though the utterance names "
                           f"{repo} — the repo is not reaching the arming tool" if asked
                           else reply[:90])))
    created.append(sub["id"])
    alive, detail = flow_alive(sub)
    r.add(case="push/github", verdict=(NOFIRE if alive else FAIL), utterance=utter, channel="web",
          integration="github", trigger="PUSH",
          response=(f"watcher armed, AP flow {detail}" if alive else ""),
          why=("armed and enabled, but NOT fired: firing means opening a PR or pushing a commit to "
               f"{repo}. That mutates a real repository, so it needs your consent, not a test flag."
               if alive else detail))
    step(phase="fire", surface="github", actor="you", utterance=utter, channel="web",
         integration="github", trigger="PUSH",
         action="ask for a watcher on the repo, then check Activepieces really holds the flow",
         expect="an enabled AP flow whose trigger is the github piece",
         got=(f"armed, AP flow {detail}" if alive else f"NOT ARMED — {detail}"), ok=alive,
         note="not fired on purpose — firing would push to a real repository")


PHASES = {"now": phase_now, "channel": phase_channel, "cron": phase_cron, "poll": phase_poll,
          "webhook": phase_webhook, "box": phase_box, "push": phase_push_github}


def main() -> int:
    ap = argparse.ArgumentParser(description="Fire real triggers and read back real answers.")
    ap.add_argument("--only", nargs="*", default=list(PHASES), choices=list(PHASES))
    a = ap.parse_args()

    print(f"\033[1mCUGA live FIRE\033[0m — {BASE}  (budget {FIRE_BUDGET}s, tick wait {TICK_WAIT}s)")
    code, st = srv("GET", "/api/events/status", timeout=20)
    if code != 200:
        print("  server is down. Run `make up` first.")
        return 2
    _, integ = srv("GET", "/api/events/integrations", timeout=20)
    print(f"  AP configured: {st.get('ap_configured')}   integrations: "
          f"{ {i['name']: i['status'] for i in (integ or {}).get('integrations', [])} }")
    if not st.get("ap_configured"):
        print("  \033[33mAP is not configured — cron/poll/push cannot arm, let alone fire.\033[0m")

    r, created = Report(), []
    try:
        for name in PHASES:
            if name not in a.only:
                continue
            if left() < 60:
                print(f"\n  \033[33mbudget exhausted — skipping {name}\033[0m")
                break
            fn = PHASES[name]
            fn(r, created) if fn in (phase_cron, phase_poll, phase_push_github) else fn(r)
    finally:
        cleanup(created)
    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
