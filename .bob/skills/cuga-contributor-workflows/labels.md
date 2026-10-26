# Labels

**Required step (MUST).** Set or update the labels in this file:

- before **every push** to a branch that has, or will get, a pull request;
- before **opening a pull request**;
- when **creating an issue**.

These labels are set by contributors and their agents, not by CodeRabbit. The development guidelines use
`design-impact:` and `area:` to choose the design path, and `design-impact:` to choose the PR review format.

## Rules

1. Use only labels that exist (`gh label list --repo "$origin_repo"`). Never invent labels.
2. Exactly one `design-impact:` label on every pull request and issue.
3. One `area:` label for every area the work changes. A pull request that changes non-test code gets at least one.
4. Exactly one `readability:` label on every pull request. Issues do not get one.
5. Judge a pull request by its diff against `main`. Judge an issue by the work it asks for.
6. Mentions in tests, mocks, fixtures, comments, docstrings, or generated files do not count toward an area.
7. Before each push, check all three groups again: the diff may have changed. Remove labels that no longer apply.
8. If you cannot set labels (for example, a pull request from a fork), list the chosen labels in the body under
   `Suggested labels:` and tell the user.

## Design impact (exactly one)

Ask whether the work needs a design decision, not how big the diff is.

| Label | When |
|---|---|
| `design-impact: low` | No design decision needed. **All** of: follows an existing pattern (no new abstraction, extension point, or subsystem); no change to a public contract (API/SDK schemas, config keys, CLI commands, event or streaming protocols, stored data schemas); no new dependency, external service, or storage backend; one concern, easy to revert. Examples: a bug fix that restores documented behavior, a new tool or model provider added through the existing interface, a mechanical rename. A large diff can still be low. |
| `design-impact: medium` | Real logic, but bounded: new behavior in one or two areas, no new public contract, and no new pattern that other features will reuse. Use medium when any low criterion fails and no high criterion applies, or when an issue is too vague to judge. |
| `design-impact: high` | Needs a design decision. **Any** of: a new or changed public contract, a breaking change, or a data migration; a new subsystem, abstraction, or pattern that other features will reuse; changes to the agent loop, multi-agent orchestration, events, function calling, or sub-agent spawning; security, auth, sandboxing, or policy enforcement behavior; hard to reverse once released. A small diff can still be high. |

## Areas (one label per changed area)

Paths are hints; judge the content. `docs`, `llm-integrations`, and `cli` are the non-major areas in the design path.

| Label | Apply when the work changes | Typical paths | Does not count |
|---|---|---|---|
| `area: frontend` | UI components, styling, accessibility, client state, navigation, chat, dashboards, browser-extension UI, desktop UI | `src/frontend_workspaces/`, `src/cuga/backend/cuga_dashboard/` | A dependency-only update |
| `area: security` | Authentication, authorization, credential or secret handling, sandboxing, tenant/session isolation, untrusted input handling, sensitive data exposure or retention. Check all layers. | Any | A regenerated `.secrets.baseline`, a dependency bump, a description that only mentions security |
| `area: policies` | Guardrails, tool approval rules, policy matching, enforcement, playbooks, policy administration | `src/cuga/backend/cuga_graph/policy/` | Disabling or mocking the policy system in tests |
| `area: orchestration` | Agent graph execution, supervisor routing, delegation, multi-agent orchestration, sub-agent spawning, shared execution state, checkpoints, cancellation, retries, human-in-the-loop pause/resume | `cuga_graph/`, `supervisor/`, `supervisor_utils/`, `backend/agent_spawn/` under `src/cuga/` | A file under `cuga_graph/` on its path alone |
| `area: prompts` | LLM prompts, agent instructions, CUGA runtime skills, prompt construction, context supplied to a model, model output requirements (in Python, configuration, or Markdown) | Any | "Prompt" meaning a user's request text; ordinary documentation |
| `area: llm-integrations` | Model-provider integrations, model selection, inference parameters, reasoning settings, token accounting, model request/response handling | `src/cuga/backend/llm/`, `src/cuga/configurations/models/` | Prompt wording alone |
| `area: tools-integrations` | Tool registration, discovery, invocation, MCP clients or servers, external service adapters, runtime skill/tool loading | `src/cuga/backend/tools_env/`, `src/cuga/backend/skills/`, demo tools | — |
| `area: browser-automation` | Browser control, page understanding, browser sessions, web navigation, automated interaction with pages | `src/cuga/backend/browser_env/`, `src/cuga/backend/web_automation/` | A browser-extension UI change alone |
| `area: api-sdk` | Server endpoints, SDK methods, public request/response schemas, streaming or event protocols, A2A/ACP integration, API compatibility | `src/cuga/api/`, `src/cuga/sdk.py`, `src/cuga/sdk_core/`, `src/cuga/backend/server/` | Internal server helpers |
| `area: cli` | CLI commands, options, terminal output, interactive terminal flows, command-driven setup | `src/cuga/cli/` | — |
| `area: knowledge` | Knowledge ingestion, document processing, retrieval, embeddings, indexing, knowledge profiles | `src/cuga/backend/knowledge/`, `src/cuga/configurations/knowledge/` | — |
| `area: memory` | Conversation or agent memory, context summarization, memory retrieval, retained context across runs | `src/cuga/backend/memory/` | Loading or showing chat history in the UI alone |
| `area: storage` | Databases, data schemas, migrations, storage backends, filesystem read/write behavior, data import/export | `src/cuga/backend/storage/`, `src/cuga/dbs/` | Reading from an existing store without changing how data is stored |
| `area: configuration` | Application settings, defaults, environment-variable handling, feature flags, configuration loading, validation, compatibility | `src/cuga/config.py`, `src/cuga/settings.toml`, `src/cuga/configurations/`, `.env.example` | One new setting that only supports a feature in another area; CI-only edits (use `area: infra`) |
| `area: observability` | Logs, traces, metrics, activity tracking, telemetry callbacks, exported execution trajectories | `src/cuga/backend/observability/`, `src/cuga/backend/activity_tracker/` | — |
| `area: testing-evaluation` | Tests or evaluation are the main point: test tooling, shared fixtures, evaluation datasets, benchmarks, scoring, stability checks, or a pull request that is mostly tests | `tests/`, `src/cuga/evaluation/` | Tests that a feature or fix adds for its own code |
| `area: infra` | CI workflows, repository automation (including CodeRabbit configuration and these agent skills), containers, deployment, release processes, build tooling, packaging | `.github/`, `.coderabbit.yaml`, `.claude/`, `.cursor/`, `.bob/`, `Dockerfile*`, `deployment/`, `scripts/` | Issue-template prose alone (use `area: docs`) |
| `area: dependencies` | Added, removed, or updated dependencies, version constraints, overrides, or lockfile entries (Python, frontend, container base, GitHub Actions) | `pyproject.toml`, `uv.lock`, `package.json`, lockfiles | Edits to unrelated sections of a package manifest |
| `area: docs` | Documentation files: guides, READMEs, architecture documents, contributor instructions, documented examples | `docs/`, `*.md` | Docstrings, code comments, runtime prompt or skill Markdown |
| `area: licensing` | Licenses, notices, attribution, license policy, dependency license allowlists/baselines, license enforcement | `LICENSE`, `NOTICE`, `.github/licenses/`, `scripts/licenses/` | — |

