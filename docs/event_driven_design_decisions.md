# Event-Driven CUGA — Design Decisions

The "why" behind the non-obvious calls. Each section is a question you might
ask reading the design, with an honest answer.

---

## 1. Why per-agent inboxes instead of one shared queue?

The most common question. Most message-queue systems (Kafka, SQS, RabbitMQ
work queues) use **one queue, many consumers competing for messages**. We
deliberately don't. Each agent has its own queue.

### TL;DR

CUGA agents have **identity** (each is its own LLM persona with its own
prompt and tool surface). Shared queues assume **interchangeable workers**.
That mismatch is the whole reason.

### The five concrete reasons

**1. Agents are not interchangeable workers.**
A shared queue assumes any consumer can do any job. CUGA's scout_agent
cannot do triage; the triage_agent cannot file Linear tickets the way a
support agent would. Each has its own prompt, policies, and tools.
"Whoever picks up first wins" — the standard shared-queue semantics — is
wrong here. The routing decision must be **deliberate**, not race-based.

**2. Per-thread ordering must be preserved.**
If Alice emails support twice in 30 seconds, the two events must be
processed in order on the *same agent + thread*. With per-agent inboxes
keyed by `thread_id`, ordering falls out for free. With a shared queue,
you'd need partition keys + consumer group semantics, AND you still have
to make sure both messages land at the same consumer. Per-agent inboxes
make this structural instead of a discipline you have to enforce.

**3. The smart routing is explicit, not consumption-time filtering.**
With a shared queue, every consumer reads every message and filters on
"is this for me?". That's wasteful and error-prone — one consumer
forgetting to filter, or filtering wrong, and events get processed twice
or dropped. With per-agent inboxes, the Dispatcher decides ONCE at
delivery: "this event goes here, end of story." The routing decision is
in *one* place.

**4. Backpressure isolation.**
If `scout_agent` is slow (e.g., long-running tool calls), its inbox
backs up. With per-agent inboxes, this doesn't affect `triage_agent` —
triage continues at full speed. With a shared queue, slow consumers
either hold up the queue head (broken) or you partition (which is just
"per-agent inboxes" with extra steps).

**5. Two agents racing on the same event is impossible by construction.**
A shared queue with multiple consumers needs consumer groups + careful
ack semantics to avoid two consumers grabbing the same message. With
per-agent inboxes, only ONE agent loop reads from each inbox — race-free
by design. No need for distributed locks, leader election, or claim leases.

### When shared queues ARE the right call

Shared queues shine for **stateless task workers** — "process this image,"
"send this email." Any worker can do any job; you scale by adding workers
to the same queue. That's not CUGA's model.

If you ever introduce a **stateless tool worker pool** (e.g., "PDF parser
pool — any worker can parse any PDF"), THAT belongs on a shared queue. But
that's a tool-execution detail, not an agent dispatch decision.

### "But Kafka has topics, which sounds like inboxes…"

Right — and that's exactly the production translation. In the Kafka
deployment ([event_driven_kafka_architecture.png](event_driven_kafka_architecture.png)),
each agent gets its own **topic**, and `thread_id` is the partition key.
Multiple worker processes for one agent join the same **consumer group** —
which gives you horizontal scaling *within one agent's identity*, while
preserving per-thread ordering.

So at scale, per-agent inboxes don't mean "one consumer per agent." They
mean "one consumer GROUP per agent, with the agent's identity defining the
group's behavior."

### The pattern name

This is the **actor model** (Erlang, Akka, Pony, even how Slack's own
internal services are built). Per-actor mailbox + supervision tree. CUGA
isn't reinventing it — we're picking it because it's the right shape for
"intelligent workers with identity."

---

## 2. Why is the Dispatcher dumb, and the Routing Agent smart?

See [event_driven_reference.md §4 + §9](event_driven_reference.md) and
[event_driven_deck.md Slide 4b](event_driven_deck.md).

Short version: the smart routing decision needs to happen **once per rule
at setup time**, not **once per event at runtime**. Baking the routing
agent's `delegate_to_*` decision into the subscription row turns N LLM
calls (one per event) into 1 LLM call (one per rule).

---

## 3. Why direct addressing (target = agent name) over topic subscriptions?

See [event_driven_reference.md](event_driven_reference.md) trigger taxonomy
and the conversation history. Short version:

- ~95% of utterances in events.md have a clear "who handles this" answer
  at setup time — direct addressing maps cleanly.
- Topics are useful when the producer shouldn't know consumers (the
  "arxiv-rag → notion-sync" example), and pub sinks already give us
  topic-shaped outbound. Agents can subscribe to pub sinks if needed.
- Inbound direct addressing + outbound pub sinks (topic-shaped) covers
  every events.md case without inventing a generalized topic system that
  only has one customer.

---

## 4. Why MCP connectors are NOT events

A common conflation: "If everything is an Event, why isn't a tool call
also an Event?"

Tool calls are **synchronous, intra-turn**. The agent thinks, calls a tool,
gets a result, keeps thinking. Making this asynchronous-via-events would:

- Add latency (encode/route/decode for every tool call).
- Break the LLM's reasoning model — tools are supposed to return results
  the LLM can immediately reason about.
- Require correlation logic for every tool call.
- Multiply the audit volume by 10–50× (LLMs make many tool calls per turn).

So MCP connectors are turn-time tools. Events are for *between-turn*
coordination — what wakes an agent, what an agent emits when it's done.

---

## 5. Why a single line of code (`runner.py:58`) is the seam

See [event_driven_from_loops.md](event_driven_from_loops.md) Part 1.

CUGA loops today calls `invoke_fn(prompt, thread_id)` directly. Replacing
that one call with `router.dispatch(Event(...))` turns loops into the
canonical `[cron]` producer with zero user-visible change. The reason it's
this small: loops already has all the persistence, scheduling, agent
registry, and run history we need. The eventing layer just changes how
the agent is *invoked*, not what loops *manages*.

---

## 6. Why Phase 1 is in-memory and not durable from day one

Three reasons:

1. **The Inbox interface is the seam.** Swapping in-memory → SQLite → Kafka
   is a one-file change. Building a durable backend day one is a months-long
   ops project that delays everything else.
2. **CUGA today loses state on restart.** The current loops registry survives,
   but in-flight invocations don't. Phase 1 doesn't regress this.
3. **Most failure modes don't need durability.** Network blips, transient
   errors, etc. are retried at the producer. The thing durability solves is
   "process restart loses in-flight events" — a real but contained problem
   that becomes acute only at multi-tenant / production scale.

See [event_driven_kafka_migration.md](event_driven_kafka_migration.md) for
when to swap.

---

## 7. Why Loops is the seed and not a parallel module

CUGA loops already routes through the routing agent
(`sdk.py:2343-2390`). It has SQLite-backed persistence, a registry, run
history, a UI, and a natural-language UX (users say "every 10 days, find
me a lead" and the supervisor schedules it). Building event-driven CUGA
as a parallel module would mean duplicating all of this. Evolving loops
means we keep all of it and add adapters.

See [event_driven_from_loops.md](event_driven_from_loops.md) for the
explicit "what survives, what generalizes, what's deprecated" table.

---

## Adding new decisions

When a design call gets made (or revisited), add a section here. Future
maintainers should be able to read this doc and understand *why* the
architecture is what it is, not just *what* it is.
