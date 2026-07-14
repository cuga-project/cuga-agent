# Agent guidelines

## Mark tests with their type

Every new or changed test must be marked with a pytest marker that declares its type. Use the markers registered in `pyproject.toml` (`[tool.pytest.ini_options].markers`), for example:

- `@pytest.mark.unit` — fast, isolated tests
- `@pytest.mark.e2e` — live services / full agent stack
- `@pytest.mark.stability` — LLM-backed e2e stability suite
- `@pytest.mark.pgvector` — requires pgvector
- `@pytest.mark.load` — concurrent load tests
- `@pytest.mark.slow` — long-running integration tests
- `@pytest.mark.manual` — needs manually started services
- `@pytest.mark.windows_smoke` — Windows CI smoke subset

Do not leave tests unmarked when a type marker applies.

## Creating issues and pull requests

When creating a new GitHub issue or pull request, use the AI agent commands documented in [CONTRIBUTING.md](CONTRIBUTING.md#ai-agent-commands) instead of inventing an ad-hoc flow. Prefer:

- `/cuga-report-bug` — open a bug issue from the `bug_report.yml` template
- `/cuga-new-feature` — open a feature request from the `feature_request.yml` template
- `/cuga-create-pr` — validate local state, pick the right PR template, and open the PR via `gh`

These commands live under `.cursor/commands/`, `.claude/commands/`, and `.bob/commands/` and follow repo conventions (templates, Conventional Commits, DCO signoff expectations, no promotional footers).
