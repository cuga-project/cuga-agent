# Labels

## Start with what changes and its impact (MUST)

Assess the work before choosing labels. Labels summarize this assessment; they do not replace it.

1. Read the full PR diff against `main`, relevant code, and validation evidence. For an issue, read the requested
   behavior, requirements, and available evidence; distinguish proposed work from implemented behavior.
2. Summarize the concrete change in each affected area and its user-visible or operational consequence. Distinguish
   substantial changes from bounded fixes and supporting edits. Do not infer impact from line count or label count.
3. Infer `area:` labels from the behavior actually changed, using the definitions below. A small behavior change can
   warrant a label. A supporting edit does not warrant another label unless it independently meets that area's
   definition. Do not repeat the same explanation under several areas; group related areas when useful.
4. Choose one overall `design-impact:` label using the criteria below. Explain which criterion applies in one short
   sentence. This is design significance, not a claim that every affected area has a major change.
5. Choose one `design-review:` label from the design path below. It states whether a brainstorm or design review is
   required before merge, so readers do not have to work it out from the other labels.
6. For a PR, choose one `evaluation:` label: does the change need a benchmark run before merge because agent
   accuracy can go up or down?
7. Update the PR or issue description with the assessment before creating it or applying labels. Preserve the
   existing template sections, requirements, links, and useful context; update an existing assessment rather than
   appending duplicates. For an existing PR, refresh it before each push when scope or impact changes. For an
   existing issue, refresh it when updating its scope or labels.

For a **PR**, add a concise table under `What changed and impact`:

| Area | What changed | Impact |
|---|---|---|
| <affected area or related areas> | <concrete before/after behavior> | <consequence and extent of the change> |

Keep validation in the PR's testing section and distinguish verified behavior from assumptions or checks not run.
For an **issue**, use `Proposed change and expected impact` instead:

| Area | Proposed change | Expected impact |
|---|---|---|
| <affected area or related areas> | <requested behavior> | <expected consequence; note uncertainty> |

Keep small changes to one short row. Below either table, add one line per label group:

```text
Design impact: <low|medium|high> — <reason>
Design review: <needed|done|not needed> — <design path cell, or link to the recorded decision>
Evaluation: <needed|done|not needed> — <trigger that applies, or link to the results comment>
```

Issues get the first two lines; `Evaluation:` is for PRs only.
Do not invent affected areas, implementation details, or validation results to fill the table. If description edits
are unavailable, provide the proposed text to the user and state that it was not saved.

## Required labels

**Required step (MUST).** Set or update the labels in this file:

- before **every push** to a branch that has, or will get, a pull request;
- before **opening a pull request**;
- when **creating an issue** or updating its scope or labels.

These labels are set by contributors and their agents, not by CodeRabbit. The development guidelines use
`design-impact:` and `area:` to choose the design path, `design-review:` to record its result, and `design-impact:`
to choose the PR review format. **`design-review: needed` and `evaluation: needed` hold the merge.**

## Rules

1. Use only labels that exist (`gh label list --repo "$origin_repo"`). Never invent labels.
2. Exactly one `design-impact:` label on every pull request and issue.
3. Exactly one `design-review:` label on every pull request and issue.
4. One `area:` label for every area the work changes. A pull request that changes non-test code gets at least one.
5. Exactly one `evaluation:` label and exactly one `readability:` label on every pull request. Issues get neither.
6. Judge a pull request by its diff against `main`, not by the increment over the PR it is stacked on. Judge an
   issue by the work it asks for.
7. Mentions in tests, mocks, fixtures, comments, docstrings, or generated files do not count toward an area.
8. Before each push, check all five groups again: the diff may have changed. Remove labels that no longer apply.
9. If you cannot set labels (for example, a pull request from a fork), list the chosen labels in the body under
   `Suggested labels:` and tell the user.

## Design impact (exactly one)

Ask whether the work needs a design decision, not how big the diff is. When any high criterion applies, the label is
high, even when a low example also fits (for example, a bug fix in a prompt or in the agent loop).

| Label | When |
|---|---|
| `design-impact: low` | No design decision needed. **All** of: follows an existing pattern (no new abstraction, extension point, or subsystem); no change to a public contract (API/SDK schemas, config keys, CLI commands, event or streaming protocols, stored data schemas); no change to what the model is told or to the agent loop; no new dependency, external service, or storage backend; one concern, easy to revert. Examples: a bug fix that restores documented behavior, a new tool or model provider added through the existing interface, a mechanical rename. A large diff can still be low. |
| `design-impact: medium` | Real logic, but bounded: new behavior in one or two areas, no new public contract, and no new pattern that other features will reuse. Use medium when any low criterion fails and no high criterion applies, or when an issue is too vague to judge. |
| `design-impact: high` | Needs a design decision. **Any** of: a new or changed public contract, a breaking change, or a data migration (a new optional SDK parameter, API field, config key, or CLI command counts, even when it is additive and off by default: once released, it must stay supported); a new subsystem, abstraction, or pattern that other features will reuse; changes to the agent loop (planning, how model replies are parsed or executed, reflection, VERIFY, retries, finalization), multi-agent orchestration, events, function calling, or sub-agent spawning; **any change to what the model is told**: runtime prompt text, agent instructions, few-shot examples, model output requirements, descriptions of the agent's built-in tools, or runtime skills (`area: prompts`), including wording edits, removals, and restores; security, auth, sandboxing, or policy enforcement behavior; hard to reverse once released. A small diff can still be high. |

