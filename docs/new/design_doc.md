# Design Doc — How Event-Driven CUGA Actually Works

Plain-English design doc covering the runtime mechanics: *what's always
running, who builds an Event, and what wakes the agent*.

This is the doc to read first if "agent loop", "registry", "inbox",
"producer", and "trigger" are all blurring together. It precedes the
architecture diagrams because it explains the words *in* those diagrams.

---

## The headline mental model

> **The agent loop is parked, not polling. Something else wakes it up by
> putting an Event in its Inbox.**

That's the whole runtime model in one sentence. The agent does **zero work**
when nothing is happening. It is a coroutine blocked on a queue. It uses
no CPU and makes no LLM calls until an Event lands in its inbox and unblocks
the queue read.

Everything else in this doc is just walking through *which thing*
puts the event in the inbox, for each kind of trigger.

---

## Naming — two layers, on purpose

When you read this design package you'll see two words that sound similar:

| Word | What it actually is | Layer |
|---|---|---|
| **Agent loop** | A long-running async coroutine. One per agent. Parked on `await inbox.get()`. The thing that *processes* events. | **Runtime** (in memory) |
| **Standing intent** | A row in the registry. Captures an *event-driven intent* the user expressed via an utterance: *"every Monday do X"*, *"when emails arrive, do Y"*. The thing that *configures* event-driven behavior. | **Configuration** (in DB) |

A standing intent is a **standing rule the user expressed via natural
language**. When CUGA's first turn classifies an utterance as
`setup_standing`, it materializes the intent into a `subscriptions` row.
That row IS the standing intent.

Two layers, two words. Don't conflate them:

- *"I have 5 standing intents configured"* — talking about DB rows
- *"The scout agent's agent loop wakes when an event lands"* — talking
  about the runtime coroutine

Historical note: an earlier iteration of this experimental code called
standing intents "CUGA loops" (because they loop in time). That name is
**dropped** — too easily confused with "agent loop". *Standing intent*
is the noun going forward.

For the technical layer: the registry table is still called
`subscriptions` because that's what it is from a data-model perspective.
The user-facing word is "standing intent"; the DB-facing word is
"subscription". Same thing, different layer.

---

## Glossary — the four words you need

Before anything else, get these straight. They mean different things.

| Word | What it actually is |
|---|---|
| **Agent loop** | A **long-running async coroutine**, one per agent. Its code is `while True: ev = await inbox.get(); run_turn(ev); inbox.task_done()`. It is **parked** at `await inbox.get()` when there's no work — zero CPU, zero LLM. |
| **Registry** | A **SQLite database** holding configuration rows: which subscriptions exist, which agents exist, which pub sinks exist. **Events are NEVER stored here.** The registry is read mostly at startup and when configuration changes. |
| **Inbox** | A **per-agent `asyncio.Queue`**. One per agent. Events get put here; the agent loop drains them. This is the only thing that "holds events" at runtime. |
| **Trigger** | The **mechanism that creates an Event and hands it to the dispatcher**. Six kinds. Each one has a different "always-running" component doing the work. |

If you find yourself mixing these up, come back here. Especially: **the
registry is not a queue.** Events don't sit in the registry waiting to be
processed.

---

## What's always running in the CUGA process

When the CUGA process is started, several things become alive at once and
*stay* alive for the process lifetime:

| # | What | What it's doing | State |
|---|---|---|---|
| 1 | **N agent loop coroutines** (one per registered agent) | Each is blocked on `await inbox.get()` waiting for its Inbox to fill | **Parked.** Zero CPU. Zero LLM. |
| 2 | **APScheduler** | Watches the clock; knows about cron + pull-poller subscriptions | Sleeping until next scheduled tick |
| 3 | **FastAPI server** | Listening on a port; has webhook routes registered for `push` subscriptions | Accepting connections |
| 4 | **Always-on listener tasks** (Slack socket, IMAP IDLE, websocket, …) | Each holds a long-lived TCP connection to an external system | Blocked on `recv()` of that connection |

