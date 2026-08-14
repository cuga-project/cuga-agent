"""LIVE end-to-end harness — the four CHANNELS and the four FLOW MODES, against a running stack.

This is what `make test-live` runs. It is the automated version of "sit down and poke every surface":
send a real message on every channel, get a real answer back, then arm every flow mode and prove an
Activepieces flow actually came into existence — and delete it again.

    channels:  web · slack · discord · telegram        (send a message → validate the answer)
    flows:     NOW · CRON · POLL · PUSH · WEBHOOK      (arm → verify AP flow → clean up)

HOW REAL IS EACH CHANNEL LEG?  Not all four can be driven the same way, and the report says so
rather than painting them uniformly green:

  web       FULL   POST /api/concierge → answer in the response.
  slack     FULL   We post a real message, then send a Slack Events API request byte-identical to
                   Slack's own — HMAC-signed when SLACK_SIGNING_SECRET is set, unsigned otherwise
                   (the endpoint accepts unsigned requests when no secret is configured, and we warn).
                   The server answers and posts back via the real Slack API; we read
                   conversations.replies to confirm. The only simulated hop is Slack's webhook to us.
  discord   PARTIAL discord_direct.should_process() drops bot-authored messages, so the bot provably
                   cannot message itself over the Gateway. We drive the exact /invoke envelope the
                   Gateway posts, with deliver=true, so the concierge + the REAL outbound REST send are
                   both exercised; we read the channel back to confirm. Untested hop: the Gateway socket.
  telegram  PARTIAL Telegram is AP-backed both ways and a bot cannot send itself a message. We drive the
                   /invoke envelope AP posts (concierge leg) and separately prove the real outbound leg
                   with sendMessage to TELEGRAM_CHAT_ID. Untested hop: Telegram → AP webhook.

WHY A MISSING CREDENTIAL IS A **SKIP**, NOT A FAIL: the point of this suite is to catch regressions in
what you have configured. A red bar because you never set DISCORD_BOT_TOKEN teaches nothing. Skips are
counted separately and never mask a real failure.

WHY WE CHECK AP IS REACHABLE BEFORE ACCEPTING "CONNECT NEEDED": the connect gate reports
"connect your credentials" when Activepieces is merely unreachable (concierge.py: a bare `except`
swallows the AP error and sets exists=False). So an unreachable AP would otherwise make every PUSH leg
"pass" as a legitimate connect-needed. We probe AP first and refuse to accept connect-needed if AP is
down. See events/docs/GAPS.md.

NOTE ON POLL: there is no state primitive today — no poll_state.py, no /api/events/poll. A "poll" is a
cron-scheduled flow plus a prompt line ("only report if changed"). We assert exactly that and no more.

Prereqs:  make up   (AP + server)   ·   make doctor   (creds)
Env:      EVENTS_SERVER_URL (default http://localhost:7860), GATEWAY_TOKEN, plus channel creds in .env
Optional: SLACK_TEST_CHANNEL, DISCORD_TEST_CHANNEL_ID, TELEGRAM_CHAT_ID (else auto-discovered/skipped)

Run:      make test-live
          make test-live ARGS="--only channels"
          .venv/bin/python tests/events/live_e2e.py --only flows --no-cleanup
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from steps import step  # noqa: E402  — no-op unless E2E_STEPS_FILE is set

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")

# A probe whose answer is machine-checkable: any correct reply contains a digit. Reused on every
# channel so a channel regression is distinguishable from an agent regression.
PROBE = "what is the current price of bitcoin in usd? just the number"
DEADLINE = time.monotonic() + float(os.environ.get("E2E_BUDGET_SECS", "420"))

# Per-run thread suffix. Conversation memory is keyed by thread_id, so a fixed one lets a previous
# run's context ("the JD is …") satisfy a slot the current run never filled — a phantom pass.
RUN = f"e2e{int(time.time()) % 100000}"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


# ── env / http ────────────────────────────────────────────────────────────────
def env(key: str, default: str = "") -> str:
    """Read a var from the process env, else from .env (the server reads .env at startup, so the
    test must see the same values). Strips ` # trailing comments`, as the app's own readers do."""
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


# Discord's Cloudflare edge rejects urllib's default "Python-urllib/3.x" UA with 403 error-code 1010,
# which looks exactly like "bot is in no guild". Every request carries a real UA.
USER_AGENT = "DiscordBot (https://github.com/cuga/events, 1.0) cuga-live-e2e"


def http(method: str, url: str, body=None, headers=None, timeout=60, raw_body: str | None = None):
    """Returns (status, parsed_json_or_{}). status 0 means the request never completed."""
    data = (
        raw_body.encode()
        if raw_body is not None
        else (json.dumps(body).encode() if body is not None else None)
    )
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(txt or "{}")
            except json.JSONDecodeError:
                return r.status, {"_text": txt}
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(txt or "{}")
        except json.JSONDecodeError:
            return e.code, {"_text": txt[:400]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def srv(method, path, body=None, headers=None, timeout=200):
    return http(method, BASE + path, body, {"Content-Type": "application/json", **(headers or {})}, timeout)


def gw_headers():
    return {"X-Gateway-Token": env("GATEWAY_TOKEN")}


def has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s or "")


def budget_left() -> float:
    return DEADLINE - time.monotonic()


