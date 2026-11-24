# Agent guidelines

This file states the rules. When a rule links to a file under `docs/contributing/`, read that file before you do the work the rule covers.

## Frontend changes

Read [the frontend rules](src/frontend_workspaces/AGENTS.md) before editing frontend workspaces. They define the shared IBM Carbon components, loading states, navigation, accessibility, layout, secret handling, and browser validation.

## Declare known arguments explicitly

- When a function's arguments are known in advance, declare them explicitly instead of reading them from `**kwargs` or `**_`. Reserve `**kwargs` for wrappers that forward arbitrary arguments that cannot be known before runtime.
- For LangChain tools, declare runtime-injected context as `Annotated[..., InjectedToolArg]`. This keeps context in the validation schema while excluding it from model-facing schemas.
- Bind conversation context from the trusted runtime or captured thread; do not let model-supplied values override it.

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

## CI discovers tests by directory — do not edit workflow YAML

CI shards in `.github/workflows/tests.yml` (and the matching commands in `external-fork-tests.yml`) select **directories and markers**, not individual `test_*.py` files. [CI test discovery](docs/contributing/ci-test-discovery.md) lists which directory and marker runs in which job.

1. Put a new unit test in `tests/unit/` or `tests/integration/` (job `unit-b`), or in a colocated dir that the `unit-a` step already lists. Do not list an individual file in a workflow.
2. `unit-a` is an explicit list of directories, not a `src/**/tests/` glob. Some colocated suites and `tests/e2e/` run in **no** job (see the known gap in that file): a test there runs locally but never in CI. For coverage, add the directory (not a file) to the `unit-a` list.
3. Opt a test out of default PR CI with a marker (`manual`, `pgvector`, `load`, `e2e`, `stability`) — not by omitting it from YAML.
4. If a suite cannot share a process with others, give it its own **job** (or its own `pytest` invocation of a directory). Do not start a file allowlist.
5. Keep fork CI on the same directories as internal CI so a test cannot pass one and skip the other.
6. Local `uv run pytest` remains full discovery. CI is a disjoint partition of that set.

## New dependencies must have an approved license

The project is licensed under Apache-2.0 (`LICENSE`, `NOTICE`). The `Licenses` workflow checks every installed dependency against `.github/licenses/policy.json` (see [License CI](docs/contributing/license-ci.md)). Before you add a dependency to `pyproject.toml`, a frontend `package.json` or a Dockerfile, or copy code into the repository:

1. Look up its license and the licenses of the packages it pulls in. For Python, `uv tree --package <name>` lists those packages and `uv run pip-licenses --packages <name> <dependency> ...` shows their licenses. For the frontend, use `pnpm view <name> license`.
2. If every license is in `approved_expressions` in `policy.json`, you may add it. For an SPDX `OR` expression, one approved choice is enough; for `AND`, every part must be approved.
3. Otherwise (GPL, LGPL, AGPL, MPL, EPL, SSPL, a custom license, or no license), do not add it. Use an approved alternative. If there is none, stop and ask: the dependency needs a reviewed approval in a separate policy PR first, because a PR cannot approve its own finding. Never add a package listed in `forbidden_packages`.
4. Being a dependency already does not make a package approved. Do not use a package whose license is not approved in new code when an approved package does the job (for example, use asyncpg, Apache-2.0, instead of psycopg, LGPL-3.0-only).
5. Do not copy code from a project whose license is not approved. When you copy from an approved project, keep its copyright and license notice, and carry its `NOTICE` text into ours if it has one.

## Lazy imports — rules for new code and tests

