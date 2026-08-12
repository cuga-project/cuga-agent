# Create pull request

All checks and the PR target **origin** upstream / base `main`.

## Step 1: Validate local repository

### Uncommitted changes

```bash
git status --porcelain
```

If this returns any output, **STOP**. Commit or stash first. Do not create the PR until clean.

### Resolve branch and origin repo

`origin` is a Git remote name — resolve its URL, then ask `gh` for `OWNER/REPO` (do not pass the literal remote name `origin` to `gh repo view`).

```bash
branch="$(git branch --show-current)"
if [ -z "$branch" ]; then
  echo "Detached HEAD; check out a local branch before creating a PR." >&2
  exit 1
fi

origin_url="$(git remote get-url origin)"
origin_repo="$(gh repo view "$origin_url" --json nameWithOwner -q .nameWithOwner)"
```

### Remote parity (`origin/<branch>`)

Prefer `refs/remotes/origin/<branch>` over `@{u}`. Fetch first so the check is not stale.

```bash
git fetch origin "$branch"

if ! git rev-parse --verify "refs/remotes/origin/$branch^{commit}" >/dev/null 2>&1; then
  echo "No origin/$branch yet. After user confirmation: git push -u origin \"$branch\""
  # STOP until pushed with -u
else
  # "<behind> <ahead>" relative to refs/remotes/origin/$branch...HEAD
  git rev-list --left-right --count "refs/remotes/origin/$branch...HEAD"
fi
```

If there is no `origin/<branch>`, obtain confirmation and push with `git push -u origin <branch-name>` before continuing.

If **ahead** is greater than zero, **STOP**. Push first (`git push origin <branch-name>`).

If **behind** is greater than zero, **STOP**. Update the local branch (pull/rebase) before creating the PR.

Only if the tree is clean and both counts are zero, continue.

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
trap 'rm -f "$body_file"' EXIT
cat >"$body_file" <<'EOF'
<filled template>
EOF

gh pr create --repo "$origin_repo" --base main --title "$title" --body-file "$body_file"
```

Return the PR URL to the user.
