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
*materially changed*. Four tiers, picked by how you phrase it:

| Tier | Behaviour | Try |
|---|---|---|
| `always` | report every tick (same as cron) | `/poll every 10 minutes the price of bitcoin` |
| `threshold` | only on a numeric move ≥ X% | `/automate tell me if bitcoin moves more than 2% — check every 10 minutes` |
| `identity` | only on genuinely new items, deduped by key | `/automate check https://news.ycombinator.com every hour and tell me only about new top stories` |
| `fuzzy` | an agent judges "did this materially change?" | `/automate check the weather in Pleasantville NY every hour and tell me only if it changes meaningfully` |

The fingerprint — not the content — is what gets stored, so this scales.

---

## Part 4 — Automations triggered by something happening (no AP needed)

These arm **direct watchers**: no Activepieces, no OAuth. The eventing service already receives the
Slack Events API and the Discord Gateway, so a watcher is just a subscription row.

```
/watch when someone reacts with :bug: in this channel, triage it
/watch new messages in #alerts and triage anything that looks like an incident
/watch when a new member joins this Discord server, greet them
```

These land on `incident_triage`, which is the agent whose HANDLES lines declare the Slack/Discord
trigger kinds.

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
their HANDLES lines already declare those triggers. What's missing is only the **delivery of the
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
5. **Clean up** — Studio → Flows → delete. (Or just wait for a Code Engine restart, which currently
   forgets everything — see the limitation note in [ARCHITECTURE.md](ARCHITECTURE.md).)

---

## Sources

`supervisor_agents.yaml` (the 8 sub-agents and their HANDLES lines) ·
`src/cuga/backend/events/triggers.py` (the 42-row trigger registry) ·
`src/cuga/backend/events/concierge.py` (slash parsing, the HITL state machine) ·
`src/cuga/backend/events/poll_state.py` (the four delta tiers)
