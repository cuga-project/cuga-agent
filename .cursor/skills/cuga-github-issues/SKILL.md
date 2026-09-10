---
name: cuga-github-issues
description: >-
  Create GitHub issues for cuga-agent (bugs, features, epics, designs, and related
  work) against origin using gh, with epic → feature → issue hierarchy and
  GraphQL sub-issue linking. Use when the user says create new issues, report a
  bug, file a bug, feature request, new feature, create an epic, GitHub issue,
  sub-issue, or parent epic.
---

# CUGA GitHub issues

File issues against **origin** upstream with `gh`. Read companions as needed:

- [github_commands.md](github_commands.md) — list/link/verify recipes
- [templates.md](templates.md) — body sections and title prefixes

## Shared rules

1. Use upstream `origin` (not a fork-only default). Pass `--repo owner/name` when needed.
2. Run `gh label list` for that repo. Only use label names that exist. Never invent labels.
3. No promotional footers (“Made with Cursor”, etc.).
4. Hierarchy depth is only: **Epic → Feature → Issue**. Never nest under a leaf issue. Never Feature → Feature. Never deeper than three levels.
5. When parented: put `Part of #<number>.` at the top of the body, create the issue, then link with GraphQL `addSubIssue`. Body text alone is not enough. Verify parent afterward.
6. Do not use unsupported `gh issue edit --set-parent`.

## Detect intent

| User intent | Branch |
|---|---|
| Epic / large workstream | [Create epic](#create-epic) |
| Feature, design, refactor, perf, security, docs, test, chore, question | [Create feature-family](#create-feature-family) |
| Bug / unexpected behavior | [Create bug](#create-bug) |
| Leaf task under an existing feature | [Create leaf under feature](#create-leaf-under-feature) |

If unclear, ask once which kind they want.

## Hierarchy placement

### Find epics

List open epics (`type: epic` and titles with `[Epic]` / `[EPIC]`). See [github_commands.md](github_commands.md).

### Find features under an epic

List sub-issues of the candidate epic; prefer children whose titles start with `[Feature]` (or other feature-family prefixes) when attaching a leaf.

### Match rules

- **0 matches:** Tell the user none fit. Ask them to name an existing parent or create one first. Do not invent a parent.
- **1 clear match:** Confirm number + title, then proceed.
- **2+ matches:** Present `#N — title` with one-line why each might fit. Do not pick for the user.

## Create epic

1. No parent search.
2. Title: `[Epic] …` (or `[Epic]: …` if matching existing style in the repo).
3. Labels: include `type: epic` plus other applicable existing labels.
4. Prefer REST create with `type=Epic` (see companion).
5. Body: summary of the workstream and what child features will cover. Use feature-request sections where useful ([templates.md](templates.md)).

## Create feature-family

Non-bug work that is not itself an epic (`[Feature]`, `[Design]`, `[Refactor]`, `[Performance]`, `[Security]`, `[Docs]`, `[Test]`, `[Chore]`, `[Question]`, …).

1. Parent **Epic is required**. Do not create with no parent.
2. Confirm epic via placement rules above.
3. Labels: always `--label needs-triage` plus other applicable existing labels (e.g. `enhancement`).
4. Title prefix from the table in [templates.md](templates.md).
5. Body sections from `feature_request.yml` ([templates.md](templates.md)).
6. Create, `addSubIssue` under the epic, verify.

## Create leaf under feature

Use when the user wants a sub-issue of an existing feature (bug/chore/task scoped to that feature).

1. Resolve parent **Feature** (must already be under an Epic — depth stays ≤ 3).
2. If the feature has no epic parent, stop and fix hierarchy with the user before filing.
3. Body/labels/title per bug or feature-family templates as appropriate.
4. Create, `addSubIssue` under the **Feature**, verify.

## Create bug

1. Labels: `--label bug` plus other applicable existing labels (template also uses `needs-triage` when present in the repo).
2. Title prefix: `[Bug]: …`
3. Body sections from `bug_report.yml` ([templates.md](templates.md)).
4. Placement:
   - **Small self-contained bug** (narrow repro, no design change, not part of a larger workstream): may proceed **without** a parent. Say so explicitly when filing.
   - **Otherwise:** search for a fitting Feature under an Epic, then for a fitting Epic.
   - If a Feature fits: confirm, parent under that Feature.
   - If no Feature fits (non-trivial bug): **ask the user** to choose:
     1. Attach under an existing or new Feature (under an Epic)
     2. Attach directly under an Epic (depth 2)
     3. File with no parent (state that in the issue)
   - Do not invent a Feature. Do not auto-pick among 2+ candidates.
5. Create, link if parented, verify.

## Done checklist

- [ ] Correct upstream repo
- [ ] Labels exist in `gh label list`
- [ ] Hierarchy depth respected
- [ ] Template sections filled from user/context
- [ ] `addSubIssue` + parent verified when parented
- [ ] Issue URL returned to the user
