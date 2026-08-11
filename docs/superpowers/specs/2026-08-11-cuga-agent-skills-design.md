# Design: CUGA agent skills (issues + contributor workflows)

Date: 2026-08-11  
Status: approved for implementation planning

## Goal

Replace mirrored slash-command files for GitHub issue filing and contributor workflows with **Agent Skills**, mirrored the same way across Cursor / Claude / Bob. Fold bug + feature (+ epic) into one issues skill; fold commit + create-PR + ruff into one contributor skill.

## Non-goals

- New slash commands / backslash command wrappers
- Changing GitHub issue form YAML templates themselves (skills mirror their sections)
- Migrating unrelated repo tooling

## Layout (mirrored full copies)

Identical trees under:

```
.cursor/skills/
.claude/skills/
.bob/skills/
```

```
cuga-github-issues/
  SKILL.md
  github_commands.md
  templates.md

cuga-contributor-workflows/
  SKILL.md
  commit.md
  create-pr.md
  ruff-check.md
```

Approach: **mirrored full copies** (not stubs, not symlinks), matching today’s command mirroring pattern.

## Cleanup

Delete these command files from `.cursor/commands/`, `.claude/commands/`, and `.bob/commands/`:

- `cuga-report-bug.md`
- `cuga-new-feature.md`
- `github_commands.md`
- `cuga-commit.md`
- `cuga-create-pr.md`
- `cuga-ruff-check.md`

If a `commands/` directory is empty afterward, remove it (or leave empty — prefer remove empty dirs).

Update:

- `CONTRIBUTING.md` — “AI Agent Commands” → skills table pointing at the three tooling paths
- `AGENTS.md` — issue/PR guidance references skills, not slash commands

## Skill 1: `cuga-github-issues`

### Frontmatter

- `name`: `cuga-github-issues`
- `description`: third person; WHAT + WHEN; trigger phrases include: create new issues, bug, feature request, epic, report a bug, new feature, GitHub issue, sub-issue, parent epic
- Do **not** set `disable-model-invocation` (agent may auto-invoke)

### Hierarchy (hard rule)

Allowed depth only:

```
Epic → Feature → Issue
```

- Never nest under a leaf Issue
- Never Feature → Feature
- Never deeper than three levels

| Kind | Parent | Notes |
|---|---|---|
| `[Epic]` | none | Top-level; label `type: epic`; prefer REST create with `type=Epic` when applicable |
| Feature-family (`[Feature]`, `[Design]`, `[Refactor]`, `[Performance]`, `[Security]`, `[Docs]`, `[Test]`, `[Chore]`, `[Question]`, …) | **Epic** | Required parent epic; link via `addSubIssue` |
| Leaf issue scoped to a feature | **Feature** | Feature must already be under an Epic |
| Small self-contained bug | optional | May skip parent; must say so when filing |
| Non-trivial bug with no fitting Feature | **ask user** | Offer: (1) attach under existing/new Feature under Epic, (2) attach directly under Epic (depth 2), (3) file with no parent and state that. Do not invent a Feature or auto-pick |

### Unified workflow (`SKILL.md`)

1. Detect intent: epic / feature-family / bug (from user wording).
2. Resolve upstream `origin`; run `gh label list`; never invent labels.
3. Place in hierarchy:
   - List open epics (`type: epic` + title `[Epic]` / `[EPIC]`).
   - For leaf-under-feature: also list Feature children of the candidate epic.
   - Decision rules: 0 matches → ask user to name/create parent; 1 clear match → confirm; 2+ → present options, wait.
4. Create issue with correct title prefix and template body sections; put `Part of #<parent>.` at top when parented.
5. Link with GraphQL `addSubIssue` (see companion); verify parent. Do not rely on unsupported `gh issue edit --set-parent`.
6. No promotional footers (“Made with Cursor”, etc.).

### Companions

- `github_commands.md` — folded from existing command file: list epics, list sub-issues, find orphans, node IDs, add/remove sub-issue, verify, create epic via REST, troubleshooting. Update “Related” / hygiene text for epic → feature → issue depth.
- `templates.md` — body section checklists matching `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml` (and epic create notes).

## Skill 2: `cuga-contributor-workflows`

### Frontmatter

- `name`: `cuga-contributor-workflows`
- `description`: third person; WHAT + WHEN; triggers: commit, create PR, pull request, ruff check, format, conventional commits
- Do **not** set `disable-model-invocation`

### Modes

| Mode | Companion | Behavior |
|---|---|---|
| Commit | `commit.md` | `git status` → selective stage → Conventional Commits with scope + bullet description; one commit per file unless user asks otherwise; build files may batch in one commit |
| Create PR | `create-pr.md` | Validate against `origin`: no uncommitted changes, no unpushed commits; pick `.github/PULL_REQUEST_TEMPLATE/*`; ask related issue (skippable); `gh pr create --base main` with conventional title |
| Ruff | `ruff-check.md` | `uv run ruff check --fix` → fix remaining issues → `uv run ruff format` |

`SKILL.md` is a short router plus shared rules (`uv`, `gh`, no promotional footers). Details stay in companions.

## Content source

Port behavior from:

- `.cursor/commands/cuga-report-bug.md`
- `.cursor/commands/cuga-new-feature.md`
- `.cursor/commands/github_commands.md`
- `.cursor/commands/cuga-commit.md`
- `.cursor/commands/cuga-create-pr.md`
- `.cursor/commands/cuga-ruff-check.md`

(`.claude` / `.bob` copies are currently identical.)

## Success criteria

- Saying “create a bug / feature / epic / new issue” loads `cuga-github-issues` and follows hierarchy rules
- Saying “commit / create PR / ruff” loads `cuga-contributor-workflows`
- No dependency on deleted slash-command files
- All three tooling directories stay in sync
- CONTRIBUTING / AGENTS document the skills
