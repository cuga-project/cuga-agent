---
name: github-commands
description: "GitHub issue and epic workflow for cuga-agent: list epics, find orphans, add/remove sub-issues via gh."
trigger: /github
---

# GitHub commands (`gh`)

Reference for working with **issues**, **epics**, and **sub-issues** in `cuga-project/cuga-agent`.

**Prerequisites**

```bash
gh auth status          # must be logged in
gh repo view            # should show cuga-project/cuga-agent
```

Set defaults once (optional):

```bash
export REPO=cuga-project/cuga-agent
export OWNER=cuga-project
export NAME=cuga-agent
```

---

## Epics in this repo

Epics are open issues with the label **`type: epic`**. Child work items are linked as **sub-issues** (GitHub parent/child), not only via labels.

```bash
gh issue list --state open --label "type: epic" --limit 50 \
  --json number,title,url \
  --jq '.[] | "#\(.number): \(.title)"'
```

---

## List sub-issues of an epic

Replace `238` with the epic number:

```bash
gh api graphql -f query='
{
  repository(owner: "cuga-project", name: "cuga-agent") {
    issue(number: 238) {
      number
      title
      subIssues(first: 50) {
        nodes { number title url state }
      }
    }
  }
}' --jq '.data.repository.issue | "#\(.number): \(.title)", (.subIssues.nodes[] | "  #\(.number): \(.title)")'
```

---

## Find issues without an epic parent

`gh issue list` cannot filter by parent. Use GraphQL:

```bash
gh api graphql -f query='
query($cursor: String) {
  repository(owner: "cuga-project", name: "cuga-agent") {
    issues(first: 100, after: $cursor, states: OPEN) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        parent { number title }
        labels(first: 20) { nodes { name } }
      }
    }
  }
}' --paginate --jq '
  [.[] | .data.repository.issues.nodes[] |
    select(.parent == null) |
    select([.labels.nodes[].name] | index("type: epic") | not) |
    "#\(.number): \(.title)"
  ] | .[]
'
```

Issues whose parent has `type: epic` are considered **under an epic**; `parent == null` means no parent at all.

---

## Get issue node IDs (required for sub-issue mutations)

GraphQL mutations use **node IDs** (`I_kwDO...`), not issue numbers.

```bash
# Single issue
gh api graphql -f query='
{
  repository(owner: "cuga-project", name: "cuga-agent") {
    issue(number: 238) { id number title }
  }
}' --jq '.data.repository.issue'

# Parent + child in one call
gh api graphql -f query='
{
  repository(owner: "cuga-project", name: "cuga-agent") {
    parent: issue(number: 238) { id number title }
    child: issue(number: 168) { id number title parent { number } }
  }
}' --jq '.data.repository'
```

---

## Add a sub-issue (link child → epic parent)

**Supported today:** `gh api graphql` + `addSubIssue` mutation.

**Not supported in current `gh issue edit`:** `--set-parent`, `--add-sub-issue` (may arrive in a future CLI release).

**REST note:** `POST /repos/{owner}/{repo}/issues/{issue_number}/sub_issues` returned 404 in this repo; prefer GraphQL.

### One child

```bash
PARENT_ID=$(gh api graphql -f query='
{ repository(owner:"cuga-project", name:"cuga-agent") { issue(number:238) { id } } }
' --jq '.data.repository.issue.id')

CHILD_ID=$(gh api graphql -f query='
{ repository(owner:"cuga-project", name:"cuga-agent") { issue(number:168) { id } } }
' --jq '.data.repository.issue.id')

gh api graphql -f query="
mutation {
  addSubIssue(input: {
    issueId: \"$PARENT_ID\"
    subIssueId: \"$CHILD_ID\"
  }) {
    issue { number title }
    subIssue { number title parent { number } }
  }
}"
```

### Multiple children (batch)

```bash
PARENT=238
CHILDREN="168 161 104 202"

PARENT_ID=$(gh api graphql -f query="
{ repository(owner:\"cuga-project\", name:\"cuga-agent\") { issue(number:$PARENT) { id } } }
" --jq '.data.repository.issue.id')

for num in $CHILDREN; do
  CHILD_ID=$(gh api graphql -f query="
  { repository(owner:\"cuga-project\", name:\"cuga-agent\") { issue(number:$num) { id parent { number } } } }
  " --jq '.data.repository.issue | "\(.id) \(.parent.number // "none")"')

  id="${CHILD_ID%% *}"
  existing="${CHILD_ID##* }"
  if [ "$existing" != "none" ]; then
    echo "#$num: skip (already parent #$existing)"
    continue
  fi

  gh api graphql -f query="
  mutation {
    addSubIssue(input: { issueId: \"$PARENT_ID\", subIssueId: \"$id\" }) {
      subIssue { number parent { number } }
    }
  }" --jq ".data.addSubIssue.subIssue | \"#\(.number) -> parent #\(.parent.number)\""
done
```