## Readability (pull requests only, exactly one)

Before opening a pull request, improve the title and description until `readability: good` is true, then apply the
label that is true.

| Label | When |
|---|---|
| `readability: good` | A maintainer can grasp the goal in one read: a plain-language sentence up front (what problem this fixes or what it enables), then what, why, and how it was tested, without jargon. At most 10 changed files, or more files with a short note on why the diff is large. The title is short and goal-oriented. |
| `readability: fair` | Useful detail, but the goal is not obvious on first read: a thin summary (missing testing or motivation), 11–25 changed files without much context, or the goal buried under technical terms. Use fair instead of poor when only one point is weak and the first sentence states the goal plainly. |
| `readability: poor` | A maintainer would need to ask the author before reviewing. **Any** of: empty, one-line, or unfilled-template description; jargon-heavy title or description with no plain goal sentence; unclear user-visible or runtime problem; missing two or more of what changed, why, and how it was tested; more than 25 files with no explanation of scope; more than about 400 net changed lines with no walkthrough of the main parts; unrelated concerns bundled with no breakdown; a large mechanical diff (lockfiles, generated output, mass renames) not called out; 3 or more top-level areas with no note on what to review first; title or description that disagrees with the diff; breaking, security, auth, migration, or config changes not mentioned; no linked issue for a non-trivial change. |

## Procedure

### Pull request: before opening it and before every push

```bash
origin_url="$(git remote get-url origin)"
origin_repo="$(gh repo view "$origin_url" --json nameWithOwner -q .nameWithOwner)"
git fetch origin main
git diff --stat origin/main...HEAD   # files the pull request changes
git diff origin/main...HEAD          # read the changes; tests, mocks, and comments do not count toward areas
```

1. Choose the labels with the rules above.
2. **Opening a pull request:** pass every chosen label to `gh pr create` with `--label` (see [create-pr.md](create-pr.md)).
3. **Pushing:** find the open pull request for the branch. If there is none yet, keep the chosen labels and pass
   them when you open the pull request (step 2); there is nothing to update. If there is one, compare with its
   current labels and fix the difference before you push.

   ```bash
   branch="$(git branch --show-current)"
   pr="$(gh pr list --repo "$origin_repo" --head "$branch" --state open --json number -q '.[0].number')"
   [ -z "$pr" ] && echo "No open pull request for $branch yet; pass the labels to gh pr create."
   gh pr view "$pr" --repo "$origin_repo" --json labels \
     --jq '[.labels[].name | select(test("^(design-impact|area|readability): "))]'
   gh pr edit "$pr" --repo "$origin_repo" --add-label "<label to add>" --remove-label "<label to remove>"
   ```

4. Tell the user which labels you set, added, or removed.

### Issue: when creating it

1. Choose one `design-impact:` label and the `area:` labels from the work the issue asks for. Add the type labels
   that the issue skill requires (`bug`, `enhancement`, `needs-triage`, `type: epic`).
2. Pass them with `--label` on `gh issue create`, or as `labels[]` in the REST call.
