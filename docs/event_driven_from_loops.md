# From CUGA Loops to Event-Driven CUGA

A grounded evolution plan, starting from the code that actually exists today.

Companion docs:
- [event_driven_reference.md](event_driven_reference.md) — first-class citizens & trigger taxonomy
- [event_driven_roadmap.md](event_driven_roadmap.md) — milestone plan with risks
- [event_driven_agent_proposal.md](event_driven_agent_proposal.md) — full design rationale

---

## Part 1 — What CUGA Loops is (the pitch)

**CUGA Loops gives any CUGA agent a clock.**

In one sentence: an agent calls `schedule_recurring("every Monday 9am", "post a digest")` as a regular tool, and CUGA promises to wake that same agent up — same `thread_id`, same persona — at that time. Forever, until it's cancelled or expires.

Why it matters as a building block:

1. **It breaks the request/response shape.** A normal CUGA turn is sync: user asks, agent answers, done. A scheduled loop turns *time itself* into a trigger. The agent now does work the user didn't directly request, on behalf of the user. That's the foundational shift toward event-driven.

2. **It's already a "trigger × agent × emit" system.** The trigger is the cron. The agent is identified by name in a registry. The emit is whatever the agent does in that turn (right now usually a side-effect via tools or a run-history record). The shape is already there for one trigger type.

3. **It's the only piece of CUGA that's been engineered for *standing rules***. Subscriptions, gateways, webhooks — none of those exist yet. But the **registry → scheduler → runner → registered-agent-invoke** path is loops solving exactly that pattern for cron. The same path scales to any trigger.

4. **The UX precedent is set.** Users already say "every 10 days find me a fresh lead" in natural language; the supervisor turns it into a `schedule_recurring` tool call. We don't need to teach the user a new mental model to extend this — "when X happens, do Y" is the same shape, just a different trigger.

So: **cuga-loops is the seed of event-driven CUGA.** It happens to only support timed triggers today, but architecturally, everything else is a sibling.

### What loops looks like today

```
src/cuga/backend/loops/
├── __init__.py        # public surface
├── models.py          # Loop, LoopRun, TriggerKind {DELAY, INTERVAL, CRON}, LoopStatus
├── registry.py        # SQLite-backed persistence (cuga_loops_* tables)
├── service.py         # LoopsService singleton: APScheduler + agent registry
├── runner.py          # fire_loop() — what APScheduler calls when a loop fires
├── tools.py           # @tool schedule_recurring / schedule_wakeup / list_my_loops / cancel_loop
├── cron_parser.py     # "5m", "every weekday 9am" → APScheduler triggers
├── api.py             # FastAPI router for the loops UI
└── ui.html            # the loops dashboard
```

The five things to remember:

| Piece | Role |
|---|---|
| `LoopsService` | Singleton holding APScheduler + `dict[agent_name → invoke_fn]` |
| `LoopsRegistry` | SQLite store for loops + run history (survives restarts) |
| `fire_loop(loop_id)` | The job APScheduler calls. Looks up the loop, finds the agent, **calls `invoke_fn(prompt, thread_id)` directly**, records a `LoopRun` |
| `schedule_*` tools | Agent-callable LangChain `@tool` functions; use context vars to know which agent + thread is calling |
| `runs/` history | Side-effect of `fire_loop` — captures `LoopRun` rows + per-app run history |

### The single most important line of code

