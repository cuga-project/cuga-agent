"""Enforce a hard cap on open pull requests per author.

Every open pull request counts toward the cap, drafts included. Bot accounts
are exempt. An author's open pull requests are ranked oldest first (by
number). The oldest ``PR_LIMIT`` stay open; every newer one is closed with a
comment. A pull request that was just reopened is ranked newest, so reopening
an old pull request never pushes out one that is already open.

Every open pull request in scope also gets a ``PR limit`` commit status
(success within the cap, failure over it). Make that status a required check
on ``main`` so an over-limit pull request cannot merge even if closing fails.

Scope: for a ``pull_request_target`` event, the open pull requests of that
event's author; for ``schedule`` and ``workflow_dispatch``, every open pull
request in the repository.

Uses only the standard library and the ``gh`` CLI (``GH_TOKEN`` must be set).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

STATUS_CONTEXT = "PR limit"
DEFAULT_LIMIT = 5
NEW_PR_ACTIONS = frozenset({"opened", "reopened"})


@dataclass(frozen=True)
class PullRequest:
    number: int
    author: str
    author_type: str
    sha: str


@dataclass(frozen=True)
class Verdict:
    pr: PullRequest
    rank: int
    total: int
    allowed: bool


def is_bot(pr: PullRequest) -> bool:
    return pr.author_type == "Bot" or pr.author.endswith("[bot]")


def evaluate(prs: list[PullRequest], limit: int, newest: int | None = None) -> dict[int, Verdict]:
    """Rank each author's open pull requests oldest first and apply the limit.

    ``newest`` is the number of a pull request that was just reopened; it is
    ranked after the author's other open pull requests. A just-opened pull
    request needs no override: it already has the highest number.
    """
    by_author: dict[str, list[PullRequest]] = {}
    for pr in prs:
        by_author.setdefault(pr.author, []).append(pr)

    verdicts: dict[int, Verdict] = {}
    for author_prs in by_author.values():
        author_prs.sort(key=lambda p: (p.number == newest, p.number))
        total = len(author_prs)
        for rank, pr in enumerate(author_prs, start=1):
            allowed = is_bot(pr) or rank <= limit
            verdicts[pr.number] = Verdict(pr=pr, rank=rank, total=total, allowed=allowed)
    return verdicts


def kept_numbers(verdicts: dict[int, Verdict], author: str) -> list[int]:
    """Numbers of the author's pull requests that stay open, in rank order."""
    kept = [v for v in verdicts.values() if v.pr.author == author and v.allowed]
    return [v.pr.number for v in sorted(kept, key=lambda v: v.rank)]


def status_description(verdict: Verdict, limit: int) -> str:
    """Commit status text. GitHub truncates descriptions over 140 characters."""
    if is_bot(verdict.pr):
        return "Bot account: exempt from the open PR limit."
    if verdict.allowed:
        return f"PR {verdict.rank} of {verdict.total} open by @{verdict.pr.author}; limit is {limit}."
    return (
        f"Over limit: PR {verdict.rank} of {verdict.total} open by @{verdict.pr.author}; "
        f"limit is {limit} open PRs, drafts included. Closed."
    )[:140]


def close_comment(verdict: Verdict, limit: int, kept: list[int]) -> str:
    listed = ", ".join(f"#{n}" for n in kept)
    return (
        f"This pull request was closed automatically. The limit is {limit} open pull requests "
        f"per author, drafts included. @{verdict.pr.author} has {verdict.total} open pull "
        f"requests; the {len(kept)} oldest stay open ({listed}) and newer ones are closed.\n\n"
        "To continue, merge or close one of the open pull requests above, then reopen this one."
    )


def _gh(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def fetch_open_prs(repo: str) -> list[PullRequest]:
    out = _gh(
        "api",
        "--paginate",
        f"repos/{repo}/pulls?state=open&per_page=100",
        "--jq",
        ".[] | {number, sha: .head.sha, author: .user.login, author_type: .user.type}",
    )
    return [PullRequest(**json.loads(line)) for line in out.splitlines() if line.strip()]


def set_status(repo: str, verdict: Verdict, limit: int, target_url: str) -> None:
    body = {
        "state": "success" if verdict.allowed else "failure",
        "context": STATUS_CONTEXT,
        "description": status_description(verdict, limit),
        "target_url": target_url,
    }
    _gh(
        "api", "-X", "POST", f"repos/{repo}/statuses/{verdict.pr.sha}", "--input", "-", stdin=json.dumps(body)
    )


def close_pr(repo: str, verdict: Verdict, limit: int, kept: list[int]) -> None:
    comment = {"body": close_comment(verdict, limit, kept)}
    _gh(
        "api",
        "-X",
        "POST",
        f"repos/{repo}/issues/{verdict.pr.number}/comments",
        "--input",
        "-",
        stdin=json.dumps(comment),
    )
    _gh("api", "-X", "PATCH", f"repos/{repo}/pulls/{verdict.pr.number}", "-f", "state=closed")


def event_pull_request(event_name: str, event: dict) -> PullRequest | None:
    if event_name != "pull_request_target" or "pull_request" not in event:
        return None
    pr = event["pull_request"]
    return PullRequest(
        number=pr["number"],
        author=pr["user"]["login"],
        author_type=pr["user"]["type"],
        sha=pr["head"]["sha"],
    )


def run(repo: str, event_name: str, event: dict, limit: int, target_url: str) -> int:
    prs = fetch_open_prs(repo)
    event_pr = event_pull_request(event_name, event)
    is_new = event_pr is not None and event.get("action") in NEW_PR_ACTIONS

    if is_new and all(p.number != event_pr.number for p in prs):
        # The list API can lag a just-opened pull request; count it anyway.
        prs.append(event_pr)

    reopened = event_pr.number if is_new and event.get("action") == "reopened" else None
    verdicts = evaluate(prs, limit, newest=reopened)

    if event_pr is not None:
        in_scope = [v for v in verdicts.values() if v.pr.author == event_pr.author]
    else:
        in_scope = list(verdicts.values())
    in_scope.sort(key=lambda v: v.pr.number)

    for verdict in in_scope:
        set_status(repo, verdict, limit, target_url)
        if verdict.allowed:
            print(f"#{verdict.pr.number}: {status_description(verdict, limit)}")
            continue
        close_pr(repo, verdict, limit, kept_numbers(verdicts, verdict.pr.author))
        print(f"#{verdict.pr.number}: {status_description(verdict, limit)}")
    return 0


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    event_name = os.environ["GITHUB_EVENT_NAME"]
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
        event = json.load(fh)
    limit = int(os.environ.get("PR_LIMIT") or DEFAULT_LIMIT)
    target_url = f"{os.environ['GITHUB_SERVER_URL']}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return run(repo, event_name, event, limit, target_url)


if __name__ == "__main__":
    sys.exit(main())
