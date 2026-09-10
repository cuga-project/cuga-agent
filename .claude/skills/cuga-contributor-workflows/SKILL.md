---
name: cuga-contributor-workflows
description: >-
  Run CUGA contributor workflows: Conventional Commits, create pull requests
  against origin with gh and PR templates, and ruff check/format via uv. Use when
  the user says commit, create a commit, create PR, pull request, open a PR, ruff
  check, ruff format, or conventional commits.
---

# CUGA contributor workflows

Detect mode from the user message, then follow the matching companion:

| Mode | Triggers | Companion |
|---|---|---|
| Commit | commit, conventional commits | [commit.md](commit.md) |
| Create PR | create PR, pull request, open a PR | [create-pr.md](create-pr.md) |
| Ruff | ruff check, ruff format, lint/format Python | [ruff-check.md](ruff-check.md) |

If multiple modes are requested, run them in a sensible order (ruff → commit → PR) unless the user specifies otherwise.

## Shared rules

- Use `uv` for Python tooling and `gh` for GitHub.
- Work against upstream `origin` for remotes/PRs.
- No promotional footers in commits or PR bodies.
- Follow repo conventions (Conventional Commits, DCO expectations when committing).
