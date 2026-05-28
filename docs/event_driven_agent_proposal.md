# Event-Driven CUGA — Proposal

*Turning the "per-agent message queue" sketch into a concrete architecture, with
CUGA loops and cuga-runtime folded in.*

---

## 0. The original statement

> "The simplest implementation is to have a **message queue per-agent**, and a part
> of the **agent loop that checks this message queue between turns** — so optimizing
> for a single-agent that's purely asynchronous. You could fit the entire
> eventing/triggering model this way. (That's how Claude does it, even for subagents
> and agent teaming.) You could also make an argument for a different style of event
> that **spawns** agents."

This proposal takes that sketch literally and shows that **one primitive — an event
delivered into a per-agent inbox — covers every capability below**: gateways,
multimodal input, pub, sub, cron, and multi-agent swarms.

CUGA already has two-thirds of the machinery:

| Existing piece | What it is | What it becomes here |
|---|---|---|
| **CUGA loops** (`backend/loops/`) | APScheduler-based time triggers; agent self-schedules via tools | The **`cron` producer** — one event source among many |
| **cuga-runtime** (`cuga-runtime/`) | `asyncio.Queue` + worker draining external `POST /events` | The **inbox + drain loop**, generalized to per-agent and in-loop |
| **MCP connectors** | Box, Gmail, Calendar, … as agent tools | Unchanged — used *within* a turn, not events |

The gap is a single unifying abstraction. That's what this is.

---

## 1. The one primitive: the `Event`

Everything inbound — a Slack mention, a cron fire, a webhook, a message from another
agent — is normalized into one envelope and dropped into a **per-agent inbox**.

```
Event {
  id            : str
  kind          : message | trigger | subscription | agent_msg
  source        : "gateway:slack" | "cron:loop:<id>" | "sub:webhook:<id>"
                   | "agent:<name>" | "runtime:/events"
  target        : { agent_name, thread_id }      # which inbox this lands in
  modality      : text | file | document | audio | video
  payload       : { text, context{}, attachments[] }
  reply_to      : { channel, address }           # where pub/outbound goes back
  credentials   : ref to per-user / per-thread credential binding
  priority      : normal | high
  created_at    : ts
}
```

**The agent loop**: between turns, drain the inbox. Each event becomes a turn on
its `thread_id`. One agent, one coroutine, purely asynchronous — exactly the
statement's "single-agent" model. `kind=agent_msg` with a new `thread_id` is the
"event that spawns an agent" variant.

---

## 2. Architecture diagram

```
                          INBOUND                                       OUTBOUND
 ┌───────────────────────────────────────────────┐         ┌──────────────────────────┐
 │  GATEWAYS  (inbound + outbound channels)       │         │  [pub] PUBLISH SINKS     │
 │  Slack · WhatsApp · Telegram · Email/IMAP      │◄───────►│  Slack channel · webhook │
 └───────────────────────┬────────────────────────┘         │  topic/queue · PagerDuty │
                          │ raw channel message               └────────────▲─────────┘
                          ▼                                                 │
 ┌────────────────────────────────────────────────┐                         │
 │  INGEST / NORMALIZATION LAYER                   │                         │
 │   • text        → passthrough                   │                         │
 │   • file/doc    → parse (PDF/decks → text+struct)│                         │
 │   • audio/video → STT / VLM in the loop          │                         │
 │   → builds a normalized Event                    │                         │
 └───────────────────────┬─────────────────────────┘                         │
                          │                                                  │
   PRODUCERS              ▼                                                   │
 ┌──────────────┐   ┌───────────────────────────────────────────┐            │
 │ [cron]       │   │           EVENT BUS / ROUTER              │            │
 │ CUGA loops   │──►│  routes Event → target agent's inbox      │            │
 │ (APScheduler)│   └───────────────────┬───────────────────────┘            │
 ├──────────────┤                       │                                    │
 │ [sub]        │                       ▼                                    │
 │  • listeners │   ┌───────────────────────────────────────────┐            │
 │   (ws/IMAP)  │──►│   PER-AGENT INBOXES  (message queues)      │            │
 │  • webhook   │   │   inbox[agentA] ── inbox[agentB] ── ...    │            │
 │   receiver   │   └───────────────────┬───────────────────────┘            │
 │  • hook      │                       │ drain between turns                 │
 │   poller     │                       ▼                                    │
 ├──────────────┤   ┌───────────────────────────────────────────┐            │
 │ [swarm]      │   │   AGENT LOOP  (single async agent)         │            │
 │ agent→agent  │──►│   while True:                              │            │
 │ messages     │◄──│     ev = inbox.get()                       │── pub ─────┘
 └──────────────┘   │     answer = run_turn(ev, ev.thread_id)    │
        ▲           │     emit replies / pub / agent_msg         │
        └───────────│   uses MCP connectors *within* the turn ───┼──┐
                     └───────────────────────────────────────────┘  │
                                                                     ▼
                                              ┌────────────────────────────────┐
                                              │  MCP CONNECTORS (existing)      │
                                              │  Box · Gmail · Calendar · ...   │
                                              │  per-user/per-thread cred bind  │
                                              └────────────────────────────────┘
```