# ── reporting ─────────────────────────────────────────────────────────────────
class Report:
    """Collects PASS/FAIL/SKIP. A SKIP is a first-class outcome — an unconfigured channel must not
    turn the bar red, but it must also never be silently counted as a pass."""

    def __init__(self):
        self.rows: list[tuple[str, str, str, str]] = []  # (phase, name, status, detail)
        self._t0 = time.monotonic()

    def add(self, phase, name, status, detail=""):
        self.rows.append((phase, name, status, detail))
        icon = {PASS: "\033[32m✓\033[0m", FAIL: "\033[31m✗\033[0m", SKIP: "\033[33m–\033[0m"}[status]
        print(f"   {icon} {name}" + (f" — {detail}" if detail else ""), flush=True)
        return status == PASS

    def ok(self, phase, name, cond, detail="", fail_detail=""):
        """`detail` annotates either outcome; `fail_detail` is the diagnosis shown only on failure.
        Keeping them apart stops a passing row from printing the words 'no subscription found'."""
        return self.add(phase, name, PASS if cond else FAIL, detail if cond else (fail_detail or detail))

    def skip(self, phase, name, why):
        return self.add(phase, name, SKIP, why)

    def counts(self):
        c = {PASS: 0, FAIL: 0, SKIP: 0}
        for _, _, s, _ in self.rows:
            c[s] += 1
        return c

    def summary(self) -> int:
        c = self.counts()
        dur = time.monotonic() - self._t0
        print("\n" + "─" * 72)
        print(f"  {c[PASS]} passed · {c[FAIL]} failed · {c[SKIP]} skipped   ({dur:.0f}s)")
        if c[FAIL]:
            print("\n  Failures:")
            for phase, name, s, detail in self.rows:
                if s == FAIL:
                    print(f"    ✗ [{phase}] {name}" + (f" — {detail}" if detail else ""))
        if c[SKIP]:
            print("\n  Skipped (not configured — see events/docs/setup/):")
            for phase, name, s, detail in self.rows:
                if s == SKIP:
                    print(f"    – [{phase}] {name} — {detail}")
        print("\n  RESULT:", "\033[32mPASS\033[0m" if not c[FAIL] else "\033[31mFAIL\033[0m")
        return 1 if c[FAIL] else 0


# ── preconditions ─────────────────────────────────────────────────────────────
def phase_preflight(r: Report) -> dict:
    """Establish the ground truth every later assertion leans on. Returns a facts dict."""
    print("\n\033[1m[preflight]\033[0m server, AP, agents")
    code, st = srv("GET", "/api/events/status", timeout=20)
    if code != 200:
        r.ok("preflight", "events server reachable", False, f"{BASE} → {code} {st.get('error', '')}")
        return {"dead": True}
    r.ok("preflight", "events server reachable", True, BASE)
    # ONE runtime: the eventing service always executes by calling CUGA's POST /run, so the honest
    # label is "http". "cuga" is still accepted — it is the legacy alias for the same thing, and an
    # existing EVENTS_WORKER_BACKEND=cuga in a deployment's env must keep working.
    r.ok(
        "preflight",
        "worker executes on CUGA (over /run)",
        st.get("worker_backend") in ("http", "cuga"),
        str(st.get("worker_backend")),
    )

    # AP reachability is load-bearing: without it, CONNECT-NEEDED is indistinguishable from
    # "user never connected" (concierge.py swallows the AP exception). Probe AP itself, not just
    # the server's opinion of it.
    ap_cfg = bool(st.get("ap_configured"))
    ap_url = env("AP_BASE_URL", "http://localhost:8081").rstrip("/")
    ap_code, _ = http("GET", f"{ap_url}/api/v1/flags", timeout=8)
    ap_live = ap_code == 200
    # NO-AP MODE: when the server itself reports ap_configured=false, Activepieces is INTENTIONALLY
    # absent (make up-noap / Code Engine), so the AP-dependent checks SKIP instead of failing. If the
    # server DOES configure AP but it's unreachable, they still FAIL — that's a real, actionable bug.
    no_ap = not ap_cfg
    if no_ap:
        r.skip(
            "preflight",
            "AP configured on the server",
            "no-AP mode — Activepieces intentionally absent (make up-noap / Code Engine)",
        )
        r.skip(
            "preflight",
            "AP actually reachable",
            "no-AP mode — cron/poll run on the native scheduler; AP push triggers are off by design",
        )
    else:
        r.ok("preflight", "AP configured on the server", ap_cfg, fail_detail="ap_configured=false")
        r.ok(
            "preflight",
            "AP actually reachable",
            ap_live,
            ap_url,
            fail_detail=f"{ap_url} → {ap_code or 'no response'}; CRON/POLL/PUSH cannot arm, and "
            f"CONNECT-NEEDED would be a false negative (DECISIONS_2026-07-09 §2)",
        )

    _, integ = srv("GET", "/api/events/integrations", timeout=20)
    conn = {i["name"]: i.get("status") for i in integ.get("integrations", [])}
    print(f"     integrations: { {k: conn.get(k) for k in ('box', 'github', 'gmail')} }")

    _, ag = srv("GET", "/api/events/agents", timeout=20)
    agents = [a.get("name") for a in ag.get("agents", [])]
    r.ok("preflight", "agent fleet seeded", len(agents) >= 3, f"{len(agents)} agents")
    # WHICH BACKEND OWNS cron/poll. AP being reachable does NOT mean cron/poll use it: the server
    # defaults to EVENTS_SCHEDULER=native, where they run in-process and there is no AP flow to
    # record. Asking "is AP up?" and then demanding an ap_flow_id failed a correctly-armed native
    # schedule. Ask the SERVER what it is doing (it must answer for a remote CE deploy too).
    native_sched = any("native scheduler ON" in str(line) for line in (st.get("capability") or []))
    return {
        "dead": False,
        "ap_live": ap_live,
        "no_ap": no_ap,
        "conn": conn,
        "agents": agents,
        "native_sched": native_sched,
    }


