# CUGA events — test report

- **When:** 2026-07-09T21:20:43Z  (2026-07-09 17:20:43 EDT)
- **Commit:** `3c728f91` (3c728f91b6da508c4e68e5da4418f98af36b88e4) on `feat/events`
- **Tree:** DIRTY — not reproducible from this commit
- **Stack:** agents=18, AP=up, worker=cuga, integrations={'gmail': 'not_connected', 'box': 'connected', 'github': 'connected'}
- **Subscriptions:** 0 before → 0 after (no leak)
- **GITHUB_TEST_REPO:** `anupamamurthi/pachyderm` (github push row armed; webhooks created by the run are deleted afterwards)
- **Raw logs:** `results/runs/20260709T212043Z/`

| Harness | Answers | Pass | Fail | XFail | XPass | Skip | Secs |
|---|---|--:|--:|--:|--:|--:|--:|
| `offline` | Do the pure-python invariants hold? (no stack, no creds) | 62 | **1** | 0 | 0 | 0 | 2 |
| `live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 33 | 0 | 0 | 0 | 1 | 47 |
| `now` | Can each of the 18 agents actually do its job? (asserts on meta.mcp) | 18 | 0 | 1 | 1 | 0 | 421 |
| `flows` | Does an English sentence become the right Activepieces flow? | 7 | **3** | 1 | 0 | 0 | 55 |
| `matrix` | Is every trigger x sink combination wired, or only the ones we tried? | 13 | 0 | 12 | 0 | 3 | 119 |


## End-to-end walkthrough

Exactly what a person would do, and exactly what came back. A blank verdict is scene-setting (posting the message), not an assertion — only rows with ✓/✗ are checked.

### channels

| Surface | Who | Does what | Expected | Actually got | |
|---|---|---|---|---|:--:|
| `web` | you | open the web chat and type "what is the current price of bitcoin in usd? just the number" | the concierge routes it to pricebot and answers with a live price | Bitcoin is currently **$63,277 USD**. | ✓ |
| `slack` | you | post "what is the current price of bitcoin in usd? just the number" in the Slack channel (C0BEYJ9NATB) | Slack accepts the message and Slack's Events API notifies CUGA | message posted, ts=1783632052.671839 |  |
| `slack` | Slack | POSTs the message event to /api/events/slack/events (an unsigned Slack event (no signing secret configured)) | CUGA verifies the signature, acks in <3s, and answers in the background | HTTP 200 {"ok": true} | ✓ |
| `slack` | you | look at the thread under your message in Slack | the bot has replied in-thread with the bitcoin price | 63277 — pricebot · via cuga-finance · 5.4s | ✓ |
| `discord` | you | type "what is the current price of bitcoin in usd? just the number" in the Discord channel (1522408587958423675)<br><sub>the Gateway socket itself is simulated: a bot cannot message itself (discord_direct.should_process drops bot authors)</sub> | the Gateway relays it to CUGA, which answers with a live price | $63,255. | ✓ |
| `discord` | you | scroll the Discord channel | the bot's reply is there — posted by a real REST call, not a mock | a new bot message is present | ✓ |
| `telegram` | you | message the bot @time4fun_bot with "what is the current price of bitcoin in usd? just the number"<br><sub>the Telegram → AP webhook hop is simulated; a bot cannot message itself</sub> | Activepieces' telegram webhook posts it to CUGA, which answers with a live price | 63218 | ✓ |
| `telegram` | you | open the Telegram chat with the bot | the bot's message is delivered for real (sendMessage) | delivered | ✓ |

### flows

| Surface | Who | Does what | Expected | Actually got | |
|---|---|---|---|---|:--:|
| `web` | you | ask "what is the current price of bitcoin in usd? just the number" and expect an answer right now (no flow) | pricebot calls its real MCP tool and returns a live number | Bitcoin is currently **$63,285 USD**. | ✓ |
| `cron` | you | say "every day at 9am send me new arxiv papers on mixture of experts" | the concierge arms a CRON flow and it really exists in Activepieces | Armed a daily 9 am cron flow for the **papers** agent to fetch and summarize new arXiv papers on mixture of ex \| AP flow: bcCgYIigXyS9KvA4Afol0 | ✓ |
| `poll` | you | say "watch bitcoin every 2 minutes and ping me on any move" | the concierge arms a POLL flow and it really exists in Activepieces | Armed poll for pricebot to check Bitcoin every 2 minutes and notify you on any change. \| AP flow: UfxPy91NKxmpNpoy5bROO | ✓ |
| `push:box` | you | say "when a resume lands in my Box, judge it against the JD and email me" | either a real Activepieces watcher on box, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | asks for the missing trigger input: Please share the job description you’d like the resumes evaluated against. Once I have it, | ✓ |
| `push:github` | you | say "when a pull request opens on my repo, summarize it and message me" | either a real Activepieces watcher on github, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | asks for the missing trigger input: Which repository (owner/repo) should I watch for new pull requests? | ✓ |
| `push:gmail` | you | say "when an email from my boss arrives, summarize it and message me" | either a real Activepieces watcher on gmail, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | CONNECT NEEDED — asks you to connect 'gmail' | ✓ |
| `webhook` | your monitoring system | POSTs {"alert":"HighCPU","service":"checkout-api","value":"97%"} to /api/events/hook/monitoring | incident_triage summarises it and assigns a P1/P2/P3 severity | HighCPU alert on checkout‑api: CPU at 97% (threshold 85%) → **P1** severity; likely the checkout‑api service; first action: investigate the host’s CPU usage (e.g., check processes, logs, and scaling) and consider restarting or scaling the … | ✓ |

## How to read this

- **`offline`** — the box-watermark test is a KNOWN pre-existing failure: it reads the real .box_since.json instead of a temp file. Not a regression.
- **`now`** — XPASS = a known gap started passing. Re-sample before believing it — support_digest fabricates on ~5 of 7 runs, so one XPASS is luck.

## Verdict vocabulary

- **FAIL** — expected to work, broke. The only thing worth acting on immediately.
- **XFAIL** — a known gap, with its reason printed in the harness output. Not a regression.
- **XPASS** — a known gap started passing. Re-sample, then delete the expectation.
- **SKIP** — surface not configured. Never counted as a pass.

Only `live_suite` and (since 2026-07-09) `live_e2e`/`live_matrix` verify that an armed flow **really exists in Activepieces**; a bare `ap_flow_id` proves nothing, because `find_or_create_flow` de-duplicates without re-checking (`concierge.py:285-289`).

**None of these harnesses fire real data through an armed watcher.** They prove a flow is created correctly, not that it behaves correctly when a real event lands. For that: `live_gmail_e2e.py`, `live_box_e2e.py`, `live_github_e2e.py`.