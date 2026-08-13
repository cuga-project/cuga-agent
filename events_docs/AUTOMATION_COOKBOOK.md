# Automation cookbook — what to actually type

Every line below is copy-pasteable into **Slack, Discord, Telegram, or the Studio chat**. They are
matched to the **8 sub-agents actually in `supervisor_agents.yaml`** and the MCP servers the stack
ships — nothing here needs Activepieces.

**The rule of thumb:** no slash verb → you get an answer now. A slash verb → you get a *confirm
card*, and nothing is armed until you reply `yes`.

> In Slack and Discord you must **@mention the bot** to engage it. Once the bot has rooted a thread,
> follow-ups in that thread need no re-mention — including your `yes`.

---

## Part 1 — Questions that answer immediately (no automation)

| Ask | Lands on | Needs |
|---|---|---|
| `what is IBM trading at?` | `pricebot` | cuga-finance |
| `price of bitcoin, and what moved it?` | `pricebot` | cuga-finance |
| `what's the weather in Pleasantville NY?` | `weatherbot` | cuga-web |
| `what's the capital of Malawi and how many people live there?` | `geobot` | cuga-knowledge |
| `find me hikes near Beacon NY` | `geobot` | cuga-geo |
| `deep-dive the history of the Bretton Woods system` | `wiki_dive` | cuga-knowledge |
| `summarize https://example.com/some-article` | `webpage_summarizer` | cuga-web |
| `what does this page link to? <url>` | `webpage_summarizer` | cuga-web |
| `audit this snippet: def f(x): return x/0` | `code_auditor` | cuga-code |

These never touch the eventing service. If you shut `cuga-events-svc` down entirely, every line
above still works from the Studio.

---

## Part 2 — Automations on a clock (`cron`)

**Fires on a schedule, always reports.** Best for the agents that can answer from tools alone.

```
/automate every weekday at 9am tell me the IBM stock price
/automate every morning at 7 give me the weather in Pleasantville NY
/automate every 30 minutes give me the price of bitcoin
/automate every Monday at 8am summarize https://news.ycombinator.com
/automate every day at 6pm deep-dive one interesting topic from Wikipedia
```

What you get back is a **confirm card** showing the cadence, the exact prompt the agent will be
handed, and where results go. Note that the cadence is *stripped out of the prompt*:

```
you typed : every weekday at 9am tell me the IBM stock price
the agent gets : "The IBM stock price."
```

That is deliberate. Leaving "every weekday" in the prompt asks the agent to *implement a loop*,
which is the silent-failure trap this whole feature exists to close.

**Explicit verb if you want to skip the router:** `/cron every weekday at 9am …`

---

## Part 3 — Automations that only speak up when something changed (`poll`)

**This is the one worth demoing.** A poll re-checks on an interval but only delivers when the answer
*materially changed*.

> **cron vs poll, in one line.** *cron* answers "tell me on a schedule"; *poll* answers "tell me
> **when it's worth knowing**." A 5-minute cron on a stock price gives you 288 messages a day. The
> same thing as a 2% threshold poll gives you three — on the days that matter.

Four tiers. **You don't pick one — your phrasing does**, so the wording below is the actual
interface.

> ### ⚠ Known gap: tier selection is not yet reliable on the deployed stack
> Every example below picks the documented tier **locally** — verified through both the LLM
> extractor and the deterministic fallback. On Code Engine, the same `/poll check bitcoin every 10
> minutes and tell me if it moves more than 2%` armed as **`fuzzy`**, twice, with no error logged
> either side. The likely cause is *which text* the tier is derived from: the raw utterance travels
> in a ContextVar that may not reach the tool, leaving a router-rewritten prompt with the "2%"
> already removed.
>
> **What this means for you:** the flows still arm and still fire, and `fuzzy` still suppresses
> unchanged results — an agent judges instead of arithmetic. What you don't reliably get yet is the
> *precise* "≥2%" semantics. A diagnostic (`src=… text=…` on the `poll … delta kind=` log line) now
> ships so the next occurrence identifies itself. Treat the threshold numbers below as intent, not
> as a guarantee, until that log confirms otherwise.

### `threshold` — a number moved by more than X%

The workhorse. Give it a percentage and it stays silent until the move is big enough.

