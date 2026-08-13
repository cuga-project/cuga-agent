"""LIVE GitHub integration e2e — review a REAL pull request with the real agent (read-only).

A true integration test (no mocks): it uses the connected GitHub PAT to fetch a **real, existing**
open pull request from a public repo, pulls the PR's actual diff, feeds it to the `pr_reviewer` agent
through `/invoke`, and asserts a real summary + risk assessment comes back.

Read-only (no repo/PR creation) — works with a read-scoped fine-grained PAT and has zero side effects
on your account. What it proves: the GitHub credential reads real GitHub data, and the agent reviews
real PR content end to end (which is exactly `pr_reviewer`'s job).

Requires: GITHUB_TOKEN in .env, a running server. Optional: E2E_PR="owner/repo#123" to pin a PR.
Run:  EVENTS_SERVER_URL=http://localhost:7860 GATEWAY_TOKEN=<..> .venv/bin/python tests/events/live_github_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
GH = "https://api.github.com"
# stable, active public repos — we take the first with an open PR
CANDIDATES = ["psf/requests", "pallets/flask", "tiangolo/fastapi", "activepieces/activepieces"]


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


TOKEN, GWTOK = _env("GITHUB_TOKEN"), _env("GATEWAY_TOKEN")


def _gh(path, accept="application/vnd.github+json"):
    req = urllib.request.Request(
        GH + path, headers={"Authorization": f"Bearer {TOKEN}", "Accept": accept, "User-Agent": "cuga-e2e"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if accept.endswith("json") and raw else raw)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {}


def _invoke(agent, text):
    req = urllib.request.Request(
        f"{SERVER}/invoke",
        method="POST",
        data=json.dumps(
            {
                "agent": agent,
                "text": text,
                "deliver": False,
                "source": {"type": "integration", "name": "github", "thread_id": "gh:e2e"},
                "event": {"kind": "new_pr", "payload": {}},
            }
        ).encode(),
        headers={"Content-Type": "application/json", "X-Gateway-Token": GWTOK},
    )
    with urllib.request.urlopen(req, timeout=200) as r:
        return json.load(r).get("answer", "")


def main() -> int:
    if not TOKEN:
        print("no GITHUB_TOKEN in .env")
        return 2
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 1) the credential is live
    st, me = _gh("/user")
    check("GitHub PAT is live (GET /user)", st == 200, me.get("login") if st == 200 else me)
    if st != 200:
        return 1

    # 2) find a REAL open PR (pinned via E2E_PR, else first candidate with one)
    target = os.environ.get("E2E_PR")
    if target and "#" in target:
        rp, n = target.split("#")
        num = int(n)
    else:
        rp = num = None
        for cand in CANDIDATES:
            s, prs = _gh(f"/repos/{cand}/pulls?state=open&per_page=1")
            if s == 200 and isinstance(prs, list) and prs:
                rp, num = cand, prs[0]["number"]
                break
    check("found a real open PR to review", bool(rp and num), f"{rp}#{num}" if rp else "none found")
    if not rp:
        return 1

    # 3) fetch the PR's real title + diff (via the PAT)
    st, pr = _gh(f"/repos/{rp}/pulls/{num}")
    check("fetched the PR via the PAT", st == 200, pr.get("title", "")[:70])
    st, diff = _gh(f"/repos/{rp}/pulls/{num}", accept="application/vnd.github.v3.diff")
    diff = (diff or "")[:6000]
    check("fetched the real diff", bool(diff), f"{len(diff)} chars")

    # 4) the agent reviews the REAL PR
    text = (
        f"Review this pull request '{pr.get('title')}' on {rp}. Summarize what it changes in a "
        f"sentence or two and flag any risks.\n\nDIFF:\n{diff}"
    )
    print("  … running pr_reviewer on the real diff (≈15s)")
    answer = _invoke("pr_reviewer", text)
    print("  reviewer:", (answer or "")[:300].replace("\n", " "))
    check(
        "pr_reviewer returned a substantive review of the real PR",
        len(answer or "") > 60 and not answer.lower().startswith(("error", "i can't", "i cannot")),
    )

    print(
        f"\nRESULT: {'PASS — GitHub integration e2e green (real PR reviewed by the agent)' if ok else 'FAIL'}"
    )
    print(f"  reviewed: https://github.com/{rp}/pull/{num}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"cannot reach {SERVER} ({e})")
        sys.exit(2)
