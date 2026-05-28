# Event-Driven CUGA — Kafka Migration Guide

How to swap the in-memory event bus for Kafka, what's actually hard about it,
and a concrete step-by-step plan.

**TL;DR:** the *code change* is small — one `Inbox` implementation plus a
schema version on the `Event`. The *operational* work is meaningfully bigger:
cluster ops, topic naming, schema evolution discipline, offset semantics,
and DLQ. Don't add Kafka until **Phase 5 (M9)** unless you already have a
real multi-process / multi-tenant story.

Companion: [event_driven_kafka_architecture.png](event_driven_kafka_architecture.png)

---

## When to migrate (and when NOT to)

| Adopt Kafka when... | Stay in-memory when... |
|---|---|
| Multiple tenants share the cluster | Single tenant, single host |
| Producer services live in separate processes | Everything in one FastAPI app |
| You need at-least-once delivery + replay | Restart-loss is acceptable |
| You need horizontal agent worker scaling | One process handles your load |
| You want cross-team event consumers (audit, analytics) | Only the agent loop reads events |
| You already operate Kafka for other things | Adding Kafka is your first message-broker |

If you can't tick at least two of the left column, **don't migrate yet.**
Redis Streams or Postgres LISTEN/NOTIFY are way cheaper alternatives at the
"durable but not enormous" scale.

---

## The mental model

```
BEFORE (in-process, Phase 1–4)
─────────────────────────────────────────
producer → Dispatcher.dispatch(ev) → inbox[agent_name].put(ev)
                                        ↓
                                     agent loop (1 coroutine per agent)


AFTER (Kafka, Phase 5)
─────────────────────────────────────────
producer → KafkaProducer.send(topic=ev.target.agent_name, value=ev)
                ↓
           Kafka cluster (topics partitioned by agent_name + thread_id)
                ↓
           Consumer group per agent (one or more worker processes)
                ↓
           agent loop in each worker
```

**What changes:**
- `Inbox` becomes a Kafka consumer wrapper.
- `Dispatcher` becomes (mostly) a Kafka producer wrapper — `match target.kind` still picks the topic, but delivery is async over the wire.
- Many agent loops can now run across many processes/hosts, joined by consumer group.

**What doesn't change:**
- The `Event` envelope.
- The agent loop's 6-stage turn shape.
- The routing agent's setup-time intelligence.
- MCP connectors and their per-thread credential binding.

---

## The actual code change

### 1. New file: `events/inbox_kafka.py`

```python
import json
from aiokafka import AIOKafkaConsumer
from cuga.backend.events.envelope import Event

class KafkaInbox:
    """Drop-in for asyncio.Queue. One topic per agent.

    Partition key = thread_id so per-thread events serialize on one
    partition (= one consumer at a time).
    """
    def __init__(self, agent_name: str, bootstrap_servers: str, group_id: str):
        self.topic = f"cuga.events.{agent_name}"
        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,                   # consumer group = horizontal scale
            enable_auto_commit=False,            # we commit AFTER the turn completes
            auto_offset_reset="latest",
        )
        self._current = None

    async def start(self):
        await self._consumer.start()

    async def stop(self):
        await self._consumer.stop()

    async def get(self) -> Event:
        msg = await self._consumer.__anext__()   # blocks until next message
        self._current = msg
        return Event.model_validate_json(msg.value)

    async def task_done(self):
        """Commit offset after agent turn completes successfully."""
        if self._current:
            await self._consumer.commit()
            self._current = None
```

### 2. New file: `events/dispatcher_kafka.py`

```python
from aiokafka import AIOKafkaProducer
from cuga.backend.events.envelope import Event

class KafkaDispatcher:
    """Replaces the in-process Dispatcher."""
    def __init__(self, bootstrap_servers: str):
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: v.encode("utf-8"),
            acks="all",                          # all in-sync replicas must ack
            enable_idempotence=True,             # producer-side dedup
        )

    async def start(self):
        await self._producer.start()

    async def dispatch(self, ev: Event):
        topic = self._topic_for(ev)
        key   = (ev.target.thread_id or ev.target.name).encode()  # partition key
        await self._producer.send(
            topic=topic,
            key=key,
            value=ev.model_dump_json(),
            headers=[
                ("event-id",      ev.id.encode()),
                ("event-version", b"1"),
                ("source",        ev.source.encode()),
            ],
        )

    def _topic_for(self, ev: Event) -> str:
        match ev.target.kind:
            case "agent": return f"cuga.events.{ev.target.name}"
            case "sink":  return f"cuga.sinks.{ev.target.name}"
            case "topic": return f"cuga.topics.{ev.target.name}"
            case "reply": return f"cuga.gateways.{ev.target.name}"
```

### 3. Config switch

```python
# settings.events.inbox_backend = "memory" | "kafka"
# settings.events.kafka.bootstrap_servers = "broker1:9092,broker2:9092"
# settings.events.kafka.consumer_group    = "cuga-workers"
```

That's the whole code change. **Single config flag flips the system.**

---

## What's actually hard (the honest list)

### Topic naming + partition strategy

- **One topic per agent**: easiest to reason about, easiest ACLs, easy to delete one agent's topic. Recommended.
- **Partition key = `thread_id`**: preserves per-thread ordering on one partition (one consumer). If you skip this, two messages on the same thread can be processed out of order by two workers — silent corruption.
- **Partition count**: start with 3–6 per topic; you can't decrease later. Avoid 1 (no parallelism) and avoid 100 (rebalance pain).
- **Multi-tenant**: namespace as `cuga.events.<tenant>.<agent_name>`. Don't share topics across tenants.

