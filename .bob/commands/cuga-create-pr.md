
### Step 1: Validate Your Local Repository do it all against upstream `origin`

Before creating a PR, you must ensure all your changes are committed and pushed to your branch.

#### Check for Uncommitted Changes

Run the following command to check for any uncommitted files:

```bash
git status --porcelain
```

**⚠️ CRITICAL: If this command returns any output, STOP HERE!**

You have uncommitted changes that must be handled before creating a PR. You must either:
- **Commit your changes**: `git add . && git commit -m "your message"`
- **Stash your changes**: `git stash`

**Do not proceed with PR creation until this is resolved.**

#### Check for Unpushed Commits

Next, confirm that all your local commits have been pushed to the remote repository.

```bash
git rev-list --count @{u}..HEAD
```

**⚠️ CRITICAL: If the count is greater than zero, STOP HERE!**

You have commits that have not been pushed to the remote repository. You must:
- **Push your changes**: `git push origin <branch-name>`

**Do not proceed with PR creation until this is resolved.**

#### Validation Complete

Only if both checks pass (no uncommitted changes AND no unpushed commits), you are ready to create your pull request.

-----

### Step 2: Create the Pull Request

Use the GitHub CLI (`gh`) to create the pull request.

#### Choose a Template

First, identify the appropriate template from your `.github/PULL_REQUEST_TEMPLATE/` directory. Your options are typically `bugfix.md`, `feature.md`, `docs.md`, or `chore.md`.

#### Related issue (ask the user)

Ask which GitHub issue this PR closes or relates to (issue number or URL). If the user says there is none, to skip the issue, or similar, continue without one—leave **Related Issue** blank, omit that line, or write that there is no linked issue. Do not block PR creation on this.

#### Fill the Template with PR Information

Before creating the PR, you must fill out the template with relevant information about your changes:

1. **Use the correct** template:
   ```bash
    .github/PULL_REQUEST_TEMPLATE/[template].md
   ```

2. **Fill out the required sections based on current commits and changes**:
   - **Related Issue**: Link to any related GitHub issue (if applicable)
   - **Description**: Brief description of what this PR accomplishes
   - **Type of Changes**: Check the appropriate boxes
   - **Root Cause**: What was causing the issue (for bugfixes)
   - **Solution**: How this fix addresses the root cause
   - **Testing**: Check off completed testing steps
   - **Checklist**: Verify all items are completed

3. **Remember filled template**

#### Run the Command


```bash
gh pr create --base main --title "<title in commit convention>" --body "<content of .md filled>"
```