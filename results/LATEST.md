# CUGA events — test report

- **When:** 2026-07-10T15:33:02Z  (2026-07-10 11:33:02 EDT)
- **Commit:** `e0b63ba9` (e0b63ba9627ac8fdf11ccbffba7bfd0f9efde369) on `feat/events`
- **Tree:** DIRTY — not reproducible from this commit
- **Stack:** agents=18, AP=up, worker=cuga, integrations={'gmail': 'connected', 'box': 'connected', 'github': 'connected'}
- **Subscriptions:** 0 before → 0 after (no leak)
- **GITHUB_TEST_REPO:** `anupamamurthi/pachyderm` (github push row armed; webhooks created by the run are deleted afterwards)
- **Raw logs:** `results/runs/20260710T153302Z/`

| Harness | Answers | Pass | Fail | XFail | XPass | Skip | Secs |
|---|---|--:|--:|--:|--:|--:|--:|
| `offline` | Do the pure-python invariants hold? (no stack, no creds) | 154 | 0 | 0 | 0 | 0 | 12 |
| `live` | Is the plumbing alive? 4 channels + 4 flow modes, one probe each | 35 | 0 | 0 | 0 | 1 | 100 |
| `now` | Can each of the 18 agents actually do its job? (asserts on meta.mcp) | 18 | 0 | 1 | 1 | 0 | 507 |
| `flows` | Does an English sentence become the right Activepieces flow? | 10 | 0 | 1 | 0 | 0 | 43 |
| `matrix` | Is every trigger x sink combination wired, or only the ones we tried? | 22 | 0 | 3 | 0 | 3 | 116 |
| `fire` | Does an armed flow FIRE and answer? (arms a 1-min schedule, waits for a real tick) | 8 | 0 | 1 | 0 | 0 | 240 |


## End-to-end walkthrough

Exactly what a person would do, and exactly what came back. A blank verdict is scene-setting (posting the message), not an assertion — only rows with ✓/✗ are checked.

### channels

| Surface | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|:--:|
| `web` | you | open the web chat and type "what is the current price of bitcoin in usd? just the number" | the concierge routes it to pricebot and answers with a live price | 64,066 USD | ✓ |
| `slack` | you | post "what is the current price of bitcoin in usd? just the number" in the Slack channel (C0BEYJ9NATB) | Slack accepts the message and Slack's Events API notifies CUGA | message posted, ts=1783697606.579939 |  |
| `slack` | Slack | POSTs the message event to /api/events/slack/events (an unsigned Slack event (no signing secret configured)) | CUGA verifies the signature, acks in <3s, and answers in the background | HTTP 200 {"ok": true} | ✓ |
| `slack` | you | look at the thread under your message in Slack | the bot has replied in-thread with the bitcoin price | 64,066.00 USD — pricebot · via cuga-finance · 6.3s | ✓ |
| `discord` | you | type "what is the current price of bitcoin in usd? just the number" in the Discord channel (1522408587958423675)<br><sub>the Gateway socket itself is simulated: a bot cannot message itself (discord_direct.should_process drops bot authors)</sub> | the Gateway relays it to CUGA, which answers with a live price | 64,066. — pricebot · via cuga-finance · 13.6s | ✓ |
| `discord` | you | scroll the Discord channel | the bot's reply is there — posted by a real REST call, not a mock | a new bot message is present | ✓ |
| `telegram` | you | message the bot @time4fun_bot with "what is the current price of bitcoin in usd? just the number"<br><sub>the Telegram → AP webhook hop is simulated; a bot cannot message itself</sub> | Activepieces' telegram webhook posts it to CUGA, which answers with a live price | Bitcoin is currently **$64,054 USD**. — pricebot · via cuga-finance · 7.9s | ✓ |
| `telegram` | you | open the Telegram chat with the bot | the bot's message is delivered for real (sendMessage) | delivered | ✓ |

### flows

| Surface | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|:--:|
| `web` | you | ask "what is the current price of bitcoin in usd? just the number" and expect an answer right now (no flow) | pricebot calls its real MCP tool and returns a live number | Bitcoin is $64,054 USD. | ✓ |
| `cron` | you | say "every day at 9am send me new arxiv papers on mixture of experts" | the concierge arms a CRON flow and it really exists in Activepieces | Armed a daily 9 am cron flow with the **papers** agent to fetch and summarize new arXiv papers on mixture of e \| AP flow: ch78mDcsqaUOh40fFlfkE | ✓ |
| `poll` | you | say "watch bitcoin every 2 minutes and ping me on any move" | the concierge arms a POLL flow and it really exists in Activepieces | Armed poll for pricebot to check Bitcoin price every 2 minutes and notify you on any change. \| AP flow: Cnt2Bj8UNF0hl2jlMj9Xe | ✓ |
| `push:box` | you | say "when a resume lands in my Box, judge it against the JD and email me" | either a real Activepieces watcher on box, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | ARMED — AP flow XXzeDjeNw5nMKciRI0fEY (POLL · new) | ✓ |
| `push:github` | you | say "when a pull request opens on my repo, summarize it and message me" | either a real Activepieces watcher on github, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | asks for the missing trigger input: Which repository (owner/repo) should I watch for new pull requests? | ✓ |
| `push:gmail` | you | say "when an email from my boss arrives, summarize it and message me" | either a real Activepieces watcher on gmail, or — if it is not connected / the trigger needs a repo or folder — a clear question instead of a silent failure | ARMED — AP flow nwFKTh2uA7W4qZJ54pVlY (PUSH · new) | ✓ |
| `webhook` | your monitoring system | POSTs {"alert":"HighCPU","service":"checkout-api","value":"97%"} to /api/events/hook/monitoring | incident_triage summarises it and assigns a P1/P2/P3 severity | High CPU alert on checkout‑api (97% > 85%) – P1 severity – Component: checkout‑api service – First action: investigate the spike (check logs, metrics) and consider scaling up or restarting the checkout‑api. — incident_triage · via cuga-tex… | ✓ |