---

## 3. Components

### 3.1 Gateways — inbound/outbound channels
Adapters that bridge a chat/email channel to the event bus. Each gateway:
- **Inbound:** receives a Slack mention / WhatsApp msg / Telegram update / inbound
  email, captures `reply_to` (channel + address), hands raw payload to Ingest.
- **Outbound:** renders an agent reply back onto the same channel.

A gateway is itself a `[sub]` listener (websocket / events API / IMAP idle) — it's
a *specialized subscriber whose events are user messages*.

### 3.2 Ingest / Normalization — modalities
Turns any payload into a text+context Event before it reaches the inbox:
- **Plain text** → passthrough.
- **File uploads / documents** (PDF, decks, slides) → parse to text + structured
  blocks; large files referenced by handle in `attachments[]`.
- **Audio / video** → STT for speech; VLM for frames/images. The agent sees text +
  structured context; raw media kept as attachment for tools that need it.

### 3.3 Event bus / router
Stateless: maps `Event.target` → the right per-agent inbox. Also the place to
enforce auth, rate limits, and credential binding.

### 3.4 Per-agent inboxes + agent loop
- One inbox (message queue) per agent. Generalizes cuga-runtime's single global
  `asyncio.Queue` ([queue.py](../cuga-runtime/cuga_runtime/queue.py)) to be
  keyed per agent.
- The agent loop drains its inbox **between turns** — the statement's core idea.
- Phase 1: in-memory `asyncio.Queue`. Phase 2: DB/Redis-backed for durability
  across restarts (cuga-runtime already documents this one-file swap).

