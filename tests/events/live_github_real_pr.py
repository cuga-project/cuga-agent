"""LIVE: the REAL GitHub fire — a PR actually opened on the repo fires the armed flow.

Every other GitHub harness stops one step short: live_github_triggers fires armed flows with
SYNTHETIC payloads; live_github_e2e reviews a real PR's content WITHOUT the webhook path. This one
closes the loop GitHub-side:

    arm new_pr watcher (real AP flow + real repo webhook)
      → CREATE a real branch + commit + PR on the pinned repo (authorized write)
      → GitHub emits the genuine pull_request webhook → AP tunnel → flow → /invoke {agent: cuga}
      → the supervisor picks the PR specialist → real review answer, read from the AP run
      → cleanup: close the PR, delete the branch, delete the subscription, strip repo webhooks.

SAFETY: hard-pinned to ALLOWED_REPO. The only writes are one branch + one PR (both removed) and
repo webhooks (all removed). Nothing else is ever touched.

Run:  .venv/bin/python tests/events/live_github_real_pr.py
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
ALLOWED_REPO = "anupamamurthi/pachyderm"
API = "https://api.github.com"


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO_DIR, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


TOKEN = _env("GITHUB_TOKEN")
GH = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github+json"}


def http(method: str, url: str, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            return e.code, {}


def main() -> int:
    assert ALLOWED_REPO == "anupamamurthi/pachyderm", "repo pin must never change"
    print(f"REAL GitHub fire — {ALLOWED_REPO} · {SERVER}")
    stamp = int(time.time())
    branch = f"e2e-real-fire-{stamp}"
    sub_id = pr_num = None
    ok = False
    try:
        # 1) ARM — through the concierge, like a user would
        code, rep = http("POST", f"{SERVER}/api/concierge",
                         {"text": f"when a PR is opened on {ALLOWED_REPO}, review it and "
                                  f"summarize the risk", "thread_id": f"real-fire-{stamp}"},
                         timeout=240)
        reply = str(rep.get("reply", ""))
        print(f"  arm: {reply[:110]}")
        assert code == 200 and ("ARMED" in reply or "REUSING" in reply), "arm failed"
        import re
        sub_id = re.search(r"subscription ([\w-]+)", reply).group(1)

        # give AP a beat to finish webhook registration on the repo
        time.sleep(5)
        c, hooks = http("GET", f"{API}/repos/{ALLOWED_REPO}/hooks", headers=GH)
        print(f"  repo webhooks now: {len(hooks)}")
        assert any(h for h in hooks), "no webhook registered on the repo"

        # 2) REAL PR — branch off default, one-file commit, open the PR
        _, repo = http("GET", f"{API}/repos/{ALLOWED_REPO}", headers=GH)
        default = repo.get("default_branch", "master")
        _, ref = http("GET", f"{API}/repos/{ALLOWED_REPO}/git/ref/heads/{default}", headers=GH)
        base_sha = ref["object"]["sha"]
        http("POST", f"{API}/repos/{ALLOWED_REPO}/git/refs", headers=GH,
             body={"ref": f"refs/heads/{branch}", "sha": base_sha})
        content = base64.b64encode(
            (f"# e2e real-fire probe {stamp}\n\nThis file exists only to open a real PR that "
             f"fires the armed watcher. It is deleted with the branch.\n").encode()).decode()
        http("PUT", f"{API}/repos/{ALLOWED_REPO}/contents/e2e/real-fire-{stamp}.md", headers=GH,
             body={"message": f"e2e: real-fire probe {stamp}", "content": content, "branch": branch})
        c, pr = http("POST", f"{API}/repos/{ALLOWED_REPO}/pulls", headers=GH,
                     body={"title": f"e2e: add retry/backoff notes (real-fire probe {stamp})",
                           "head": branch, "base": default,
                           "body": "Probe PR for the real-webhook fire test. Auto-closed."})
        pr_num = pr.get("number")
        print(f"  REAL PR opened: #{pr_num}  {pr.get('html_url')}")
        assert c == 201 and pr_num, f"PR create failed HTTP {c}: {pr}"

        # 3) WAIT for the genuine webhook → AP run → answer
        print("  waiting for GitHub → AP tunnel → flow run …")
        answer = ""
        deadline = time.time() + 360
        while time.time() < deadline and not answer:
            time.sleep(10)
            _, runs = http("GET", f"{SERVER}/api/events/runs", timeout=60)
            for r in (runs.get("runs") or []):
                if r.get("subscription_id") == sub_id and r.get("status") == "SUCCEEDED":
                    _, det = http("GET", f"{SERVER}/api/events/runs/{r['id']}", timeout=60)
                    answer = str(det.get("answer") or "")
                    break
        assert answer, "no SUCCEEDED run for the subscription within 6 min"
        print(f"  ✓ FIRED on the real webhook → answer: {answer[:140]!r}")
        ok = True
    finally:
        # 4) CLEANUP — PR closed, branch gone, subscription + repo webhooks removed
        if pr_num:
            http("PATCH", f"{API}/repos/{ALLOWED_REPO}/pulls/{pr_num}", headers=GH,
                 body={"state": "closed"})
        http("DELETE", f"{API}/repos/{ALLOWED_REPO}/git/refs/heads/{branch}", headers=GH)
        if sub_id:
            http("DELETE", f"{SERVER}/api/events/subscriptions/{sub_id}", timeout=60)
        _, hooks = http("GET", f"{API}/repos/{ALLOWED_REPO}/hooks", headers=GH)
        for h in hooks if isinstance(hooks, list) else []:
            http("DELETE", f"{API}/repos/{ALLOWED_REPO}/hooks/{h['id']}", headers=GH)
        _, hooks2 = http("GET", f"{API}/repos/{ALLOWED_REPO}/hooks", headers=GH)
        _, prs = http("GET", f"{API}/repos/{ALLOWED_REPO}/pulls?state=open", headers=GH)
        print(f"  cleanup: PR closed · branch deleted · webhooks left: "
              f"{len(hooks2) if isinstance(hooks2, list) else '?'} · open PRs: "
              f"{len(prs) if isinstance(prs, list) else '?'}")
    print(f"\nRESULT: {'PASS — a REAL GitHub event fired the flow end-to-end' if ok else 'FAIL'}")
    if ok:
        try:
            import sys as _s
            _s.path.insert(0, os.path.dirname(__file__))
            from _ledger import record as _lrec
            _lrec("github", "fire_real", "ok",
                  "REAL PR opened on the pinned repo → genuine webhook fired the flow → cleaned")
        except Exception:  # noqa: BLE001
            pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