### Verify after linking

```bash
gh issue view 168 --json number,title,parent --jq '{number, title, parent: .parent}'
```

Or GraphQL:

```bash
gh api graphql -f query='
{ repository(owner:"cuga-project", name:"cuga-agent") {
    issue(number:168) { number title parent { number title } }
}}' --jq '.data.repository.issue'
```

---

## Remove a sub-issue (unlink parent)

```bash
PARENT_ID=$(gh api graphql -f query='
{ repository(owner:"cuga-project", name:"cuga-agent") { issue(number:238) { id } } }
' --jq '.data.repository.issue.id')

CHILD_ID=$(gh api graphql -f query='
{ repository(owner:"cuga-project", name:"cuga-agent") { issue(number:168) { id } } }
' --jq '.data.repository.issue.id')

gh api graphql -f query="
mutation {
  removeSubIssue(input: {
    issueId: \"$PARENT_ID\"
    subIssueId: \"$CHILD_ID\"
  }) {
    subIssue { number parent { number } }
  }
}"
```

---

## Create an issue

### Standard (no parent)

```bash
gh issue create \
  --title "[Feature]: Example title" \
  --body "## Summary\n..." \
  --label "enhancement" \
  --label "component: agent"
```

### With issue type (REST — supports `type` field)

```bash
gh api repos/cuga-project/cuga-agent/issues \
  -X POST \
  -f title="[Epic] Example epic" \
  -f body="## Summary\n..." \
  -f type="Epic" \
  -f labels[]="type: epic" \
  -f labels[]="component: agent" \
  --jq '{number, html_url}'
```

Then link children with `addSubIssue` (above). Creating a sub-issue with parent in one step is not documented in `gh issue create` for this CLI version.

---

## Update / close issues

```bash
gh issue edit 168 --add-label "priority: high"
gh issue close 168 --comment "Fixed in #..."
gh api repos/cuga-project/cuga-agent/issues/168 \
  -X PATCH \
  -f title="Updated title" \
  --jq '{number, html_url}'
```

---

## Epic hygiene (conventions)

| Label / pattern | Meaning |
|-----------------|--------|
| `type: epic` | Top-level epic issue |
| `component: *` | Area (agent, frontend, policies, …) |
| Sub-issue parent | Epic issue number via GitHub parent link |

**When to parent an issue**

- **Must:** Clear scope match to epic title (e.g. multi-provider work → models epic #238).
- **Should:** Reasonable fit; alternate epic possible.
- **Skip:** One-off chores (#78-style cleanup) unless using a Platform Health epic.

**Epics with no children** are planning placeholders until sub-issues are linked.

---

## Example: models epic (#238)

Children linked under [#238 Open Source Models](https://github.com/cuga-project/cuga-agent/issues/238):

- #168, #161, #104, #202, #137, #110

Re-list:

```bash
gh api graphql -f query='
{ repository(owner:"cuga-project", name:"cuga-agent") {
  issue(number:238) { subIssues(first:20) { nodes { number title } } }
}}' --jq '.data.repository.issue.subIssues.nodes[] | "#\(.number): \(.title)"'
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Forbidden` on `gh api graphql` | Run outside sandbox / check `gh auth status` |
| `addSubIssue` succeeds but UI slow | Refresh issue page; parent shows under "Sub-issues" |
| Child already has parent | `removeSubIssue` first, or skip |
| `404` on REST `sub_issues` | Use GraphQL `addSubIssue` instead |
| Need assignee/labels only | `gh issue edit` — no parent flags needed |

---

## Related

- `cuga-new-feature.md` — create feature issues; must pick a parent epic and link via `addSubIssue`
- `cuga-report-bug.md` — create bug issues; same epic link flow except small self-contained bugs
- `.agents/skills/github-issues/SKILL.md` — create/update issues, templates, labels
- `.agents/skills/github-pr-comments/SKILL.md` — PR comments, inline review, batch feedback
- `.claude/commands/cuga-create-pr.md` — create PRs, then comment with the PR-comments skill
- User rule: conventional commits; do not commit unless asked
