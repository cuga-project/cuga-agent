"""LIVE identity / isolation / permissions e2e — over HTTP against a seeded server.

Proves the identity foundation (decision 0007): two users are isolated, per-agent permissions
gate what each may use, and channel account-linking binds a native id to a profile.

Prereq: server with EVENTS_ENABLED=1 EVENTS_SEED_AGENTS=1 (seeds users admin/alice/bob +
agents; market_briefer is access=[builder,admin]). Env: EVENTS_SERVER_URL, GATEWAY_TOKEN.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
TOKEN = os.environ.get("GATEWAY_TOKEN", "")


def _req(method, path, body=None, headers=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:  # noqa: BLE001
            return e.code, {"error": e.read().decode(errors="replace")[:300]}


def _me(scope):
    return _req("GET", f"/api/events/me?scope={scope}")[1]


def _concierge(text, scope):
    # act as a specific user via X-User-Id (tenant/instance default)
    c, r = _req("POST", "/api/concierge", {"text": text, "thread_id": f"web:{scope}"},
                headers={"X-User-Id": scope.split("/")[-1]})
    return json.dumps(r.get("reply", r))


def main() -> int:
    checks = []

    # 1. profiles: alice is a plain user, admin has admin role
    alice, admin = _me("default/default/alice"), _me("default/default/admin")
    checks.append(("profile: alice roles=[user]", alice.get("roles") == ["user"]))
    checks.append(("profile: admin has admin role", "admin" in (admin.get("roles") or [])))

    # 2. per-agent permissions — alice can't SEE the restricted market_briefer; admin can
    print("  concierge as alice (capabilities)…")
    cap_alice = _concierge("what can you do for me?", "default/default/alice")
    print("   alice sees:", cap_alice[:180])
    checks.append(("perms: alice does NOT see restricted market_briefer",
                   "market_briefer" not in cap_alice))
    checks.append(("perms: alice sees an open agent (pricebot/geobot/papers)",
                   any(a in cap_alice for a in ("pricebot", "geobot", "papers", "weatherbot"))))
    print("  concierge as admin (capabilities)…")
    cap_admin = _concierge("what can you do for me?", "default/default/admin")
    checks.append(("perms: admin DOES see market_briefer", "market_briefer" in cap_admin))

    # 3. channel linking — issue a token as alice, redeem it via a Telegram /start, /me shows it
    _, lk = _req("POST", "/api/events/link/telegram", {"scope": "default/default/alice"})
    token = lk.get("token")
    checks.append(("link: token issued from profile", bool(token)))
    env = {"source": {"type": "channel", "name": "telegram", "thread_id": "gw:telegram:12345"},
           "event": {"kind": "message", "payload": {}}, "text": f"/start {token}",
           "agent": "pricebot", "deliver": False}
    _, redeemed = _req("POST", "/invoke", env, headers={"X-Gateway-Token": TOKEN})
    checks.append(("link: /start <token> binds the telegram id", redeemed.get("linked") is True))
    alice2 = _me("default/default/alice")
    linked = any(l.get("channel") == "telegram" and l.get("native_id") == "12345"
                 for l in alice2.get("linked_channels", []))
    checks.append(("link: profile now shows the linked telegram id", linked))

    print("\n---")
    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{passed}/{len(checks)} checks passed")
    print("RESULT:", "PASS — identity/isolation/perms green" if passed == len(checks) else "PARTIAL/FAIL")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"Cannot reach {BASE} ({e}). Start a seeded server first.")
        sys.exit(2)