### Schema evolution of the `Event` envelope

The moment events live in Kafka, you can't freely change the shape.

- **Pin a version field** on `Event` from day one. The Kafka header `event-version: 1` is your second line of defense.
- **Schema registry** (Confluent SR, Apicurio, AWS Glue) is overkill for one team but pays for itself when consumers proliferate.
- **Backward-compatible changes only**: add optional fields; never remove; never change types in place.
- **Breaking change?** new topic (`cuga.events.v2.<agent_name>`), dual-publish for a window, migrate consumers, retire v1.

### Offset commit semantics

- **Commit AFTER the agent turn completes**, not on receive. Otherwise a crashed worker loses the event.
- This is at-least-once: the same event can be delivered twice if the worker crashes mid-turn. **Make turns idempotent** — use the event id as a dedup key in DBs/Linear/Slack (Linear has `idempotency_key`; Slack supports `client_msg_id`; pub sink writes should check `seen_event_ids` table).

### Consumer rebalances during deploys

- Rolling deploys cause consumer group rebalances. Mid-turn events may be reassigned to a different worker.
- Mitigation: **graceful shutdown** — finish current turn, commit, then drop out of the group.
- Use `static_group_membership` (Kafka 2.3+) to avoid rebalances for transient restarts.

### Dead-letter queue (DLQ)

- An event that crashes the agent loop on retry will block the partition forever.
- After N retries (configurable, e.g. 3), publish to `cuga.dlq.<agent_name>` and commit the original. Build a tiny UI or alert on DLQ depth.

### Multi-tenant ACLs

- One Kafka principal per tenant. ACLs restrict each principal to its own topic prefix.
- If you skip this, a misconfigured consumer can read another tenant's events. Real risk.

### Monitoring

- **Consumer lag per agent** — the canonical "are we falling behind?" metric.
- **DLQ depth** — non-zero means something needs investigation.
- **Producer error rate** — usually means broker / network issue.
- Burrow, Kafka Lag Exporter, or Confluent Control Center for the dashboard.

---

## Migration plan (step-by-step)

Assumes you're at Phase 4 — in-memory bus working for a single host.

**Step 1 — Add the version field, don't deploy Kafka yet.**
Add `version: int = 1` to `Event`. Audit table starts capturing it. Internal-only change, zero risk.

**Step 2 — Stand up a Kafka cluster.**
Managed service preferred (Confluent Cloud / MSK / Aiven). Three brokers, replication factor 3, min in-sync replicas 2. Create one test topic.

**Step 3 — Implement `KafkaInbox` + `KafkaDispatcher`.**
Per the code in the previous section. Behind a feature flag (`CUGA_EVENTS_BACKEND=kafka`).

**Step 4 — Run in-memory and Kafka side-by-side in staging.**
Producers dual-publish: every `dispatch(ev)` goes both to the in-memory `Inbox` AND to Kafka. Consumers still read from in-memory. This validates throughput and serialization without changing behavior.

**Step 5 — Cut consumers over, one agent at a time.**
Switch the `scout_agent`'s consumer from in-memory to Kafka. Compare outcomes. Keep the others on in-memory. Roll forward agent-by-agent.

**Step 6 — Remove the in-memory writes.**
Once all consumers are on Kafka, drop the dual-publish. Producers only write to Kafka.

**Step 7 — Multi-process.**
Now you can run multiple worker processes per agent in a consumer group. Scale horizontally by spinning up more workers, not by restarting the FastAPI.

**Step 8 — Multi-tenant.**
Namespace topics, add ACLs, deploy per-tenant consumer groups.

---

## How easy is this, honestly?

| Aspect | Difficulty | Notes |
|---|---|---|
| **Code change** | ⭐ Easy | Two files, ~300 lines. The `Inbox` interface contract makes it surgical. |
| **Schema discipline** | ⭐⭐ Medium | Version field + back-compat-only changes. Easy if you commit to it. |
| **Operational** | ⭐⭐⭐ Real work | Cluster ops, monitoring, ACLs, DLQ. Use managed Kafka unless you already operate it. |
| **Multi-tenant** | ⭐⭐⭐ Real work | ACLs + namespacing must be airtight. A leak is bad. |
| **Cutover** | ⭐⭐ Medium | Dual-publish staging step is the safety net. |

**Net call:** ~2 weeks of focused engineering for a small team to do the
migration well. Most of the time is on ops/monitoring/cutover, not on code.

---

## Alternative backends — when each fits

| Backend | When it's the right call |
|---|---|
| **In-memory** (Phase 1–4) | Single host, single tenant, restart-loss acceptable |
| **SQLite-backed inbox** | Single host, durability needed, no multi-process |
| **Postgres LISTEN/NOTIFY** | Single Postgres you already operate, light scale |
| **Redis Streams** | Multi-process on a few hosts, no replay needed, easy ops |
| **Kafka** | Multi-tenant, multi-team, replay needed, you can operate it |

If you're a single team running CUGA for one company, **Redis Streams** is
probably the better Phase 5 backend. Kafka becomes correct when you have
multiple tenants OR multiple producer/consumer teams OR a need to replay
weeks of events.

---

## One more honest caveat

If the goal is "make CUGA event-driven", **Kafka is not on the critical path.** Everything in Phases 1–4 ships without it. The whole event-driven story works on an asyncio.Queue. Kafka is a *production-scale-out* concern that you'll know you need when:

- You're restarting the process and dropping in-flight events
- You're hitting CPU/memory limits on the single agent worker
- You're being asked to support a second tenant
- A second team wants to subscribe to "every email triaged"

Until then, don't pay the operational tax.
