# GitHub commands (`gh`)

Reference for issues, epics, features, and sub-issues in `cuga-project/cuga-agent`.

**Prerequisites**

```bash
gh auth status
gh repo view   # should show cuga-project/cuga-agent (or pass --repo)
```

Optional defaults:

```bash
export REPO=cuga-project/cuga-agent
export OWNER=cuga-project
export NAME=cuga-agent
```

Hierarchy: **Epic → Feature → Issue** (max depth 3). Link with GraphQL `addSubIssue`.

---

## Epics in this repo

Open issues with label **`type: epic`**. Also search titles for `[Epic]` / `[EPIC]`.

```bash
gh issue list --state open --label "type: epic" --limit 50 \
  --json number,title,url \
  --jq '.[] | "#\(.number): \(.title)"'
```

---

## List sub-issues of a parent

Replace `238` with the parent number (epic or feature):

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

## Find issues without a parent

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

---

## Get issue node IDs (required for sub-issue mutations)

```bash
gh api graphql -f query='
{
  repository(owner: "cuga-project", name: "cuga-agent") {
    issue(number: 238) { id number title }
  }
}' --jq '.data.repository.issue'

gh api graphql -f query='
{
  repository(owner: "cuga-project", name: "cuga-agent") {
    parent: issue(number: 238) { id number title }
    child: issue(number: 168) { id number title parent { number } }
  }
}' --jq '.data.repository'
```

---

## Add a sub-issue (link child → parent)

**Supported:** `gh api graphql` + `addSubIssue`.

**Not supported** in current `gh issue edit`: `--set-parent`, `--add-sub-issue`.

**REST note:** `POST .../sub_issues` returned 404 here; prefer GraphQL.

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

Or:

```bash
gh api graphql -f query='
{ repository(owner:"cuga-project", name:"cuga-agent") {
    issue(number:168) { number title parent { number title } }
}}' --jq '.data.repository.issue'
```

Before attaching a leaf under a Feature, confirm the Feature’s parent is an Epic (depth ≤ 3):

```bash
gh issue view <feature> --json number,title,parent --jq '{number, title, parent: .parent}'
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

Creating with parent in one step is not supported by `gh issue create` for this CLI version. Create, then `addSubIssue`.

```bash
gh issue create \
  --title "[Feature]: Example title" \
  --body "Part of #<parent>.\n\n## What you want and why\n..." \
  --label "enhancement" \
  --label "needs-triage" \
  --label "component: agent"
```

### Create an epic (REST — supports `type` field)

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

## Hierarchy hygiene

| Pattern | Meaning |
|---|---|
| `type: epic` | Top-level epic |
| Feature-family title prefix | Child of an epic |
| Leaf under feature | Child of a feature (grandchild of epic) |
| `component: *` | Area label |

**When to parent**

- **Epic:** never.
- **Feature-family:** must parent to an epic.
- **Leaf under feature:** parent to that feature (feature must already be under an epic).
- **Small self-contained bug:** may skip parent; say so.
- **Non-trivial bug, no fitting feature:** ask user (feature / epic / none). Never invent a Feature.
- **Ask:** two or more parents could fit; do not auto-pick.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Forbidden` on `gh api graphql` | Run outside sandbox / check `gh auth status` |
| `addSubIssue` succeeds but UI slow | Refresh; parent shows under Sub-issues |
| Child already has parent | `removeSubIssue` first, or skip |
| `404` on REST `sub_issues` | Use GraphQL `addSubIssue` |
| Depth would exceed 3 | Stop; re-home under Feature or Epic with user |

---

## Related

Use skill `cuga-github-issues` (`SKILL.md`) for filing. Use skill `cuga-contributor-workflows` for PRs.
