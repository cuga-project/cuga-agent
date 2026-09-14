"""LIVE Box integration e2e — upload a REAL file, then the watcher detects + judges it.

A true integration test (no mocks): uploads an actual résumé-like file to a Box folder via the Box
API (your BOX_DEV_TOKEN), calls the direct-poll endpoint, and asserts the new file is detected and
the `resume_judge` agent produces a verdict — then deletes the file to clean up.

Box dev tokens expire ~60 min; if it's stale you'll see a clear "regenerate" message.

Run:  BOX_FOLDER_ID=0 EVENTS_SERVER_URL=http://localhost:7860 GATEWAY_TOKEN=<..> \
        .venv/bin/python tests/events/live_box_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import httpx

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
API = "https://api.box.com/2.0"
UPLOAD = "https://upload.box.com/api/2.0/files/content"


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


TOKEN = _env("BOX_DEV_TOKEN") or _env("EVENTS_BOX_TOKEN")
GWTOK = _env("GATEWAY_TOKEN")
FOLDER = os.environ.get("BOX_FOLDER_ID", "0")

RESUME = (
    "Jane Doe — Senior ML Engineer\n"
    "8 years building production ML systems in Python. Led a team shipping a fraud-detection\n"
    "platform on Kubernetes; expert in PyTorch, feature stores, and low-latency inference.\n"
    "Prior: staff engineer at a fintech; MS in CS. Looking for senior/staff ML roles.\n"
)
JD = "Senior ML Engineer — 5+ yrs Python, production ML, Kubernetes, PyTorch."


def _hb():
    return {"Authorization": f"Bearer {TOKEN}"}


def main() -> int:
    if not TOKEN:
        print("no BOX_DEV_TOKEN in .env")
        return 2
    ok = True
    file_id = None

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    with httpx.Client(timeout=30) as c:
        # 1) token valid?
        r = c.get(f"{API}/users/me", headers=_hb())
        check(
            "Box token valid (whoami)",
            r.status_code == 200,
            r.json().get("login") if r.status_code == 200 else "EXPIRED — regenerate in the Box console",
        )
        if r.status_code != 200:
            return 1
        try:
            # 2) upload a REAL résumé file to the folder
            files = {"file": ("jane_doe_resume.txt", RESUME.encode(), "text/plain")}
            data = {"attributes": json.dumps({"name": "jane_doe_resume.txt", "parent": {"id": FOLDER}})}
            r = c.post(UPLOAD, headers=_hb(), data=data, files=files)
            up_ok = r.status_code in (201, 409)  # 409 = already exists (a prior run)
            check("uploaded a real résumé to Box", up_ok, f"HTTP {r.status_code}")
            if r.status_code == 201:
                file_id = r.json()["entries"][0]["id"]
            elif r.status_code == 409:  # find the existing one to clean up later
                ctx = r.json().get("context_info", {}).get("conflicts", {})
                file_id = ctx.get("id") if isinstance(ctx, dict) else None

            # 3) poll → the watcher detects it and resume_judge runs
            since = None
            req = urllib.request.Request(
                f"{SERVER}/api/events/box/poll",
                method="POST",
                data=json.dumps({"folder_id": FOLDER, "since": since, "agent": "resume_judge"}).encode(),
                headers={"Content-Type": "application/json", "X-Gateway-Token": GWTOK},
            )
            with urllib.request.urlopen(req, timeout=200) as resp:
                pr = json.load(resp)
            names = [f["name"] for f in pr.get("processed", [])]
            print("   poll processed:", names[:6])
            check(
                "poll detected the résumé + fired resume_judge",
                pr.get("ok") and any("jane_doe" in n for n in names),
            )
        finally:
            if file_id:
                d = c.delete(f"{API}/files/{file_id}", headers=_hb())
                print(f"  cleanup: delete file {file_id} → HTTP {d.status_code}")

    print(
        f"\nRESULT: {'PASS — Box integration e2e green (real file uploaded → detected → judged)' if ok else 'FAIL'}"
    )
    if ok:
        try:
            import sys as _s
            import os as _o

            _s.path.insert(0, _o.path.dirname(__file__))
            from _ledger import record as _lrec

            _lrec("box", "fire_real", "ok", "REAL upload → poller detected → judged → cleaned")
        except Exception:  # noqa: BLE001
            pass
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"cannot reach {SERVER} ({e})")
        sys.exit(2)
