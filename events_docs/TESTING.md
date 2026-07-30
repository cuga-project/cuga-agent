# Testing

Two layers: an **offline** suite that runs on every change (no stack, no creds), and **live**
harnesses that drive the real stack. `SETUP.md` §"Which test do I run, and when?" is the task-oriented
version; here is the reference.

## Offline — the fast green gate

```bash
make test          # pytest tests/events -q   (~30s, no stack, no network)
make test-all      # events + tests/unit (offline superset; some unit tests are pre-existing product failures)
```

Pure-Python invariants: envelope validation, flow builders, dedup, the API contract (every endpoint's
status codes + isolation, with AP faked), the Box download shaping, credential rotation, and the
**consistency gates** that fail the build if docs drift from code:

- `test_api_spec_is_golden` — `events_docs/api/api_spec.html` must match `scripts/gen_api_spec.py`.
- `test_every_route_appears_in_the_api_reference` — every route in `app.py` has a row in `api.html`.
- `test_examples_board_matches_the_catalog` — `examples.html` must match `events/catalog.py`.
- `test_slides_deck_matches_the_registry` — `events_docs/slides.html` (the deck) must match
  `triggers.py` + `catalog.py` (`make slides` regenerates).

### The supervisor gates — `test_supervisor_roster.py` + `live_delegation_bench.py`

The single-agent world (plans/SUPERVISOR_REFACTOR.md) moved routing from compiled arm-time
bindings to per-wake-up supervisor picks, so routing quality is now a measured gate:

- **Offline** (`test_supervisor_roster.py`): `supervisor_agents.yaml` parses; **every registry
  trigger is claimed** by some sub-agent's HANDLES line (an unclaimed trigger = the supervisor
  routes blind); no stale HANDLES hints after a trigger rename.
- **Live** (`make test-delegation`): the real supervisor over the full roster, labelled
  payloads across trigger families + chat + ambiguity traps. Gate: **≥90% pick accuracy, with
  self-answers counted as failures** (a hard zero proved brittle on a 14-case sample — consecutive
  runs measured 14/14 · 12/14 · 13/14; self-answer counts stay printed so drift is visible).

### The NL→Flow benchmark — `test_flowspec_bench.py`

47 hand-labelled cases (`utterance → expected FlowSpec`): a strict + a paraphrase case per push
trigger, cron/poll/now negatives, and genuine-ambiguity cases. Scores the deterministic resolver
(`events/flowspec.py`) that fronts the concierge. The gates, strongest first:

- **Zero wrong-at-high** — a high-confidence resolution that disagrees with the label is a user
  silently arming the WRONG watcher: zero tolerance. Falling back to the LLM path is always fine.
- **Every push trigger has a proven happy path**, **asks fire when a required slot is missing**
  (never a guess), **slot values extract verbatim**, and the bench must cover every registry row.
- The scorecard prints on every run (`pytest -s`): currently **fast-path 35/37 push cases (94%),
  correct-at-high 35/35**.

The ask-till-legit loop (park a question → the next message fills the slot → armed; a topic-change
reply is never crammed into the slot) is unit-tested in the same file and proven live in
`results/` runs.
- `test_integrations_auth_matches_the_oauth_provider_registry` — `connectors` and `oauth` must agree
  on how each app connects (a sibling test, `test_github_is_not_a_token_app_anywhere`, extends the same
  invariant to `setup_guides`).

### The trigger suite — `test_events_triggers.py` (22 checks)

The registry ([`events/triggers.py`](../src/cuga/backend/events/triggers.py)) is the source of truth
for all 33 triggers, so its tests are **parametrized over every row** — a new trigger is tested the
moment it is added, and cannot be added half-wired:

- `test_every_ap_row_builds_its_own_flow` — the core regression the registry exists to prevent:
  `(app, event)` must select *that event's* piece trigger and *that event's* payload map. The old code
  ignored `event` and armed the app default for everything.
- `test_classifier_eval_set_routes_every_trigger` — a **31-utterance labelled eval set**, one per
  NL-classified trigger (the 33 registry rows minus `webhook` and Telegram, which never take the NL
  path). A misroute here is a user arming the *wrong* watcher, silently.
  `test_classifier_eval_covers_every_ap_and_channel_trigger` stops the eval falling behind the
  registry.
- `test_github_synths_satisfy_the_pieces_real_run_filters` — pins **two Activepieces bugs** found by
  reading `piece-github@0.8.5`'s bundled source: its `new_release` trigger only accepts
  `action: "created"` while its own sample data ships `"published"`, and its `new_commit` trigger keeps
  only commits with `distinct: true`, which its sample omits. **A payload copied faithfully from either
  sample is silently discarded** — AP accepts the trigger and no run is ever created.
- `test_every_github_trigger_declares_its_delivery_header` (+ `test_debug_run_sends_the_github_delivery_header`,
  in `test_events_api_contract.py`) — one repo webhook carries *every* subscribed event type, so the piece disambiguates on
  `X-GitHub-Event`. A synthetic fire without it makes the piece emit **nothing**.
- `test_validate_gate_asks_for_missing_slots_and_rejects_unknowns` — the arm-time gate.
- `test_oauth_state_signature_roundtrip_tamper_and_expiry` — a forged/unsigned/expired `state` is a
  hard reject (it used to be trusted, allowing a connection hijack).
- `test_dedup_unique_index_turns_the_race_into_reuse` — the check-then-write race is now refereed by a
  DB constraint.
- `test_direct_match_applies_config_filters` / `test_direct_dispatch_posts_the_invoke_envelope` — the
  direct (Slack/Discord/Telegram) watcher matching + dispatch.

### Live — every GitHub trigger, end to end

