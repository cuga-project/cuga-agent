"""Unit tests for .github/scripts/pr_limit.py (open pull request cap per author)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github/scripts/pr_limit.py"

_spec = importlib.util.spec_from_file_location("pr_limit", SCRIPT)
pr_limit = importlib.util.module_from_spec(_spec)
sys.modules["pr_limit"] = pr_limit  # dataclasses look up their module here
_spec.loader.exec_module(pr_limit)

PullRequest = pr_limit.PullRequest


def _pr(number: int, author: str = "alice", author_type: str = "User") -> PullRequest:
    return PullRequest(number=number, author=author, author_type=author_type, sha=f"sha{number}")


class FakeGh:
    """Records gh calls and serves the open pull request list."""

    def __init__(self, open_prs: list[PullRequest]) -> None:
        self.open_prs = open_prs
        self.statuses: dict[str, str] = {}
        self.comments: dict[int, str] = {}
        self.closed: list[int] = []

    def __call__(self, *args: str, stdin: str | None = None) -> str:
        endpoint = next(a for a in args if a.startswith("repos/"))
        if "/pulls?state=open" in endpoint:
            return "\n".join(
                json.dumps(
                    {"number": p.number, "sha": p.sha, "author": p.author, "author_type": p.author_type}
                )
                for p in self.open_prs
            )
        if "/statuses/" in endpoint:
            self.statuses[endpoint.rsplit("/", 1)[1]] = json.loads(stdin)["state"]
        elif endpoint.endswith("/comments"):
            self.comments[int(endpoint.split("/")[-2])] = json.loads(stdin)["body"]
        elif "/pulls/" in endpoint:
            assert "state=closed" in args
            self.closed.append(int(endpoint.rsplit("/", 1)[1]))
        return ""


def _event(action: str, pr: PullRequest) -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": pr.number,
            "user": {"login": pr.author, "type": pr.author_type},
            "head": {"sha": pr.sha},
        },
    }


@pytest.mark.unit
def test_evaluate_keeps_oldest_five_and_flags_newer() -> None:
    prs = [_pr(n) for n in (30, 10, 50, 20, 40, 60, 70)]
    verdicts = pr_limit.evaluate(prs, limit=5)
    assert sorted(n for n, v in verdicts.items() if v.allowed) == [10, 20, 30, 40, 50]
    assert sorted(n for n, v in verdicts.items() if not v.allowed) == [60, 70]
    assert verdicts[70].rank == 7 and verdicts[70].total == 7


@pytest.mark.unit
def test_evaluate_counts_each_author_separately() -> None:
    prs = [_pr(n, "alice") for n in range(1, 7)] + [_pr(n, "bob") for n in range(10, 13)]
    verdicts = pr_limit.evaluate(prs, limit=5)
    assert [n for n, v in verdicts.items() if not v.allowed] == [6]


@pytest.mark.unit
def test_bots_are_exempt() -> None:
    prs = [_pr(n, "dependabot[bot]", "Bot") for n in range(1, 10)]
    assert all(v.allowed for v in pr_limit.evaluate(prs, limit=5).values())


@pytest.mark.unit
def test_reopened_pr_is_ranked_newest() -> None:
    prs = [_pr(1)] + [_pr(n) for n in range(10, 15)]
    verdicts = pr_limit.evaluate(prs, limit=5, newest=1)
    assert not verdicts[1].allowed
    assert all(verdicts[n].allowed for n in range(10, 15))


@pytest.mark.unit
def test_opened_over_limit_closes_new_pr_and_keeps_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    new = _pr(106)
    gh = FakeGh([_pr(n) for n in range(101, 107)])
    monkeypatch.setattr(pr_limit, "_gh", gh)

    pr_limit.run("o/r", "pull_request_target", _event("opened", new), limit=5, target_url="u")

    assert gh.closed == [106]
    assert "#101, #102, #103, #104, #105" in gh.comments[106]
    assert gh.statuses["sha106"] == "failure"
    assert all(gh.statuses[f"sha{n}"] == "success" for n in range(101, 106))


@pytest.mark.unit
def test_burst_of_opened_prs_keeps_exactly_five(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh([_pr(n) for n in range(1, 9)])
    monkeypatch.setattr(pr_limit, "_gh", gh)

    for n in (8, 6, 7):
        pr_limit.run("o/r", "pull_request_target", _event("opened", _pr(n)), limit=5, target_url="u")

    assert sorted(set(gh.closed)) == [6, 7, 8]


@pytest.mark.unit
def test_schedule_sweep_closes_newest_backlog_for_every_author(monkeypatch: pytest.MonkeyPatch) -> None:
    alice = [_pr(n, "alice") for n in range(1, 9)]
    bob = [_pr(n, "bob") for n in range(20, 23)]
    gh = FakeGh(alice + bob)
    monkeypatch.setattr(pr_limit, "_gh", gh)

    pr_limit.run("o/r", "schedule", {}, limit=5, target_url="u")

    assert gh.closed == [6, 7, 8]
    assert all(gh.statuses[f"sha{n}"] == "success" for n in (1, 2, 3, 4, 5, 20, 21, 22))


@pytest.mark.unit
def test_closed_event_rechecks_only_that_author(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh([_pr(n, "alice") for n in range(1, 5)] + [_pr(n, "bob") for n in range(20, 27)])
    monkeypatch.setattr(pr_limit, "_gh", gh)

    pr_limit.run("o/r", "pull_request_target", _event("closed", _pr(5, "alice")), limit=5, target_url="u")

    assert gh.closed == []
    assert set(gh.statuses) == {f"sha{n}" for n in range(1, 5)}


@pytest.mark.unit
def test_status_description_fits_github_limit() -> None:
    verdict = pr_limit.evaluate([_pr(n, "a" * 39) for n in range(1, 8)], limit=5)[7]
    assert len(pr_limit.status_description(verdict, 5)) <= 140