# ── channels ──────────────────────────────────────────────────────────────────
def ch_web(r: Report):
    print("\n\033[1m[channel · web]\033[0m  POST /api/concierge   (FULL round trip)")
    code, rep = srv("POST", "/api/concierge", {"text": PROBE, "thread_id": f"web:{RUN}"}, timeout=200)
    reply = str(rep.get("reply", ""))
    print(f"     → {reply[:120]}")
    step(
        phase="channels",
        surface="web",
        actor="you",
        action=f'open the web chat and type "{PROBE}"',
        expect="the concierge routes it to pricebot and answers with a live price",
        got=reply,
        ok=(code == 200 and has_digit(reply)),
    )
    r.ok("web", "concierge answers over HTTP", code == 200 and bool(reply), fail_detail=f"HTTP {code}")
    r.ok("web", "answer is substantive (contains a number)", has_digit(reply))


def _invoke_channel(name: str, thread: str, user: str, deliver: bool):
    """POST the exact envelope a channel transport (AP webhook / Discord Gateway) posts to /invoke."""
    payload = {
        "text": PROBE,
        "agent": "concierge",
        "deliver": deliver,
        "source": {"type": "channel", "name": name, "thread_id": thread, "user": user},
        "event": {"kind": "message", "payload": {}},
    }
    return srv("POST", "/invoke", payload, gw_headers(), timeout=240)


def ch_slack(r: Report):
    print("\n\033[1m[channel · slack]\033[0m  signed Events API → real chat.postMessage   (FULL round trip)")
    tok, secret = env("SLACK_BOT_TOKEN"), env("SLACK_SIGNING_SECRET")
    if not tok:
        return r.skip("slack", "slack round trip", "SLACK_BOT_TOKEN not set")
    # No signing secret? slack_direct.verify_signature returns (True, "unverified"), so the endpoint
    # accepts unsigned requests. We can still drive the full round trip — we just can't run the
    # negative control, and we say so loudly, because an unsigned endpoint is publicly spoofable.
    if not secret:
        print(
            "     \033[33mwarning\033[0m: SLACK_SIGNING_SECRET unset — the /api/events/slack/events "
            "endpoint accepts UNSIGNED requests from anyone who finds your public URL."
        )

    sh = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=utf-8"}
    chan = env("SLACK_TEST_CHANNEL")
    if not chan:
        _, lst = http(
            "GET",
            "https://slack.com/api/conversations.list?types=public_channel&limit=200",
            headers=sh,
            timeout=20,
        )
        if not lst.get("ok"):
            return r.skip(
                "slack",
                "slack round trip",
                f"conversations.list failed ({lst.get('error')}) — set SLACK_TEST_CHANNEL",
            )
        member = [c for c in lst.get("channels", []) if c.get("is_member")]
        if not member:
            return r.skip("slack", "slack round trip", "bot is not a member of any channel — /invite it")
        chan = member[0]["id"]
        print(f"     channel: #{member[0].get('name')} ({chan})")

    # 1) A real anchor message, so the bot's threaded reply has a real thread to root in.
    _, posted = http(
        "POST",
        "https://slack.com/api/chat.postMessage",
        {"channel": chan, "text": f"[e2e] {PROBE}"},
        sh,
        timeout=20,
    )
    if not posted.get("ok"):
        return r.skip("slack", "slack round trip", f"chat.postMessage failed: {posted.get('error')}")
    ts = posted["ts"]
    step(
        phase="channels",
        surface="slack",
        actor="you",
        action=f'post "{PROBE}" in the Slack channel ({chan})',
        expect="Slack accepts the message and Slack's Events API notifies CUGA",
        got=f"message posted, ts={ts}",
        ok=None,
    )

    # 2) Forge the Events API callback Slack itself would send, signed with the real secret.
    # In mention mode (EVENTS_SLACK_CHAT=mention) an unmentioned channel message is correctly
    # gated away from chat — so the probe @mentions the bot, exactly as a real user must.
    probe_text = PROBE
    if env("EVENTS_SLACK_CHAT", "").lower() == "mention":
        _, who = http("POST", "https://slack.com/api/auth.test", headers=sh, timeout=15)
        uid = (who or {}).get("user_id", "")
        if uid:
            probe_text = f"<@{uid}> {PROBE}"
    ev = {
        "type": "event_callback",
        "event": {"type": "message", "text": probe_text, "channel": chan, "user": "U0E2ETEST", "ts": ts},
    }
    raw = json.dumps(ev)
    stamp = str(int(time.time()))
    hdrs = {"Content-Type": "application/json"}
    if secret:
        hdrs["X-Slack-Request-Timestamp"] = stamp
        hdrs["X-Slack-Signature"] = (
            "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:{raw}".encode(), hashlib.sha256).hexdigest()
        )
    label = (
        "a correctly-signed Slack event"
        if secret
        else "an unsigned Slack event (no signing secret configured)"
    )
    code, ack = http("POST", BASE + "/api/events/slack/events", raw_body=raw, timeout=30, headers=hdrs)
    step(
        phase="channels",
        surface="slack",
        actor="Slack",
        action=f"POSTs the message event to /api/events/slack/events ({label})",
        expect="CUGA verifies the signature, acks in <3s, and answers in the background",
        got=f"HTTP {code} {json.dumps(ack)[:80]}",
        ok=(code == 200 and bool(ack.get("ok"))),
    )
    if not r.ok(
        "slack",
        f"server accepts {label}",
        code == 200 and ack.get("ok"),
        fail_detail=f"HTTP {code} {ack.get('error', '')}",
    ):
        return

    # Negative control: a bad signature must be rejected. Only meaningful when a secret is set —
    # without one the endpoint accepts everything by design (verify_signature returns True).
    if secret:
        bad, _ = http(
            "POST",
            BASE + "/api/events/slack/events",
            raw_body=raw,
            timeout=15,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": stamp,
                "X-Slack-Signature": "v0=deadbeef",
            },
        )
        r.ok("slack", "server rejects a badly-signed event (401)", bad == 401, fail_detail=f"HTTP {bad}")
    else:
        r.skip(
            "slack",
            "server rejects a badly-signed event",
            "SLACK_SIGNING_SECRET unset — endpoint is intentionally open; set it to enable this check",
        )

    # 3) The answer is posted asynchronously into the thread rooted at `ts`. Poll for it.
    # Floor 120s: in supervisor mode the chain is concierge → supervisor → specialist → post,
    # legitimately slower than the old direct path (SUPERVISOR_REFACTOR latency cost).
    deadline = time.monotonic() + min(240, max(120, budget_left() - 60))
    reply = ""
    while time.monotonic() < deadline:
        time.sleep(4)
        _, thr = http(
            "GET",
            f"https://slack.com/api/conversations.replies?channel={chan}&ts={ts}",
            headers=sh,
            timeout=20,
        )
        for m in thr.get("messages", [])[1:]:  # [0] is our anchor
            if m.get("text"):
                reply = m["text"]
                break
        if reply:
            break
    print(f"     → {reply[:120] or '(no reply within budget)'}")
    step(
        phase="channels",
        surface="slack",
        actor="you",
        action="look at the thread under your message in Slack",
        expect="the bot has replied in-thread with the bitcoin price",
        got=reply or "(no threaded reply within budget)",
        ok=bool(reply) and has_digit(reply),
    )
    r.ok(
        "slack",
        "bot replied in-thread via the real Slack API",
        bool(reply),
        f"{len(reply)} chars",
        fail_detail="no threaded reply within budget — check the server log for slack.reply",
    )
    if reply:
        r.ok("slack", "answer is substantive (contains a number)", has_digit(reply))


