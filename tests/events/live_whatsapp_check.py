"""Live WhatsApp check — the Meta Cloud API channel (direct; AP's piece cannot receive).

Proves every leg that does not need a human, and — unusually for a channel harness — it checks the
THREE-PART webhook wiring, because two of the three fail silently:

  1. the callback URL answers Meta's verification handshake (and REFUSES a wrong token)
  2. the app-level field subscription includes ``messages``
  3. **the app is subscribed to the WABA** — the step Meta's guided onboarding usually does for you.
     When it hasn't, the console's own *Send message* button still works (it runs through Meta's
     ``WA DevX Webhook Events 1P App``), so everything looks wired while your callback gets nothing.
     That cost an evening; it is checked here so it never does again.

The one leg no code can drive is a human messaging the number for the full inbound round trip.

Run:
    EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/live_whatsapp_check.py
    # optional, to prove the delivery leg end-to-end (must be on the test number's allow-list):
    WHATSAPP_TEST_TO=<your number, digits only> .venv/bin/python tests/events/live_whatsapp_check.py
    # optional, to check the WABA subscription (read it off the API Setup page):
    WHATSAPP_WABA_ID=<waba id> .venv/bin/python tests/events/live_whatsapp_check.py

Reads .env for WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN.
Secrets are never printed.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:8100").rstrip("/")
GRAPH = "https://graph.facebook.com/" + os.environ.get("WHATSAPP_API_VERSION", "v23.0")


def _env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


def _http(method, url, body=None, headers=None, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
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


def _graph(path, token, method="GET"):
    return _http(method, f"{GRAPH}/{path}", None, {"Authorization": f"Bearer {token}"})


def main():
    tok = _env("WHATSAPP_TOKEN")
    pnid = _env("WHATSAPP_PHONE_NUMBER_ID")
    verify = _env("WHATSAPP_VERIFY_TOKEN")
    secret = _env("WHATSAPP_APP_SECRET")
    if not tok or not pnid:
        print("SKIP — no WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID in .env")
        return 0

    results = []

    def ok(name, cond, detail=""):
        results.append(bool(cond))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    print(f"WhatsApp check — server {SERVER}\n")

    # ── credentials ──────────────────────────────────────────────────────────
    st, d = _graph(f"debug_token?input_token={urllib.parse.quote(tok)}", tok)
    info = (d or {}).get("data") or {}
    expires = info.get("expires_at")
    ok("token is valid", st == 200 and info.get("is_valid"), f"type={info.get('type')}")
    # A 24-hour dev token is THE recurring footgun: it works all afternoon and dies overnight,
    # resurfacing as an unexplained 401. A System User token reports expires_at = 0.
    ok(
        "token does not expire (System User, not the 24h dev token)",
        expires == 0,
        "expires_at=0" if expires == 0 else f"expires_at={expires} — this token WILL die",
    )
    scopes = set(info.get("scopes") or [])
    need = {"whatsapp_business_messaging"}
    ok("token has whatsapp_business_messaging", need <= scopes, ", ".join(sorted(need - scopes)) or "ok")

    # ── the number ───────────────────────────────────────────────────────────
    st, d = _graph(f"{pnid}?fields=display_phone_number,verified_name,quality_rating", tok)
    ok(
        "phone number id resolves",
        st == 200,
        f"{d.get('display_phone_number')} ({d.get('verified_name')}) quality={d.get('quality_rating')}"
        if st == 200
        else str((d.get("error") or {}).get("message"))[:90],
    )
    if st != 200 and str(pnid).isdigit() and len(str(pnid)) <= 13:
        print("         ↑ that value looks like a phone NUMBER; the API wants the phone number ID")

    # ── webhook leg 1: the callback answers, and refuses a wrong token ────────
    url = f"{SERVER}/api/events/whatsapp/events"
    st, _ = _http("GET", f"{url}?hub.mode=subscribe&hub.verify_token=deliberately-wrong&hub.challenge=x")
    ok("callback REFUSES a wrong verify token", st == 403, f"HTTP {st}")
    st, d = _http(
        "GET",
        f"{url}?hub.mode=subscribe&hub.verify_token={urllib.parse.quote(verify)}&hub.challenge=probe12345",
    )
    body = d.get("_text") if isinstance(d, dict) else ""
    ok("callback echoes the challenge as plain text", st == 200 and body == "probe12345", f"HTTP {st}")
    ok("app secret is configured (inbound is verified)", bool(secret), "" if secret else "NOT SET")

    # ── webhook leg 2: the app-level field subscription ───────────────────────
    app_id = info.get("app_id")
    if app_id and secret:
        st, d = _http("GET", f"{GRAPH}/{app_id}/subscriptions?access_token={app_id}|{secret}")
        rows = (d or {}).get("data") or []
        fields, cb = set(), ""
        for r in rows:
            if r.get("object") == "whatsapp_business_account":
                fields = {f.get("name") for f in (r.get("fields") or [])}
                cb = r.get("callback_url") or ""
        ok("app subscribes the `messages` field", "messages" in fields, ", ".join(sorted(fields))[:80])
        ok("callback_url matches this server", cb.startswith(SERVER), cb or "(none registered)")
    else:
        print("  [INFO] no app secret — skipping the app-level subscription check")

    # ── webhook leg 3: the app is subscribed to the WABA ──────────────────────
    waba = _env("WHATSAPP_WABA_ID")
    if waba:
        st, d = _graph(f"{waba}/subscribed_apps", tok)
        apps = (d or {}).get("data") or []
        names = []
        mine = False
        for a in apps:
            w = a.get("whatsapp_business_api_data") or {}
            names.append(str(w.get("name") or w.get("id")))
            if app_id and str(w.get("id")) == str(app_id):
                mine = True
        ok(
            "YOUR app is subscribed to the WABA",
            mine,
            "subscribed: "
            + (", ".join(names) or "none")
            + ("  ← only Meta's 1P app: run POST /<WABA>/subscribed_apps" if apps and not mine else ""),
        )
    else:
        print("  [INFO] set WHATSAPP_WABA_ID=<waba id> to check the app→WABA subscription")
        print("         (the step that fails SILENTLY — see events_docs/setup/WHATSAPP.md)")

    # ── the descriptor + delivery leg ─────────────────────────────────────────
    st, d = _http("GET", f"{SERVER}/api/events/channels")
    chans = {c.get("name"): c for c in (d.get("channels") or [])}
    ok("whatsapp is registered as a channel", "whatsapp" in chans, str(list(chans))[:70])
    ok(
        "whatsapp backend is direct",
        (chans.get("whatsapp") or {}).get("backend") == "direct",
        (chans.get("whatsapp") or {}).get("backend", "?"),
    )

    to = _env("WHATSAPP_TEST_TO")
    if to:
        st, d = _http(
            "POST",
            f"{GRAPH}/{pnid}/messages",
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": "CUGA live check — you can ignore this."},
            },
            {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        )
        sent = st == 200 and bool(d.get("messages"))
        ok("delivery leg (a real send)", sent, str((d.get("error") or {}).get("message", ""))[:90])
        if not sent and "24" in str((d.get("error") or {}).get("message", "")):
            print("         ↑ outside the 24-hour window — message the number first, or use a template")
    else:
        print("  [INFO] set WHATSAPP_TEST_TO=<allow-listed number> to prove the delivery leg")

    good = all(results)
    print(f"\nRESULT: {'PASS — WhatsApp credentials + webhook wiring confirmed' if good else 'PARTIAL/FAIL'}")
    print(
        "  The inbound round trip needs a human: message the number and watch for\n"
        "  `whatsapp.direct messages=1` → `whatsapp.ask` → `whatsapp.reply` in the logs."
    )
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
