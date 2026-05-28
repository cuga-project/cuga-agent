# Event-Driven CUGA — Roadmap

Companion to [event_driven_agent_proposal.md](event_driven_agent_proposal.md). The
proposal argues *one primitive* — an `Event` dropped into a per-agent inbox —
covers cron, gateways, pub, sub, and swarm. This doc is the *delivery plan*:
what we build, in what order, and what each milestone unlocks.

## 0. North star

> **Trigger × agent × emit.** A trigger fires → an event lands in an agent's
> inbox → the agent drains the inbox between turns → it may emit zero or more
> events back out. Cron, Slack mention, webhook, agent-to-agent: same shape.

If a milestone introduces a primitive that *isn't* `Event → inbox → turn → emit`,
that's a smell. Adapters multiply; primitives shouldn't.

## 1. What we're starting from

| Piece | Where | What it gives us |
|---|---|---|
| **CUGA loops** | [src/cuga/backend/loops/](../src/cuga/backend/loops/) | `[cron]` producer — APScheduler + SQLite + self-scheduling tools + a UI. Agent invocation is direct ([runner.py:58](../src/cuga/backend/loops/runner.py#L58)). |
| **cuga-runtime** | [cuga-runtime/](../../cuga-agent-apr10/cuga-runtime/) | A global `asyncio.Queue` ([queue.py](../../cuga-agent-apr10/cuga-runtime/cuga_runtime/queue.py)) drained by a worker. Single agent, single inbox — the *seed* of the per-agent inbox. |
| **MCP connectors** | existing | Turn-time tools. Stay orthogonal. Gap: per-thread credential binding. |
| **Claude's model** | reference | Single agent, async, inbox drained between turns; subagents are also inbox-addressable; hooks are just events. The "shape" we're copying. |

The **gap** is the unifying abstraction. Loops calls `invoke()` directly; runtime
has one global queue; gateways/sub/pub/swarm don't exist. Everything else is
adapters around a primitive we haven't written yet.

## 2. Guiding principles

1. **One envelope, one inbox.** Don't ship two event shapes; don't ship a
   parallel "webhook handler" concept alongside "agent turn."
2. **Loops becomes a producer, not a caller.** `fire_loop` enqueues; it does not
   invoke. This is the single edit that proves the abstraction.
3. **Push vs. pull is a producer detail.** Webhooks for push sources, scheduled
   pollers (reusing loops) for pull sources. Both emit the same `Event`.
4. **MCP stays turn-time.** Connectors are tools the agent uses *within* a turn.
   They are never events. This keeps the eventing layer small.
5. **Phase 1 in-memory; durability is a one-file swap.** `asyncio.Queue` + dict
   in P1; SQLite/Redis-backed inbox in P2. The agent loop never learns about it.
6. **Ship a real channel end-to-end before generalizing.** Slack text → reply is
   the forcing function for the envelope, the dispatcher, and `reply_to`. Doing
   gateways in the abstract leads to a leaky envelope.

## 3. Milestones

Each milestone is sized to be **shippable**, **demoable**, and to *retire a
specific risk* in the design. Estimates are rough order-of-magnitude.

### M0 — Event envelope + per-agent inbox  *(foundation, ~1 wk)*

**Goal:** the primitive exists; one agent has an inbox; one synthetic producer
puts events on it; the agent drains between turns.

- Define `Event` (proposal §1) as a Pydantic model in a new
  `src/cuga/backend/events/` package.
- Per-agent `Inbox` keyed by `agent_name`. Phase-1 impl is
  `dict[str, asyncio.Queue[Event]]` — generalized from
  [cuga-runtime/queue.py](../../cuga-agent-apr10/cuga-runtime/cuga_runtime/queue.py).
- `Dispatcher.dispatch(event)` → picks inbox by `event.target.agent_name`.
- Agent loop: a thin `async def run(agent_name)` that loops
  `ev = await inbox.get(); await invoke(ev.payload.text, ev.target.thread_id); inbox.task_done()`.
- **Risk retired:** the envelope is good enough to express a cron fire *and* a
  user message without branching the agent loop.
- **Demo:** `curl POST /events` → event lands in inbox → agent answers → reply
  observable in run history.

### M1 — Loops as a producer  *(prove the abstraction, ~2–3 days)*

**Goal:** `fire_loop` stops calling `invoke()` and instead enqueues a
`kind=trigger` Event. *No user-visible change.* Same loops UI, same run
history, same self-scheduling tools.

- Replace [runner.py:58](../src/cuga/backend/loops/runner.py#L58)
  (`answer = await invoke_fn(loop.prompt, loop.thread_id)`) with
  `await dispatcher.dispatch(Event(kind="trigger", source=f"cron:loop:{loop_id}", target=..., payload=...))`.
- `target` initially points at the **routing agent** — that matches today's
  behavior (when a loop fires, the routing agent is re-invoked, not a
  specialist; see `sdk.py:2343-2390`). The routing agent's planner then
  uses its existing `delegate_to_*` mechanism to do dynamic routing per
  fire. No regression in routing semantics.
- Run-recording moves from runner into the agent loop's post-turn hook (turn
  finished → write `LoopRun` if `source.startswith("cron:")`).
- **Risk retired:** the envelope survives contact with a real, existing
  producer. If recording semantics get awkward here, the envelope is wrong.
- **Demo:** existing loops keep working, but every fire is now visible as an
  event on the bus (sets up M3 observability).

### M1.5 — Setup-time intelligent routing  *(unlocks direct dispatch, ~3 days)*

**Goal:** routing agent decides the target agent **once at setup**, bakes it
into the subscription row. Runtime path becomes direct (no LLM call per
event for routine rules).

- Extend `schedule_recurring` and the new `subscribe_*` tools to accept an
  optional `target_agent` argument.
- When the routing agent is in setup mode and recognizes the rule maps to a
  specialist (e.g., "scout leads" → `scout_agent`), it picks the target via
  `delegate_to_*` and passes it as `target_agent` into the setup tool.
- Open-ended rules continue to target the routing agent (the M1 default) —
  per-event delegation still works.
- **Risk retired:** the two-layer routing model (smart at setup, mechanical
  at runtime) is real and migrations are smooth.

### M2 — First gateway (Slack), text only  *(real inbound, ~1 wk)*

**Goal:** a user mentions `@cuga` in Slack; the agent replies in-thread.

- Slack adapter: events-API receiver → builds an `Event` with
  `source="gateway:slack"`, captures `reply_to={channel, thread_ts}`.
- Outbound: a tiny `gateway.reply(event, text)` the agent loop calls after each
  turn whose triggering event had a `reply_to`.
- Thread mapping: `thread_id = f"slack:{channel}:{thread_ts}"`.
- **Risk retired:** `reply_to` and `target` are sufficient; inbound modality
  parsing isn't being smuggled into the envelope.
- **Demo:** the brief's "@cuga in Slack: scout new restaurants in Brooklyn".

### M3 — `[pub]` publish tool + sinks  *(agent emits, ~3–5 days)*

**Goal:** the agent can call `publish(destination, payload)` and the event
fans out to a Slack channel / webhook / topic.

- `publish` MCP-style tool the agent can call. Backed by a tiny routing table
  (`topic → [slack_channel, webhook_url, …]`).
- Sinks: `slack_channel`, `generic_webhook`, `email`. Each is ~50 LOC.
- Reuse the loops run history table to log emitted events (audit/replay).
- **Risk retired:** outbound is symmetric with inbound; we don't grow a
  parallel "notification" concept.
- **Demo:** "When you find a hot lead, post it to #sales-hot" — a cron loop
  whose agent calls `publish("sales-hot", …)`.

### M4 — `[sub]` webhook receiver + state-diff  *(push-source reactions, ~1 wk)*

**Goal:** third parties POST to us and the right agent wakes up.

- `POST /sub/{subscription_id}` endpoint; subscription registry maps
  `subscription_id → (target_agent, prompt_template, secret)`.
- State-diff store (proposal §3.8): per-subscription "last seen value" so
  pollers/listeners fire only on transitions. Tiny SQLite table.
- **Risk retired:** subscriptions are *just rows* + a producer; not a new
  agent-loop concept.
- **Demo:** "Calendly booking → draft prep doc."

### M5 — `[sub]` hook poller (reuse loops) + always-on listeners  *(pull sources, ~1–2 wk)*

**Goal:** pull sources work without leaking the push/pull distinction.

- **Hook poller:** subscriptions of kind `poll` become cron loops whose
  `fire_loop` runs a small *check* (HTTP fetch, RSS, scrape), diffs against the
  state store, and enqueues an Event only on change. **No new scheduler** —
  this is the payoff for M1.
- **Always-on listeners:** long-lived processes (IMAP idle, Slack RTM/socket,
  websockets) running as supervised asyncio tasks; each is a thin event
  producer.
- **Risk retired:** the brief's claim — "don't try to unify push and pull, just
  have both produce the same event" — survives implementation.

### M6 — Remaining gateways  *(WhatsApp, Telegram, Email/IMAP, ~2–3 wk total)*

Each is an adapter pair (inbound + outbound) over the same envelope. Email is
the spiciest because of IMAP idle and MIME parsing — keep it text-only here.

### M7 — Modality: documents → audio → video  *(ingest layer, multi-week)*

Ingest layer (proposal §3.2) sits between gateway and dispatcher:
- **Documents** first (PDF, decks): parse to text + structured blocks; raw kept
  as `attachments[]`.
- **Audio** next: STT in the loop, transcript becomes `payload.text`, raw audio
  stays as attachment.
- **Video** last: VLM on frames; only when a real use case lands.

Phasing matters because each modality has its own failure modes (bad OCR,
hallucinated transcripts) — don't debug them simultaneously.

### M8 — `[swarm]` agent-to-agent  *(near-free after M0, ~3 days)*

Agent A's tool: `send_to(agent_b, payload, thread_id=…)` → writes an
`Event(kind="agent_msg", source="agent:A", target={agent_b, …})` into B's
inbox. Fan-out is N writes; critic pairs are two agents writing to each other.
**No new primitive** — this is the proposal's point.

### M9 — Durability + multi-process  *(productionization, multi-week)*

- Swap in-memory inbox for SQLite (single-host) or Redis Streams (multi-host).
- Per-user/per-thread credential binding on `Event.credentials` flows through
  to MCP connectors at turn time.
- At-least-once delivery + idempotency keys on producers.
- This is the cuga-runtime "Phase 2" promise made concrete.

## 4. Risks & how each milestone retires them

| Risk | Where it bites | Retired by |
|---|---|---|
| Envelope leaks modality/channel quirks into the agent loop | M0 looks fine in isolation; M2 forces honesty | M2 |
| Loops semantics (run history, expiry, UI) get awkward when invocation moves | First real producer | M1 |
| Push and pull subscriptions grow divergent abstractions | M5 | M5 (poller reuses loops; both emit same Event) |
| Pub becomes a parallel notification system | M3 | M3 (single `publish` tool; sinks are dumb) |
| Per-user credentials retrofit is painful | Anywhere a connector touches user data | M9 (carry on `Event.credentials` from day one in M0) |
| Multi-modal ingest debugging blocks channel rollout | M7 if attempted alongside M6 | sequencing — M6 text-only first |
| Durability rewrite forces agent-loop changes | M9 | M0 contract (`Inbox` is opaque; agent loop sees only `get/put/task_done`) |

## 5. What this is *not* committing to

- A new framework. The agent loop, the supervisor, and the loops UI all stay.
  We are adding an envelope and a dispatcher, and moving one call site.
- Replacing CUGA loops. Loops becomes the canonical `[cron]` producer and gets
  *more* used, not less.
- A new persistence layer for runs. We piggyback on the loops `runs` table
  until M9 forces the question.
- Cross-process orchestration before M9. Phase 1 is one process, many
  coroutines.

## 6. The first PR

Concretely, the smallest move that proves this is real:

1. Add `src/cuga/backend/events/{envelope.py, inbox.py, dispatcher.py}` — ~150 LOC.
2. Wire a single `Inbox` keyed by `agent_name`; agent loop drains it.
3. Change [runner.py:58](../src/cuga/backend/loops/runner.py#L58) to enqueue
   instead of invoke; move run-recording to a post-turn hook.
4. Smoke test: existing loops still fire and record runs identically.

That single PR is M0 + M1. Everything after is adapters.