```
/automate check bitcoin every 10 minutes and tell me if it moves more than 2%
/automate watch IBM every 15 minutes, alert me on a 1% swing either way
/automate every 5 minutes check ethereum — only ping me on a 3% move
```
→ **`pricebot`** · `cuga-finance`. Verified behaviour with a 2% threshold from a baseline of 100:

| tick | value | result |
|---|---|---|
| 1 | 100.0 | silent — *first observation seeds the baseline, never alerts* |
| 2 | 100.5 | silent (0.5%) |
| 3 | 101.0 | silent (1.0%) |
| 4 | **104.0** | **DELIVER** (4.0%) — and the baseline re-ratchets to 104 |
| 5 | 104.2 | silent (0.2% from the new baseline) |

That re-ratcheting is `reset_policy: ratchet`, the default: the next move is measured from the last
*alert*, not from the original. So a slow climb reports each 2% step rather than shouting forever.

### `identity` — something *new* appeared

Fires on a key it has never seen, never twice on the same item.

```
/automate check https://news.ycombinator.com every hour and tell me only about new top stories
/automate every 30 minutes check the Wikipedia current-events page and report only new entries
/automate check https://status.cloud.ibm.com every 15 minutes and tell me about any NEW incident
```
→ **`webpage_summarizer`** · `cuga-web`. Verified:

| tick | keys the agent returned | result |
|---|---|---|
| 1 | `[storyA, storyB]` | **DELIVER** — *2 new of 2* |
| 2 | `[storyA, storyB]` | silent — 0 new |
| 3 | `[storyA, storyB, storyC]` | **DELIVER** — 1 new of 3 |
| 4 | `[storyC]` | silent — already seen |

**Note tick 1 delivers.** Unlike `threshold`, identity has no quiet seeding tick — the first run
reports everything it finds. Expect one full list, then only deltas.

### `fuzzy` — "did this materially change?"

No number to compare, so the agent judges the delta itself and its previous state is fed back to it.

```
/automate check the weather in Pleasantville NY every hour, tell me only if it changes meaningfully
/automate look at https://example.com/pricing every day and tell me if the terms materially changed
/automate re-read the Wikipedia article on quantum error correction weekly and tell me if it materially changed
```
→ **`weatherbot`**, **`webpage_summarizer`**, **`wiki_dive`**. Verified: `sunny → storm` delivers;
`storm → storm` stays silent.

### `always` — Tier 0, every tick

No delta gate at all — cron semantics in poll's clothing. Useful as a heartbeat.

```
/poll every 10 minutes the price of bitcoin
```

### Phrasings that pick a tier you didn't mean

This is the fuzzy seam, so it's worth knowing where it bends:

- `"tell me if bitcoin changes"` → **fuzzy**, because there is no number. You almost certainly meant
  `"...moves more than 1%"`.
- The word **"new"** pulls hard toward `identity`. `"tell me what's new in this article"` picks
  identity even though prose has no discrete keys — say **"materially changed"** for fuzzy.
- Naming both a percentage *and* "meaningfully" → **threshold** wins; the number is the stronger signal.

### Two agents that poll serves badly

**`pr_reviewer`** and **`code_auditor`** are *event*-shaped, not poll-shaped: they want a push when a
PR opens, which is the Activepieces-gated path. **`incident_triage`** is the same — except its
Slack/Discord watchers work today with no AP:

```
/watch when someone reacts with :bug: in this channel, triage it
```

### How the state is kept

Only a **fingerprint**, never the content: a single `baseline` float for threshold, a set of keys for
identity, one short state string for fuzzy. It lives in the `watch_state` table in Postgres beside the
subscription, so it survives restarts and instance replacement.

**Known limit:** the identity seen-set is capped at 5,000 keys, and on overflow the eviction keeps an
arbitrary 5,000 rather than the most recent — so a very high-churn feed could re-report an old item
as new. Fine for feeds and status pages; not for a firehose.

---

## Part 4 — Automations triggered by something happening (no AP needed)

These arm **direct watchers**: no Activepieces, no OAuth. The eventing service already receives the
Slack Events API and the Discord Gateway, so a watcher is just a subscription row.

```
/watch when someone reacts with :bug: in this channel, triage it
/watch new messages in #alerts and triage anything that looks like an incident
/watch when a new member joins this Discord server, greet them
```

These land on `incident_triage`, the agent that declares the Slack/Discord trigger kinds in its
`integrations` (`events/seed.py`).