That's the "always on" picture. **Critically**: the agent loops do NOT
check anything. They are asleep. The things that "wait for stuff" are
APScheduler (waits on clock), FastAPI (waits on incoming HTTP), and the
listener tasks (wait on a TCP socket).

You can think of the agents as **the workers in a warehouse**. They don't
patrol looking for orders. They sit at a counter, asleep. Other systems
hand them work tickets (Events) when work shows up.

---

## How each trigger wakes an agent — step by step

Six trigger kinds. The pattern is always the same shape:

> *Some always-running thing decides "an event should exist now," builds an
> Event, calls `dispatcher.dispatch(event)`. The dispatcher puts it in the
> right Inbox. The agent that was parked on that Inbox wakes up.*

What varies is **who** the "always-running thing" is.

### 1. Timed (cron) — APScheduler firing standing intents

> *"Every Monday at 9am, post an HN digest."*

```
APScheduler hits the moment 'Mon 9am'
   │
   ▼ runs the registered job function: fire_standing_intent(intent_id)
   │
   ▼ fire_standing_intent reads the subscription row from registry
   │  (target_agent = 'digest_agent', prompt = '…')
   │
   ▼ builds Event(target = digest_agent, payload.text = prompt)
   │
   ▼ dispatcher.dispatch(event)
   │
   ▼ dispatcher: match ev.target.kind=agent
   │  → inbox['digest_agent'].put(event)
   │
   ▼ digest_agent's blocked `await inbox.get()` returns
   │
   ▼ digest_agent runs its 5-stage turn
   │
   ▼ returns to top of loop, parks on inbox.get() again
```

**Who wakes the agent?** APScheduler did, indirectly. The agent has no idea
the clock exists.

### 2. Push (webhook receiver)

> *"When Stripe MRR drops > 5% week-over-week, draft a churn analysis."*

```
Stripe POSTs to https://cuga.example.com/sub/abc123
   │
   ▼ FastAPI handler runs
   │
   ▼ handler reads subscription abc123 from registry
   │  (target_agent = 'finance_agent', filter check, state-diff check)
   │
   ▼ MRR check fails (it dropped 7%) → emit
   │
   ▼ builds Event(target = finance_agent, payload = invoice details)
   │
   ▼ dispatcher → inbox['finance_agent'].put(event)
   │
   ▼ finance_agent wakes
```

**Who wakes the agent?** Stripe did, by hitting the FastAPI endpoint. The
FastAPI handler just routes that signal into the bus.

### 3. Push (always-on listener) — Slack socket / IMAP IDLE / WebSocket

> *"@cuga in Slack: scout new restaurants in Brooklyn."*

```
The Slack listener has been parked on a socket for hours.
A new message arrives on the socket.
   │
   ▼ the listener's recv() returns
   │
   ▼ listener parses the Slack event, looks up subscription
   │
   ▼ builds Event(target = scout_agent, payload = "scout new restaurants…")
   │
   ▼ dispatcher → inbox['scout_agent'].put(event)
   │
   ▼ scout_agent wakes
```

**Who wakes the agent?** The Slack listener task did, by reading bytes
off a held TCP connection.

### 4. Pull (poller)

> *"Watch the OpenAI changelog every 6 hours; ping me on a new model."*

```
APScheduler hits 'every 6h' moment
   │
   ▼ runs the poller function for subscription 'openai-changelog-watch'
   │
   ▼ poller fetches the URL (HTTP GET)
   │
   ▼ poller compares response to last-seen value in state_diff table
   │
   ┌─────────────────────────┬────────────────────────────┐
   ▼                         ▼
NO change                  CHANGED
   │                         │
   ▼ return silently         ▼ builds Event(target=alerter_agent, …)
   │ no Event built          │
   │ agent stays asleep      ▼ dispatcher → inbox → agent wakes
```

**Who wakes the agent?** APScheduler scheduled the work, the poller did
the check, and the agent ONLY wakes if something actually changed
externally. Most ticks produce no Event.

