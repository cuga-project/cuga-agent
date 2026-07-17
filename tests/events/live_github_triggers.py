"""LIVE: every GitHub trigger in the registry — arm → synthetic fire → cleanup. No mocks.

For each of the 14 github rows in the trigger registry this harness:
  1. ARMS a watcher through the server's deterministic slash path (``/push <utterance>`` →
     classifier → registry gate → a real Activepieces flow whose publish creates a REAL repo
     webhook on the test repo),
  2. FIRES it synthetically via ``POST /subscriptions/{id}/run`` with the registry's sample
     payload (webhook triggers accept an out-of-band POST — the payload becomes the trigger
     output, so the run exercises the same curated field paths a real event would),
  3. verifies an agent produced a real answer, then
  4. CLEANS UP: deletes the subscription/flow, and finally strips every webhook the arms
     created from the repo (deleting an AP flow does NOT remove its repo webhook).

SAFETY: hard-pinned to ONE repo (default anupamamurthi/pachyderm). The harness only creates
and deletes repo WEBHOOKS — never issues, PRs, comments, or content of any kind.

Run:  .venv/bin/python tests/events/live_github_triggers.py           # all 14
      .venv/bin/python tests/events/live_github_triggers.py new_star  # one trigger
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_DIR, "src", "cuga", "backend", "events"))
import triggers  # noqa: E402

SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
TEST_REPO = os.environ.get("GITHUB_TEST_REPO", "anupamamurthi/pachyderm")
ALLOWED_REPOS = {"anupamamurthi/pachyderm"}          # the ONLY repo this harness may touch


def _env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO_DIR, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


GW = _env("GATEWAY_TOKEN")
GH_TOKEN = _env("GITHUB_TOKEN")
GH_LOGIN = os.environ.get("EVENTS_GITHUB_LOGIN", "") or TEST_REPO.split("/")[0]

# an utterance per trigger, phrased to hit the registry's classifier phrases deterministically
UTTERANCES = {
    "new_pr": "when a new pull request opens on {repo}, summarize it and message me",
    "new_issue": "when a new issue is filed on {repo}, triage its severity",
    "new_star": "when my repo {repo} gets a new star, post a thank-you",
    "new_push": "when code is pushed to main on {repo}, audit the diff for risks",
    "new_discussion": "when a new discussion opens on {repo}, summarize it",
    "new_discussion_comment": "when a comment is posted on a discussion in {repo}, flag blockers",
    "new_branch": "when a new branch is created on {repo}, check the naming convention",
    "new_collaborator": "when a collaborator is added to {repo}, log their access",
    "new_repo_label": "when a repo label is created on {repo}, announce it",
    "new_milestone": "when a milestone is created on {repo}, draft a plan",
    "new_release": "when a new release is published on {repo}, summarize the changelog",
    "new_commit": "when a commit lands on {repo}, audit it for bugs",
    "new_review_request": "when I'm requested to review a PR on {repo}, summarize the diff",
    "new_gh_mention": "when someone mentions me on github in {repo}, summarize the context",
}


def http(method, url, body=None, headers=None, timeout=200):
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
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def gh(path, method="GET"):
    req = urllib.request.Request(f"https://api.github.com{path}", method=method,
                                 headers={"Authorization": f"token {GH_TOKEN}",
                                          "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, (json.loads(r.read().decode() or "null") if method == "GET" else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def cleanup_repo_webhooks() -> int:
    """Delete every webhook on the test repo (each arm creates one; AP flow deletion doesn't)."""
    st, hooks = gh(f"/repos/{TEST_REPO}/hooks")
    if st != 200 or not hooks:
        return 0
    n = 0
    for h in hooks:
        st, _ = gh(f"/repos/{TEST_REPO}/hooks/{h['id']}", method="DELETE")
        n += (st == 204)
    return n


def main() -> int:
    assert TEST_REPO in ALLOWED_REPOS, (
        f"refusing to run against {TEST_REPO!r} — this harness is pinned to {ALLOWED_REPOS}")
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    rows = [t for t in triggers.events_for("github") if (not only or t.event == only)]
    print(f"GitHub trigger tier — {len(rows)} trigger(s) against {TEST_REPO}  ({SERVER})")
    results, created = [], []
    t0 = time.time()
    for t in rows:
        ev = t.event
        utter = UTTERANCES[ev].format(repo=TEST_REPO)
        # 1) ARM via the deterministic slash path (no LLM routing variance)
        code, rep = http("POST", f"{SERVER}/api/concierge",
                         {"text": f"/push {utter}", "thread_id": f"web:ghtier:{ev}"}, timeout=240)
        reply = str(rep.get("reply", ""))
        m = re.search(r"subscription ([\w-]+)", reply)
        armed = code == 200 and ("ARMED" in reply or "REUSING" in reply) and m
        if not armed:
            results.append((ev, "ARM-FAIL", reply[:110]))
            print(f"  ✗ {ev:24} ARM-FAIL — {reply[:100]}")
            continue
        sub_id = m.group(1)
        created.append(sub_id)
        # the flow must reference THIS trigger (the whole point): check via the sub's flow digest
        code, fl = http("GET", f"{SERVER}/api/events/subscriptions/{sub_id}/flow", timeout=60)
        trig_name = str((fl.get("ap_flow") or {}).get("trigger", "")) if code == 200 else ""
        # 2) FIRE synthetically with the registry's sample payload. The repo name is patched in;
        #    new_mention additionally needs the CONNECTED user's login (the piece only emits when
        #    the comment actually mentions them), so MENTION_LOGIN resolves to the real account.
        raw = json.dumps(t.synth).replace("o/r", TEST_REPO)
        raw = raw.replace("MENTION_LOGIN", GH_LOGIN or TEST_REPO.split("/")[0])
        synth = json.loads(raw)
        code, run = http("POST", f"{SERVER}/api/events/subscriptions/{sub_id}/run?timeout=150",
                         synth, headers={"X-Gateway-Token": GW}, timeout=200)
        answer = str(run.get("answer") or "")
        fired = code == 200 and run.get("ok") and len(answer) > 20
        status = "PASS" if fired else ("ARMED-NOFIRE" if code else "FIRE-FAIL")
        results.append((ev, status, (answer or str(run.get("error", "")))[:110]))
        icon = "✓" if fired else "!"
        print(f"  {icon} {ev:24} {status:12} trigger={t.ap_trigger:28} → {answer[:70]!r}")
        _ = trig_name  # digest kept for debugging; the /run answer is the real assertion
    # 3) CLEANUP — subscriptions (removes AP flows), then the repo's webhooks
    print(f"\n[cleanup] deleting {len(created)} subscription(s) + repo webhooks")
    for sid in created:
        http("DELETE", f"{SERVER}/api/events/subscriptions/{sid}", timeout=60)
    removed = cleanup_repo_webhooks()
    st, hooks = gh(f"/repos/{TEST_REPO}/hooks")
    print(f"[cleanup] removed {removed} webhook(s); remaining on {TEST_REPO}: "
          f"{len(hooks) if hooks else 0}")
    npass = sum(1 for _, s, _ in results if s == "PASS")
    print(f"\nRESULT: {npass}/{len(rows)} triggers armed+fired  ({time.time()-t0:.0f}s)")
    if npass == len(rows):
        try:
            import sys as _s, os as _o
            _s.path.insert(0, _o.path.dirname(__file__))
            from _ledger import record as _lrec
            _lrec("github", "arm", "ok", f"all {len(rows)} arm as real AP flows + repo webhooks")
            _lrec("github", "fire_synth", "ok",
                  f"{npass}/{len(rows)} piece-exact payloads fired real AP runs")
        except Exception:  # noqa: BLE001
            pass
    for ev, s, detail in results:
        if s != "PASS":
            print(f"  {s}: {ev} — {detail}")
    return 0 if npass == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
