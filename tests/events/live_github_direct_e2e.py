"""LIVE (CE): the REAL GitHub fire, DIRECT path — a PR actually opened on the repo fires the armed
watcher through github-direct, no Activepieces.

Flow:
    arm new_pr watch on the repo  (concierge → a DIRECT subscription, no AP flow)
      → CREATE a real branch + commit + PR on the repo (needs a GITHUB_TOKEN with write)
      → the GitHub App's ONE webhook delivers pull_request → /api/events/github/events
      → github_direct verifies the signature, matches the repo, dispatches → pr_reviewer via /invoke
      → verify via the events LOG ('github.direct … matched=N'), because /api/events/runs is
        AP-only and empty on an AP-free deploy
      → cleanup: close the PR, delete the branch, delete the subscription. The App webhook is
        App-level and shared — it is NEVER touched.

This is the DIRECT counterpart to live_github_real_pr.py (which is the AP path). The OFFLINE mock of
the same arm→match→dispatch loop is tests/events/test_github_direct.py
(test_route_matches_an_armed_watch_by_repo_and_dispatches) — that one needs no tokens.

Run against the deployed events service:
    EVENTS_SERVER_URL=https://cuga-events-svc… GITHUB_E2E_REPO=owner/repo \
        .venv/bin/python tests/events/live_github_direct_e2e.py
or:  make test-github-e2e-ce CE_URL=https://cuga-events-svc…

Reads GITHUB_TOKEN + GATEWAY_TOKEN from .env (must match the deployed secret). The PR is auto-closed
and the branch/subscription removed in a finally block even on failure.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:8100").rstrip("/")
REPO = os.environ.get("GITHUB_E2E_REPO", "anupamamurthi/cuga-apps")
CE_APP = os.environ.get("EVENTS_CE_APP", "cuga-events-svc")  # for the log-based verify
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


GW = _env("GATEWAY_TOKEN")


def _app_jwt(app_id: str, pem: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=")

    now = int(time.time())
    hdr = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    pay = b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
    signing = hdr + b"." + pay
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    return (signing + b"." + b64(key.sign(signing, padding.PKCS1v15(), hashes.SHA256()))).decode()


def _write_token() -> "tuple[str, str, bool]":
    """Credential to open the PR: a GitHub App INSTALLATION token (preferred — the App carries
    Contents+PR write and needs no rotation), else a GITHUB_TOKEN PAT. Returns (token, kind, can_write).
    For an App, can_write comes from the token-mint response's granted permissions (contents +
    pull_requests == write) — NOT from GET /repos' repo-role `permissions.push`, which an App token
    does not populate the way a user token does."""
    app_id = _env("GITHUB_APP_ID")
    pem = _env("GITHUB_APP_PRIVATE_KEY").replace("\\n", "\n")
    inst = _env("GITHUB_APP_INSTALLATION_ID")
    if app_id and pem and inst:
        try:
            jwt = _app_jwt(app_id, pem)
            req = urllib.request.Request(
                f"{API}/app/installations/{inst}/access_tokens", method="POST",
                headers={"Authorization": f"Bearer {jwt}", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.loads(r.read())
            perms = d.get("permissions", {})
            cw = perms.get("contents") == "write" and perms.get("pull_requests") == "write"
            return d["token"], "app-installation-token", cw
        except Exception as e:  # noqa: BLE001
            print(f"  (App token mint failed: {e} — falling back to GITHUB_TOKEN)")
    pat = _env("GITHUB_TOKEN")
    return pat, "pat", bool(pat)  # optimistic for a PAT; the branch-create asserts on a scope failure


TOKEN, TOKEN_KIND, CAN_WRITE = _write_token()
GH = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github+json"}


def http(method, url, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            return e.code, {}


def _match_count() -> int:
    """How many 'github.direct … matched=N (N>0)' lines for REPO are currently in the tail.

    Returns a COUNT, not a bool, so the caller can compare against a baseline captured BEFORE it
    opens the PR. A bare "is there a match?" would be satisfied by a matching line from an EARLIER
    run still in the last 200 log entries, making the current run pass without its own delivery.
    """
    try:
        out = subprocess.run(
            ["ibmcloud", "ce", "app", "logs", "-n", CE_APP, "--tail", "200"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception:  # noqa: BLE001
        return -1  # unreadable — never let this look like "the count went up"
    n = 0
    for line in out.splitlines():
        if "github.direct" in line and REPO.split("/")[-1] in line and "matched=" in line and "matched=0" not in line:
            n += 1
    return n


def main() -> int:
    if not TOKEN:
        print("SKIP — no GITHUB_TOKEN (needs a PAT with Contents+Pull-requests WRITE on the repo to open a PR)")
        return 0
    print(f"REAL GitHub DIRECT fire — {REPO} · {SERVER}  (write cred: {TOKEN_KIND})")
    stamp = int(time.time())
    branch = f"e2e-direct-fire-{stamp}"
    sub_id = pr_num = None
    branch_created = False
    ok = False
    try:
        # 1) ARM — through the concierge, like a user would (github → a DIRECT subscription)
        # Arming is a two-step human-in-the-loop gate (by design, so nothing schedules itself):
        #   1. '/automate <request>'  → PROPOSES a standing flow (a confirm card; stores a pending arm)
        #   2. 'yes' on the SAME thread → APPROVES it → ARMED
        thread = f"direct-fire-{stamp}"
        code, rep = http(
            "POST", f"{SERVER}/api/concierge",
            {"text": f"/automate when a new pull request opens on {REPO}, summarize it and flag risks",
             "thread_id": thread},
            headers={"X-Gateway-Token": GW}, timeout=240,
        )
        print(f"  propose: {str(rep.get('reply',''))[:100]}")
        assert code == 200, f"propose failed HTTP {code}: {str(rep.get('reply',''))[:160]}"
        code, rep = http(
            "POST", f"{SERVER}/api/concierge",
            {"text": "yes", "thread_id": thread}, headers={"X-Gateway-Token": GW}, timeout=240,
        )
        reply = str(rep.get("reply", ""))
        print(f"  arm: {reply[:120]}")
        assert code == 200 and ("ARMED" in reply or "REUSING" in reply), f"arm failed (HTTP {code}): {reply[:200]}"
        import re

        m = re.search(r"[Ss]ubscription ([\w-]+)", reply)  # reply says "Subscription cuga-…" (capital S)
        sub_id = m.group(1) if m else None
        assert sub_id, f"armed but could not parse the subscription id from: {reply[:160]}"

        # 2) FIRE the trigger. Prefer a REAL PR (proves GitHub itself delivers the webhook). Fall back
        #    to a SIGNED SYNTHETIC pull_request event whenever the repo is not writable — no write
        #    credential, the repo is ARCHIVED (read-only), or a ruleset blocks the App. The synthetic is
        #    GitHub's exact payload shape with a valid X-Hub-Signature-256, so it exercises the same
        #    github-direct receive → match → dispatch path (only "GitHub sent it" differs, and the
        #    signed ping→pong check on the real App webhook already proves GitHub reaches this service).
        def fire_synthetic(reason: str) -> bool:
            secret = _env("GITHUB_WEBHOOK_SECRET")
            assert secret, "repo not writable AND no GITHUB_WEBHOOK_SECRET to sign a synthetic event"
            print(f"  ({reason} — firing a SIGNED synthetic pull_request:opened instead)")
            payload = json.dumps(
                {"action": "opened", "repository": {"full_name": REPO},
                 "pull_request": {"number": 0, "title": f"synthetic probe {stamp}",
                                  "html_url": f"https://github.com/{REPO}/pull/0", "user": {"login": "cuga-e2e"}}}
            ).encode()
            sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            # send the EXACT signed bytes (http() would re-serialize and break the signature)
            req = urllib.request.Request(
                f"{SERVER}/api/events/github/events", data=payload, method="POST",
                headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sig,
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as _r:
                    c, d = _r.status, json.loads(_r.read().decode() or "{}")
            except urllib.error.HTTPError as e:
                c, d = e.code, {}
            # the route returns matched directly — a deterministic proof the armed watch was hit
            assert c == 200 and d.get("matched", 0) >= 1, f"synthetic fire not matched: HTTP {c} {d}"
            print(f"  ✓ FIRED — signed synthetic pull_request → github-direct matched={d.get('matched')} → dispatched")
            return True

        # CAN_WRITE comes from the App installation token's GRANTED permissions (contents+pull_requests
        # write), computed at mint time — NOT from GET /repos' permissions.push, which App tokens never set.
        real = False
        if not CAN_WRITE:
            ok = fire_synthetic(f"no repo-write credential [{TOKEN_KIND}]")
        else:
            _, repo = http("GET", f"{API}/repos/{REPO}", headers=GH)
            if repo.get("archived"):
                # An archived repo is read-only: EVERY write 403s ("Resource not accessible by
                # integration") no matter the granted perms. The App cred is valid — unarchive the
                # repo (Settings → General → Danger Zone) for a true real-PR fire.
                ok = fire_synthetic(f"repo {REPO} is ARCHIVED (read-only); App write cred is valid")
            else:
                default = repo.get("default_branch", "main")
                # Baseline the log matches BEFORE we fire, so only a NEW match (from THIS PR) counts —
                # a match line from an earlier run still in the tail must not make this run pass.
                log_base = _match_count()
                _, ref = http("GET", f"{API}/repos/{REPO}/git/ref/heads/{default}", headers=GH)
                base_sha = (ref.get("object") or {}).get("sha")
                assert base_sha, f"could not read {default} head: {ref}"
                c, rr = http("POST", f"{API}/repos/{REPO}/git/refs", headers=GH,
                             body={"ref": f"refs/heads/{branch}", "sha": base_sha})
                if c not in (200, 201):
                    # write blocked despite a valid cred (ruleset / protected repo) — degrade, don't crash
                    ok = fire_synthetic(f"branch create blocked HTTP {c}: {(rr or {}).get('message')}")
                else:
                    branch_created = True
                    content = base64.b64encode(
                        f"# e2e direct-fire probe {stamp}\n\nOpens a real PR to fire the armed watcher. "
                        f"Auto-deleted.\n".encode()).decode()
                    c, _ = http("PUT", f"{API}/repos/{REPO}/contents/e2e/direct-fire-{stamp}.md", headers=GH,
                                body={"message": f"e2e probe {stamp}", "content": content, "branch": branch})
                    assert c in (200, 201), f"commit failed HTTP {c}"
                    c, pr = http("POST", f"{API}/repos/{REPO}/pulls", headers=GH,
                                 body={"title": f"e2e: direct-fire probe {stamp}", "head": branch, "base": default,
                                       "body": "Probe PR for the github-direct fire test. Auto-closed."})
                    pr_num = pr.get("number")
                    assert c == 201 and pr_num, f"PR create failed HTTP {c}: {pr}"
                    print(f"  REAL PR opened: #{pr_num}  {pr.get('html_url')}")
                    # verify via the events log (the App webhook's delivery reaches github-direct, not us)
                    print("  waiting for GitHub App webhook → github-direct match (events log) …")
                    deadline = time.time() + 300
                    while time.time() < deadline and not ok:
                        time.sleep(15)
                        ok = _match_count() > log_base  # a NEW match since the baseline
                    assert ok, "no NEW 'github.direct … matched' in the log within 5 min"
                    print("  ✓ FIRED — github-direct matched the REAL PR and dispatched to the agent")
                    real = True
    finally:
        # 3) CLEANUP — PR + branch (only if we opened one) + subscription. NEVER the App webhook.
        if pr_num:
            http("PATCH", f"{API}/repos/{REPO}/pulls/{pr_num}", headers=GH, body={"state": "closed"})
        if branch_created:
            http("DELETE", f"{API}/repos/{REPO}/git/refs/heads/{branch}", headers=GH)
        if sub_id:
            http("DELETE", f"{SERVER}/api/events/subscriptions/{sub_id}", headers={"X-Gateway-Token": GW}, timeout=60)
        print(f"  cleanup: PR {'closed' if pr_num else '—'} · subscription {'deleted' if sub_id else '—'}")
    mode = "a REAL GitHub PR" if pr_num else "a signed synthetic pull_request"
    print(f"\nRESULT: {'PASS — ' + mode + ' fired the direct watcher e2e' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
