# Issue body templates

Mirror `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml`. Put `Part of #<parent>.` above these sections when parented.

## Title prefixes

| Prefix | When |
|---|---|
| `[Bug]:` | Unexpected behavior |
| `[Feature]` / `[Feature]:` | New functionality |
| `[Design]` | Architecture, API, or UX proposal |
| `[Refactor]` | Internal restructuring, no behavior change |
| `[Performance]` | Speed, memory, efficiency |
| `[Security]` | Vulnerability, safety, hardening |
| `[Docs]` | Documentation |
| `[Test]` | Tests or test infrastructure |
| `[Chore]` | Dependencies, cleanup, tooling |
| `[Epic]` / `[Epic]:` | Large workstream grouping children |
| `[Question]` | Clarification, not a task |

Match existing open-issue style in the repo when choosing `]` vs `]:`.

## Bug (`bug_report.yml`)

Default labels from template: `bug`, `needs-triage` (only if they exist in the repo).

```markdown
## What happened

<what you did, expected, what went wrong>

## How to reproduce

<steps or commands>

## Environment

<OS, CUGA version, Python/Node/browser as relevant>

## Logs, screenshots, or config

<optional; redacted>

## Checklist

- [x] I searched existing issues and this doesn’t look like a duplicate
```

## Feature-family (`feature_request.yml`)

Default labels from template: `enhancement`, `needs-triage` (only if they exist). Always pass `needs-triage` when present. Skill also requires it for feature-family creates.

State the chosen issue type in the body or title.

```markdown
## Issue type

<[Feature] | [Design] | …>

## What you want and why

<problem, capability, how you’d use it>

## How it could work

<ideal behavior/API; workarounds or alternatives>

## Links or extra context

<optional>

## Checklist

- [x] I searched existing issues and this doesn’t look like a duplicate
```

## Epic

No parent. Include `type: epic`. Prefer REST `type=Epic` on create.

```markdown
## Summary

<workstream goal and why it matters>

## Scope

<what child features should cover; out of scope>

## Links or extra context

<optional>
```