### fire

| Surface | Utterance | Channel | Integration | Trigger | Who | Does what | Expected | Actually got |  |
|---|---|---|---|---|---|---|---|---|:--:|
| `web` | “what is the current price of bitcoin?” | web | — | NOW | you | ask pricebot directly on /invoke (no channel, no concierge) | a substantive answer, produced by a real MCP tool | Bitcoin is currently priced at **$63,910.00**, up **1.52%** over the past 24 hours. — pricebot · via cuga-finance · 7.4s [mcp: cuga-finance] | ✓ |
| `web` | “what is the capital of Peru?” | web | — | NOW | you | ask geobot directly on /invoke (no channel, no concierge) | a substantive answer, produced by a real MCP tool | The capital of Peru is **Lima**. — geobot · via cuga-knowledge, cuga-geo · 14.0s [mcp: cuga-knowledge, cuga-geo] | ✓ |
| `web` | “what is the weather in Tokyo right now?” | web | — | NOW | you | ask weatherbot directly on /invoke (no channel, no concierge) | a substantive answer, produced by a real MCP tool | Tokyo’s current weather (as of 2026‑07‑11 00:30 local time) is **partly cloudy**, with a temperature of **25.2 °C** ( 77.4 °F ), humidity **89 %**, and a wind from the south at **2 [mcp: cuga-web] | ✓ |
| `slack` | “what is the current price of bitcoin?” | slack | — | NOW | you | type it into the Slack channel C0BEYJ9NATB | Slack's Events API notifies CUGA | posted, ts=1783698401.071409 |  |
| `slack` | “what is the current price of bitcoin?” | slack | — | NOW | the bot | reply in the thread under your message | a price, delivered back into the same Slack thread | Bitcoin is currently about **$63,910 USD**, up roughly **1.5 %** in the last 24 hours. — pricebot · via cuga-finance · 6.2s | ✓ |
| `web` | “every minute send me the price of bitcoin” | web | — | CRON | you | say this in chat, then wait for the schedule to come round | a real Activepieces flow, enabled, on a 1-minute schedule | armed CRON flow pPmUgUmappvqOqHVgYfUV | ✓ |
| `web` | “every minute send me the price of bitcoin” | web | — | CRON | the flow | fire on its schedule and run the agent | a finished run whose answer is a real, tool-derived response | Bitcoin is priced at **$63,913** USD, up **1.52 %** over the past 24 hours. This modest rise suggests a slight bullish momentum. — pricebot · via cuga-finance · 5.4s | ✓ |
| `web` | “check the weather in Tokyo every minute and ping me if it changes” | web | — | POLL | you | say this in chat, then wait for the schedule to come round | a real Activepieces flow, enabled, on a 1-minute schedule | armed POLL flow MvZSpzoX3jV1NdWs053NJ | ✓ |
| `web` | “check the weather in Tokyo every minute and ping me if it changes” | web | — | POLL | the flow | fire on its schedule and run the agent | a finished run whose answer is a real, tool-derived response | Tokyo weather update: 25 °C, Partly cloudy. — weatherbot · via cuga-web · 21.6s | ✓ |
| `webhook` | “(an external system POSTs a monitoring alert)” | — | webhook | WEBHOOK | an external system | POST {"alert": "HighCPU", "service": "checkout-api", "value": 97, "threshold": 85} to /api/events/hook/fire-479a47 | the agent triages the alert and the answer rides back in the response | HighCPU alert on checkout‑api (97 % > 85 % threshold) – severity P1, component checkout‑api; first action: investigate the service’s CPU usage (e.g., check logs, metrics, and consi | ✓ |
| `box` | “(a resume lands in the watched Box folder)” | — | box | POLL | the poller | list the Box folder and run resume_judge on every new file | one agent dispatch per file, and a watermark to resume from | 7 file(s): chloe_adams.md, coActMgr.dll, coActMgr.loc | ✓ |
| `github` | “watch the repo anupamamurthi/pachyderm for new pull requests and summarize each one” | web | github | PUSH | you | ask for a watcher on the repo, then check Activepieces really holds the flow<br><sub>not fired on purpose — firing would push to a real repository</sub> | an enabled AP flow whose trigger is the github piece | armed, AP flow KWSi6WHR73oTFDI9TbDzd | ✓ |

## How to read this

- **`now`** — XPASS = a known gap started passing. Re-sample before believing it — support_digest fabricates on ~5 of 7 runs, so one XPASS is luck.
- **`fire`** — ARMED/NOFIRE mean the flow exists but no answer was observed — either the schedule never came round, or firing it would mutate a real repo/inbox. Neither is a pass.

## Verdict vocabulary

- **FAIL** — expected to work, broke. The only thing worth acting on immediately.
- **XFAIL** — a known gap, with its reason printed in the harness output. Not a regression.
- **XPASS** — a known gap started passing. Re-sample, then delete the expectation.
- **SKIP** — surface not configured. Never counted as a pass.

Only `live_suite` and (since 2026-07-09) `live_e2e`/`live_matrix` verify that an armed flow **really exists in Activepieces**; a bare `ap_flow_id` proves nothing, because `find_or_create_flow` de-duplicates without re-checking (`concierge.py:285-289`).

**None of these harnesses fire real data through an armed watcher.** They prove a flow is created correctly, not that it behaves correctly when a real event lands. For that: `live_gmail_e2e.py`, `live_box_e2e.py`, `live_github_e2e.py`.