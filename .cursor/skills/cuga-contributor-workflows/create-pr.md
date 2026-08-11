# Create pull request

All checks and the PR target **origin** upstream / base `main`.

## Step 1: Validate local repository

### Uncommitted changes

```bash
git status --porcelain
```

If this returns any output, **STOP**. Commit or stash first. Do not create the PR until clean.

### Resolve branch and origin repo

```bash
branch="$(git branch --show-current)"
origin_repo="$(gh repo view origin --json nameWithOwner -q .nameWithOwner)"
```

### Unpushed commits

Prefer explicit `origin/<branch>` over `@{u}` (upstream may be unset or point elsewhere).

```bash
if ! git rev-parse --verify "origin/$branch" >/dev/null 2>&1; then
  echo "No origin/$branch yet. After user confirmation: git push -u origin \"$branch\""
  # STOP until pushed with -u
else
  git rev-list --count "origin/$branch"..HEAD
fi
```

If there is no `origin/<branch>`, obtain confirmation and push with `git push -u origin <branch-name>` before continuing.

If the rev-list count is greater than zero, **STOP**. Push first (`git push origin <branch-name>`). Do not create the PR until pushed.

Only if both checks pass (clean tree AND branch matches origin), continue.

## Step 2: Create the pull request

1. Pick the right template from `.github/PULL_REQUEST_TEMPLATE/` (typically `bugfix.md`, `feature.md`, `docs.md`, or `chore.md`).
2. Ask which GitHub issue this PR closes or relates to. If the user skips / says none, leave Related Issue blank or note none — do not block.
3. Fill the template from current commits and changes:
   - Related Issue
   - Description
   - Type of Changes
   - Root Cause / Solution (bugfixes)
   - Testing
   - Checklist
4. Create (pass title via a variable; pass body via `--body-file` so shell does not interpolate the Markdown):

```bash
title="<title in commit convention>"
body_file="$(mktemp)"
cat >"$body_file" <<'EOF'
<filled template>
EOF

gh pr create --repo "$origin_repo" --base main --title "$title" --body-file "$body_file"
rm -f "$body_file"
```

Return the PR URL to the user.
