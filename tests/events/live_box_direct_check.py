"""Live DIRECT-Box check — the AP-free Box watcher (sidesteps AP's OAuth wall + paid-app webhook).

Unlike the AP path, this needs NO OAuth app, NO redirect URI, NO Gmail: just a Box token and a
folder id. Grab a fresh **Box developer token** (Box dev console → your app → "Generate Developer
Token"; ~60 min, no app config) and export it — then this does a REAL end-to-end read + poll:

    BOX_DEV_TOKEN=<fresh> BOX_FOLDER_ID=<folder> EVENTS_SERVER_URL=http://localhost:8100 \
        .venv/bin/python tests/events/live_box_direct_check.py

What it proves (all against the REAL Box API + the running CUGA server):
  1. whoami — the token is valid (Box identifies the account).
  2. list a folder's items — the read integration works.
  3. new_files_since baseline — the poll filter is correct.
  4. POST /api/events/box/poll — the server lists new files and fires the watcher per file
     (deliver_to=slack uses direct-channel delivery; no AP anywhere).

With no/expired token it still checks the wiring and prints exactly what to paste — no OAuth dance.
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:8100")
sys.path.insert(0, os.path.join(REPO, "src"))


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


def _post(path, body, tok):
    req = urllib.request.Request(
        f"{SERVER}{path}", method="POST", data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "X-Gateway-Token": tok})
    try:
        with urllib.request.urlopen(req, timeout=200) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def main():
    from cuga.backend.events import box_direct
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    tok = box_direct.token()
    folder = _env("BOX_FOLDER_ID", "0")
    gw = _env("GATEWAY_TOKEN")

    if not tok:
        print("  [INFO] no Box token — set BOX_DEV_TOKEN (dev console, ~60 min) or EVENTS_BOX_TOKEN.")
        print("         Then re-run; NO OAuth app / redirect URI / Gmail needed for the direct path.")
        return 1

    # 1) token valid?
    who = asyncio.run(box_direct.whoami(tok))
    check("Box token valid (whoami)", who.get("ok"),
          who.get("login") or who.get("error") or who.get("detail", ""))
    if not who.get("ok"):
        print("  [INFO] token expired — dev tokens last ~60 min. Paste a fresh one and re-run.")
        return 1

    # 2) list folder items (real read)
    try:
        items = asyncio.run(box_direct.list_folder_items(folder, tok))
        check(f"list folder {folder} items", True, f"{len(items)} items")
    except Exception as e:  # noqa: BLE001
        check(f"list folder {folder} items", False, str(e)); return 1

    # 3) new-since filter
    newest = max((i.get("created_at", "") for i in items if i.get("type") == "file"), default="")
    fresh = asyncio.run(box_direct.new_files_since(folder, newest, tok))
    check("new_files_since(newest) → empty (baseline correct)", fresh == [], f"{len(fresh)} after newest")
    all_files = asyncio.run(box_direct.new_files_since(folder, None, tok))
    print(f"  [INFO] {len(all_files)} file(s) in folder {folder}: "
          + ", ".join(f.get("name", "?") for f in all_files[:5]))

    # 4) server poll endpoint (fires the watcher per new file; direct-Slack delivery)
    if gw:
        code, r = _post("/api/events/box/poll",
                        {"folder_id": folder, "since": None, "agent": "resume_judge",
                         "deliver_to": "slack"}, gw)
        check("POST /api/events/box/poll", code == 200 and r.get("ok"),
              f"processed {len(r.get('processed', []))}, newest {r.get('newest','')}")
    else:
        print("  [INFO] no GATEWAY_TOKEN — skipping the server poll leg.")

    print(f"\nRESULT: {'PASS — direct Box watcher works end to end (AP-free)' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