[`runner.py:58`](../src/cuga/backend/loops/runner.py#L58):

```python
answer = await invoke_fn(loop.prompt, loop.thread_id)
```

This is **where loops becomes event-driven.** Today it's a direct call. In the event-driven world, this becomes:

```python
await dispatcher.dispatch(Event(
    kind="trigger",
    source=f"cron:loop:{loop_id}",
    target=Target(agent_name=loop.agent_name, thread_id=loop.thread_id),
    payload=Payload(text=loop.prompt),
))
```

One mutation. Everything else in this document follows from that change.

---

## Part 2 — Why loops is the right starting point

Four reasons to evolve *from* loops rather than build event-driven CUGA as a parallel module.

### 0. Loops already routes through the routing agent (just like we want)

When a loop fires today, [`runner.py`](../src/cuga/backend/loops/runner.py)
calls `invoke_fn(prompt, thread_id)` on a **routing agent** registered for the
app — not on a specialist agent directly (`sdk.py:2343-2390`). The
routing agent's LLM then uses its existing `delegate_to_<agent>` mechanism to
pick the right specialist per fire.

**This is exactly the runtime fallback routing pattern we want for the
event-driven model.** No new routing intelligence to build — CUGA already
has it. The only addition is *setup-time* baking: at setup, the routing agent
decides once which agent should receive future fires and stamps that on the
subscription row. Then runtime dispatch is direct (no LLM cost per event)
for rules where the target is unambiguous.

### 1. Loops already proves the data model

Look at `Loop` in [models.py](../src/cuga/backend/loops/models.py):

- `agent_name` + `thread_id` — exactly the `Event.target` shape.
- `prompt` — the `Event.payload.text` we need.
- `trigger_kind` + `trigger_spec` + `metadata_json` — the producer-side spec.
- `status` + `fire_count` + `error_count` + `last_error` — operational state.

These fields aren't loops-specific. They're "what does a producer need to know to wake an agent." Every other trigger type (push subscription, pull poller, webhook) needs the same fields. **Generalize `Loop` and you get `Subscription`.**

### 2. The agent registry IS the consumer-side inbox dispatcher

`LoopsService._agents: Dict[str, InvokeFn]` is already the "given an agent name, how do I deliver work to it" lookup table. The event-driven version replaces `InvokeFn` with `Inbox.put(Event)`, but the registry itself doesn't change. It's the right abstraction at the right level — we just swap the value type.

### 3. Run history is the audit log we'd build anyway

[`registry.insert_run`](../src/cuga/backend/loops/registry.py) writes a `LoopRun` after every fire. Event-driven CUGA needs the same thing — every Event consumed by an agent should leave an audit trail. Loops already runs that table; we extend it to `event_runs` with a `source` discriminator.

**Net:** loops is not a side feature to absorb. It's the prototype of the system, with one trigger type implemented. We're not refactoring loops — we're letting loops grow.

---

## Part 3 — The evolution, in nine steps

Each step is a single shippable change. The first three are mechanical (no user-visible difference); the rest add capability one trigger or sink at a time. Every step keeps loops working.

### Step 1 — Define the `Event` envelope (no behavior change)

**Where:** new `src/cuga/backend/events/envelope.py`.

```python
class Event(BaseModel):
    id: str
    kind: Literal["message", "trigger", "subscription", "agent_msg"]
    source: str                       # "cron:loop:<id>", "sub:webhook:<id>", "gateway:slack", "agent:<name>"
    target: Target                    # agent_name + thread_id (same shape as Loop)
    modality: Literal["text", "file", "document", "audio", "video"] = "text"
    payload: Payload                  # text + attachments + context
    reply_to: Optional[ReplyTo] = None
    credentials: Optional[CredRef] = None
    priority: Literal["normal", "high"] = "normal"
    created_at: float
```

Nothing wired up yet. This is the contract.

**Touches:** new package. No existing files change.

### Step 2 — Per-agent `Inbox` + `Dispatcher` (still no behavior change)

**Where:** new `src/cuga/backend/events/inbox.py` and `dispatcher.py`.

```python
class Inbox:                          # one per agent
    async def put(self, ev: Event): ...
    async def get(self) -> Event: ...
    def task_done(self): ...

class Dispatcher:
    def __init__(self, inboxes: dict[str, Inbox]): ...
    async def dispatch(self, ev: Event): ...   # picks by ev.target.agent_name
```

Phase 1 implementation: `asyncio.Queue` per agent, in-memory dict for the registry. **Generalizes** `LoopsService._agents`: instead of `dict[name → InvokeFn]`, it's `dict[name → Inbox]`. The agent's *invoke* function becomes a long-running coroutine draining its inbox.

**Touches:** new files. `service.py` grows an `inboxes` field alongside `_agents` — they coexist for one PR so the migration is safe.

### Step 3 — Loops becomes a producer (the abstraction-proving step)

**The single mutation.** [`runner.py:58`](../src/cuga/backend/loops/runner.py#L58):

```python
# before
answer = await invoke_fn(loop.prompt, loop.thread_id)

# after
ev = Event(
    id=str(uuid4()),
    kind="trigger",
    source=f"cron:loop:{loop_id}",
    target=Target(agent_name=loop.agent_name, thread_id=loop.thread_id),
    payload=Payload(text=loop.prompt, context={"loop_metadata": loop.metadata_json}),
    created_at=time.time(),
)
await dispatcher.dispatch(ev)
```

The agent loop (the long-running coroutine consuming `Inbox`) is what now produces the answer string. Run-recording moves from `fire_loop` to a post-turn hook in the agent loop — when the turn finishes, if `ev.source.startswith("cron:")`, write a `LoopRun` as today.

This is also when **`get_loops_service().register_agent(name, invoke_fn)`** becomes deprecated. Agents instead call `register_inbox(name)` to get their inbox and own their consumer loop. The old API can stay as a thin shim during the migration: it spawns an agent loop that calls the supplied `invoke_fn`.

**Risk retired:** the envelope is good enough to express a real, existing producer. If something feels awkward here, the envelope is wrong — fix it before adding any more producers.

**Touches:** [runner.py](../src/cuga/backend/loops/runner.py), [service.py](../src/cuga/backend/loops/service.py). Loops UI is unchanged. Loops models are unchanged.

### Step 4 — Generalize `Loop` into `Subscription`

Now we have three pieces:
1. The `Event` envelope (Step 1).
2. The bus (Step 2).
3. One producer — cron loops — writing to it (Step 3).

To add more producers cleanly, generalize the storage. The current [`Loop`](../src/cuga/backend/loops/models.py) model becomes the cron-specific subset of a broader `Subscription`:

```python
class Subscription(BaseModel):
    id: str
    app_name: str
    target_agent: str
    target_thread: Optional[str]      # nullable for "spawn new thread per fire"
    kind: SubKind                     # cron | webhook | poll | listener
    spec: dict                        # kind-specific config (cadence, URL pattern, filter)
    prompt_template: str              # how to phrase the Event payload
    metadata: Optional[dict]
    status: Status                    # active | paused | expired | cancelled | orphaned
    # ... created_at, fire_count, etc. — copied verbatim from Loop
```

Loops becomes `Subscription where kind=cron`. Tables grow from `cuga_loops_loops` to `cuga_subscriptions` (with a view that keeps the loops UI working unchanged).

**Touches:** [models.py](../src/cuga/backend/loops/models.py), [registry.py](../src/cuga/backend/loops/registry.py), new `events/subscriptions.py`. The `schedule_*` tools stay as a convenience layer over `create_subscription(kind=cron, ...)`.

### Step 5 — `publish()` tool + sinks (symmetric to `schedule_*`)

The same way [`tools.py`](../src/cuga/backend/loops/tools.py) gives the agent self-scheduling tools, we add **self-publishing tools**:

```python
@tool
async def publish(destination: str, payload: dict) -> str:
    """Emit an event to a registered destination (Slack channel, webhook, topic, email)."""

@tool
async def register_pub_sink(name: str, kind: str, config: dict) -> str:
    """Register a new pub destination. Stored in cuga_pub_sinks."""
```

Sinks are dumb: Slack channel, generic webhook, email, PagerDuty. ~50 LOC each. Audit trail uses the same `LoopRun` table extended with a `source` discriminator (or a sibling `event_runs` table).

**Touches:** new `events/sinks/{slack.py, webhook.py, email.py}`, extend [tools.py](../src/cuga/backend/loops/tools.py).

### Step 6 — First gateway: Slack inbound + outbound

The first non-cron producer. A FastAPI sub-router receives Slack events API callbacks, builds an Event with `source="gateway:slack"`, captures `reply_to`, and dispatches via the router.

When the consuming agent emits with a `reply_to`, the gateway renders it back to Slack thread.

**Touches:** new `events/gateways/slack.py`. Existing CUGA agents start receiving Slack-sourced events the same way they receive cron-sourced ones — no agent-side change.

This is the **risk-retiring step** for gateways. Reply paths and modality go from "described in the envelope" to "actually working." If the envelope leaks Slack quirks into the agent, fix that first before WhatsApp/Email/TG.

### Step 7 — Push subscriptions + state-diff

`POST /sub/<subscription_id>` for third-party webhooks. Subscriptions table now used for real — GitHub PR opens, Stripe webhooks, Calendly bookings.

State-diff store: a `cuga_event_state` table with `(subscription_id, key, value, updated_at)`. Push subscriptions with transition semantics ("MRR drops >5%", "CI was pending now green") read/write this before deciding to emit an Event.

**Touches:** new `events/sub/webhook.py`, new `events/state.py`.

### Step 8 — Pull subscriptions (poller reuses loops!)

**The payoff for the work done in Steps 1–4.**

A pull subscription is *just a cron loop* whose "agent" is the poller itself. When the cron fires:
1. Poller fetches the source (HTTP / RSS / API).
2. Checks state-diff store.
3. If changed → emits a real Event for the target agent.

No new scheduler. The same APScheduler + SQLite that drives loops drives pollers. Setup tools: `subscribe_poll(url, target_agent, check_cadence)`.

Always-on listeners (Slack RTM/socket-mode, IMAP idle, websockets) are separate — they're long-lived asyncio tasks supervised by the FastAPI lifespan.

**Touches:** new `events/sub/poll.py`, `events/sub/listener.py`. **Loops itself is unchanged** — the poller just creates loops with a special prompt that the poller-agent recognizes.

### Step 9 — Swarm + durability

**Swarm** falls out of the work already done. A new `send_to(agent, payload, thread_id?)` tool writes an `Event(kind="agent_msg", source="agent:A", target={B, ...})` into B's inbox. Fan-out is N writes; critic pairs are two agents writing to each other. **No new primitive.**

**Durability** is the inbox swap. `asyncio.Queue` → SQLite-backed queue (Phase 2 of cuga-runtime's plan). The `Inbox` interface stays identical; the agent loop never knows. At-least-once delivery + idempotency keys land here.

**Touches:** `events/inbox.py` (new impl), agent loop unchanged.

---

## Part 4 — What survives, what generalizes, what's deprecated

| Loops API | Fate |
|---|---|
| `schedule_recurring`, `schedule_wakeup`, `cancel_loop`, `list_my_loops` | **Survive unchanged** — they become convenience over `create_subscription(kind=cron, ...)` |
| `Loop` model | **Generalizes** to `Subscription`; old fields retained for cron subs |
| `LoopsService.register_agent(name, invoke_fn)` | **Deprecated** after Step 3 — shimmed for one release; new code uses `register_inbox(name)` and owns the consumer loop |
| `LoopsRegistry` | **Generalizes** to `SubscriptionsRegistry`; tables renamed via view for back-compat |
| `fire_loop(loop_id)` | **Survives** but its body shrinks — it now just builds an Event and dispatches |
| Loops UI | **Survives unchanged** as the Subscriptions UI's "cron" tab |
| `LoopRun` | **Generalizes** to `EventRun` with a `source` discriminator |
| `cron_parser.py` | **Survives unchanged** — useful for both `schedule_*` and `subscribe_poll` |

The migration path is: loops users keep working. New users discover gateways/sinks/subscriptions via the supervisor and the same natural-language UX.

---

## Part 5 — The first PR concretely

If we're committing to the evolution, here's the smallest PR that's real:

1. **Add** `src/cuga/backend/events/{envelope.py, inbox.py, dispatcher.py}` — ~150 LOC. Pure infra.
2. **Wire** an `Inbox` per registered agent in `LoopsService`. Old `_agents` dict stays for one PR.
3. **Mutate** [`runner.py:58`](../src/cuga/backend/loops/runner.py#L58): build Event, dispatch.
4. **Move** run-recording into a post-turn hook in the agent loop. Same DB writes, different call site.
5. **Smoke test:** create a `5s` interval loop via UI → it still fires → `LoopRun` still recorded → next_fire_at still updates.

That single PR is Step 1 + Step 2 + Step 3. After it merges, every subsequent step is purely additive — gateways, sinks, new subscription kinds — none of them require touching loops again.

---

## Part 6 — What changes for users (TL;DR)

| Today | After evolution |
|---|---|
| *"Every Monday, post HN digest"* → supervisor schedules a loop | Same. Now backed by `subscribe_cron` under the hood. |
| *"When Stripe MRR drops >5%, draft a churn analysis"* | Newly supported. routing agent calls `subscribe_webhook` + `register_pub_sink`. |
| *"@cuga in Slack: scout restaurants in Brooklyn"* | Newly supported once the Slack gateway is wired. |
| Loops UI shows scheduled loops | Subscriptions UI shows loops + webhook subs + pollers + always-on listeners, side by side. |
| Run history per loop | Same table, now also captures non-cron Event runs. |

---

## TL;DR for an engineer skimming this

- **Loops is already 70% of the event-driven runtime** — it has persistence, a scheduler, an agent registry, run history, a UI, and a setup-via-supervisor UX.
- **One mutation** (`runner.py:58`) turns it from a direct-invoke timed-trigger system into a producer on a generic event bus.
- **Every other trigger type is a sibling producer** writing into the same inbox the cron producer writes into.
- **The agent never learns about gateways, webhooks, or pollers** — it only sees Events.
- **No new framework, no rewrite** — additive across nine PRs, each shippable.
