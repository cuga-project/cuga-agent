# CUGA events — test report

- **When:** 2026-07-16T16:46:26Z  (2026-07-16 12:46:26 EDT)
- **Commit:** `d6a79d23` (d6a79d230dad7c4c1fa8a685dce4a15355303c11) on `feat/events_1`
- **Tree:** DIRTY — not reproducible from this commit
- **Stack:** agents=27, AP=up, worker=cuga, integrations={'gmail': 'connected', 'box': 'connected', 'github': 'connected'}
- **Subscriptions:** 2 before → 2 after (no leak)
- **Raw logs:** `results/runs/2026-07-16_12-46-26/`

| Harness | Answers | Pass | Fail | XFail | XPass | Skip | Secs |
|---|---|--:|--:|--:|--:|--:|--:|
| `offline` | Do the pure-python invariants hold? (no stack, no creds) | 203 | 0 | 0 | 0 | 0 | 44 |
| `live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 38 | 0 | 0 | 0 | 0 | 174 |
| `now` | _skipped by request_ | | | | | | |
| `flows` | Does an English sentence become the right Activepieces flow? | 10 | 0 | 1 | 0 | 0 | 46 |
| `matrix` | _skipped by request_ | | | | | | |
| `fire` | _skipped by request_ | | | | | | |
| `delegation` | Does the supervisor pick the right sub-agent? (labelled payloads, >=90% gate) | 14 | 0 | 0 | 0 | 0 | 619 |


## End-to-end walkthrough

Exactly what a person would do, and exactly what came back. A blank verdict is scene-setting (posting the message), not an assertion — only rows with ✓/✗ are checked.

### channels

| Surface | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|:--:|
| `web` | you | open the web chat and type "what is the current price of bitcoin in usd? just the number" | the concierge routes it to pricebot and answers with a live price | The current price of Bitcoin is $64,329. | ✓ |
| `slack` | you | post "what is the current price of bitcoin in usd? just the number" in the Slack channel (C0BEYJ9NATB) | Slack accepts the message and Slack's Events API notifies CUGA | message posted, ts=1784220449.064119 |  |
| `slack` | Slack | POSTs the message event to /api/events/slack/events (a correctly-signed Slack event) | CUGA verifies the signature, acks in <3s, and answers in the background | HTTP 200 {"ok": true} | ✓ |
| `slack` | you | look at the thread under your message in Slack | the bot has replied in-thread with the bitcoin price | The current price of Bitcoin is $64,329 USD. — cuga · 9.5s | ✓ |
| `discord` | you | type "what is the current price of bitcoin in usd? just the number" in the Discord channel (1522408587958423675)<br><sub>the Gateway socket itself is simulated: a bot cannot message itself (discord_direct.should_process drops bot authors)</sub> | the Gateway relays it to CUGA, which answers with a live price | The current price of Bitcoin is $64,329 USD. — cuga · 5.6s | ✓ |
| `discord` | you | scroll the Discord channel | the bot's reply is there — posted by a real REST call, not a mock | a new bot message is present | ✓ |
| `telegram` | you | message the bot @time4fun_bot with "what is the current price of bitcoin in usd? just the number"<br><sub>the Telegram → AP webhook hop is simulated; a bot cannot message itself</sub> | Activepieces' telegram webhook posts it to CUGA, which answers with a live price | The current price of Bitcoin is $64,329. — cuga · 10.4s | ✓ |
| `telegram` | you | open the Telegram chat with the bot | the bot's message is delivered for real (sendMessage) | delivered | ✓ |

### flows

| Surface | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|:--:|
| `web` | you | ask "what is the current price of bitcoin in usd? just the number" and expect an answer right now (no flow) | pricebot calls its real MCP tool and returns a live number | 64329 | ✓ |
| `cron` | you | say "every day at 9am send me new arxiv papers on mixture of experts" | the concierge arms a CRON flow and it really exists in Activepieces | ARMED cron for cuga → web. Flow name: "ea:cron-cuga-0_9_*_*_*-c687" (subscription cuga-063454). \| AP flow: 9WMmOcwygL92sAIGd1kP1 | ✓ |
| `poll` | you | say "watch bitcoin every 2 minutes and ping me on any move" | the concierge arms a POLL flow and it really exists in Activepieces | ARMED poll for cuga → web. Flow name: "ea:poll-cuga-2m-e056". \| AP flow: np599txhEHOifFZzAiVy4 | ✓ |
| `push:box` | you | say "when a resume lands in my Box, judge it against the JD and email me" | either a real Activepieces watcher on box, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | ARMED — AP flow eQjIRQAQguBITJlo5g5za (POLL · new) | ✓ |
| `push:github` | you | say "when a pull request opens on my repo, summarize it and message me" | either a real Activepieces watcher on github, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | asks for the missing trigger input: Which repository (owner/repo) should I watch? | ✓ |
| `push:gmail` | you | say "when an email from my boss arrives, summarize it and message me" | either a real Activepieces watcher on gmail, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | ARMED — AP flow ZfAoceYUlrDaA331pKFqa (PUSH · new) | ✓ |
| `webhook` | your monitoring system | POSTs {"alert":"HighCPU","service":"checkout-api","value":"97%"} to /api/events/hook/monitoring | incident_triage summarises it and assigns a P1/P2/P3 severity | The alert has been triaged: - **Alert:** HighCPU on `checkout-api` (97% > 85%) – **P1** severity - **Component:** checkout‑api service - **Recommended action:** Immediately investigate the instance’s CPU usage (check running processes, rec… | ✓ |
| `webhook` | your CI system | POSTs {"event":"build.failed","repo":"…","status":"failed"} to /api/events/hook/monitoring | the SAME generic worker triages an arbitrary payload — not monitoring-specific | The build for **anupamamurthi/pachyderm** on the **main** branch has failed (job: **unit‑tests**). This is classified as a **P2** severity incident. **Next steps:** 1. Open the log at the provided URL: https://ci/logs/42. 2. Identify the s… | ✓ |
| `webhook` | an external system (no agent named) | POSTs a PR-shaped payload to /api/events/hook/ci?route=1 | the ONE agent (cuga) handles it — its supervisor picks the specialist internally | agent=cuga, answered=True | ✓ |

## How to read this

- **`now`** — fleet-era; superseded in supervisor mode
- **`matrix`** — fleet-era; superseded in supervisor mode
- **`fire`** — fleet-era; superseded in supervisor mode

## Verdict vocabulary

- **FAIL** — expected to work, broke. The only thing worth acting on immediately.
- **XFAIL** — a known gap, with its reason printed in the harness output. Not a regression.
- **XPASS** — a known gap started passing. Re-sample, then delete the expectation.
- **SKIP** — surface not configured. Never counted as a pass.

Only `live_suite` and (since 2026-07-09) `live_e2e`/`live_matrix` verify that an armed flow **really exists in Activepieces**; a bare `ap_flow_id` proves nothing, because `find_or_create_flow` de-duplicates without re-checking (`concierge.py:285-289`).

**None of these harnesses fire real data through an armed watcher.** They prove a flow is created correctly, not that it behaves correctly when a real event lands. For that: `live_gmail_e2e.py`, `live_box_e2e.py`, `live_github_e2e.py`.