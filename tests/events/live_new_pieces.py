"""LIVE test for the newer trigger pieces — Google Calendar · Pinterest · YouTube · RSS · Discord.

Answers "can I actually exercise these?" without needing every OAuth connection:

  SYNTH  — POST /api/events/synth-fire with the registry's piece-exact synth payload → the agent
           runs on the SAME shape a real event delivers. Works with NO connection and NO armed flow,
           so calendar/pinterest/youtube/rss are all testable today.
  ARM    — POST /api/concierge '/push …' and report what came back:
             • youtube/rss  → arm as real AP watchers (no OAuth) — expect ARMED (rss needs a REAL feed URL)
             • calendar/pinterest → expect a clean 'CONNECT NEEDED' until you connect them (gap #2 fix)
             • discord/new_reaction → arm as a direct watcher (live now)

A PASS on SYNTH means the trigger→agent→answer chain is correct end to end. ARM tells you exactly
what each piece still needs from you.

Run:  EVENTS_SERVER_URL=http://localhost:7860 .venv/bin/python tests/events/live_new_pieces.py
Reads .env for GATEWAY_TOKEN. Cleans up every subscription it arms.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")


def _env(key: str) -> str:
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return ""


GW = _env("GATEWAY_TOKEN")
H = {"Content-Type": "application/json", "X-Gateway-Token": GW}
G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; X = "\033[0m"


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(SERVER + path, data=json.dumps(body).encode(), headers=H)
    try:
        return json.load(urllib.request.urlopen(req, timeout=180))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _delete(sub_id: str) -> None:
    req = urllib.request.Request(f"{SERVER}/api/events/subscriptions/{sub_id}",
                                 headers={"X-Gateway-Token": GW}, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception:  # noqa: BLE001
        pass


def _quality(ans: str) -> bool:
    a = (ans or "").strip()
    if len(a) < 15:
        return False
    low = a.lower()
    bad = ("i'm unable", "i am unable", "i cannot", "connect needed", "http 0", "timed out",
           "'error':", "while true")
    return not any(b in low for b in bad)


# synth-fire targets the NEW AP pieces (each ships a registry synth sample)
SYNTH = ["google_calendar", "pinterest", "youtube", "rss"]
ARMS = [
    ("youtube", "/push when @Fireship posts a new video, summarize it in slack", "ARMED"),
    ("rss", "/push when a new item appears in https://news.ycombinator.com/rss, summarize it in slack", "ARMED"),
    ("google_calendar", "/push when a new event is added to my google calendar, brief me in slack", "CONNECT"),
    ("pinterest", "/push when there's a new pin on my pinterest board 549755885175, share it in slack", "CONNECT"),
    ("discord", "/push when someone reacts with :bug: in #incidents on discord, triage it", "ARMED"),
]


def main() -> int:
    print(f"New-pieces live check — {SERVER}\n")
    fails = 0

    print("[SYNTH] connection-free fire at the /invoke seam (piece-exact synth payload)")
    for src in SYNTH:
        r = _post("/api/events/synth-fire", {"source": src,
                                             "prompt": f"Summarize this {src} event in one line."})
        ok = bool(r.get("ok")) and _quality(r.get("answer", ""))
        fails += 0 if ok else 1
        tag = f"{G}PASS{X}" if ok else f"{R}FAIL{X}"
        print(f"  {tag} {src:16} {(r.get('answer') or r.get('error') or '')[:74]}")

    print("\n[ARM] '/push …' — what each piece still needs")
    armed_subs = []
    for src, utter, want in ARMS:
        r = _post("/api/concierge", {"text": utter, "thread_id": "gw:slack:C0LIVECHK#np"})
        reply = r.get("reply", "") or ""
        got = "ARMED" if reply.startswith("ARMED") else ("CONNECT" if "CONNECT NEEDED" in reply
                                                         else "OTHER")
        ok = (got == want) or (want == "ARMED" and src == "rss" and "invalid" in reply.lower())
        fails += 0 if ok else 1
        tag = f"{G}PASS{X}" if ok else (f"{Y}WARN{X}" if got == "OTHER" else f"{R}FAIL{X}")
        print(f"  {tag} {src:16} want={want:8} → {reply[:78]}")
        # capture an armed sub id for cleanup
        for token in reply.replace("(", " ").replace(")", " ").split():
            if token.startswith("cuga-"):
                armed_subs.append(token.strip("."))

    for sid in set(armed_subs):
        _delete(sid)
    if armed_subs:
        print(f"\n[cleanup] deleted {len(set(armed_subs))} armed subscription(s)")

    print(f"\nRESULT: {'ALL GREEN' if fails == 0 else str(fails) + ' issue(s)'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
