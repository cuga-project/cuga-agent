"""Signed-webhook integration check — the inbound webhook seams against a LIVE server.

This is the repeatable version of the by-hand round-trips used to verify a deploy: it POSTs REAL
signed payloads to the deployed endpoints and asserts the signature gates behave. It exercises what
the offline unit tests cannot — that the running service, with the deployed secrets, accepts a valid
signature and rejects a bad one. Nothing here needs Activepieces.

Covers, per seam:
  • generic webhook  /api/events/hook/<name>   — no key → 401 ; correct key → 202 (ack-fast async)
  • GitHub direct    /api/events/github/events  — unsigned → 401 ; signed ping → 200 pong
  • WhatsApp direct  /api/events/whatsapp/events — wrong verify token → 403 ; correct → echoes the
                                                   challenge ; signed inbound (FAKE number) → 200

Each seam SKIPs (not fails) when its secret is not set, so this runs clean locally and in CI, and
does real work only where creds exist.

    EVENTS_SERVER_URL=https://cuga-events-svc… .venv/bin/python tests/events/live_signed_webhooks_check.py

Secrets are read from the environment or the repo .env, and MUST match the deployed secret. The
WhatsApp inbound uses a FAKE sender number, so no real WhatsApp account is ever messaged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = (os.environ.get("EVENTS_SERVER_URL") or "http://localhost:8100").rstrip("/")


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


def _http(method, url, body=None, headers=None, timeout=30):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return r.status, {"_text": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"_text": raw}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def _sig256(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def main() -> int:
    results: list[bool] = []

    def ok(name, cond, detail=""):
        results.append(bool(cond))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    def skip(name, why):
        print(f"  [SKIP] {name} — {why}")

    print(f"Signed-webhook check — server {SERVER}\n")

    # ── generic inbound webhook ────────────────────────────────────────────────
    print("generic webhook  /api/events/hook/<name>")
    st, _ = _http("POST", f"{SERVER}/api/events/hook/liveprobe", {})
    ok("no key → 401 (fails closed)", st == 401, f"got {st}")
    wk = _env("EVENTS_WEBHOOK_KEY")
    if wk:
        st, d = _http(
            "POST",
            f"{SERVER}/api/events/hook/liveprobe?key={urllib.parse.quote(wk)}&agent=incident_triage",
            {"source": "signed-webhook-check", "message": "probe — no action"},
            {"Content-Type": "application/json"},
        )
        # ack-fast async default → 202 accepted (the agent runs in the background)
        ok("correct key → 202 accepted (ack-fast async)", st == 202 and d.get("accepted") is True, f"got {st} {d}")
    else:
        skip("correct-key fire", "EVENTS_WEBHOOK_KEY not set")

    # ── GitHub direct ──────────────────────────────────────────────────────────
    print("\ngithub direct    /api/events/github/events")
    ghsec = _env("GITHUB_WEBHOOK_SECRET")
    body = json.dumps({"zen": "Keep it simple.", "hook_id": 1}).encode()
    st, _ = _http("POST", f"{SERVER}/api/events/github/events", body,
                  {"X-GitHub-Event": "ping", "Content-Type": "application/json"})
    ok("unsigned ping → 401 (fails closed)", st == 401, f"got {st}")
    if ghsec:
        st, d = _http("POST", f"{SERVER}/api/events/github/events", body,
                      {"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sig256(ghsec, body),
                       "Content-Type": "application/json"})
        ok("signed ping → 200 pong", st == 200 and d.get("pong") is True, f"got {st} {d}")
    else:
        skip("signed ping", "GITHUB_WEBHOOK_SECRET not set (empty in .env.ce; read the live secret to run)")

    # ── WhatsApp direct ────────────────────────────────────────────────────────
    print("\nwhatsapp direct  /api/events/whatsapp/events")
    verify = _env("WHATSAPP_VERIFY_TOKEN")
    appsec = _env("WHATSAPP_APP_SECRET")
    if verify:
        ch = "livecheck123"
        st, d = _http("GET", f"{SERVER}/api/events/whatsapp/events?hub.mode=subscribe"
                             f"&hub.verify_token={urllib.parse.quote(verify)}&hub.challenge={ch}")
        got = d.get("_text") if isinstance(d, dict) else str(d)
        ok("correct verify token → echoes challenge", st == 200 and got == ch, f"got {st} {got!r}")
        st, _ = _http("GET", f"{SERVER}/api/events/whatsapp/events?hub.mode=subscribe"
                             f"&hub.verify_token=WRONG&hub.challenge=x")
        ok("wrong verify token → 403", st == 403, f"got {st}")
    else:
        skip("verify handshake", "WHATSAPP_VERIFY_TOKEN not set")
    if appsec:
        # FAKE sender number → no real account is messaged; the inbound path is what we verify.
        wb = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "TEST"},
            "messages": [{"from": "15550000000", "id": "wamid.PROBE", "type": "text",
                          "text": {"body": "signed-webhook-check probe"}}]}}]}]}).encode()
        st, d = _http("POST", f"{SERVER}/api/events/whatsapp/events", wb,
                      {"X-Hub-Signature-256": _sig256(appsec, wb), "Content-Type": "application/json"})
        ok("signed inbound (fake #) → 200 accepted", st == 200 and d.get("ok") is True, f"got {st} {d}")
    else:
        skip("signed inbound", "WHATSAPP_APP_SECRET not set")

    fails = results.count(False)
    print(f"\n{'ALL PASSED' if fails == 0 else str(fails) + ' FAILED'} — {results.count(True)}/{len(results)} checks")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