```bash
.venv/bin/python tests/events/live_github_triggers.py            # all 14
.venv/bin/python tests/events/live_github_triggers.py new_star   # one
```

For each of the 14 GitHub triggers: **arm** (a real Activepieces flow whose publish creates a real
repo webhook) → **fire** synthetically via `POST /subscriptions/{id}/run` with the piece's real payload
and delivery header → assert a real agent answer → **clean up** (delete the subscription *and* strip
the repo webhooks — deleting an AP flow does **not** remove its webhook).

**Safety:** hard-pinned to `anupamamurthi/pachyderm` (`ALLOWED_REPOS`) and it only ever creates and
deletes repo *webhooks* — never an issue, PR, comment, or any content. Last run: **14/14 in 91s.**

Run this before pushing. A red offline gate is never "just flaky."

## Live — driving the real stack

Need `make up` + creds. Each is broader than the last.

| Command | Answers | ~Time |
|---|---|---|
| `make test-live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 2 min |
| `make test-suite-flows` | Does an English sentence become the right AP flow? | 6 min |
| `make test-delegation` | Does the supervisor route to the right sub-agent? (≥90% gate over the real roster) | 10 min |
| `make test-exhaustive` | **Everything**: every agent + every registry trigger armed AND fired, answer-QUALITY gated (planted markers + a forbid-list), REAL/SYNTH/BLOCKED marked honestly, zero-leak cleanup gate | 45–75 min |
| `make doctor` | Live credential doctor — hit each service with its `.env` cred (never fails; reports). Detects the dead-AP-tunnel failure (baked `AP_FRONTEND_URL` unresolvable → every flow run dies) by name | 30 s |
| `make test-suite-now` / `test-matrix` / `test-fire` | **fleet-era** — they assert per-agent invocation by name and auto-skip under `EVENTS_SUPERVISOR=1`; `test-exhaustive` + `test-delegation` are their supervisor-world replacements | — |

### `test-exhaustive` — the one that proves the whole matrix *runs*

Arming is never enough: `make test-exhaustive` (tests/events/live_exhaustive.py, design in
plans/EXHAUSTIVE_MATRIX.md) applies THREE gates per case — **ARMED** (the flow really exists),
**FIRED** (an event traverses the real path), **QUALITY** (the answer contains the case's planted
markers and none of the forbid-list: executor scaffolding, deliberation leaks, refusals,
loop-attempts). Every registry trigger and every roster agent is covered — the case table is
GENERATED from `triggers.rows()` + `supervisor_agents.yaml` + `catalog.py`, so a new trigger or
agent without a case fails the run. Legs print **REAL / SYNTH / BLOCKED(reason)** honestly; the
final gate asserts zero leaked subscriptions. Gmail/Box app-polling triggers fire at the /invoke
seam (SYNTH); real inbound events close those (see below).

### Firing a real event through one integration

The polling watchers need a genuine event:

```bash
.venv/bin/python tests/events/live_github_real_pr.py  # REAL branch+PR on the pinned repo → genuine
                                                      #   webhook fire → review → auto-cleanup
.venv/bin/python tests/events/live_box_e2e.py         # real upload → resume_judge (fresh BOX_DEV_TOKEN)
.venv/bin/python tests/events/live_gmail_e2e.py       # connection + arm + a synthetic-fire QUALITY
                                                      #   gate (40KB email, deliberation-leak check);
                                                      #   the REAL leg = send an email to the account
```

## The consolidated report

```bash
GITHUB_TEST_REPO=owner/repo make test-report      # the harness ladder in order (~15 min in
                                                  # supervisor mode: offline · live · flows ·
                                                  # delegation; fleet-era rungs auto-skip)
```

Writes a timestamped, commit-stamped run to `results/runs/<UTC>/` and emits **two renderings of the
same run**: `report.md` (→ `results/LATEST.md`, for a PR) and `report.html` (→ `results/index.html`,
open with `make report`). Both carry the **end-to-end walkthrough** — a row per step in the second
person, with *utterance · channel · integration · trigger · expected · actually got* columns — plus a
"Did the flow actually fire?" table. Both are **generated**; never hand-edit `results/index.html`.

The report is stamped with the commit and whether the tree was **dirty** — a dirty tree means the
commit id does not describe the code that ran, so commit first if you want a citable result.

## What none of the arming harnesses prove

`test`, `test-live`, `test-suite-flows` arm a flow and verify it exists in Activepieces — they never
wait for a real event. `test-exhaustive` closes that gap for every synthetic-fireable trigger (and
labels the rest BLOCKED with the unblock); the `live_*_e2e.py` harnesses close it with genuine
events (real PR, real upload, real email). A green "armed" row is not a green "it works" row, and
the harnesses are careful to distinguish the two.

## Verdict vocabulary (across all harnesses)

- **PASS / FAIL** — worked / broke. FAIL is the only thing to act on immediately.
- **XFAIL** — a known gap, reason printed. Not a regression.
- **XPASS** — a known gap passed this time; re-sample before believing it (some agents are flaky).
- **SKIP** — surface not configured.
- **FIRED / ARMED / NOFIRE** — (fire harness) fired-and-answered / exists-but-didn't-fire / deliberately-not-fired.
- **CRASH** — the harness died before reporting. Silence is not success.

## When flows mysteriously stop firing

Run `make doctor` first — it now detects this failure BY NAME (it resolves the container's baked
`AP_FRONTEND_URL`; a dead quick-tunnel hostname means every flow run dies at AP's payload callback
with `INTERNAL_ERROR` while arming still "works"). Fix: `make ap` then `make channels`. This is the
single most common false alarm — see [GAPS.md](GAPS.md).
