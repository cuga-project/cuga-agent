# Development Contributing Guide

## How to Contribute

1. Fork the repository to your own GitHub account. (not needed if you are CUGA team)
2. Create a feature branch from `main` in your fork: `git checkout -b feature/<short-topic>` (see Branch Naming Convention below).
3. Keep PRs small and focused (prefer < ~300 changed lines and limited file count).
4. Follow Conventional Commits for all commits and PR titles.
5. Run formatting, linting, and tests locally before opening a PR.
6. Open a Pull Request from your fork to `main` with a clear description and checklist results.

Notes:
- All PRs are merged using "Squash and merge". The PR title will become the final commit message — write it carefully using the Conventional Commits format.
- Prefer one topic per PR. If your changes touch many areas, split into multiple PRs.

## DCO

This repository requires a Developer's Certificate of Origin 1.1 signoff on every commit. A DCO provides your assurance to the community that you wrote the code you are contributing or have the right to pass on the code that you are contributing. It is generally used in place of a Contributor License Agreement (CLA). You can easily signoff a commit by using the -s or --signoff flag:

```bash
git commit -s -m 'This is my commit message'
```

If you are using the web interface, this should happen automatically. If you've already made a commit, you can fix it by amending the commit and force-pushing the change:

```bash
git commit --amend --no-edit --signoff
git push -f
```

This will only amend your most recent commit and will not affect the message. If there are multiple commits that need fixing, you can try:

```bash
git rebase --signoff HEAD~<n>
git push -f
```

where `<n>` is the number of commits missing signoffs.

## Commit Messages: Conventional Commits

