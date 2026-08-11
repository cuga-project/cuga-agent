# Create pull request

All checks and the PR target **origin** upstream / base `main`.

## Step 1: Validate local repository

### Uncommitted changes

```bash
git status --porcelain
```

If this returns any output, **STOP**. Commit or stash first. Do not create the PR until clean.

### Unpushed commits

```bash
git rev-list --count @{u}..HEAD
```

If the count is greater than zero, **STOP**. Push first (`git push origin <branch-name>`). Do not create the PR until pushed.

Only if both checks pass, continue.

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
4. Create:

```bash
gh pr create --base main --title "<title in commit convention>" --body "<filled template>"
```

Return the PR URL to the user.
