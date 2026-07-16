# CUGA events — test report

- **When:** 2026-07-15T21:49:53Z  (2026-07-15 17:49:53 EDT)
- **Commit:** `4a1745ea` (4a1745ea15cc3f874a6fb477bf9ba453170b2c20) on `feat/events`
- **Tree:** DIRTY — not reproducible from this commit
- **Stack:** agents=27, AP=up, worker=cuga, integrations={'gmail': 'connected', 'box': 'connected', 'github': 'connected'}
- **Subscriptions:** 3 before → 3 after (no leak)
- **GITHUB_TEST_REPO:** `anupamamurthi/pachyderm` (github push row armed; webhooks created by the run are deleted afterwards)
- **Raw logs:** `results/runs/2026-07-15_17-49-53/`

| Harness | Answers | Pass | Fail | XFail | XPass | Skip | Secs |
|---|---|--:|--:|--:|--:|--:|--:|
| `offline` | Do the pure-python invariants hold? (no stack, no creds) | 199 | 0 | 0 | 0 | 0 | 43 |
| `live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 38 | 0 | 0 | 0 | 0 | 96 |
| `now` | _skipped by request_ | | | | | | |
| `flows` | Does an English sentence become the right Activepieces flow? | 11 | 0 | 0 | 0 | 0 | 50 |
| `matrix` | _skipped by request_ | | | | | | |
| `fire` | _skipped by request_ | | | | | | |
| `delegation` | Does the supervisor pick the right sub-agent? (labelled payloads, >=90% gate) | — | **CRASH** | — | — | — | 531 |


## End-to-end walkthrough

Exactly what a person would do, and exactly what came back. A blank verdict is scene-setting (posting the message), not an assertion — only rows with ✓/✗ are checked.

### channels

| Surface | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|:--:|
| `web` | you | open the web chat and type "what is the current price of bitcoin in usd? just the number" | the concierge routes it to pricebot and answers with a live price | Bitcoin is currently priced at $64,787 USD. | ✓ |
| `slack` | you | post "what is the current price of bitcoin in usd? just the number" in the Slack channel (C0BEYJ9NATB) | Slack accepts the message and Slack's Events API notifies CUGA | message posted, ts=1784152243.020809 |  |
| `slack` | Slack | POSTs the message event to /api/events/slack/events (a correctly-signed Slack event) | CUGA verifies the signature, acks in <3s, and answers in the background | HTTP 200 {"ok": true} | ✓ |
| `slack` | you | look at the thread under your message in Slack | the bot has replied in-thread with the bitcoin price | Bitcoin is currently priced at $64,787 USD. — cuga · 4.0s | ✓ |
| `discord` | you | type "what is the current price of bitcoin in usd? just the number" in the Discord channel (1522408587958423675)<br><sub>the Gateway socket itself is simulated: a bot cannot message itself (discord_direct.should_process drops bot authors)</sub> | the Gateway relays it to CUGA, which answers with a live price | 64,787 — cuga · 5.1s | ✓ |
| `discord` | you | scroll the Discord channel | the bot's reply is there — posted by a real REST call, not a mock | a new bot message is present | ✓ |
| `telegram` | you | message the bot @time4fun_bot with "what is the current price of bitcoin in usd? just the number"<br><sub>the Telegram → AP webhook hop is simulated; a bot cannot message itself</sub> | Activepieces' telegram webhook posts it to CUGA, which answers with a live price | Bitcoin is currently priced at $64,787 USD. — cuga · 4.1s | ✓ |
| `telegram` | you | open the Telegram chat with the bot | the bot's message is delivered for real (sendMessage) | delivered | ✓ |

### flows

| Surface | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|:--:|
| `web` | you | ask "what is the current price of bitcoin in usd? just the number" and expect an answer right now (no flow) | pricebot calls its real MCP tool and returns a live number | Bitcoin is currently priced at $64,787 USD. | ✓ |
| `cron` | you | say "every day at 9am send me new arxiv papers on mixture of experts" | the concierge arms a CRON flow and it really exists in Activepieces | ARMED cron for cuga → web. Flow name: "ea:cron-cuga-0_9_*_*_*-35c3". \| AP flow: 4DK6LsaMOu4E3uFUr35Td | ✓ |
| `poll` | you | say "watch bitcoin every 2 minutes and ping me on any move" | the concierge arms a POLL flow and it really exists in Activepieces | ARMED poll for cuga → web. Flow name: "ea:poll-cuga-2m-7b99". \| AP flow: d36xB1Vc5ootjwUiDoFbt | ✓ |
| `push:box` | you | say "when a resume lands in my Box, judge it against the JD and email me" | either a real Activepieces watcher on box, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | ARMED — AP flow ckY57WrjkmFTfAmtsyPkX (POLL · new) | ✓ |
| `push:github` | you | say "when a pull request opens on my repo, summarize it and message me" | either a real Activepieces watcher on github, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | asks for the missing trigger input: Which repository (owner/repo) should I watch? | ✓ |
| `push:gmail` | you | say "when an email from my boss arrives, summarize it and message me" | either a real Activepieces watcher on gmail, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | ARMED — AP flow YJh54tiUTY1Q0jOqjygiQ (PUSH · new) | ✓ |
| `webhook` | your monitoring system | POSTs {"alert":"HighCPU","service":"checkout-api","value":"97%"} to /api/events/hook/monitoring | incident_triage summarises it and assigns a P1/P2/P3 severity | The alert has been triaged as follows: - **Alert:** HighCPU - **Service:** checkout‑api - **Current value:** 97% (exceeds the 85% threshold) - **Severity:** **P1** (critical) - **Component:** checkout‑api service **Recommended first action… | ✓ |
| `webhook` | your CI system | POSTs {"event":"build.failed","repo":"…","status":"failed"} to /api/events/hook/monitoring | the SAME generic worker triages an arbitrary payload — not monitoring-specific | Here’s the concise triage report for the failed unit‑test job: **Event:** `build.failed` **Repository:** `anupamamurthi/pachyderm` (branch `main`) **Job:** `unit-tests` – **Status:** failed **Log Access:** The provided log URL (`https://ci… | ✓ |
| `webhook` | an external system (no agent named) | POSTs a PR-shaped payload to /api/events/hook/ci?route=1 | the ONE agent (cuga) handles it — its supervisor picks the specialist internally | agent=cuga, answered=True | ✓ |

## How to read this

- **`now`** — fleet-era; superseded in supervisor mode
- **`matrix`** — fleet-era; superseded in supervisor mode
- **`fire`** — fleet-era; superseded in supervisor mode
- **`delegation`** — harness did not run to completion (exit 2): make: *** [test-delegation] Error 1

## Verdict vocabulary

- **FAIL** — expected to work, broke. The only thing worth acting on immediately.
- **XFAIL** — a known gap, with its reason printed in the harness output. Not a regression.
- **XPASS** — a known gap started passing. Re-sample, then delete the expectation.
- **SKIP** — surface not configured. Never counted as a pass.

Only `live_suite` and (since 2026-07-09) `live_e2e`/`live_matrix` verify that an armed flow **really exists in Activepieces**; a bare `ap_flow_id` proves nothing, because `find_or_create_flow` de-duplicates without re-checking (`concierge.py:285-289`).

**None of these harnesses fire real data through an armed watcher.** They prove a flow is created correctly, not that it behaves correctly when a real event lands. For that: `live_gmail_e2e.py`, `live_box_e2e.py`, `live_github_e2e.py`.