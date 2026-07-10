# CUGA events — test report

- **When:** 2026-07-09T20:46:36Z  (2026-07-09 16:46:36 EDT)
- **Commit:** `3c728f91` (3c728f91b6da508c4e68e5da4418f98af36b88e4) on `feat/events`
- **Tree:** DIRTY — not reproducible from this commit
- **Stack:** agents=18, AP=up, worker=cuga, integrations={'gmail': 'not_connected', 'box': 'connected', 'github': 'connected'}
- **Subscriptions:** 0 before → 0 after (no leak)
- **Raw logs:** `results/runs/20260709T204636Z/`

| Harness | Answers | Pass | Fail | XFail | XPass | Skip | Secs |
|---|---|--:|--:|--:|--:|--:|--:|
| `offline` | Do the pure-python invariants hold? (no stack, no creds) | 62 | **1** | 0 | 0 | 0 | 2 |
| `live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 32 | **1** | 0 | 0 | 1 | 94 |
| `now` | _skipped by request_ | | | | | | |
| `flows` | _skipped by request_ | | | | | | |
| `matrix` | _skipped by request_ | | | | | | |

## How to read this

- **`offline`** — the box-watermark test is a KNOWN pre-existing failure: it reads the real .box_since.json instead of a temp file. Not a regression.

## Verdict vocabulary

- **FAIL** — expected to work, broke. The only thing worth acting on immediately.
- **XFAIL** — a known gap, with its reason printed in the harness output. Not a regression.
- **XPASS** — a known gap started passing. Re-sample, then delete the expectation.
- **SKIP** — surface not configured. Never counted as a pass.

Only `live_suite` and (since 2026-07-09) `live_e2e`/`live_matrix` verify that an armed flow **really exists in Activepieces**; a bare `ap_flow_id` proves nothing, because `find_or_create_flow` de-duplicates without re-checking (`concierge.py:285-289`).

**None of these harnesses fire real data through an armed watcher.** They prove a flow is created correctly, not that it behaves correctly when a real event lands. For that: `live_gmail_e2e.py`, `live_box_e2e.py`, `live_github_e2e.py`.