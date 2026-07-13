# Testing

Two layers: an **offline** suite that runs on every change (no stack, no creds), and **live**
harnesses that drive the real stack. `SETUP.md` §"Which test do I run, and when?" is the task-oriented
version; here is the reference.

## Offline — the fast green gate

```bash
make test          # pytest tests/events -q   (156 checks, ~10s, no network)
```

Pure-Python invariants: envelope validation, flow builders, dedup, the API contract (every endpoint's
status codes + isolation, with AP faked), the Box download shaping, credential rotation, and the
**consistency gates** that fail the build if docs drift from code:

- `test_api_spec_is_golden` — `events_docs/api/api_spec.html` must match `scripts/gen_api_spec.py`.
- `test_every_route_appears_in_the_api_reference` — every route in `app.py` has a row in `api.html`.
- `test_integrations_auth_matches_the_oauth_provider_registry` — `connectors`, `oauth`, and
  `setup_guides` must agree on how each app connects.

Run this before pushing. A red offline gate is never "just flaky."

## Live — driving the real stack

Need `make up` + creds. Each is broader than the last.

| Command | Answers | ~Time |
|---|---|---|
| `make test-live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 2 min |
| `make test-suite-now` | Can each of the 18 agents do its job? (asserts on `meta.mcp`, so it can't pass from memory) | 14 min |
| `make test-suite-flows` | Does an English sentence become the right AP flow? | 6 min |
| `make test-matrix` | Is every trigger × sink combination wired? | 6 min |
| `make test-fire` | **Does an armed flow actually FIRE and answer?** | 9 min |
| `make doctor` | Live credential doctor — hit each service with its `.env` cred (never fails; reports) | 30 s |

### `test-fire` — the one that proves flows *run*

Every other harness stops at *armed* (a flow exists in AP). `test-fire` arms a real 1-minute schedule,
waits for a genuine tick, and reads the agent's answer out of the run log. It fires **cron/poll**
(real schedules) and **GitHub push** (a webhook trigger, fed a synthetic PR). It **cannot** fire a
Gmail or Box watcher, because those are app-*polling* triggers Activepieces will not run out of band —
for those, only a real inbound event proves the loop (see below).

Verdicts: `FIRED · ARMED · NOFIRE · FAIL · SKIP`. **ARMED and NOFIRE are not passes** — the flow
exists but no answer was observed (the schedule never came round, or firing would mutate a real
repo/inbox). Only `FIRED` proves the loop closes.

### Firing a real event through one integration

The polling watchers need a genuine event:

```bash
.venv/bin/python tests/events/live_github_e2e.py   # real open PR → pr_reviewer
.venv/bin/python tests/events/live_box_e2e.py      # real upload  → resume_judge  (needs a fresh BOX_DEV_TOKEN)
.venv/bin/python tests/events/live_gmail_e2e.py    # Gmail OAuth connection + arm the inbox watcher
```

## The consolidated report

```bash
GITHUB_TEST_REPO=owner/repo make test-report      # runs all 6 harnesses in order (~40 min)
```

Writes a timestamped, commit-stamped run to `results/runs/<UTC>/` and emits **two renderings of the
same run**: `report.md` (→ `results/LATEST.md`, for a PR) and `report.html` (→ `results/index.html`,
open with `make report`). Both carry the **end-to-end walkthrough** — a row per step in the second
person, with *utterance · channel · integration · trigger · expected · actually got* columns — plus a
"Did the flow actually fire?" table. Both are **generated**; never hand-edit `results/index.html`.

The report is stamped with the commit and whether the tree was **dirty** — a dirty tree means the
commit id does not describe the code that ran, so commit first if you want a citable result.

## What none of the arming harnesses prove

`test`, `test-live`, `test-suite-*`, `test-matrix` arm a flow and verify it exists in Activepieces —
they never wait for a real event. `test-fire` closes that gap for schedule/webhook triggers; the three
`live_*_e2e.py` close it for polling triggers. A green "armed" row is not a green "it works" row, and
the harnesses are careful to distinguish the two.

## Verdict vocabulary (across all harnesses)

- **PASS / FAIL** — worked / broke. FAIL is the only thing to act on immediately.
- **XFAIL** — a known gap, reason printed. Not a regression.
- **XPASS** — a known gap passed this time; re-sample before believing it (some agents are flaky).
- **SKIP** — surface not configured.
- **FIRED / ARMED / NOFIRE** — (fire harness) fired-and-answered / exists-but-didn't-fire / deliberately-not-fired.
- **CRASH** — the harness died before reporting. Silence is not success.

## When flows mysteriously stop firing

Check AP's tunnel first (`make tunnels`). The cloudflared quick tunnel is ephemeral; when it dies,
every flow fails with `INTERNAL_ERROR` on AP's payload callback and it looks like a code regression.
Fix: `make ap`. This is the single most common false alarm — see [GAPS.md](GAPS.md).