### 3.5 Producers
| Producer | Mechanism | Status |
|---|---|---|
| **[cron]** | CUGA loops — APScheduler `delay`/`interval`/`cron`; agent self-schedules via `schedule_recurring` / `schedule_wakeup` tools | **Exists** ([backend/loops/](../src/cuga/backend/loops/)) |
| **[sub] always-on listener** | Long-lived websocket / IMAP idle / events-API connection that emits an Event on each external message | New |
| **[sub] webhook receiver** | HTTP endpoint third parties POST to (GitHub, Calendly, Stripe) → Event | New (cuga-runtime's `POST /events` is the seed) |
| **[sub] hook-style poller** | Subscriptions stored in a registry; a poller checks sources on a schedule and emits Events on change/diff | New — reuses the loops scheduler |
| **[swarm]** | One agent addresses another: writes an `agent_msg` Event into the target's inbox | New (thin) |

### 3.6 [pub] — publish sinks
A `publish(destination, event)` tool so the agent can **emit** rather than only
reply inline. Destinations: Slack channel, webhook URL, topic/queue, PagerDuty.
Symmetric with subscribe — one agent's `pub` can be another's `sub`.

### 3.7 MCP connectors
Unchanged. These are **tools used inside a turn**, not events. Only new work is
per-user/per-thread credential binding, carried on `Event.credentials`.

### 3.8 State-diff helper
Several `[sub]` cases ("MRR drops >5%", "no-online-ordering → online-ordering",
"CI turns green") need *change detection*. A small keyed store ("last seen value
per subscription") lets pollers/listeners fire only on a real transition.

---

## 4. How loops & cuga-runtime fold in

```
                 BEFORE                              AFTER (this proposal)
   ┌──────────────┐  ┌──────────────┐      ┌────────────────────────────────────┐
   │ CUGA loops   │  │ cuga-runtime │      │  Unified event bus + per-agent inbox │
   │ time triggers│  │ POST /events │      │                                      │
   │ → direct     │  │ → global queue│ ──► │  loops      = the [cron] producer    │
   │   invoke()   │  │ → worker      │     │  runtime    = inbox + drain loop     │
   └──────────────┘  └──────────────┘      │  + gateways, [sub], [pub], [swarm]   │
     (cron only)      (one global queue)   └────────────────────────────────────┘
```

- **CUGA loops** stops calling `invoke()` directly ([runner.py:58](../src/cuga/backend/loops/runner.py));
  instead `fire_loop` **enqueues a `trigger` Event** into the target agent's inbox.
  All loop semantics (cron parsing, expiry, run history, the loops UI) stay.
- **cuga-runtime** stops being a separate dispatcher; its queue becomes the inbox
  primitive, partitioned per agent, and the drain moves *into* the agent loop.

---

## 5. Capability coverage matrix

Every utterance from the brief, mapped to the components it exercises.

| Utterance | Gateway | Modality | Producer | Pub | Connector |
|---|---|---|---|---|---|
| "@cuga in Slack: scout restaurants in Brooklyn" | Slack | text | — | — | — |
| "[WhatsApp] here's a deck, summarize top 3 risks" | WhatsApp | document | — | — | — |
| "[Email + PDF] extract action items and reply" | Email/IMAP | document | — | reply | — |
| "[Telegram voice] schedule a lead-gen loop every 10 days" | Telegram | audio→STT | **cron** | — | — |
| "[email forward] turn this thread into a CRM entry" | Email | text | — | — | CRM/MCP |
| "Enrich each lead in Box folder 'leads-Q2'" | — | — | — | — | **Box** |
| "Summarize last week of Gmail label 'investor'" | — | — | — | — | **Gmail** |
| "Find a free 30-min slot for me and Alice" | — | — | — | — | **Calendar** |
| "Hot lead (fit≥8) → post to #sales-hot" | — | — | — | **Slack pub** | — |
| "After arxiv sweep, publish to arxiv-rag topic" | — | — | cron | **topic pub** | — |
| "Staging deploy fails → page oncall" | — | — | sub/cron | **PagerDuty** | — |
| "Lead site gains online-ordering → email me" | — | — | **sub poller + state-diff** | email pub | — |
| "Calendly booking → draft prep doc from LinkedIn" | — | — | **sub: webhook** | — | LinkedIn/MCP |
| "PR opened w/ label needs-design → ping design ch." | — | — | **sub: GitHub webhook** | **Slack pub** | — |
| "Slack complaint posted → file Linear ticket" | — | — | **sub: Slack events** | — | **Linear** |
| "Stripe MRR drops >5% WoW → draft churn analysis" | — | — | **sub: webhook + state-diff** | — | — |
| "Gmail 'invoice' → extract amount → append to sheet" | — | — | **sub: IMAP idle / Gmail push** | — | **Sheets** |
| "Check arxiv daily, email a digest" | — | — | **cron** | email pub | — |
| "Every 10 days find a fresh lead" | — | — | **cron (interval)** | — | — |
| "In 2h check if PR #482 reviewed, ping me" | — | — | **cron (one-shot delay)** | ping | — |
| "Watch PR every 30m — CI green → merge & stop" | — | — | **cron (interval + self-cancel)** | — | GitHub |
| "[sub+pub] support email → classify → Linear or #sales" | Email | text | **sub** | **Linear + Slack** | — |
| "[cron+pub] Mon: scout leads, post top 3 for approval" | — | — | **cron** | **Slack pub** | — |
| "[gateway+sub] WhatsApp me when watched paper >50 cites" | WhatsApp | — | **sub poller + state-diff** | **WhatsApp pub** | — |
| "[gateway+cron+pub] 8am voice-call a 30s brief" | phone/voice | **audio out** | **cron** | **voice pub** | — |

Coverage holds: nothing in the brief needs a primitive beyond *Event → inbox*,
*tool within a turn* (MCP), or *publish sink*.

---

## 6. What exists vs. what's new

| Layer | Status |
|---|---|
| `[cron]` triggers | ✅ CUGA loops — done |
| MCP connectors | ✅ done; only per-user/thread credential binding to add |
| Inbox + drain loop | 🟡 cuga-runtime has the seed (global queue + worker) — generalize per-agent, move drain into agent loop |
| Event envelope + router | 🔴 new (small) |
| Gateways (Slack/WA/TG/Email) | 🔴 new — one adapter per channel |
| Ingest: docs / STT / VLM | 🔴 new — parsing + model calls |
| `[sub]` listeners / webhook / poller | 🔴 new — poller can reuse the loops scheduler |
| `[pub]` publish tool + sinks | 🔴 new (small) |
| `[swarm]` agent→agent messaging | 🟡 trivial once inboxes exist — it's an `agent_msg` Event |
| State-diff store | 🔴 new (small) |

**Suggested phasing**
1. Event envelope + per-agent inbox + in-loop drain (generalize cuga-runtime); re-point CUGA loops to enqueue.
2. First gateway (Slack) + text modality, end-to-end.
3. `[pub]` publish tool; `[sub]` webhook receiver + state-diff.
4. `[sub]` always-on listeners + hook poller; remaining gateways.
5. Document / audio / video ingest.
6. `[swarm]` — agent-to-agent, fan-out/fan-in, critic pairs.

---

## 7. Why this matches the statement

- **One async agent, one inbox, drained between turns** — verbatim.
- **"Fit the entire eventing/triggering model this way"** — gateways, cron, sub,
  pub, and swarm are all just *who writes the Event* and *which inbox it lands in*.
- **"Event that spawns agents"** — an `agent_msg` Event targeting a fresh
  `thread_id` (or a not-yet-running agent) is the spawn variant.
- **MCP stays orthogonal** — connectors are turn-time tools, never events, so the
  eventing model doesn't disturb what already works.
