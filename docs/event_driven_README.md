# Event-Driven CUGA — Document Index

Start here. This package proposes evolving CUGA from request/response into a
fully event-driven agent runtime, grounded in the use cases listed in
[/Users/anu/events.md](file:///Users/anu/events.md).

## The one-paragraph summary

CUGA today answers when asked. Event-driven CUGA reacts to triggers — push (webhook / Slack / IMAP), pull (poller + state-diff), and timed (cron). One unifying primitive: every trigger produces an **Event** envelope, the **Dispatcher** mechanically dispatches it into the right agent's **Inbox**, the agent loop drains the inbox and does the work. The **CUGA routing agent** is the intelligent dispatcher *at setup time* — its existing `delegate_to_*` LLM picks which agent should own a given rule, then bakes that decision into a subscription row. Runtime dispatch is fast and LLM-free for routine rules. Loops becomes the canonical `[cron]` producer with one line-of-code change at [`runner.py:58`](../src/cuga/backend/loops/runner.py#L58).

## Reading order

Pick one path based on what you need.

### Path A — For a deck / overview (15 min)
1. **[event_driven_deck.md](event_driven_deck.md)** — 14 slides, ready to lift into a deck. Includes inline architecture + flow diagrams.
2. **[event_driven_full_architecture.png](event_driven_full_architecture.png)** — the canonical architecture diagram.

### Path B — For engineering planning (30 min)
1. **[event_driven_reference.md](event_driven_reference.md)** — the first-class citizens (Event, Trigger, Dispatcher, Inbox, Agent Loop, Subscription, Pub Sink, State-diff Store, **CUGA routing agent**) plus the push/pull/timed taxonomy.
2. **[event_driven_roadmap.md](event_driven_roadmap.md)** — milestone plan with risks each step retires.
3. **[event_driven_roadmap.png](event_driven_roadmap.png)** — capability Gantt by milestone.

### Path C — For implementing the first PR (45 min)
1. **[event_driven_from_loops.md](event_driven_from_loops.md)** — explicit code-level evolution: what survives, what generalizes, what's deprecated. Identifies the **single line at [runner.py:58](../src/cuga/backend/loops/runner.py#L58)** as the seam.
2. **[event_driven_agent_proposal.md](event_driven_agent_proposal.md)** — original design rationale (kept for reference).

### Path D — For Phase 5 (Kafka / production scale-out)
1. **[event_driven_kafka_migration.md](event_driven_kafka_migration.md)** — how to swap the in-memory bus for Kafka, the `KafkaInbox` adapter, schema discipline, multi-tenant ACLs, step-by-step cutover.
2. **[event_driven_kafka_architecture.png](event_driven_kafka_architecture.png)** — the multi-process M9 deployment topology.

## The visual assets

| File | Purpose | When to use |
|---|---|---|
| [event_driven_full_architecture.png](event_driven_full_architecture.png) | Canonical architecture diagram. Producers / Bus / Consumers, plus the explicit routing agent-as-setup-dispatcher box | Slide 3 of the deck |
| [event_driven_setup_flow.png](event_driven_setup_flow.png) | Two-phase: setup turn (utterance → registry rows) + runtime activation (event arrives → agent wakes) | Slide 5 |
| [event_driven_roadmap.png](event_driven_roadmap.png) | Capability evolution per milestone | Slide 12 |
| [event_flow/flow_push_support_email.gif](event_flow/flow_push_support_email.gif) | Push trigger walkthrough (support email triage) — 9 frames, CUGA stages visible | Slide 7 |
| [event_flow/flow_timed_hn_monday_digest.gif](event_flow/flow_timed_hn_monday_digest.gif) | Timed trigger walkthrough (Monday HN digest) — 9 frames | Slide 8 |
| [event_flow/flow_pull_changelog_watch.gif](event_flow/flow_pull_changelog_watch.gif) | Pull trigger walkthrough (OpenAI changelog watch) — 9 frames | Bonus |
| [event_flow/flow_swarm_scout_critic.gif](event_flow/flow_swarm_scout_critic.gif) | Multi-agent collaboration (scout sends to critic, critic replies, scout posts) — 10 frames | Multi-agent slide |

## The five claims everything else supports

1. **One primitive — `Event → Inbox → Agent Loop → Emit`** covers everything in events.md (push, pull, timed, swarm, all combinations).
2. **CUGA is the smart part; the bus is plumbing.** Smart routing (intent → target agent) happens once, at setup time, in the routing agent. The runtime bus is intentionally dumb.
3. **Loops is the seed**, not a side feature. A single line change at [runner.py:58](../src/cuga/backend/loops/runner.py#L58) turns it into the canonical timed-trigger producer.
4. **Phase 1 is one host, one process.** Same FastAPI app + APScheduler + in-memory inboxes + sibling listener processes. No new infra to start.
5. **~3 months of focused work** to demo every events.md utterance end-to-end on a single tenant. Productization (multi-tenant SaaS, OAuth, security) is another year and mostly not eventing work.

## What's *not* in scope here

- Productization (multi-tenant SaaS, billing, security review)
- Cowork-like polish (Slack app review, interactive approval UX)
- Model selection / Claude-specific features
- New scheduler library — we reuse APScheduler via loops

See [event_driven_deck.md](event_driven_deck.md) Slide 11 (deployment) and the *"how far off from Cowork"* discussion for context.

## Glossary (quick reference)

| Term | Means |
|---|---|
| **Event** | The envelope: `{id, kind, source, target, modality, payload, reply_to, credentials}` |
| **Trigger** | What decides "an Event should exist now" — push, pull, timed, or another agent |
| **Push trigger** | External system actively delivers (webhook, Slack events, IMAP idle) |
| **Pull trigger** | CUGA-driven poller + state-diff (reuses the loops scheduler) |
| **Timed trigger** | Pure clock (cron / interval / one-shot delay) — what loops gives today |
| **Subscription** | A registry row: "*when trigger X fires, target agent Y with payload template Z*" |
| **CUGA routing agent** | Existing `CugaSupervisor` — at setup time, its LLM picks `target_agent` via `delegate_to_*` |
| **Dispatcher** | New, mechanical. `match ev.target.kind → inbox.put / sink.deliver` |
| **Inbox** | One asyncio.Queue per agent. Phase 1 in-memory; Phase 2 durable |
| **Agent loop** | Long-running async coroutine that drains *one* inbox |
| **Pub sink** | Named outbound destination (Slack channel, webhook, Linear, email, etc.) |
| **State-diff store** | Per-subscription last-seen value, lets pull/push fire only on real transitions |
| **`send_to(agent)`** | Agent-to-agent message. Just another Event with `target.kind=agent`, re-enters the bus |
| **`publish(sink)`** | Emit to a pub sink. Just another Event with `target.kind=sink` |

---

**Questions / where to look:**

- *"How does routing work?"* → [reference §4 + §9](event_driven_reference.md), [deck Slide 4b](event_driven_deck.md)
- *"How do agents collaborate?"* → [swarm flow GIF](event_flow/flow_swarm_scout_critic.gif), [reference §9 fallback section](event_driven_reference.md)
- *"What's the first PR?"* → [from-loops Part 5](event_driven_from_loops.md)
- *"What changes per milestone?"* → [roadmap milestones](event_driven_roadmap.md), [roadmap PNG](event_driven_roadmap.png)
- *"What does CUGA do at runtime?"* → any flow GIF, plus [deck Slide 5](event_driven_deck.md)
