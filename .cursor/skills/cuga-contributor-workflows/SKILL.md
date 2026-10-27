---
name: cuga-contributor-workflows
description: >-
  Run CUGA contributor workflows: Conventional Commits, create pull requests
  against origin with gh and PR templates, ruff check/format via uv, and the
  required design-impact, area, and readability labels before every push and
  pull request. Use when the user says commit, create a commit, push, create PR,
  pull request, open a PR, labels, ruff check, ruff format, or conventional commits.
---

# CUGA contributor workflows

Detect mode from the user message, then follow the matching companion:

| Mode | Triggers | Companion |
|---|---|---|
| Commit | commit, conventional commits | [commit.md](commit.md) |
| Push | push, push the branch | [labels.md](labels.md), then `git push` |
| Create PR | create PR, pull request, open a PR | [create-pr.md](create-pr.md) |
| Labels | labels, update labels | [labels.md](labels.md) |
| Ruff | ruff check, ruff format, lint/format Python | [ruff-check.md](ruff-check.md) |

If multiple modes are requested, run them in a sensible order (ruff → commit → labels → push → PR) unless the user specifies otherwise.

## Labels (MUST)

[labels.md](labels.md) is a required step, not an option:

- **Before every push** to a branch that has, or will get, a pull request: check the `design-impact:`, `area:`, and
  `readability:` labels against the diff and fix them.
- **Before opening a pull request:** choose the labels and pass them to `gh pr create`.

Do not push or open a pull request until this step is done.

## Shared rules

- Use `uv` for Python tooling and `gh` for GitHub.
- Work against upstream `origin` for remotes/PRs.
- No promotional footers in commits or PR bodies.
- Follow repo conventions (Conventional Commits, DCO expectations when committing).