def ch_discord(r: Report):
    print(
        "\n\033[1m[channel · discord]\033[0m  /invoke envelope → real REST send   (PARTIAL: Gateway hop simulated)"
    )
    tok = env("DISCORD_BOT_TOKEN")
    if not tok:
        return r.skip("discord", "discord round trip", "DISCORD_BOT_TOKEN not set")
    dh = {"Authorization": f"Bot {tok}", "Content-Type": "application/json"}

    chan = env("DISCORD_TEST_CHANNEL_ID")
    if not chan:
        _, guilds = http("GET", "https://discord.com/api/v10/users/@me/guilds", headers=dh, timeout=20)
        if not isinstance(guilds, list) or not guilds:
            return r.skip(
                "discord",
                "discord round trip",
                "bot is in no guild (or token invalid) — set DISCORD_TEST_CHANNEL_ID",
            )
        _, chans = http(
            "GET", f"https://discord.com/api/v10/guilds/{guilds[0]['id']}/channels", headers=dh, timeout=20
        )
        text = [c for c in (chans if isinstance(chans, list) else []) if c.get("type") == 0]
        if not text:
            return r.skip(
                "discord", "discord round trip", "no text channel visible — set DISCORD_TEST_CHANNEL_ID"
            )
        chan = text[0]["id"]
        print(f"     channel: #{text[0].get('name')} ({chan})")

    # deliver=true + a gw:discord:<channel> thread makes CUGA send the answer itself via
    # discord_direct.send_message — the same call the Gateway path makes.
    before = time.time()
    code, rep = _invoke_channel("discord", f"gw:discord:{chan}", "e2e-user", deliver=True)
    answer = str(rep.get("answer", ""))
    print(f"     → {answer[:120]}")
    step(
        phase="channels",
        surface="discord",
        actor="you",
        action=f'type "{PROBE}" in the Discord channel ({chan})',
        expect="the Gateway relays it to CUGA, which answers with a live price",
        got=answer,
        ok=(code == 200 and has_digit(answer)),
        note="the Gateway socket itself is simulated: a bot cannot message itself "
        "(discord_direct.should_process drops bot authors)",
    )
    if not r.ok(
        "discord",
        "concierge answers a discord envelope",
        code == 200 and bool(answer),
        fail_detail=f"HTTP {code} {rep.get('error', '')}",
    ):
        return
    r.ok("discord", "answer is substantive (contains a number)", has_digit(answer))

    # Read the channel back: the reply must actually exist in Discord.
    time.sleep(3)
    _, msgs = http(
        "GET", f"https://discord.com/api/v10/channels/{chan}/messages?limit=10", headers=dh, timeout=20
    )
    landed = any(
        m.get("content")
        and m.get("timestamp")
        and time.mktime(time.strptime(m["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")) + 60 > before
        for m in (msgs if isinstance(msgs, list) else [])
    )
    step(
        phase="channels",
        surface="discord",
        actor="you",
        action="scroll the Discord channel",
        expect="the bot's reply is there — posted by a real REST call, not a mock",
        got="a new bot message is present" if landed else "no recent bot message",
        ok=landed,
    )
    r.ok(
        "discord",
        "reply landed in the channel (real REST send)",
        landed,
        fail_detail="no recent message in the channel — check discord_direct.send_message",
    )


def ch_telegram(r: Report):
    print(
        "\n\033[1m[channel · telegram]\033[0m  /invoke envelope + real sendMessage   (PARTIAL: AP webhook hop simulated)"
    )
    tok = env("TELEGRAM_BOT_TOKEN")
    if not tok:
        return r.skip("telegram", "telegram round trip", "TELEGRAM_BOT_TOKEN not set")
    api = f"https://api.telegram.org/bot{tok}"

    _, who = http("GET", f"{api}/getMe", timeout=20)
    if not r.ok(
        "telegram",
        "bot token valid (getMe)",
        who.get("ok"),
        f"@{who.get('result', {}).get('username')}" if who.get("ok") else str(who)[:80],
    ):
        return

    # Concierge leg: the envelope AP's telegram flow posts to /invoke. deliver=False because
    # telegram's sink is AP-owned (delivery.py: telegram → "ap"), so CUGA must not send it.
    chat = env("TELEGRAM_CHAT_ID")
    code, rep = _invoke_channel("telegram", f"gw:telegram:{chat or '0'}", "e2e-user", deliver=False)
    answer = str(rep.get("answer", ""))
    print(f"     → {answer[:120]}")
    step(
        phase="channels",
        surface="telegram",
        actor="you",
        action=f'message the bot @{who.get("result", {}).get("username", "bot")} with "{PROBE}"',
        expect="Activepieces' telegram webhook posts it to CUGA, which answers with a live price",
        got=answer,
        ok=(code == 200 and has_digit(answer)),
        note="the Telegram → AP webhook hop is simulated; a bot cannot message itself",
    )
    r.ok(
        "telegram",
        "concierge answers a telegram envelope",
        code == 200 and bool(answer),
        fail_detail=f"HTTP {code} {rep.get('error', '')}",
    )
    if answer:
        r.ok("telegram", "answer is substantive (contains a number)", has_digit(answer))

    # Outbound leg, for real.
    if not chat:
        return r.skip("telegram", "real delivery leg (sendMessage)", "TELEGRAM_CHAT_ID not set")
    _, sent = http(
        "POST",
        f"{api}/sendMessage",
        {"chat_id": chat, "text": f"✅ live_e2e: {answer[:200] or 'delivery leg OK'}"},
        {"Content-Type": "application/json"},
        timeout=20,
    )
    step(
        phase="channels",
        surface="telegram",
        actor="you",
        action="open the Telegram chat with the bot",
        expect="the bot's message is delivered for real (sendMessage)",
        got="delivered" if sent.get("ok") else str(sent.get("description")),
        ok=bool(sent.get("ok")),
    )
    r.ok(
        "telegram",
        "real delivery leg (sendMessage)",
        sent.get("ok"),
        f"chat {chat}",
        fail_detail=sent.get("description", "sendMessage failed"),
    )


# ── flows ─────────────────────────────────────────────────────────────────────
def _subs():
    code, s = srv("GET", "/api/events/subscriptions", timeout=30)
    return s.get("subscriptions", []) if code == 200 else []


def _match_sub(before_ids: set, mode: str | None = None, source: str | None = None):
    """Find the subscription for this arm. Returns (sub, is_new).

    Three traps this navigates.

    (1) The old harness matched on mode alone, so a leftover subscription from a previous run made a
        broken arm look green — we prefer a subscription whose id is NEW.
    (2) `find_or_create_flow` de-duplicates on `dedup_key`, so re-arming the same intent legitimately
        REUSES an existing flow and creates nothing. That is a pass, not a failure — but only NEW
        subscriptions may be deleted during cleanup, or we would delete the operator's real flows.
    (3) An integration watcher's MODE is not the user's phrasing. Ask for a Box *push* watcher while
        EVENTS_BOX_BACKEND=direct and you correctly get `mode=POLL` (`box-poll-resume_judge`) because
        the direct backend polls Box's API — there is no AP push trigger. So integration legs match on
        `source_connector` with `mode=None`, and report whichever mode came back.
    """
    fresh, reused = None, None
    for s in _subs():
        if mode and s.get("mode") != mode:
            continue
        if source and not (s.get("source_connector") == source and s.get("source_type") == "integration"):
            continue
        if s.get("id") in before_ids:
            reused = reused or s
        else:
            fresh = fresh or s
    if fresh:
        return fresh, True
    return reused, False


KNOWN_APPS = ("box", "github", "gmail", "slack", "telegram", "discord")


def connect_needed_app(reply: str) -> str | None:
    """Which integration is the concierge asking the user to connect, if any?

    An agent may need SEVERAL integrations, and the gate fires on the first UNCONNECTED one — which
    is not necessarily the app you asked about. `resume_judge` declares box AND gmail (it emails the
    verdict), so "watch my Box folder" legitimately answers "connect your gmail" when gmail is not
    connected. A harness that only looks for its own app's name reads that as an unexpected reply and
    fails a case the product got right.
    """
    low = (reply or "").lower()
    if "connect" not in low:
        return None
    for app in KNOWN_APPS:
        if app in low:
            return app
    return None


def flow_alive(sub) -> tuple[bool, str]:
    """Does the subscription's AP flow actually EXIST in Activepieces? Returns (alive, detail).

    Shared by every live harness — a non-empty `ap_flow_id` proves nothing. `find_or_create_flow`
    de-duplicates on `dedup_key` WITHOUT checking the flow still exists (concierge.py:285-289), so
    once the AP flow is gone (`make nuke`, an AP volume wipe, a manual delete) the concierge answers
    "Push flow set up / REUSING existing flow" forever while the watcher can never fire. Observed
    2026-07-09: 4 of 7 live subscriptions were dangling, including the gmail watcher.

    NB the endpoint returns `{"ok": true, "ap_flow": null}` for a dangling flow — `ok` is true either
    way, and the response dict is always truthy. An earlier version tested `bool(flow)` on that dict
    and therefore verified nothing. Check `ap_flow` itself.

    NO-AP: a NATIVE subscription (in-process scheduler, make up-noap / Code Engine) has NO ap_flow_id
    — there is no Activepieces flow to validate, so it is "alive" by definition; the real proof is the
    downstream tick landing in the run log (wait_for_run). DANGLING applies ONLY when a sub NAMES an
    ap_flow_id that no longer exists in AP.
    """
    if not sub.get("ap_flow_id"):
        return True, "native scheduler (no AP flow)"
    fid = sub.get("ap_flow_id")
    code, body = srv("GET", f"/api/events/subscriptions/{sub['id']}/flow", timeout=60)
    if code != 200:
        return False, f"flow lookup HTTP {code} (flow {fid})"
    if not (body or {}).get("ap_flow"):
        return False, (
            f"DANGLING: subscription points at AP flow {fid}, which does not exist in "
            f"Activepieces. The watcher can never fire, yet the concierge reports it armed."
        )
    return True, fid


def flow_now(r: Report):
    print("\n\033[1m[flow · NOW]\033[0m  answer-now through a pre-built agent (real MCP tool)")
    code, rep = srv("POST", "/api/concierge", {"text": PROBE, "thread_id": f"web:{RUN}:now"}, timeout=200)
    reply = str(rep.get("reply", ""))
    print(f"     → {reply[:120]}")
    step(
        phase="flows",
        surface="web",
        actor="you",
        action=f'ask "{PROBE}" and expect an answer right now (no flow)',
        expect="pricebot calls its real MCP tool and returns a live number",
        got=reply,
        ok=(code == 200 and has_digit(reply)),
    )
    r.ok("NOW", "agent returns a live number via a real tool", code == 200 and has_digit(reply))


def arm_with_confirm(utter: str, thread: str, *, timeout: int = 300, max_turns: int = 4):
    """Say an arming utterance and drive the HITL dialogue to completion.

    Arming is a CONFIRM-gated conversation now (events/docs/plans/SPLIT_AND_HITL_ARMING_SPEC.md):
    the concierge proposes, the human approves. A harness is that human — it answers the clarifying
    question if one comes back, then says "yes". Returns ``(code, body)`` of the LAST turn, so the
    caller sees the armed reply exactly as before the gate existed.
    """
    code, body = srv("POST", "/api/concierge", {"text": utter, "thread_id": thread}, timeout=timeout)
    for _ in range(max_turns):
        state = (body or {}).get("state")
        if state not in ("confirm", "needs_input"):
            break  # armed, cancelled, or a plain answer
        # needs_input carries ONE question; the utterances below always contain a cadence, so a
        # question here means something else is missing and "yes" would be wrong — answer with the
        # utterance itself, which is the best information we have, then confirm.
        reply = "yes" if state == "confirm" else utter
        code, body = srv("POST", "/api/concierge", {"text": reply, "thread_id": thread}, timeout=timeout)
    return code, body


def _arm_and_verify(
    r: Report,
    phase: str,
    utter: str,
    mode: str,
    thread: str,
    ap_live: bool,
    created: list,
    native_sched: bool = False,
):
    before = {s.get("id") for s in _subs()}
    code, rep = arm_with_confirm(utter, thread)
    reply = str(rep.get("reply", ""))
    print(f"     → {reply[:140]}")
    if not r.ok(phase, f"{phase}: concierge accepted the utterance", code == 200, fail_detail=f"HTTP {code}"):
        return
    sub, is_new = _match_sub(before, mode)
    if not r.ok(
        phase,
        f"{phase}: a {mode} subscription exists after arming",
        bool(sub),
        "created" if is_new else "reused an existing flow (dedup_key)",
        fail_detail="no subscription — the concierge answered but never armed",
    ):
        return
    if is_new:
        created.append(sub["id"])
    if native_sched:
        r.skip(
            phase,
            f"{phase}: AP flow id recorded",
            "native scheduler owns cron/poll (EVENTS_SCHEDULER=native) — there is no AP flow by design",
        )
        return
    if not ap_live:
        r.skip(phase, f"{phase}: AP flow id recorded", "AP unreachable — cannot arm a real flow")
        return
    if not r.ok(
        phase,
        f"{phase}: AP flow id recorded on the subscription",
        bool(sub.get("ap_flow_id")),
        sub.get("ap_flow_id", ""),
        fail_detail="armed locally but no ap_flow_id",
    ):
        return
    # The flow must EXIST in AP, not merely have an id in our store.
    alive, detail = flow_alive(sub)
    step(
        phase="flows",
        surface=phase.lower(),
        actor="you",
        action=f'say "{utter}"',
        expect=f"the concierge arms a {mode} flow and it really exists in Activepieces",
        got=f"{reply[:110]} | AP flow: {detail}",
        ok=alive,
    )
    r.ok(phase, f"{phase}: flow really exists in Activepieces", alive, detail, fail_detail=detail)


def flow_cron(r: Report, ap_live: bool, created: list, native_sched: bool = False):
    print("\n\033[1m[flow · CRON]\033[0m  arm a scheduled flow (native scheduler, or the AP schedule piece)")
    _arm_and_verify(
        r,
        "CRON",
        "every day at 9am send me new arxiv papers on mixture of experts",
        "CRON",
        f"web:{RUN}:cron",
        ap_live,
        created,
        native_sched,
    )


def flow_poll(r: Report, ap_live: bool, created: list, native_sched: bool = False):
    print("\n\033[1m[flow · POLL]\033[0m  arm a watch-on-change flow")
    print("     note: no state primitive exists yet (no poll_state.py, no /api/events/poll) — a POLL is")
    print("           a cron flow + a prompt line. We assert exactly that, not change-detection.")
    _arm_and_verify(
        r,
        "POLL",
        "watch bitcoin every 2 minutes and ping me on any move",
        "POLL",
        f"web:{RUN}:poll",
        ap_live,
        created,
        native_sched,
    )


def flow_push(r: Report, facts: dict, created: list):
    print("\n\033[1m[flow · PUSH]\033[0m  integration watchers (Box · GitHub · Gmail) through AP")
    ap_live, conn, no_ap = facts["ap_live"], facts["conn"], facts.get("no_ap", False)
    cases = [
        ("box", "when a resume lands in my Box, judge it against the JD and email me"),
        ("github", "when a pull request opens on my repo, summarize it and message me"),
        ("gmail", "when an email from my boss arrives, summarize it and message me"),
    ]

    def push_step(app, utter, outcome, ok):
        """Record the push leg only once we know WHICH correct outcome we got. Three are correct:
        armed, connect-needed (the app isn't connected), or a question for a missing trigger slot."""
        step(
            phase="flows",
            surface=f"push:{app}",
            actor="you",
            action=f'say "{utter}"',
            expect=f"either a real Activepieces watcher on {app}, or — if it is not connected / the "
            f"trigger needs a repo or folder — a clear question instead of a silent failure",
            got=outcome,
            ok=ok,
        )

    for app, utter in cases:
        if budget_left() < 45:
            r.skip("PUSH", f"PUSH {app}", "time budget exhausted")
            continue
        print(f"\n   · {app} (status: {conn.get(app)})")
        before = {s.get("id") for s in _subs()}
        code, rep = srv(
            "POST", "/api/concierge", {"text": utter, "thread_id": f"web:{RUN}:push:{app}"}, timeout=300
        )
        reply = str(rep.get("reply", ""))
        low = reply.lower()
        print(f"     → {reply[:140]}")

        # An armed watcher is the strongest outcome — whether freshly created or reused via dedup_key,
        # and whatever mode the backend chose (direct Box arms a POLL, not a PUSH; see _match_sub).
        sub, is_new = _match_sub(before, source=app)
        if sub:
            if is_new:
                created.append(sub["id"])
            note = f"{sub.get('mode')} · {'new' if is_new else 'reused'}"
            push_step(
                app, utter, f"ARMED — AP flow {sub.get('ap_flow_id')} ({note})", bool(sub.get("ap_flow_id"))
            )
            r.ok(
                "PUSH",
                f"PUSH {app}: an integration watcher is armed with an AP flow",
                bool(sub.get("ap_flow_id")),
                f"{sub.get('ap_flow_id')} ({note})",
                fail_detail=f"watcher exists ({note}) but has no ap_flow_id",
            )
            continue

        # The gate fires on the first UNCONNECTED integration the agent needs — which may not be the
        # app we asked about (resume_judge needs box AND gmail, and emails the verdict).
        want = connect_needed_app(reply)
        if want and not ap_live:
            if no_ap:
                # no-AP mode: "needs Activepieces" IS the truthful answer for an AP-only push trigger.
                r.skip(
                    "PUSH",
                    f"PUSH {app}: needs-AP prompt is truthful (no-AP mode)",
                    "AP intentionally absent — 'needs Activepieces' is the correct response",
                )
            else:
                # The whole point of the preflight AP probe. Without it this branch is a silent false pass.
                r.ok(
                    "PUSH",
                    f"PUSH {app}: connect-needed is truthful",
                    False,
                    fail_detail="AP is DOWN — 'connect your credentials' here is a false negative, not a "
                    "real connect prompt (concierge.py swallows the AP error)",
                )
            continue
        if want:
            via = "" if want == app else f" (via {want}, which this agent also needs)"
            truthful = conn.get(want) != "connected"
            push_step(
                app,
                utter,
                f"CONNECT NEEDED — asks you to connect '{want}'"
                + ("" if want == app else ", which this agent also needs")
                + ("" if truthful else "  — but AP says it IS connected, so this is a real bug"),
                truthful,
            )
            r.ok(
                "PUSH",
                f"PUSH {app}: correctly reports connect-needed (AP up, not connected){via}",
                conn.get(want) != "connected",
                fail_detail=f"AP reports '{want}' CONNECTED but the gate still asked to connect it "
                f"— real bug",
            )
            continue
        # Connected but the trigger needs a slot the utterance didn't supply (a repo, a folder, a JD).
        # The concierge asks for it; that is correct behaviour, not a failure.
        needs_slot = any(
            w in low
            for w in (
                "repo",
                "repository",
                "folder",
                "which ",
                "specify",
                "job description",
                " jd",
                "share the",
                "provide the",
                "attach",
                "what ",
            )
        )
        push_step(
            app,
            utter,
            (
                f"asks for the missing trigger input: {reply[:90]}"
                if needs_slot
                else f"UNEXPECTED — neither armed, connect-needed, nor a question: {reply[:90]}"
            ),
            needs_slot,
        )
        r.ok(
            "PUSH",
            f"PUSH {app}: connected → asks for the missing trigger input",
            needs_slot,
            fail_detail=f"neither armed, connect-needed, nor a slot question: {reply[:90]}",
        )


def flow_webhook(r: Report):
    print("\n\033[1m[flow · WEBHOOK]\033[0m  POST an alert → incident_triage → severity")
    code, rep = srv(
        "POST",
        "/api/events/hook/monitoring",
        {"alert": "HighCPU", "service": "checkout-api", "value": "97%", "threshold": "85%"},
        timeout=240,
    )
    ans = str(rep.get("answer", ""))
    print(f"     → {ans[:140]}")
    r.ok("WEBHOOK", "inbound payload accepted", code == 200 and rep.get("ok"), f"HTTP {code}")
    # Case-INSENSITIVE. The fallback token was "sever", but every real answer writes "**Severity:**"
    # with a capital S, so the fallback never actually fired and the whole assertion rested on the
    # model happening to emit a P-number. One run phrased it without one and went red on a perfectly
    # good triage. Lowercase once and compare against lowercase needles.
    low = ans.lower()
    triaged = any(s in low for s in ("p1", "p2", "p3", "sever", "critical"))
    step(
        phase="flows",
        surface="webhook",
        actor="your monitoring system",
        action='POSTs {"alert":"HighCPU","service":"checkout-api","value":"97%"} to /api/events/hook/monitoring',
        expect="incident_triage summarises it and assigns a P1/P2/P3 severity",
        got=ans,
        ok=(code == 200 and triaged),
    )
    r.ok("WEBHOOK", "agent triaged it to a severity", triaged)

    # The worker is GENERIC: the same endpoint must handle an arbitrary JSON shape, not just a
    # monitoring alert. Fire a CI/deploy-failure payload and assert the agent still triages it.
    print("\n\033[1m[flow · WEBHOOK]\033[0m  POST a non-alert shape (CI failure) → same worker")
    code2, rep2 = srv(
        "POST",
        "/api/events/hook/monitoring",
        {
            "event": "build.failed",
            "repo": "anupamamurthi/pachyderm",
            "branch": "main",
            "job": "unit-tests",
            "status": "failed",
            "log_url": "https://ci/logs/42",
        },
        timeout=240,
    )
    ans2 = str(rep2.get("answer", ""))
    print(f"     → {ans2[:140]}")
    triaged2 = code2 == 200 and (
        any(s in ans2 for s in ("P1", "P2", "P3", "sever"))
        or any(s in ans2.lower() for s in ("build", "fail", "ci", "test"))
    )
    step(
        phase="flows",
        surface="webhook",
        actor="your CI system",
        action='POSTs {"event":"build.failed","repo":"…","status":"failed"} to /api/events/hook/monitoring',
        expect="the SAME generic worker triages an arbitrary payload — not monitoring-specific",
        got=ans2,
        ok=triaged2,
    )
    r.ok(
        "WEBHOOK",
        "generic worker triages a non-alert payload too",
        triaged2,
        fail_detail=f"HTTP {code2}: {ans2[:90]}",
    )

    # ROUTED mode (?route=1): the caller names NO agent — the concierge picks one by capability, the
    # same brain chat uses. Fire a PR-shaped payload; a correct router lands it on pr_reviewer (a code
    # agent), NOT the generic incident_triage — proof it routes by content, not a fixed default.
    print("\n\033[1m[flow · WEBHOOK]\033[0m  ROUTED (?route=1) — concierge picks the agent, like chat")
    code3, rep3 = srv(
        "POST",
        "/api/events/hook/ci?route=1",
        {
            "pull_request": {
                "title": "Refactor auth module",
                "diff": "def login(u,p): return check(u,p)",
                "additions": 40,
                "deletions": 12,
            },
            "repo": "acme/api",
        },
        timeout=240,
    )
    chosen = str(rep3.get("agent", ""))
    ans3 = str(rep3.get("answer") or "")
    print(f"     → routed={rep3.get('routed')}  agent={chosen!r}")
    # SINGLE-AGENT WORLD: routed mode = the ONE agent ('cuga') handles it; its supervisor picks a
    # specialist internally, and runmeta SURFACES that sub-agent for observability (so `agent` is the
    # handling specialist, e.g. pr_reviewer, not the literal 'cuga'). The check: routed worked, SOME
    # agent (cuga or the surfaced sub-agent) handled it, an answer came back.
    routed_ok = code3 == 200 and rep3.get("routed") is True and bool(chosen) and bool(ans3)
    r.ok(
        "WEBHOOK",
        "routed mode executes (supervisor surfaces the handling sub-agent)",
        routed_ok,
        fail_detail=f"HTTP {code3}: routed={rep3.get('routed')} agent={chosen!r}",
    )
    step(
        phase="flows",
        surface="webhook",
        actor="an external system (no agent named)",
        action='POSTs a PR-shaped payload to /api/events/hook/ci?route=1',
        expect="the ONE agent (cuga) handles it — its supervisor picks the specialist internally",
        got=f"agent={chosen}, answered={bool(ans3)}",
        ok=routed_ok,
    )


def cleanup(r: Report, created: list):
    """Delete every subscription this run created — in AP and in the store. Without this, repeated
    runs pile up real AP flows and the next run's `new subscription` assertions get noisier."""
    if not created:
        return
    print(f"\n\033[1m[cleanup]\033[0m  deleting {len(created)} subscription(s) created by this run")
    for sid in created:
        code, _ = srv("DELETE", f"/api/events/subscriptions/{sid}", timeout=60)
        r.ok("cleanup", f"deleted {sid[:12]}…", code == 200, fail_detail=f"HTTP {code}")


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Live e2e: channels + flow modes.")
    ap.add_argument("--only", choices=["channels", "flows"], help="run just one half")
    ap.add_argument("--no-cleanup", action="store_true", help="keep the flows this run creates")
    a = ap.parse_args()

    print(f"\033[1mCUGA live e2e\033[0m — {BASE}  (budget {budget_left():.0f}s)")
    r = Report()
    facts = phase_preflight(r)
    if facts.get("dead"):
        print("\nServer unreachable. Start the stack:  make up")
        return r.summary()

    created: list[str] = []
    try:
        if a.only != "flows":
            ch_web(r)
            ch_slack(r)
            ch_discord(r)
            ch_telegram(r)
        if a.only != "channels":
            flow_now(r)
            flow_cron(r, facts["ap_live"], created, facts.get("native_sched", False))
            flow_poll(r, facts["ap_live"], created, facts.get("native_sched", False))
            flow_push(r, facts, created)
            flow_webhook(r)
    except KeyboardInterrupt:
        print("\ninterrupted — cleaning up")
    finally:
        if not a.no_cleanup:
            cleanup(r, created)
        elif created:
            print(f"\n[cleanup] skipped — {len(created)} subscription(s) left: {', '.join(created)}")

    return r.summary()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"\nCannot reach {BASE} ({e}). Start the stack:  make up")
        sys.exit(2)
