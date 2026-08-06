#!/usr/bin/env python3
"""Verify the GitHub token — independent of CUGA/AP.

Mirrors the CUGA use case (the PR/issue watcher): AP creates a repository webhook and reads the
repo, which needs the `repo` + `admin:repo_hook` scopes. A Personal Access Token proves that same
capability *headlessly* — no browser OAuth grant needed, which is the whole point of using it here.

Keys:   GITHUB_TOKEN
Extra:  GITHUB_TEST_REPO=owner/name  (optional — verify repo read + webhook listing on a real repo)
"""

import sys

from _common import PASS, FAIL, SKIP, load_env, request_json, title, ok, bad, warn, info

H_BASE = {"User-Agent": "cuga-prereq-check", "Accept": "application/vnd.github+json"}


def main():
    env = load_env()
    title("GitHub")
    tok = env.get("GITHUB_TOKEN")
    if not tok:
        warn("GITHUB_TOKEN not set — skipping")
        return SKIP

    h = {**H_BASE, "Authorization": f"Bearer {tok}"}
    st, hd, j, _ = request_json("GET", "https://api.github.com/user", headers=h)
    if st != 200 or not j:
        bad(f"/user failed (HTTP {st}) — token invalid")
        return FAIL
    ok(f"token valid — @{j.get('login')}")

    scopes = hd.get("x-oauth-scopes")
    if scopes is not None:
        have = {s.strip() for s in scopes.split(",") if s.strip()}
        missing = {"repo", "admin:repo_hook"} - have
        if missing:
            warn(f"classic PAT missing scope(s): {', '.join(sorted(missing))} — PR-watch webhooks need them")
        else:
            ok("scopes ok — repo + admin:repo_hook present (can create PR/issue webhooks)")
    else:
        info("fine-grained token — ensure it grants Repository permissions -> Webhooks: Read & write")

    repo = env.get("GITHUB_TEST_REPO")
    if not repo:
        info("set GITHUB_TEST_REPO=owner/name to verify repo access + webhook listing on a real repo")
        return PASS

    st, _, _, _ = request_json("GET", f"https://api.github.com/repos/{repo}", headers=h)
    if st != 200:
        bad(f"cannot read repo {repo} (HTTP {st})")
        return FAIL
    ok(f"can read repo {repo}")

    st, _, hooks, _ = request_json("GET", f"https://api.github.com/repos/{repo}/hooks", headers=h)
    if st == 200:
        ok(f"can list webhooks on {repo} ({len(hooks)} present) — admin:repo_hook works")
    else:
        warn(f"cannot list webhooks on {repo} (HTTP {st}) — need admin on the repo + admin:repo_hook")
    return PASS


if __name__ == "__main__":
    sys.exit(main())