### 5. Agent emit (swarm)

> *"Scout finds leads, sends them to Critic for review."*

```
scout_agent is in the middle of its turn.
It calls a tool: send_to(critic_agent, {leads: [...]})
   │
   ▼ send_to is a wrapper around dispatcher.dispatch(...)
   │  it builds Event(kind=agent_msg, target=critic_agent, payload={leads:…})
   │
   ▼ dispatcher → inbox['critic_agent'].put(event)
   │
   ▼ critic_agent's blocked await inbox.get() returns
   │
   ▼ critic_agent runs its own turn (independent coroutine)
   │
   ▼ meanwhile scout_agent continues its turn,
   │  unaffected — its own send_to was fire-and-forget
```

**Who wakes critic?** scout's tool call did. The dispatcher doesn't care
that the producer is another agent — it just routes by `target`.

### 6. Hook — the `SkillHookDispatcher` (the new architectural addition)

> *"Every time any agent calls linear.create_ticket, log to the audit DB."*

```
triage_agent is in the middle of its turn.
It calls a tool: linear.create_ticket(...)
   │
   ▼ SkillHookDispatcher wraps the call
   │  the actual Linear API call runs → success
   │
   ▼ SkillHookDispatcher: do any subscriptions match
   │   (on=post_skill, skill=linear.create_ticket)?
   │
   ▼ YES — subscription 'audit-linear-tickets' matches
   │
   ▼ builds Event(kind=skill_event, target=audit_logger, payload={skill, args, result, ...})
   │
   ▼ dispatcher → inbox['audit_logger'].put(event)
   │
   ▼ audit_logger wakes
   │
   ▼ meanwhile triage_agent continues its turn,
   │  unaware that anything fired
```

**Who wakes audit_logger?** triage_agent's tool call did, indirectly,
through the `SkillHookDispatcher` wrapping that tool call. This is the
only mechanism in the system where an event is produced by *another
agent's tool execution*. It's the reason hooks need architectural
work — the other five trigger types existed already.

---

## The single mental model — restated

All six trigger types are the same shape:

```
[long-running thing decides "event should exist now"]
            │
            ▼
[builds Event with a target]
            │
            ▼
[dispatcher.dispatch(event)]
            │
            ▼
[match ev.target → inbox[name].put(event)]
            │
            ▼
[agent's blocked await inbox.get() returns]
            │
            ▼
[agent runs its 5-stage turn]
            │
            ▼
[agent parks on inbox.get() again]
```

The "long-running thing" varies — APScheduler, a FastAPI handler, a TCP
listener, another agent's tool call, the SkillHookDispatcher. But once an
Event exists, the path is **identical**: dispatcher → inbox → wake.

---

## What "drain the inbox" means (and what it doesn't)

When an agent's `await inbox.get()` returns one Event:

- If the inbox has more events queued (because two arrived in quick
  succession), the agent processes them **one at a time** — that's
  "draining."
- If the inbox is empty after processing one, the next `await inbox.get()`
  **parks the agent again** until someone else writes to it.

"Draining" doesn't mean "checking." The agent doesn't poll. It only acts
when the queue actually has something. Between events, it's truly idle.

---

## Three quick reality checks

**Q: Does the agent loop check the clock?**
A: **No.** APScheduler checks the clock. The agent only sees an Event
once APScheduler fires a job that builds one.

**Q: Does the agent loop check for new emails?**
A: **No.** The IMAP listener task does that, holding an IDLE connection.
The agent only sees an Event when the listener delivers one to the bus.

**Q: Does the agent loop read the registry?**
A: **Not at runtime, per event.** The registry was read at startup to
know which agents/subscriptions exist. Producers (APScheduler jobs,
webhook handlers, listeners) read subscription rows when they need to know
the target. The agent loop just reads from its own Inbox.

---

## Producer vs Processor — when these words matter

If you find yourself in a meeting where someone uses "producer" or
"processor" without context:

