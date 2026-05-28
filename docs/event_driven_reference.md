# Event-Driven CUGA — Reference

A focused reference for the first-class citizens of the event-driven model. The
full design rationale lives in [event_driven_agent_proposal.md](event_driven_agent_proposal.md);
the delivery plan in [event_driven_roadmap.md](event_driven_roadmap.md). This
doc is the short version: *what are the building blocks, and what's the
trigger taxonomy.*

---

## First-class citizens

### 1. `Event` — the envelope

The single inbound shape. Everything that wakes an agent is an `Event`.

```
Event {
  id            : str
  kind          : message | trigger | subscription | agent_msg
  source        : "gateway:slack" | "cron:loop:<id>"
                  | "sub:webhook:<id>" | "sub:poll:<id>"
                  | "agent:<name>"
  target        : { agent_name, thread_id }    # which inbox
  modality      : text | file | document | audio | video
  payload       : { text, context{}, attachments[] }
  reply_to      : { channel, address }         # where outbound goes
  credentials   : ref to per-user/per-thread creds
  priority      : normal | high
  created_at    : ts
}
```

Note: this is the *inbound* shape. Outbound emissions (`publish()`,
`reply_to`) are themselves Events — symmetric.

### 2. `Trigger` — what produces an Event

A trigger is anything that decides "an Event should exist now." Triggers fall
into three categories — see [Trigger taxonomy](#trigger-taxonomy) below.

A trigger does **not** know about agents or inboxes. It writes Events into the
bus stamped with `target` (resolved by the routing agent at setup time); the
dispatcher does the mechanical dispatch to the right inbox.

### 3. `Inbox` — per-agent message queue

**An Inbox is a message queue.** Specifically: one queue per agent, keyed
by `agent_name`. Opaque `get / put / task_done` interface to the agent loop,
so the implementation can swap from in-memory (`asyncio.Queue`) to durable
(SQLite/Redis/Kafka topic) without the loop knowing.

Per-thread ordering is preserved: events with the same `thread_id` are
serialized through the same agent loop.

**Why per-agent, not a single shared queue?** Five reasons:

1. **Agents are not interchangeable workers.** Each has its own LLM persona,
   prompt, tools, and policies. A shared-queue "whoever picks up first wins"
   model is wrong when consumers are non-interchangeable.
2. **Per-thread ordering** is structural, not enforced — two emails from
   Alice on the same thread land in the same inbox, processed in order, no
   partition-key discipline required.
3. **Smart routing is explicit**, decided once at setup by the routing agent
   (§9). With a shared queue, every consumer would filter on every message —
   wasteful and error-prone.
4. **Backpressure isolates per agent.** A slow scout doesn't block triage.
5. **Race-free by construction.** Only one agent loop reads from each
   inbox — no consumer-group leases or distributed locks needed.

This is the **actor model** (Erlang, Akka, Slack's internal services).
Per-actor mailbox + supervision. Shared queues are correct for *stateless*
task pools (e.g., "process this image"), not for identity-bearing agents.

For the full rationale (and the answer to *"but Kafka has topics, which
sounds like inboxes…"*), see
[event_driven_design_decisions.md §1](event_driven_design_decisions.md).

### 4. `Dispatcher` — bus internals (mechanical dispatch)

Stateless. Reads `Event.target`, drops the Event into that inbox or sink.
Also where auth, rate limits, and credential binding are enforced. **No
intelligence — the smart routing already happened at setup time, by the
routing agent (see §9).** Routing logic is just:

```
match ev.target.kind:
  "agent" → inbox[name]
  "sink"  → pub_sinks[name]
  "topic" → all subscribers
  "reply" → gateway.reply
```

### 5. `Agent loop` — the consumer

A long-running async coroutine, one per agent:

```python
while True:
    ev = await inbox.get()
    await run_turn(ev, ev.thread_id)
    inbox.task_done()
```

- **Long-running** (lives for the process lifetime, idle when the inbox is empty).
- **Turn-bounded** (each `run_turn` is short-lived; turns serialize per-inbox).
- **Pure CUGA** inside the turn — the LLM reads the Event, reasons, and may
  call CUGA tools. There is no special "classifier" service — classification
  is just the LLM thinking. Tools are reached for only when the agent needs
  to *do* something external (write to GitHub, query a CRM, hit a webhook).

### 6. `Pub sink` — downstream consumer

A destination for events the agent emits via `publish(destination, payload)`.
Slack channel, webhook URL, topic/queue, PagerDuty, email. Symmetric with
subscribe — one agent's `pub` can be another's `sub`.

### 7. `State-diff store`

A tiny keyed table: `last-seen value per subscription_id`. Pull triggers and
push triggers with transition semantics ("MRR drops >5%", "CI was pending now
green") use it to fire only on a real change.

### 8. `MCP connectors` — turn-time tools

Used **inside** a turn. **Not** events. Box, Gmail, Calendar, etc. The only
new work in the event-driven model is per-user/per-thread credential binding,
carried on `Event.credentials` and unwrapped at tool-call time.

### 9. `CUGA routing agent` — the intelligent dispatcher

The smart routing that the dispatcher *doesn't* do. This is the same
`CugaSupervisor` CUGA already ships today (`src/cuga/backend/cuga_supervisor_graph.py`)
— its LLM planner picks which agent should handle a request by selecting a
`delegate_to_<agent_name>` function.

In the event-driven model, the routing agent runs in **two distinct modes**:

| Mode | When | What it does |
|---|---|---|
| **Setup-time dispatcher** | A user types "*when X, do Y*" | LLM picks the right target agent via `delegate_to_*`, **bakes** `target_agent=...` into the new subscription row. One LLM call per rule. |
| **Runtime fallback dispatcher** | A subscription targets the routing agent itself (open-ended rules like "*@cuga in Slack: anything*") | The routing agent's inbox receives every event; LLM delegates per-event via `delegate_to_*`. One LLM call per event. |

**Critical:** for the common case, the routing agent decides routing **once at
setup**, then the dispatcher does cheap mechanical dispatch forever. The
routing agent is not in the runtime hot path unless the rule needs it.

This mirrors how loops works today: when a loop fires, the **routing agent**
is the one re-invoked, not a specialist directly
(`sdk.py:2343-2390`).

---

## Trigger taxonomy

Three categories. Every utterance in events.md fits one (or composes them).

| Category | Producer mechanism | Examples |
|---|---|---|
| **Push** | External system actively delivers a signal: webhook receiver, Slack events API, IMAP idle, GitHub webhook, Stripe webhook. CUGA *receives*. | "Calendly booking → draft prep doc", "PR opened with label → ping #design", "Customer emails support → triage" |
| **Pull** | CUGA *checks* a source on a schedule and emits an Event only on change. Hook poller + state-diff. Cheap when sources are passive (RSS, web pages, third-party APIs without webhooks). | "Watch OpenAI changelog every 6h, ping me on new model", "Check flight prices every 12h, alert if < $700", "Watch careers page hourly for AI roles" |
| **Timed** | Pure clock. Interval, cron string, or one-shot delay. No external state to check; just fire at time T. | "Every Monday 9am, post HN digest", "Daily at 8am email an arxiv summary", "In 2 hours check if PR #482 was reviewed" |

### Why the distinction matters

- **Push is cheapest at runtime, most expensive to set up.** You need a public
  endpoint (or an always-on listener), and the source needs to support
  webhooks/idle. When available, prefer it.
- **Pull is the universal fallback.** Reuses the loops scheduler — every pull
  trigger is a cron loop whose body is "fetch, diff, maybe emit." Slightly
  laggy, but works for sources that don't push.
- **Timed has no external state.** No diff needed. Simplest category.

### What goes where

| Pattern | Category |
|---|---|
| Webhook from third-party | Push |
| Slack events API mention | Push |
| IMAP idle / Gmail push | Push |
| Always-on websocket / socket-mode | Push |
| Scheduled scrape, RSS poll, API check + diff | Pull |
| Pure schedule (cron / interval / one-shot delay) | Timed |
| Agent-to-agent message (swarm) | (Push — special-case: producer is another agent) |

### The shape they share

All three categories produce the same `Event` envelope and use the same
dispatcher → inbox → agent-loop path. **The agent never knows what kind of
trigger fired it** — it just sees an Event with a `source` field. This is
the property that lets gateways and producers be developed as adapters
without rewiring the agent.

---

## Composition (where it gets interesting)

| Utterance | Categories used |
|---|---|
| "Support email → classify → Linear or #sales" | Push + Pub |
| "Monday 9am: scout leads, post top 3 for approval" | Timed + Pub |
| "Watch PR every 30m — CI green → merge & stop" | Pull + Pub (via MCP) + self-cancel |
| "WhatsApp me when watched paper crosses 50 citations" | Pull + state-diff + Push (outbound gateway) |
| "Critic reviews each scout result before posting" | Timed + Swarm + Pub |

Composition is free because the trigger type only affects *who writes the
Event*. Everything after the envelope is identical.

---

## Setup vs. runtime

- **Setup** is a synchronous CUGA turn. You tell the supervisor in natural
  language ("when X happens, do Y") and it calls setup tools:
  `subscribe_*`, `create_or_update_agent`, `register_pub_sink`. Subscriptions,
  agents, and sinks become rows in the same SQLite the loops use.
- **Runtime** is asynchronous. Triggers fire, the bus routes, agent loops
  consume. No human in the loop.

Don't conflate the two. The setup *configures* the runtime; it's not part of it.

---

## Deployment posture

- **Phase 1:** one host, one Python process. UI + agent loops + APScheduler +
  in-memory inbox + webhook receiver all in the same FastAPI app. Always-on
  listeners (IMAP idle, Slack socket-mode) live as sibling processes on the
  same host, posting events to `localhost`.
- **Phase 2 (M9):** factor when durability, multi-tenant credentials, or
  scale forces it. Same code, four deployables: UI, event bus, agent
  worker(s), producer services. The `Inbox` interface is the seam — one file
  swap.
