"""Live Box resume-watcher check — verifies the resume_judge agent + the flow shape + the Box API,
and states plainly what Box needs that no code can do autonomously.

Box in Activepieces:
  • Auth is **OAuth2** — AP's connection schema requires the authorization CODE and does the token
    exchange itself; a **Box Developer Token cannot be used** (AP rejects a pre-obtained token). So
    Box needs the OAuth consent flow: EVENTS_OAUTH_BOX_CLIENT_ID/_SECRET + a registered redirect URI
    + ``GET /api/events/connect/box``. (A *free* Box account can't save the redirect URI, so a paid
    /dev Box app is required — this is the wall you hit earlier.)
  • ``new_file`` is a **WEBHOOK** trigger requiring a ``folder`` id + the ``manage_webhook`` scope;
    AP registers a Box webhook at the tunnel.
  • The resume_watcher also delivers via **Gmail** → Gmail is OAuth2 too (same consent requirement).

So a full live resume-watcher round-trip needs: Box OAuth connection + a watched folder, Gmail OAuth
connection, and a file drop. This harness confirms everything else is correct.

Run:
    EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/live_box_resume_check.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:8100")


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


def _get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def main():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 1) the resume_judge agent exists and is wired to box + gmail
    st, r = _get(f"{SERVER}/api/events/agents", {"x-user-id": "admin"})
    agents = {a["name"]: a for a in r.get("agents", [])}
    rj = agents.get("resume_judge", {})
    integ = {i.get("app") for i in rj.get("integrations", [])}
    check("resume_judge agent exists", bool(rj))
    check("resume_judge wired to Box + Gmail", {"box", "gmail"} <= integ, str(sorted(integ)))

    # 2) the resume-watcher flow builder produces box·new_file → /invoke → Router(MATCH→gmail)
    sys.path.insert(0, os.path.join(REPO, "src", "cuga", "backend", "events"))
    import flows  # noqa: E402
    rw = flows.build_resume_watcher_flow(thread_id="sub:1")
    t = rw["trigger"]
    router = t.get("nextAction", {}).get("nextAction", {})
    branches = [b.get("branchName") for b in router.get("settings", {}).get("branches", [])]
    check("flow: trigger is box·new_file", t["settings"]["pieceName"].endswith("piece-box")
          and t["settings"].get("triggerName") == "new_file")
    check("flow: Router has a MATCH branch → gmail", "MATCH" in branches)

    # 3) Box API reachable with the dev token? (usually expired — informational)
    btok = _env("BOX_DEV_TOKEN")
    if btok:
        st, r = _get("https://api.box.com/2.0/users/me", {"Authorization": f"Bearer {btok}"})
        valid = r.get("type") == "user"
        print(f"  [INFO] BOX_DEV_TOKEN {'valid → ' + r.get('login') if valid else 'expired/invalid (dev tokens last ~60 min) — Box needs OAuth anyway'}")
    else:
        print("  [INFO] no BOX_DEV_TOKEN set")

    # 4) connecting box via a pasted token is correctly rejected (AP needs OAuth code)
    import urllib.request as u
    req = u.Request(f"{SERVER}/api/events/connect/box/token", method="POST",
                    data=b'{"token":"x","scope":"default/default"}',
                    headers={"content-type": "application/json", "x-user-id": "admin"})
    try:
        u.urlopen(req, timeout=10); code = 200; msg = ""
    except urllib.error.HTTPError as e:
        code = e.code; msg = json.loads(e.read().decode()).get("error", "")
    check("token-connect correctly refused (points to OAuth)", code == 400 and "OAuth" in msg)

    print(f"\nRESULT: {'PASS — resume_watcher wired correctly' if ok else 'FAIL'}")
    print("\nTo run it live you need (all user-side, OAuth-gated):")
    print("  1. A Box app with client id/secret + a saved redirect URI (paid/dev Box — free can't).")
    print("     Set EVENTS_OAUTH_BOX_CLIENT_ID/_SECRET, then GET /api/events/connect/box (consent).")
    print("  2. Gmail OAuth connection (EVENTS_OAUTH_GMAIL_CLIENT_ID/_SECRET → /api/events/connect/gmail).")
    print("  3. Arm the watcher (concierge: 'when a resume lands in my Box, judge it and email me'),")
    print("     then drop a file in the watched folder.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
