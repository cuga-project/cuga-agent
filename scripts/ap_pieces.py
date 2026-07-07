#!/usr/bin/env python3
"""ap_pieces.py — guarantee the Activepieces pieces this project depends on are INSTALLED.

Why this exists: AP CE (0.82) syncs its piece catalog from cloud.activepieces.com at boot
(PIECES_SYNC_MODE=OFFICIAL_AUTO). On a FRESH DB (after `make nuke`/`make fresh`) that first sync
can race or fail if the network is briefly down (laptop asleep / VPN off), leaving the piece table
empty or half-populated (e.g. 28 pieces). Then creating any integration connection fails with
`ENTITY_NOT_FOUND piece_metadata_not_found pieceName=@activepieces/piece-gmail` — the exact symptom.

How it works (and why): that first sync DELIVERS every piece — it just takes ~3-4 min and saturates
cloud.activepieces.com meanwhile, so racing it with our own cloud calls only gets us rate-limited.
So this script WAITS for the sync to deliver the needed pieces (returning the instant they're all
present), and only if the sync genuinely stalls does it install them directly — using PINNED versions
so the fallback never depends on the flaky cloud version API. Idempotent; acts only on what's missing.

Usage:
  python3 scripts/ap_pieces.py            # ensure: wait for sync, install missing as fallback
  python3 scripts/ap_pieces.py --status   # report only, no wait/changes (exit 1 if any missing)

Env: AP_BASE_URL / AP_EMAIL / AP_PASSWORD from .env. EVENTS_PIECES_WAIT overrides the wait (default
300s; set 0 to skip the wait and install immediately).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

# The pieces the events layer actually uses (see flows.SOURCE_TRIGGER + AP-backed channels).
# slack/discord are DIRECT backends (not AP), so they are intentionally not here.
NEEDED = [
    "@activepieces/piece-gmail",         # gmail push (new-email) + send
    "@activepieces/piece-github",        # github PR / issue push
    "@activepieces/piece-box",           # box new-file push (OAuth path)
    "@activepieces/piece-telegram-bot",  # telegram channel (AP webhook)
    "@activepieces/piece-schedule",      # CRON / POLL flows
]
CLOUD = "https://cloud.activepieces.com/api/v1/pieces"

# Pinned known-good versions for the pinned AP image (0.82). Used as the install version so the
# fallback NEVER depends on the flaky cloud version API. Refresh these if you bump the AP image
# (get current values from: curl -s https://cloud.activepieces.com/api/v1/pieces | jq).
PINNED = {
    "@activepieces/piece-gmail": "0.12.7",
    "@activepieces/piece-github": "0.8.3",
    "@activepieces/piece-box": "0.1.5",
    "@activepieces/piece-telegram-bot": "0.6.4",
    "@activepieces/piece-schedule": "0.1.17",
}


def _env(key, default=""):
    try:
        for line in open(".env"):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].split(" #")[0].strip().strip('"')
    except FileNotFoundError:
        pass
    return os.environ.get(key, default)


def _req(url, method="GET", body=None, token=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _count(base):
    """Best-effort catalog size for display. The list endpoint is filtered/paginated — do NOT use it
    to decide whether a specific piece is installed (it omits installed pieces). Returns None if down."""
    st, d = _req(f"{base}/api/v1/pieces", timeout=15)
    return len(d) if st == 200 and isinstance(d, list) else None


def _present(base, name):
    """Authoritative usability check: the per-piece metadata endpoint the Connect path resolves
    against (the one that 404s with piece_metadata_not_found when a piece is missing)."""
    st, _ = _req(f"{base}/api/v1/pieces/{name}", timeout=15)
    return st == 200


def main():
    status_only = "--status" in sys.argv
    base = _env("AP_BASE_URL", "http://localhost:8081").rstrip("/")

    count = _count(base)
    if count is None:
        print(f"✗ AP not reachable at {base} (is it up? `make ap`)")
        return 2
    missing = [p for p in NEEDED if not _present(base, p)]

    def short(n):
        return n.split("piece-")[-1]

    print(f"AP {base} — {count} pieces in catalog")
    for p in NEEDED:
        print(f"  {short(p):14s} {'✓' if p not in missing else '✗ MISSING'}")

    if not missing:
        print("✓ all needed pieces present")
        return 0
    if status_only:
        print(f"✗ {len(missing)} missing — run `make ap-pieces`")
        print("  (until installed, .env-token integrations like github/telegram can't auto-connect)")
        return 1

    # ROOT CAUSE this handles: a FRESH AP runs an OFFICIAL_AUTO catalog sync from cloud.activepieces.com
    # that takes ~3-4 min to deliver all pieces AND saturates that host meanwhile — so an integration
    # Connect 404s until it lands, and racing it by fetching versions from that same cloud just gets us
    # rate-limited. Two independent paths make the pieces resolve; we use BOTH and take whichever wins:
    #   1) install the needed pieces directly with PINNED versions (the package is pulled from npm — a
    #      DIFFERENT host — and PINNED means no cloud version call, so this works even mid-sync), and
    #   2) the OFFICIAL_AUTO sync, delivering the same pieces in the background.
    # Then poll until all present. Common case resolves in seconds; the sync is the backstop.
    email, pw = _env("AP_EMAIL"), _env("AP_PASSWORD")
    st, auth = _req(f"{base}/api/v1/authentication/sign-in", "POST", {"email": email, "password": pw})
    token = auth.get("token") if isinstance(auth, dict) else None
    if not token:
        print(f"✗ AP sign-in failed (HTTP {st}). Check AP_EMAIL/AP_PASSWORD in .env.")
        return 2

    print(f"installing {[short(p) for p in missing]} (pinned; the catalog sync also delivers them)…")
    for p in missing:
        ver = PINNED.get(p)
        if not ver:
            print(f"  {short(p)}: ✗ no pinned version — add it to PINNED in scripts/ap_pieces.py")
            continue
        st, res = _req(f"{base}/api/v1/pieces", "POST",
                       {"pieceName": p, "pieceVersion": ver,
                        "packageType": "REGISTRY", "scope": "PLATFORM"}, token=token)
        print(f"  install {short(p)} @ {ver}: HTTP {st} {'OK' if st in (200, 201) else str(res)[:100]}")

    # Poll until all resolve — the install may settle asynchronously, and the sync backstops anything
    # that didn't take. A fresh-DB sync can take ~3-4 min, so wait generously (override: EVENTS_PIECES_WAIT).
    wait_secs = int(_env("EVENTS_PIECES_WAIT", "300") or "300")
    deadline = time.time() + max(wait_secs, 20)
    while time.time() < deadline:
        still = [p for p in NEEDED if not _present(base, p)]
        if not still:
            print(f"✓ all needed pieces present ({_count(base)} in catalog)")
            return 0
        time.sleep(10)
        print(f"  … {len(NEEDED) - len(still)}/{len(NEEDED)} present; waiting on {[short(p) for p in still]}")

    still = [short(p) for p in NEEDED if not _present(base, p)]
    print(f"✗ still missing: {still} — network may be down; re-run `make ap-pieces`")
    return 1


if __name__ == "__main__":
    sys.exit(main())