Why prompt changes are high: #1102 shortened the agent, reflection, and answer prompts, was labeled low, and merged
without a benchmark run. It removed rules that AppWorld tasks depended on (#1108, #1110), and two follow-up PRs
(#1111, #1112) restore them.

## Design review (exactly one)

This label is the result of the design path. It answers one question: must a brainstorm or design review happen
before this work merges?

| Label | When |
|---|---|
| `design-review: needed` | The design path table says brainstorm, or the PR review's last gate says yes, and no decision is recorded on the issue yet. Holds the merge. |
| `design-review: done` | The brainstorm, design review, or board decision is recorded as a comment on the linked issue. Link that comment in the description. Never set `done` without the link. |
| `design-review: not needed` | The design path table says skip design, and the last gate has not said yes. |

Design path. Non-major areas are `docs`, `llm-integrations`, and `cli`; every other `area:` label is major.

| Design impact | No major area | One major area | 2+ major areas |
|---|---|---|---|
| low | not needed | not needed | not needed |
| medium | not needed | needed (brainstorm on the issue, async) | needed (brainstorm meeting) |
| high | needed (brainstorm meeting) | needed (brainstorm meeting) | needed (brainstorm meeting) |

A PR takes the issue's `design-review:` label; with no linked issue, choose it from the table. Change it to `needed`
when the PR has a major area or a higher design impact than the issue, or deviates from the recorded decision.

## Evaluation (pull requests only, exactly one)

Ask whether the change can make the agent pass more or fewer benchmark tasks. Unit tests do not answer this: #1102
passed 2899 unit tests.

| Label | When |
|---|---|
| `evaluation: needed` | Any trigger below applies. Holds the merge until benchmark results are posted on the PR and accepted. |
| `evaluation: done` | A results comment compares the PR with `main`, a reviewer accepted it, and no trigger file changed after the evaluated commit. Link the comment in the description. |
| `evaluation: not needed` | No trigger applies. |

Triggers (any one is enough):

- **What the model is told:** any `area: prompts` change (runtime prompts, agent instructions, few-shot examples,
  model output requirements, built-in tool descriptions, runtime skills).
- **Agent loop:** planning, code generation and extraction, reply parsing, reflection, VERIFY, retries,
  auto-continue, step or tool-call caps, finalization, and final answer formatting.
- **Tool selection:** shortlisting, `find_tools`, tool binding modes, and how tool schemas are shown to the model.
- **Context:** conversation history, memory, summarization, truncation, and context window sizing.
- **Model defaults:** default model, model profiles, reasoning effort, temperature, and other inference settings.
- **Retrieval quality:** embedding models, chunking, ranking, rerankers, and keyword scoring (`area: knowledge`).

How to run it:

1. Ask a person listed in `.github/workflows/run-pr-eval.yml` to comment on the PR, for example
   `/run-pr-eval sha=<40-character PR head SHA> agent=cuga benchmark=appworld eval_key=test_med`. The workflow
   supports `benchmark=appworld` and `benchmark=m3`. Other candidate benchmarks in `cuga-eval` (for example `tau2`,
   `automationbench`, `enterpriseops_gym`) run outside the workflow; name the one you used.
2. Compare with `main` on the same day, with the same model, settings, and task set. Report the pass rate on each
   side and the tasks that each side passes and the other fails. #800 and #1003 show this format.
3. If a trigger file changes after the run, set the label back to `needed` and run again. Changes to tests or docs
   alone do not need a new run.
4. If the PR loses tasks, fix it, or record the accepted trade-off in the PR and get a maintainer's approval.
5. When no benchmark covers the change (for example, retrieval quality), say so in the PR and ask a maintainer which
   check replaces it. The label stays `needed` until the maintainer answers.

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

1. Complete the assessment above, infer the labels, and prepare or update the PR description with the table and
   the three label lines. Then choose `readability:` based on the resulting description.
2. **Opening a pull request:** pass every chosen label to `gh pr create` with `--label` (see [create-pr.md](create-pr.md)).
3. **Pushing:** find the open pull request for the branch. If there is none yet, keep the chosen labels and pass
   them when you open the pull request (step 2); there is nothing to update. If there is one, compare with its
   current labels and fix the difference before you push.

   ```bash
   branch="$(git branch --show-current)"
   pr="$(gh pr list --repo "$origin_repo" --head "$branch" --state open --json number -q '.[0].number')"
   [ -z "$pr" ] && echo "No open pull request for $branch yet; pass the labels to gh pr create."
   gh pr view "$pr" --repo "$origin_repo" --json labels \
     --jq '[.labels[].name | select(test("^(design-impact|design-review|area|evaluation|readability): "))]'
   gh pr edit "$pr" --repo "$origin_repo" --add-label "<label to add>" --remove-label "<label to remove>"
   ```

4. Verify the saved description and labels. Tell the user which labels you set, added, or removed and whether
   the description was updated.

### Issue: when creating it or updating its scope or labels

1. Complete the assessment above, infer one `design-impact:` label, one `design-review:` label, and the `area:`
   labels, and prepare or update the issue description with the proposed-change table and the two label lines. Add the type labels that the
   issue skill requires (`bug`, `enhancement`, `needs-triage`, `type: epic`).
2. On creation, pass them with `--label` on `gh issue create`, or as `labels[]` in the REST call. For an existing
   issue, read its current body first, preserve useful context, and apply the updated body and labels with
   `gh issue edit`. Use `--body-file` for description updates, including `gh pr edit`, to preserve Markdown.
3. Verify the saved description and labels, then tell the user what changed.
