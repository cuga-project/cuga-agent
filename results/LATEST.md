# CUGA events — test report

- **When:** 2026-07-10T20:59:49Z  (2026-07-10 16:59:49 EDT)
- **Commit:** `70776ab2` (70776ab2e33ef054813c87c2b0aa4b5e182f22b0) on `feat/events`
- **Tree:** DIRTY — not reproducible from this commit
- **Stack:** agents=26, AP=up, worker=cuga, integrations={'gmail': 'not_connected', 'box': 'connected', 'github': 'not_connected'}
- **Subscriptions:** 0 before → 0 after (no leak)
- **Raw logs:** `results/runs/20260710T205949Z/`

| Harness | Answers | Pass | Fail | XFail | XPass | Skip | Secs |
|---|---|--:|--:|--:|--:|--:|--:|
| `offline` | Do the pure-python invariants hold? (no stack, no creds) | 154 | 0 | 0 | 0 | 0 | 12 |
| `live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 34 | 0 | 0 | 0 | 0 | 52 |
| `now` | Can each of the 18 agents actually do its job? (asserts on meta.mcp) | 18 | 0 | 2 | 0 | 0 | 451 |
| `flows` | Does an English sentence become the right Activepieces flow? | 7 | **3** | 1 | 0 | 0 | 30 |
| `matrix` | Is every trigger x sink combination wired, or only the ones we tried? | 13 | 0 | 12 | 0 | 3 | 82 |
| `fire` | Does an armed flow FIRE and answer? (arms a 1-min schedule, waits for a real tick) | 7 | **1** | 0 | 0 | 1 | 274 |


## End-to-end walkthrough

Exactly what a person would do, and exactly what came back. A blank verdict is scene-setting (posting the message), not an assertion — only rows with ✓/✗ are checked.

### channels

| Surface | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|:--:|
| `web` | you | open the web chat and type "what is the current price of bitcoin in usd? just the number" | the concierge routes it to pricebot and answers with a live price | Bitcoin is currently $63,799 USD. | ✓ |
| `slack` | you | post "what is the current price of bitcoin in usd? just the number" in the Slack channel (C0BEYJ9NATB) | Slack accepts the message and Slack's Events API notifies CUGA | message posted, ts=1783717207.038729 |  |
| `slack` | Slack | POSTs the message event to /api/events/slack/events (a correctly-signed Slack event) | CUGA verifies the signature, acks in <3s, and answers in the background | HTTP 200 {"ok": true} | ✓ |
| `slack` | you | look at the thread under your message in Slack | the bot has replied in-thread with the bitcoin price | Bitcoin is $63,799 USD. — pricebot · via cuga-finance · 8.3s | ✓ |
| `discord` | you | type "what is the current price of bitcoin in usd? just the number" in the Discord channel (1522408587958423675)<br><sub>the Gateway socket itself is simulated: a bot cannot message itself (discord_direct.should_process drops bot authors)</sub> | the Gateway relays it to CUGA, which answers with a live price | 63,800. — pricebot · via cuga-finance · 5.0s | ✓ |
| `discord` | you | scroll the Discord channel | the bot's reply is there — posted by a real REST call, not a mock | a new bot message is present | ✓ |
| `telegram` | you | message the bot @time4fun_bot with "what is the current price of bitcoin in usd? just the number"<br><sub>the Telegram → AP webhook hop is simulated; a bot cannot message itself</sub> | Activepieces' telegram webhook posts it to CUGA, which answers with a live price | 63801 | ✓ |
| `telegram` | you | open the Telegram chat with the bot | the bot's message is delivered for real (sendMessage) | delivered | ✓ |

### flows

| Surface | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|:--:|
| `web` | you | ask "what is the current price of bitcoin in usd? just the number" and expect an answer right now (no flow) | pricebot calls its real MCP tool and returns a live number | Bitcoin is $63,800 USD. | ✓ |
| `cron` | you | say "every day at 9am send me new arxiv papers on mixture of experts" | the concierge arms a CRON flow and it really exists in Activepieces | Armed a daily 9 AM cron flow for the **papers** agent to fetch and summarize new arXiv papers on mixture‑of‑ex \| AP flow: 2JeI4B9b5OO3GPGDitCrW | ✓ |
| `poll` | you | say "watch bitcoin every 2 minutes and ping me on any move" | the concierge arms a POLL flow and it really exists in Activepieces | Armed poll for pricebot to watch Bitcoin every 2 minutes and notify you on any change. \| AP flow: wH0ilOyGsCg4iUFtl3SIw | ✓ |
| `push:box` | you | say "when a resume lands in my Box, judge it against the JD and email me" | either a real Activepieces watcher on box, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | CONNECT NEEDED — asks you to connect 'gmail', which this agent also needs | ✓ |
| `push:github` | you | say "when a pull request opens on my repo, summarize it and message me" | either a real Activepieces watcher on github, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | CONNECT NEEDED — asks you to connect 'github' | ✓ |
| `push:gmail` | you | say "when an email from my boss arrives, summarize it and message me" | either a real Activepieces watcher on gmail, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | CONNECT NEEDED — asks you to connect 'gmail' | ✓ |
| `webhook` | your monitoring system | POSTs {"alert":"HighCPU","service":"checkout-api","value":"97%"} to /api/events/hook/monitoring | incident_triage summarises it and assigns a P1/P2/P3 severity | HighCPU alert on checkout‑api (97% > 85%) – severity P1, component checkout‑api; first action: investigate running processes and scale up CPU resources or restart the service. — incident_triage · via cuga-text · 1.0s | ✓ |

### fire

| Surface | Utterance | Channel | Integration | Trigger | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|---|---|---|---|:--:|
| `web` | “what is the current price of bitcoin?” | web | — | NOW | you | ask pricebot directly on /invoke (no channel, no concierge) | a substantive answer, produced by a real MCP tool | Bitcoin is trading at **$64,329.12**, up **1.80%** over the past 24 hours. — pricebot · via cuga-finance · 58.9s [mcp: cuga-finance] | ✓ |
| `web` | “what is the capital of Peru?” | web | — | NOW | you | ask geobot directly on /invoke (no channel, no concierge) | a substantive answer, produced by a real MCP tool | The capital of Peru is **Lima**. — geobot · via cuga-knowledge, cuga-geo · 20.4s [mcp: cuga-knowledge, cuga-geo] | ✓ |
| `web` | “what is the weather in Tokyo right now?” | web | — | NOW | you | ask weatherbot directly on /invoke (no channel, no concierge) | a substantive answer, produced by a real MCP tool | The current weather in Tokyo is **25.4 °C (77.7 °F)** with **partly cloudy** conditions. — weatherbot · via cuga-web · 15.2s [mcp: cuga-web] | ✓ |
| `slack` | “what is the current price of bitcoin?” | slack | — | NOW | you | type it into the Slack channel C0BEYJ9NATB | Slack's Events API notifies CUGA | posted, ts=1783717910.834839 |  |
| `slack` | “what is the current price of bitcoin?” | slack | — | NOW | the bot | reply in the thread under your message | a price, delivered back into the same Slack thread | Bitcoin is trading at **$63,872 USD**. — pricebot · via cuga-finance · 8.8s | ✓ |
| `web` | “every minute send me the price of bitcoin” | web | — | CRON | you | say this in chat, then wait for the schedule to come round | a real Activepieces flow, enabled, on a 1-minute schedule | armed CRON flow zDghu0hJz6PxM5cYWzJg3 | ✓ |
| `web` | “every minute send me the price of bitcoin” | web | — | CRON | the flow | fire on its schedule and run the agent | a finished run whose answer is a real, tool-derived response | Bitcoin is currently priced at **$63,880** USD, with a **+0.95%** change over the past 24 hours. It’s showing a modest upward move. — pricebot · via cuga-finance · 4.2s | ✓ |
| `web` | “check the weather in Tokyo every minute and ping me if it changes” | web | — | POLL | you | say this in chat, then wait for the schedule to come round | a real Activepieces flow, enabled, on a 1-minute schedule | armed POLL flow 8PVddN5jZGqIjXS5loH3e | ✓ |
| `web` | “check the weather in Tokyo every minute and ping me if it changes” | web | — | POLL | the flow | fire on its schedule and run the agent | a finished run whose answer is a real, tool-derived response | No change in weather. — weatherbot · via cuga-web · 36.4s | ✓ |
| `webhook` | “(an external system POSTs a monitoring alert)” | — | webhook | WEBHOOK | an external system | POST {"alert": "HighCPU", "service": "checkout-api", "value": 97, "threshold": 85} to /api/events/hook/fire-d5d1bb | the agent triages the alert and the answer rides back in the response | P1 – checkout‑api high CPU (97 % > 85 %); investigate immediately (check load, logs, and consider restarting or scaling the service). — incident_triage · via cuga-text · 2.4s | ✓ |
| `box` | “(a resume lands in the watched Box folder)” | — | box | POLL | the poller | list the Box folder and run resume_judge on every new file<br><sub>Box rejected the token: Box list folder 0 failed: HTTP 401 — note that /api/events/integrations still reports box 'connected', because for the direct backend t…</sub> | one agent dispatch per file, and a watermark to resume from | Box rejected the token: Box list folder 0 failed: HTTP 401 — note that /api/events/integrations still reports box 'connected', because for the direct backend that only means BOX_DEV_TOKEN is a non-empty string. | **✗** |

## Verdict vocabulary

- **FAIL** — expected to work, broke. The only thing worth acting on immediately.
- **XFAIL** — a known gap, with its reason printed in the harness output. Not a regression.
- **XPASS** — a known gap started passing. Re-sample, then delete the expectation.
- **SKIP** — surface not configured. Never counted as a pass.

Only `live_suite` and (since 2026-07-09) `live_e2e`/`live_matrix` verify that an armed flow **really exists in Activepieces**; a bare `ap_flow_id` proves nothing, because `find_or_create_flow` de-duplicates without re-checking (`concierge.py:285-289`).

**None of these harnesses fire real data through an armed watcher.** They prove a flow is created correctly, not that it behaves correctly when a real event lands. For that: `live_gmail_e2e.py`, `live_box_e2e.py`, `live_github_e2e.py`.