We use the Conventional Commits specification. See the full spec at [conventionalcommits.org](https://www.conventionalcommits.org/en/v1.0.0/).

Structure:

```
<type>[optional scope]: <short description>

[optional body]

[optional footer(s)]
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`, `perf`, `style`.

Good examples:

```
feat(api): add list-accounts endpoint to registry
fix(browser): prevent crash when page has no active frame
```

Breaking change example:

```
feat(api)!: switch account id field to string

BREAKING CHANGE: API consumers must treat account ids as strings.
```

Bad examples (do not use):

```
update stuff
wip: changes
fixes
typo
```

Why this matters:
- Enables clean history and automated tooling (changelogs, versioning).
- Because we squash-merge, the PR title becomes the final commit — use Conventional Commits in the PR title too.

## Branch Naming Convention

We follow the Conventional Branch specification. See the full spec at [conventional-branch.github.io](https://conventional-branch.github.io/).

### Branch Naming Structure

```
<type>/<description>
```

### Supported Branch Types

| Type         | Good Example                | Why It's Good                   | Bad Example                  | Why It's Bad                      |
| ------------ | --------------------------- | ------------------------------- | ---------------------------- | --------------------------------- |
| Feature      | `feature/add-login-page`    | Lowercase, hyphens, descriptive | `Feature/AddLoginPage`       | Uppercase & no hyphens            |
| Fix          | `bugfix/header-bug`         | Clear, lowercase                | `feat/add_login`             | Uses underscore instead of hyphen |
| Hotfix       | `hotfix/security-patch`     | Clear, proper prefix            | `hotfix#security-patch`      | Contains invalid character `#`    |
| Release      | `release/v1.2.0`            | Correct dot usage for versions  | `release/v1..2.0`            | Consecutive dots                  |
| Chore        | `chore/update-dependencies` | Descriptive and valid           | `chore/update-dependencies-` | Trailing hyphen                   |
| Missing Desc | `feat/issue-123-new-login`  | Includes ticket, traceable      | `feature/`                   | Missing description               |

### Branch Naming Rules

1. **Use lowercase alphanumerics, hyphens, and dots**: Always use lowercase letters (`a-z`), numbers (`0-9`), and hyphens(`-`) to separate words. Avoid special characters, underscores, or spaces. For release branches, dots (`.`) may be used in the description to represent version numbers (e.g., `release/v1.2.0`).
2. **No consecutive, leading, or trailing hyphens or dots**: Ensure that hyphens and dots do not appear consecutively, nor at the start or end of the description.
3. **Keep it clear and concise**: The branch name should be descriptive yet concise, clearly indicating the purpose of the work.
4. **Include ticket numbers**: If applicable, include the ticket number from your project management tool to make tracking easier.

Why this matters:
- **Clear Communication**: The branch name alone provides a clear understanding of its purpose.
- **Automation-Friendly**: Easily hooks into automation processes (e.g., different workflows for `feature`, `release`, etc.).
- **Team Collaboration**: Encourages collaboration by making branch purpose explicit.

## Pull Request Guidelines

- Keep diffs small; avoid drive-by refactors. Separate formatting-only PRs from feature/fix PRs.
- Include a brief summary of what/why, and link related issues (e.g., `Refs: #123`).
- Add/update tests when changing behavior.
- Do not include generated files, large assets, secrets, or local config (e.g., `.env`).
- Ensure CI passes. If you see flaky tests, note it in the PR description.

### Pull Request Templates

We provide specific PR templates to help you create well-structured pull requests. When creating a PR, you can use one of these templates by adding the appropriate query parameter to the GitHub URL:

- **Feature PRs**: `?template=feature.md` - For new features and enhancements
- **Bug Fix PRs**: `?template=bugfix.md` - For bug fixes and issue resolutions
- **Documentation PRs**: `?template=docs.md` - For documentation updates and improvements
- **Chore PRs**: `?template=chore.md` - For maintenance tasks, dependency updates, and refactoring

Each template includes:
- Related issue linking
- Type of changes checkboxes
- Testing checklist
- Standard review checklist

GitHub will also automatically suggest these templates when you create a new pull request.

### Pre-PR Checklist (run locally)

Use `uv` for environment and tooling.

```
uv sync --dev
uv run ruff format
uv run ruff check --fix
# Run tests as described below
```

Must:
- If your change touches the browser/env, verify relevant demos still run.
- Update README.md or docs if only needed, discuss before

## Security Scanning

Before committing, run security scanning to detect potential secrets:

```bash
uv pip install --upgrade "git+https://github.com/ibm/detect-secrets.git@master#egg=detect-secrets"
detect-secrets scan --update .secrets.baseline
detect-secrets audit .secrets.baseline
```

If everything passes, no need to mark secrets or false positives. This ensures no sensitive information is accidentally committed to the repository.

## Running Tests

### 1) Install dev dependencies

```bash
uv sync --dev
```


### Run tests

Comprehensive test suite including linting, unit tests, and e2e tests:

```bash
chmod +x ./src/scripts/run_tests.sh
./src/scripts/run_tests.sh
```

This will run:
- **Linting checks**: Ruff code quality and formatting validation
- **Unit tests**: Variables manager, API response handling, registry functionality
- **E2E tests**: System tests across Fast and Balanced modes for real-world scenarios


## AI Agent Commands

If you are working in an AI-assisted IDE or using an AI agent (Cursor, Claude, Bob), a set of pre-built workflow commands is available to streamline common contributor tasks. The same commands are mirrored across all three tooling directories:

| Location | For |
|---|---|
| `.cursor/commands/cuga-*.md` | Cursor agent |
| `.claude/commands/cuga-*.md` | Claude / claude-code |
| `.bob/commands/cuga-*.md` | Bob agent |

### Available Commands

| Command | What it does |
|---|---|
| `cuga-commit` | Stages and commits changes using Conventional Commits with scoped messages and bullet-point descriptions |
| `cuga-create-pr` | Validates local state, picks the right PR template, fills it out from current changes, and opens the PR via `gh` |
| `cuga-report-bug` | Creates a GitHub issue using the `bug_report.yml` template with context from the current code |
| `cuga-new-feature` | Creates a GitHub issue using the `feature_request.yml` template |
| `cuga-ruff-check` | Runs `uv run ruff check --fix` and `uv run ruff format` on the project |

These commands follow all repo conventions (Conventional Commits, `gh` CLI, no promotional footers). To invoke them, use the slash-command syntax of your tool (e.g. `/cuga-commit` in Cursor).

## IDE Setup Quick Links

First make sure that your IDE environment is properly configured
[See Python Code Formatting Guide](#python-code-formatting-guide)

# Python Code Formatting Guide
Before every commit make sure to run:
```commandline
ruff format
ruff check --fix
```

### Ruff formatter and linter installation on IDE

#### VS Code
[https://github.com/astral-sh/ruff-vscode](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff)

#### Pycharm
https://docs.astral.sh/ruff/editors/setup/#pycharm

# IDE Debug Mode Setup

## VSCode and PyCharm Debug Mode

**Important**: Select the correct Python interpreter for debugging:

- **VS Code**: Press `Ctrl+Shift+P` → "Python: Select Interpreter" → Choose the `.venv` from your previous setup
- **PyCharm**: Go to Settings → Project → Python Interpreter → Select the uv virtual environment

## Available Configurations

### Demo Mode

For local development and testing:

1. **API Registry Demo** - Runs the API registry server for demo environment

   - Port: 8001
   - Uses: `mcp_servers.yaml`

2. **Cuga Demo** - Runs the main FastAPI server for demo
   - Port: 7860

**To run demo mode:**

1. Start "API Registry Demo" first
2. Then start "Cuga Demo"


## VSCode Instructions

1. Open VS Code's Run and Debug panel
2. Select the desired configuration from the dropdown
3. Start debugging