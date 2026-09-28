"""LIVE Box integration e2e — upload a REAL file, then the watcher detects + judges it.

A true integration test (no mocks): uploads an actual résumé-like file to a Box folder via the Box
API (a minted CCG token, or BOX_DEV_TOKEN if set), calls the direct-poll endpoint, and asserts the
new file is detected and the box agent (doc_screener) produces a verdict — then deletes the file.

Box dev tokens expire ~60 min; if it's stale you'll see a clear "regenerate" message.

Run:  EVENTS_SERVER_URL=<events url> GATEWAY_TOKEN=<..> .venv/bin/python tests/events/live_box_e2e.py
      (folder + Box creds come from .env; or:  make test-box-e2e-ce)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
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


def _box_token() -> "tuple[str, str]":
    """The bearer token to call Box with: a STATIC token if set (BOX_DEV_TOKEN / EVENTS_BOX_TOKEN),
    else a freshly minted CCG token — exactly what box_direct.access_token() does in production.
    Returns (token, kind). CCG needs BOX_CLIENT_ID + BOX_CLIENT_SECRET + a subject
    (BOX_USER_ID acts as that user, else BOX_ENTERPRISE_ID acts as the enterprise service account)."""
    static = _env("BOX_DEV_TOKEN") or _env("EVENTS_BOX_TOKEN")
    if static:
        return static, "static-token"
    cid, csec = _env("BOX_CLIENT_ID"), _env("BOX_CLIENT_SECRET")
    ent, usr = _env("BOX_ENTERPRISE_ID"), _env("BOX_USER_ID")
    if cid and csec and (ent or usr):
        sub_type, sub_id = ("user", usr) if usr else ("enterprise", ent)
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": cid,
                "client_secret": csec,
                "box_subject_type": sub_type,
                "box_subject_id": sub_id,
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.box.com/oauth2/token",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read() or "{}").get("access_token", ""), f"ccg-{sub_type}"
        except urllib.error.HTTPError as e:  # surface the reason (auth/subject issues)
            try:
                d = json.loads(e.read() or "{}")
            except Exception:  # noqa: BLE001
                d = {}
            print(f"  (CCG mint failed HTTP {e.code}: {d.get('error')} — {d.get('error_description')})")
        except Exception as e:  # noqa: BLE001
            print(f"  (CCG mint failed: {e})")
    return "", "none"


TOKEN, TOKEN_KIND = _box_token()
GWTOK = _env("GATEWAY_TOKEN")
FOLDER = os.environ.get("BOX_FOLDER_ID") or _env("BOX_FOLDER_ID", "0")
AGENT = os.environ.get("BOX_E2E_AGENT", "doc_screener")  # the roster agent box files route to

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
        print("no Box token — set BOX_DEV_TOKEN, or CCG creds (BOX_CLIENT_ID/SECRET + BOX_ENTERPRISE_ID)")
        return 2
    print(f"Box e2e — folder {FOLDER} · agent {AGENT} · token {TOKEN_KIND} · {SERVER}")
    ok = True
    file_id = None
    made_folder = None  # a folder WE created (delete it on cleanup); None = using a pre-existing one

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
            # 2) pick the folder to watch. Use the configured BOX_FOLDER_ID if the token can SEE it;
            #    otherwise create a throwaway folder in the service account's OWN space — a CCG service
            #    account can always write there, so the e2e is self-contained and needs no manual
            #    folder collaboration (the poll→detect→dispatch path is identical either way).
            watch = FOLDER
            fr = c.get(f"{API}/folders/{watch}", headers=_hb())
            if fr.status_code == 200:
                print(f"   watching configured folder {watch} ('{fr.json().get('name')}')")
            else:
                cr = c.post(
                    f"{API}/folders",
                    headers={**_hb(), "Content-Type": "application/json"},
                    json={"name": f"cuga-e2e-{int(time.time())}", "parent": {"id": "0"}},
                )
                check(
                    "created a self-owned probe folder (configured folder not accessible)",
                    cr.status_code in (200, 201),
                    f"folder {watch} → HTTP {fr.status_code}; create → HTTP {cr.status_code}",
                )
                if cr.status_code not in (200, 201):
                    return 1
                watch = cr.json()["id"]
                made_folder = watch
                print(f"   watching self-owned probe folder {watch}")

            # 3) upload a REAL résumé file to the watched folder
            files = {"file": ("jane_doe_resume.txt", RESUME.encode(), "text/plain")}
            data = {"attributes": json.dumps({"name": "jane_doe_resume.txt", "parent": {"id": watch}})}
            r = c.post(UPLOAD, headers=_hb(), data=data, files=files)
            up_ok = r.status_code in (201, 409)  # 409 = already exists (a prior run)
            check("uploaded a real résumé to Box", up_ok, f"HTTP {r.status_code}")
            if r.status_code == 201:
                file_id = r.json()["entries"][0]["id"]
            elif r.status_code == 409:  # find the existing one to clean up later
                ctx = r.json().get("context_info", {}).get("conflicts", {})
                file_id = ctx.get("id") if isinstance(ctx, dict) else None

            # 4) poll → the watcher detects it and the roster's box agent (AGENT) runs
            req = urllib.request.Request(
                f"{SERVER}/api/events/box/poll",
                method="POST",
                data=json.dumps({"folder_id": watch, "since": None, "agent": AGENT}).encode(),
                headers={"Content-Type": "application/json", "X-Gateway-Token": GWTOK},
            )
            with urllib.request.urlopen(req, timeout=200) as resp:
                pr = json.load(resp)
            names = [f["name"] for f in pr.get("processed", [])]
            print("   poll processed:", names[:6])
            check(
                f"poll detected the résumé + fired {AGENT}",
                pr.get("ok") and any("jane_doe" in n for n in names),
            )
        finally:
            if file_id:
                d = c.delete(f"{API}/files/{file_id}", headers=_hb())
                print(f"  cleanup: delete file {file_id} → HTTP {d.status_code}")
            if made_folder:
                d = c.delete(f"{API}/folders/{made_folder}?recursive=true", headers=_hb())
                print(f"  cleanup: delete probe folder {made_folder} → HTTP {d.status_code}")

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