**Inbound webhook** — for anything that can POST:

```
/watch inbound webhook alerts and triage what arrives
```

then

```bash
curl -X POST "$EVENTS_URL/api/events/hook/alerts?agent=incident_triage" \
     -H 'Content-Type: application/json' \
     -d '{"service":"auth","error":"token refresh failing","rate":"12/min"}'
```

---

## Part 5 — Managing what you armed

| Type | Effect |
|---|---|
| `cancel` (while the card is showing) | drops the draft, arms nothing |
| `change the prompt to …` | edits before arming |
| `/cancel <watch id>` | disarms an armed flow |
| Studio → **Flows** | list / inspect / delete everything |

**Delete anything you arm when you're done — a cron fires forever.**

---

## What you cannot automate today (needs Activepieces)

These are **27 registry triggers** whose backend is `ap`, and they are all *push from SaaS you don't
own*. With AP off, arming one returns a clean "CONNECT NEEDED" rather than failing oddly.

| App | Triggers | Example that will NOT arm today |
|---|---|---|
| GitHub | 14 (`new_pr`, `new_issue`, `new_star`, `new_push`, …) | `/watch when a PR opens in my repo, review it` |
| Gmail | 4 (`new_email`, `new_labeled_email`, …) | `/watch new email from my boss and summarize it` |
| Google Calendar | 3 | `/watch when a meeting is about to end` |
| Pinterest | 3 · RSS 1 · YouTube 1 · Box `new_file` 1 | `/watch new items on this RSS feed` |

Note the asymmetry: `pr_reviewer` and `incident_triage` are *ready* for GitHub and Gmail events —
their `integrations` already declare those triggers. What's missing is only the **delivery of the
event**, which is exactly the job AP does. See the AP re-enable notes in
[CORE_VS_LAYER.md](CORE_VS_LAYER.md).

---

## A 5-minute demo script

1. **Plain chat** — `@bot what is IBM trading at?` → answer in-thread, eventing never involved.
2. **Arm** — `@bot /automate every 5 minutes tell me the IBM stock price` → confirm card. Point out
   the stripped prompt.
3. **Show the gate** — reply `change the prompt to the IBM stock price and the 24h move`, get a new
   card, *then* `yes`.
4. **Watch it fire** — a tick lands in the same thread ~5 min later, on a fresh execution thread.
   In Slack/Discord/Telegram the bot posts into the thread. In the **web** chat the fire appears in
   the transcript as a `⚡ flow fired` message (see below).
5. **Clean up** — Studio → Flows → delete. **A cron fires forever**, and it now survives restarts —
   state lives in PostgreSQL, so an instance replace is a non-event. Deleting is the only way to
   stop one.

---

## Where a fire shows up, per surface

A standing flow fires when nobody is waiting. How the answer reaches you depends on what the channel
can receive:

| Armed from | How the fire reaches you |
|---|---|
| Slack · Discord | pushed into the same thread the flow was armed in |
| Telegram | pushed into the chat |
| **Web** (Studio chat or the main chat) | a browser has no socket to push into, so the fire is delivered to a durable **per-thread mailbox** and the chat drains it — it appears as a `⚡ flow fired · <trigger> · <flow>` message |

The web mailbox is durable, which is the point: a fire that happens while the tab is **closed** is
still there when you come back. Reopen the chat and it appears in the transcript. You can also read
it directly:

```bash
curl -s -H "X-Gateway-Token: $GATEWAY_TOKEN" \
  "$EVENTS_URL/api/events/inbox?thread_id=web:studio" | jq '.messages[].text'
```

> Before this existed, a web-armed flow fired, wrote a row to the runs log, and told nobody — "it
> ran, the dashboard knows, my chat never heard back." If you see that symptom again, check
> `GET /api/events/inbox` first: a message present there but absent from the chat is a UI problem;
> an empty mailbox is a delivery problem.

---

## Sources

`supervisor_agents.yaml` (the 8 sub-agents) · `src/cuga/backend/events/seed.py` (trigger ownership) ·
`src/cuga/backend/events/triggers.py` (the 42-row trigger registry) ·
`src/cuga/backend/events/concierge.py` (slash parsing, the HITL state machine) ·
`src/cuga/backend/events/poll_state.py` (the four delta tiers)
