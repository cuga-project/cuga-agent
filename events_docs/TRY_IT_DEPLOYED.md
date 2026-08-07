# Try CUGA — the deployed one

**Start here.** Nothing to install, nothing to clone. You need a browser, and for the chat channels,
an invite to the workspace. ~10 minutes.

> Building or deploying it yourself instead? → **[RUNBOOK_TRY_IT.md](RUNBOOK_TRY_IT.md)**
> Want to know how it works? → **[ARCHITECTURE.md](ARCHITECTURE.md)**
> Want a list of things to type? → **[AUTOMATION_COOKBOOK.md](AUTOMATION_COOKBOOK.md)**

| | |
|---|---|
| **Studio (web UI)** | https://cuga-core.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud/studio |
| **Slack** | `#eda-test` in the `eda-test` workspace — mention `@eda-test-app` |
| **Telegram** | DM `@time4fun_bot` |
| **Discord** | mention the bot in `#general` |

---

## The one idea, before you start

CUGA answers questions. **It can also keep answering one** — on a schedule, or when something
changes — and the difference is a single word you type.

```
what is IBM trading at?              → an answer, now
/automate every weekday at 9am tell me the IBM stock price
                                     → a confirm card, then it runs forever
```

Nothing is ever armed without you approving the exact prompt first. That gate is the point of the
feature: the risky part of a standing job is not the schedule, it's what the agent gets asked on
every single fire, forever, with nobody watching.

---

## 1 · Two minutes: plain chat

Open the **Studio** link above → the **Chat** tab. Ask anything:

```
what's the weather in Pleasantville NY?
what is IBM trading at?
summarize https://news.ycombinator.com
```

A supervisor routes to one of 8 specialists (`pricebot`, `weatherbot`, `geobot`, `wiki_dive`,
`webpage_summarizer`, `code_auditor`, `pr_reviewer`, `incident_triage`). Answers take **10–60 s** —
it's calling real tools, not guessing.

## 2 · Five minutes: arm something, watch it fire

In the Studio → **Concierge** tab (or the chat box), type:

```
/automate every 5 minutes tell me the IBM stock price
```

You get a **confirm card**, not an armed flow. Read it — note that the schedule has been *stripped
out of the prompt*: you typed "every 5 minutes tell me…", the agent will be asked "The IBM stock
price." That's deliberate; leaving the cadence in would ask the agent to implement a loop.

Try all three responses:

| Reply | What happens |
|---|---|
| `yes` | armed — check the **Flows** tab |
| `change the prompt to the IBM price and the 24h move` | re-confirms with your wording |
| `cancel` | nothing armed |

Then watch the **Runs** tab. Within 5 minutes a tick appears with a live answer.

**Delete it when you're done** — Flows tab → delete. A cron fires forever.

## 3 · The same thing, from Slack

In `#eda-test`:

```
@eda-test-app what is IBM trading at?
```
The bot replies **in a thread**. Then, in that same channel:

```
@eda-test-app /automate every 5 minutes tell me the IBM stock price
```
The confirm card comes back in a thread. Reply **`yes` in that thread** — no new mention needed,
because the bot already rooted it. The tick is then delivered back into that same thread.

Telegram and Discord behave identically (Telegram needs no mention — it's a DM).

---

## What to look for while you're poking

- **Plain chat never touches the eventing layer.** Only a slash verb, or a reply inside an open
  arming dialogue, does. A message that merely *sounds* like a schedule ("every morning I check the
  price") is answered once, as chat — auto-detection was removed on purpose because it misfires.
- **A poll only speaks up when something changed.** Try
  `/automate check the weather in Pleasantville NY every hour and tell me only if it changes
  meaningfully` — it goes quiet when nothing moves.
- **Every fire runs on a fresh conversation.** Tick #288 doesn't drag 287 prior turns of context.
- **Delivery goes back where you armed it.** Arm from a Slack thread, the results land in that thread.

## What will NOT work today, and why

Activepieces is **switched off**, so anything that needs a push from SaaS we don't own returns a
clean *"CONNECT NEEDED"* rather than arming:

| Won't arm today | Needs |
|---|---|
| `/watch when a PR opens in my repo` | GitHub (AP) |
| `/watch new email from my boss` | Gmail (AP) |
| Calendar / Pinterest / RSS / YouTube watchers | AP |

Everything else — all four chat channels, cron, poll, inbound webhooks, and Slack/Discord/Telegram/Box
watchers — needs no Activepieces at all.

Also: **one process at a time may serve a given Slack app or Telegram bot.** If someone is running a
local stack against the same bot tokens, inbound chat will fight. Outbound delivery is unaffected.

---

## If something looks broken

| Symptom | Check |
|---|---|
| Slack/Discord bot silent | Is it in the channel? Did you @mention it? |
| First call is slow or 502 | MCP tool servers scale to zero — retry once warm |
| Studio tab empty | `GET /api/events/status` → `.durability` should say `postgres` |
| Armed flow vanished | Should not happen any more — [tell us](#), it used to before the database landed |

**Health, no login needed:**
```bash
curl -s https://cuga-events-svc.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud/health
```

## Feedback worth sending back

1. Did the **confirm card** show you what you expected the agent to be asked?
2. Did an armed flow ever fire with a prompt you would not have approved?
3. Did plain chat ever get mistaken for an automation, or vice versa?
4. Anything that answered *wrongly* rather than slowly — the routing to specialists is the newest part.
