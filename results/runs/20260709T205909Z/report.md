# CUGA events — test report

- **When:** 2026-07-09T20:59:09Z  (2026-07-09 16:59:09 EDT)
- **Commit:** `3c728f91` (3c728f91b6da508c4e68e5da4418f98af36b88e4) on `feat/events`
- **Tree:** DIRTY — not reproducible from this commit
- **Stack:** agents=18, AP=up, worker=cuga, integrations={'gmail': 'not_connected', 'box': 'connected', 'github': 'connected'}
- **Subscriptions:** 0 before → 0 after (no leak)
- **Raw logs:** `results/runs/20260709T205909Z/`

| Harness | Answers | Pass | Fail | XFail | XPass | Skip | Secs |
|---|---|--:|--:|--:|--:|--:|--:|
| `offline` | Do the pure-python invariants hold? (no stack, no creds) | 62 | **1** | 0 | 0 | 0 | 2 |
| `live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 33 | 0 | 0 | 0 | 1 | 45 |
| `now` | _skipped by request_ | | | | | | |
| `flows` | _skipped by request_ | | | | | | |
| `matrix` | _skipped by request_ | | | | | | |


## End-to-end walkthrough

Exactly what a person would do, and exactly what came back. A blank verdict is scene-setting (posting the message), not an assertion — only rows with ✓/✗ are checked.

### channels

| Surface | Who | Does what | Expected | Actually got | |
|---|---|---|---|---|:--:|
| `web` | you | open the web chat and type "what is the current price of bitcoin in usd? just the number" | the concierge routes it to pricebot and answers with a live price | The current price of Bitcoin is $63,218 USD. | ✓ |
| `slack` | you | post "what is the current price of bitcoin in usd? just the number" in the Slack channel (C0BEYJ9NATB) | Slack accepts the message and Slack's Events API notifies CUGA | message posted, ts=1783630758.450769 |  |
| `slack` | Slack | POSTs the message event to /api/events/slack/events (an unsigned Slack event (no signing secret configured)) | CUGA verifies the signature, acks in <3s, and answers in the background | HTTP 200 {"ok": true} | ✓ |
| `slack` | you | look at the thread under your message in Slack | the bot has replied in-thread with the bitcoin price | Bitcoin is currently **$63,240 USD**. — pricebot · via cuga-finance · 6.4s | ✓ |
| `discord` | you | type "what is the current price of bitcoin in usd? just the number" in the Discord channel (1522408587958423675)<br><sub>the Gateway socket itself is simulated: a bot cannot message itself (discord_direct.should_process drops bot authors)</sub> | the Gateway relays it to CUGA, which answers with a live price | $63,257. | ✓ |
| `discord` | you | scroll the Discord channel | the bot's reply is there — posted by a real REST call, not a mock | a new bot message is present | ✓ |
| `telegram` | you | message the bot @time4fun_bot with "what is the current price of bitcoin in usd? just the number"<br><sub>the Telegram → AP webhook hop is simulated; a bot cannot message itself</sub> | Activepieces' telegram webhook posts it to CUGA, which answers with a live price | 63218 | ✓ |
| `telegram` | you | open the Telegram chat with the bot | the bot's message is delivered for real (sendMessage) | delivered | ✓ |

### flows

| Surface | Who | Does what | Expected | Actually got | |
|---|---|---|---|---|:--:|
| `web` | you | ask "what is the current price of bitcoin in usd? just the number" and expect an answer right now (no flow) | pricebot calls its real MCP tool and returns a live number | Bitcoin is $63,240 USD. | ✓ |
| `cron` | you | say "every day at 9am send me new arxiv papers on mixture of experts" | the concierge arms a CRON flow and it really exists in Activepieces | Armed a daily 9 am cron flow with the **papers** agent to fetch and summarize new arXiv papers on mixture of e \| AP flow: nWf6EB7fLJV1o8qIlSht3 | ✓ |
| `poll` | you | say "watch bitcoin every 2 minutes and ping me on any move" | the concierge arms a POLL flow and it really exists in Activepieces | Armed poll for pricebot to watch Bitcoin every 2 minutes and notify you on any change. \| AP flow: sPJnHnWQWSfqrQjOvTFIm | ✓ |
| `push:box` | you | say "when a resume lands in my Box, judge it against the JD and email me"<br><sub>verdict on the next row — armed / connect-needed / needs-a-slot are all correct</sub> | an integration watcher on box is armed with a real Activepieces flow (or, if box is not connected, the concierge asks you to connect it) | CONNECT NEEDED — connect your gmail: https://pregnant-reveal-defile.ngrok-free.dev/api/events/connect/gmail?scope=default/default/admin&agent=resume_judge |  |
| `push:github` | you | say "when a pull request opens on my repo, summarize it and message me"<br><sub>verdict on the next row — armed / connect-needed / needs-a-slot are all correct</sub> | an integration watcher on github is armed with a real Activepieces flow (or, if github is not connected, the concierge asks you to connect it) | Which repository (owner/repo) should I watch for new pull requests? |  |
| `push:gmail` | you | say "when an email from my boss arrives, summarize it and message me"<br><sub>verdict on the next row — armed / connect-needed / needs-a-slot are all correct</sub> | an integration watcher on gmail is armed with a real Activepieces flow (or, if gmail is not connected, the concierge asks you to connect it) | CONNECT NEEDED — connect your gmail: https://pregnant-reveal-defile.ngrok-free.dev/api/events/connect/gmail?scope=default/default/admin&agent=mailbot |  |
| `webhook` | your monitoring system | POSTs {"alert":"HighCPU","service":"checkout-api","value":"97%"} to /api/events/hook/monitoring | incident_triage summarises it and assigns a P1/P2/P3 severity | HighCPU alert on checkout‑api: CPU at 97% (threshold 85%) – P1 severity – Component: checkout‑api service – First action: investigate the CPU spike (check logs, metrics, and consider scaling or restarting the service). — incident_triage · … | ✓ |

## How to read this

- **`offline`** — the box-watermark test is a KNOWN pre-existing failure: it reads the real .box_since.json instead of a temp file. Not a regression.

## Verdict vocabulary

- **FAIL** — expected to work, broke. The only thing worth acting on immediately.
- **XFAIL** — a known gap, with its reason printed in the harness output. Not a regression.
- **XPASS** — a known gap started passing. Re-sample, then delete the expectation.
- **SKIP** — surface not configured. Never counted as a pass.

Only `live_suite` and (since 2026-07-09) `live_e2e`/`live_matrix` verify that an armed flow **really exists in Activepieces**; a bare `ap_flow_id` proves nothing, because `find_or_create_flow` de-duplicates without re-checking (`concierge.py:285-289`).

**None of these harnesses fire real data through an armed watcher.** They prove a flow is created correctly, not that it behaves correctly when a real event lands. For that: `live_gmail_e2e.py`, `live_box_e2e.py`, `live_github_e2e.py`.