Cold-start work (#486) made `import cuga` ~4 ms and cut SDK `import_s` ~66%. An eager import in one of the files below undoes that. [Lazy imports](docs/contributing/lazy-imports.md) has the background, the patch-target table, and test examples.

- **`cuga/__init__.py`:** do not add top-level imports. Add a new public export to the `_import_map` dict (lazy), not as a bare `from cuga.xxx import Yyy` statement. The `if TYPE_CHECKING:` block is fine.
- **`cuga/backend/llm/models.py`:** import each provider library (`langchain_openai`, `langchain_ibm`, `langchain_groq`, …) inside the `if platform == "..."` branch that uses it, never at the top of the file. Build `ReasoningChatOpenAI` only inside the `_get_reasoning_chat_openai()` factory. Never re-export provider classes (`ChatWatsonx`, `AzureChatOpenAI`, …) as module attributes.
- **`cuga/sdk.py` and `cuga/backend/server/main.py`:** import heavy dependencies (graph, policy, provider, observability) inside the function that uses them, not at the top of the file. `openlit` must stay behind the disabled-by-default observability gate.
- **Tests:** provider classes are not attributes of `cuga.backend.llm.models`. Patch the source module (`langchain_openai.AzureChatOpenAI`, `langchain_ibm.ChatWatsonx`) or the factory (`cuga.backend.llm.models._get_reasoning_chat_openai`). If a test fails with `AttributeError: <module 'cuga.backend.llm.models' ...> does not have the attribute '…'`, update the patch target — do not re-add a module-level import to make the patch work.

## HARD RULE: a database schema change must state what happens to existing data

**Every pull request that changes a database schema must state what happens to data written by older versions. This is critical: a user who upgrades must not lose data, and the server must still start on an old database. There are no exceptions** — not for one new column, an index, a renamed field, or a store that "nobody uses yet".

A schema change is a change to a table, index or column definition, to the format of a stored value (JSON keys, status values, embedding dimension), or to the location of the data, in SQLite or Postgres. This repository has no migration framework, and `CREATE TABLE IF NOT EXISTS` does nothing on an existing table, so a column added only to the `CREATE TABLE` statement is missing in every existing installation.

Before you open or push the pull request, read [Schema changes](docs/contributing/schema-changes.md) and:

1. Write the upgrade code in the same pull request. It runs at every startup, so it must be safe to run again.
2. Do not drop, rename, or change the type of a column or table that holds user data unless the same pull request copies the data first. If the data cannot be carried over, stop and ask the user.
3. Add a test that upgrades a database created with the old schema.
4. Add the `Data migration` section from that file to the pull request description and fill in every line.

When you review a pull request that changes a schema, request changes if the `Data migration` section is missing or a line is empty.

## HARD RULE: at most 5 open pull requests per author

**No author may have more than 5 open pull requests in this repository at any time. Drafts count. There are no exceptions** — not for urgent fixes, stacked pull requests, small changes, or a user request to "just open one more".

Before you open, reopen, or push a new branch for a pull request:

1. Count the author's open pull requests, drafts included:
   ```bash
   gh pr list --author "@me" --state open --json number --jq length
   ```
2. If the count is 5 or more, **do not open the pull request**. Stop and tell the user. Do not open it as a draft, under another branch name, or by splitting the work.
3. Combine related changes into one pull request instead of opening one per fix.

CI enforces this (`.github/workflows/pr-limit.yml`): for each author, the **5 oldest** open pull requests stay open and **every newer one is closed automatically** with a comment. A reopened pull request counts as the newest. To continue, merge or close one of the 5 oldest, then reopen the closed pull request. Bot accounts are exempt.

## Creating issues and pull requests

When creating a new GitHub issue or pull request, use the AI agent skills documented in [CONTRIBUTING.md](CONTRIBUTING.md#ai-agent-skills) instead of inventing an ad-hoc flow. Prefer:

- `cuga-github-issues` — open bugs, features, epics, and related issues (`bug_report.yml` / `feature_request.yml`), with epic → feature → issue hierarchy
- `cuga-contributor-workflows` — commit (Conventional Commits), create PRs via `gh`, and run ruff check/format

These skills are mirrored under `.cursor/skills/`, `.claude/skills/`, and `.bob/skills/` and follow repo conventions (templates, Conventional Commits, DCO signoff expectations, no promotional footers).

**Labels (MUST).** Before every push to a branch with a pull request, before opening a pull request, and when creating an issue, set the `design-impact:`, `design-review:`, `area:`, and (pull requests only) `evaluation:` and `readability:` labels with the rules in `.claude/skills/cuga-contributor-workflows/labels.md` (same file in the `.cursor/` and `.bob/` mirrors). CodeRabbit does not set these labels. When you change `labels.md` or another skill file, update all three mirrors.