- **Producer** = anything that *creates an Event and calls dispatch*.
  Six kinds: cron, gateway, webhook receiver, pull poller, agent's
  `send_to` / `publish` / `reply_to` tool calls, and the SkillHookDispatcher.

- **Processor** = anything that *consumes an Event*. Four kinds: agent
  loops (the primary one), pub sink adapters (Slack channel writer, Linear
  API writer, etc.), outbound gateway adapters (renders agent's reply back
  on the originating channel), and the routing agent (when a subscription
  targets it for runtime delegation).

A given thing can be both — e.g., an agent loop is a processor *and*, when
it emits, it produces. Same for the routing agent.

---

## Where the registry, the inbox, and the bus really sit

A picture in words:

```
┌──────────────────────────────────────────────────────────────────────┐
│ CUGA process (one OS process; everything below runs concurrently)    │
│                                                                      │
│  ┌──────────────┐                                                    │
│  │  REGISTRY    │   read-mostly. Configuration only. NO events here. │
│  │  (SQLite)    │   APScheduler / FastAPI / listeners read at start. │
│  └──────────────┘                                                    │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ ALWAYS-RUNNING TASKS                                           │  │
│  │  • APScheduler (watches clock)                                 │  │
│  │  • FastAPI server (listens on port)                            │  │
│  │  • N listener tasks (hold TCP connections)                     │  │
│  │  • N agent loops, PARKED on their Inboxes                      │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ THE BUS                                                        │  │
│  │  • dispatcher (a function, ~50 LOC)                            │  │
│  │  • N per-agent inboxes (asyncio.Queue each)                    │  │
│  │  These are where events actually FLOW.                         │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

Three categorically different things:

1. **Registry** — configuration storage. Disk-backed. Reads ≫ writes.
2. **Always-running tasks** — the things that produce events.
3. **The bus** — dispatcher + inboxes, the channels events flow through.

Mixing them up is the source of most confusion.

---

## Why this matters for the configuration story

With the runtime mechanics clear, the configuration architecture story
gets simpler:

- **Modes 1 and 2** (interactive and declarative) **only write to the
  registry** — they change *what subscriptions exist*. They don't change
  any always-running task or any bus mechanic. The producers (APScheduler,
  FastAPI handlers, listeners) re-read the registry to learn about the new
  subscription and arm themselves.

- **Mode 3** (hooks) requires an *additional always-running thing* — the
  `SkillHookDispatcher`. That's why it's the only new architecture. It's
  a new producer category, and the new producer needs new wiring (a
  wrapper around the agent's tool runtime).

So the two-architecture framing maps cleanly to this doc:

| Mode | Changes the registry? | Adds a new always-running task? | New architecture? |
|---|---|---|---|
| 1. Interactive | Yes | No | No |
| 2. Declarative | Yes | No | No |
| 3. Hook | Yes | **Yes — SkillHookDispatcher** | **Yes — but additive** |

---

## See also

- [intent_classification.md](intent_classification.md) — the **first turn**
  CUGA takes on any utterance: classify into one-shot / setup_standing /
  modify_standing / query_standings. This is what decides whether the rest of
  this doc even applies.
- [configuration_architecture.md](configuration_architecture.md) — the
  architecture-level story (Modes 1/2 share an architecture; Mode 3 adds
  the hook layer)
- [configuration_modes.md](configuration_modes.md) — the day-to-day
  overview of the three surfaces
- [declarative_config.md](declarative_config.md) — the YAML/CLI spec
- [skill_hooks.md](skill_hooks.md) — the hook design
- [producers_and_processors.png](producers_and_processors.png) — visual
  reference for the six producers and four processors
- [unified_architecture_part1.png](unified_architecture_part1.png) +
  [unified_architecture_part2.png](unified_architecture_part2.png) — the
  Part 1 / Part 2 comparison showing how additive B is on top of A
- [runtime_mechanics.png](runtime_mechanics.png) — the runtime mechanics
  with PARKED / WAKES labels making this doc visual
