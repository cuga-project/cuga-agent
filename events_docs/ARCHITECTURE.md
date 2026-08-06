# Architecture — the door, the blocks, and the four flows

> **The one rule.** Every message from every channel lands on CUGA's `POST /run`. CUGA — not the
> eventing service — decides whether this one is ordinary chat or an attempt to arm something.
> An explicit slash verb, or a thread with an arming dialogue already open, goes to the eventing
> service. **Everything else is ordinary chat and never touches it.**

This document is the architectural record. For a narrative version see [DECK.html](DECK.html); for
step-by-step instructions see [RUNBOOK_TRY_IT.md](RUNBOOK_TRY_IT.md); for what to *type* see
[AUTOMATION_COOKBOOK.md](AUTOMATION_COOKBOOK.md).

---

## 0. The whole system, in one diagram

Every channel, every trigger, both processes, the database. **If you print one picture, print this
one.** The numbered flows in §2–§5 are close-ups of the paths shown here.

```mermaid
flowchart TB
    subgraph IN["INBOUND — a human talks (4 channels)"]
        direction LR
        W["Web / Studio<br/><i>HTTP</i>"]
        S["Slack<br/><i>Events API — inbound webhook</i>"]
        D["Discord<br/><i>Gateway websocket — outbound</i>"]
        T["Telegram<br/><i>long-poll — outbound</i>"]
    end

    subgraph TRIG["TRIGGERS — nobody is in the room (42 registry rows + 2 timer modes)"]
        direction LR
        CR["cron<br/><i>native scheduler</i>"]
        PO["poll + delta<br/><i>always·threshold·identity·fuzzy</i>"]
        HK["webhook inbound<br/><i>POST /hook/&lt;name&gt;</i>"]
        DW["direct watchers — 15<br/><i>slack 8 · discord 3 · box 2 · telegram 1 · webhook 1</i>"]
        AP["Activepieces push — 27<br/><i>github 14 · gmail 4 · gcal 3 · pinterest 3<br/>box 1 · rss 1 · youtube 1</i>"]
    end

    subgraph EV["cuga-events-svc :8100 — owns sockets, tokens, time"]
        direction LR
        ADP["channel adapters<br/><i>hold the bot tokens</i>"]
        CON["concierge<br/><i>NL→Flow · HITL gate</i>"]
        SCH["native scheduler"]
        INV["POST /invoke<br/><i>fire seam + run log</i>"]
        DEL["delivery<br/><i>back to origin thread</i>"]
    end

    subgraph CORE["cuga-core :7860 — THE DOOR (vanilla CUGA)"]
        direction LR
        RUN["POST /run<br/><b>the one rule</b>"]
        STR["GET /stream<br/><i>same rule</i>"]
        SUP["supervisor 'cuga'<br/>+ 8 sub-agents"]
        RA["GET /run/agents<br/><i>the roster</i>"]
    end

    DB[("PostgreSQL<br/>armed flows · runs · identity · drafts")]
    MCP["MCP tool servers<br/>finance · web · geo · knowledge · code · text · local"]

    W --> RUN
    S --> ADP
    D --> ADP
    T --> ADP
    ADP -->|"cuga_door.ask()"| RUN
    RUN -->|"slash verb OR open dialogue ONLY"| CON
    CON --> DB
    CR --> SCH
    PO --> SCH
    SCH --> INV
    HK --> INV
    DW --> INV
    AP -.->|"OFF today"| INV
    INV -->|"every fire"| RUN
    RUN --> SUP
    SUP --> MCP
    INV --> DEL
    DEL --> S & D & T & W
    SCH --> DB
    INV --> DB
    RA -.->|"roster read by events"| ADP

    classDef off fill:#eee,stroke:#999,stroke-dasharray:4 3,color:#666
    class AP off
```

**Read it in one sentence:** humans enter on the left and always reach **`/run`**; time and external
systems enter on the right and always reach **`/invoke`**, which also calls `/run`. Everything that
outlives a request is in Postgres. The dashed box is Activepieces — present, switched off.

---

## 1. Two processes

Everything lives in exactly one of two processes. The split is not cosmetic — it is what lets CUGA
ship without any of this.

### `cuga-core` · :7860 — vanilla CUGA

Imports **nothing** from `cuga.backend.events` (a test fails if that regresses). Holds no bot token,
runs no scheduler.

| Block | Owns | Decides |
|---|---|---|
| **The door** — `POST /run` | The single entry point for every utterance from every channel. Non-streaming sibling of `/stream`. | **Chat or eventing** — one regex + one set lookup |
| `GET /stream` | SSE for the Studio UI. Applies the *same* rule. | Same one rule |
| Supervisor `cuga` | 8 sub-agents, the graph, model credentials, HITL, policies | Which sub-agent answers |
| `GET /run/agents` | The roster, machine-readable. **The roster belongs to whoever executes.** | — |
| MCP tool clients | finance · web · geo · knowledge · code · text · local | — |

### `cuga-events-svc` · :8100 — the eventing service

Its own process, its own deploy, its own lifecycle. Depends on CUGA (it cannot execute anything
itself). **CUGA does not depend on it.**

| Block | Owns | Decides |
|---|---|---|
| **Channel adapters** slack · discord · telegram | The sockets, and therefore **the bot tokens** | *nothing* — pure transport |
| `cuga_door.ask()` | Turns a channel message into `POST cuga-core/run`. Builds `thread_id = gw:<channel>:<native>#<locus>` — memory locus *and* delivery address in one string. | *nothing* |
| **Concierge** — `POST /api/concierge` | NL → Flow. The HITL state machine. Returns a `state` the door uses to keep the dialogue routed. | What gets armed — **only after a human confirms** |
| Subscription store | `events.db`, tenant-scoped. Armed flows + parked drafts (10-min TTL). | — |
| Native scheduler | cron + poll, in-process. Poll state answers *"did it actually change?"* | When to fire |
| `POST /invoke` · `POST /hook/<name>` | The fire seam and the inbound-webhook seam. Run logging, then delivery. | — |
| Activepieces engine | Present, **off**. One trigger backend for push on SaaS we don't own. | — |

**The shape of it:** the eventing service talks to CUGA **twice** — once *inbound* as a transport (a
human's message, via `/run`) and once *outbound* as an executor (a fired tick, via `/invoke` →
`/run`). CUGA talks to the eventing service exactly once, in one function, guarded by one
environment variable (`EVENTS_API_URL`).

### Triggers-only — a deliberate boundary

The events layer arms **triggers** and delivers the agent's answer. It does **not** run connector
*actions* (a Gmail reply, a GitHub comment). Anything the agent should *do*, it does through its own
tools — which keeps credentials and side-effects inside the agent rather than spread through the
event plumbing. The action half was built and then deleted (2026-07-30, ~2,640 LOC) rather than
gated, because a half-present feature is worse than an absent one.

**Legacy escape hatches**, both off by default: `EVENTS_SCHEDULER=ap` routes recurrence through an
Activepieces schedule instead of the native scheduler, and `EVENTS_<CHANNEL>_BACKEND=ap` routes a
channel through an AP webhook flow. Native and direct are the defaults everywhere.

---

## 1b. Every channel and every trigger, in one sequence

The companion to §0: the same coverage, but ordered in time. **This is the second diagram to print.**

```mermaid
sequenceDiagram
    autonumber
    actor H as Human
    participant CH as Channel<br/>(web · slack · discord · telegram)
    participant AD as events-svc<br/>adapters
    participant DOOR as cuga-core<br/>POST /run — THE DOOR
    participant CO as events-svc<br/>concierge
    participant DB as PostgreSQL
    participant SC as events-svc<br/>scheduler / watchers
    participant IN as events-svc<br/>POST /invoke
    participant AG as Supervisor + MCP

    rect rgb(240,248,246)
    Note over H,AG: A · INBOUND — identical for ALL FOUR channels
    H->>CH: a message
    alt web
        CH->>DOOR: POST /run (or /stream — same rule)
    else slack · discord · telegram
        CH->>AD: Events API / Gateway ws / long-poll
        AD->>AD: verify · strip @mention · dedup · ack <3s
        AD->>DOOR: cuga_door.ask() → POST /run
    end
    end

    rect rgb(245,245,250)
    Note over DOOR,DB: B · THE ONE RULE — the only decision in the system
    alt plain chat (no slash verb, no open dialogue)
        DOOR->>AG: run
        AG-->>DOOR: answer
        DOOR-->>CH: answer — eventing never touched
    else /automate /watch /schedule /cron /poll /push /cancel — or thread mid-dialogue
        DOOR->>CO: POST /api/concierge
        CO->>DB: park draft (10-min TTL)
        CO-->>DOOR: state=confirm + card
        DOOR-->>CH: card — NOTHING ARMED
        H->>CH: yes
        CH->>DOOR: (hops repeat) thread is OPEN → forward
        DOOR->>CO: POST /api/concierge
        CO->>DB: ARM subscription
        CO-->>CH: ARMED
    end
    end

    rect rgb(250,247,240)
    Note over SC,AG: C · FIRE — nobody is in the room. All trigger kinds converge on /invoke.
    alt cron — clock
        SC->>DB: due?
        SC->>IN: tick
    else poll — clock + delta gate
        SC->>IN: tick
        IN->>IN: poll_state.decide(threshold·identity·fuzzy)
        Note right of IN: unchanged → stay SILENT
    else webhook — external system
        Note over IN: POST /hook/:name?agent=... (key-checked)
    else direct watcher — 15 kinds, no AP
        Note over AD: slack reaction/message/… · discord · telegram · box
        AD->>IN: matched watcher
    else Activepieces push — 27 kinds
        Note over IN: github · gmail · calendar · pinterest · rss · youtube<br/>OFF today — arming returns CONNECT NEEDED
    end
    IN->>DOOR: POST /run — a FRESH thread
    DOOR->>AG: run (pinned sub-agent if named)
    AG-->>IN: answer
    IN->>DB: log the run
    IN->>CH: deliver into the ORIGIN thread
    CH-->>H: result
    end
```

Three things the diagram is meant to make obvious:

1. **Box A is the same for every channel.** The adapter differs; what reaches CUGA does not.
2. **Box B is the only decision.** One regex plus one set lookup, in one file, in one process.
3. **Box C converges.** Five different trigger kinds, one seam (`/invoke`), one execution path
   (`/run`) — so a new trigger type never touches the agent, and the agent never learns about time.

---

## 2. Flow A — plain chat (no slash verb)

The eventing service carries the message and carries the answer back. **The concierge, the scheduler
and the store are never touched.** Nothing is persisted.

```mermaid
sequenceDiagram
    autonumber
    actor H as Human
    participant SL as Slack
    participant RX as events-svc<br/>slack receiver
    participant DR as events-svc<br/>cuga_door.ask()
    participant RUN as cuga-core<br/>POST /run — THE DOOR
    participant SUP as Supervisor "cuga"
    participant MCP as MCP tools

    H->>SL: @cuga-app what is IBM trading at?
    SL->>RX: POST /api/events/slack/events (signed)
    RX->>RX: verify HMAC · strip <@U…> · dedup on ts
    RX-->>SL: 200 OK — ack in < 3 s
    Note over RX,DR: slow work continues in a background task
    RX->>DR: _slack_answer(text, channel, thread_ts)
    DR->>RUN: POST /run {query, thread_id: gw:slack:C123#1712…}
    Note over RUN: _forwards_to_events() → FALSE<br/>no slash verb · thread not open
    RUN->>SUP: run
    SUP->>MCP: get_stock_quote("IBM")
    MCP-->>SUP: 291.42
    SUP-->>RUN: answer
    RUN-->>DR: {"answer": "IBM is trading at …"}
    DR->>SL: chat.postMessage(thread_ts)
    SL-->>H: reply, in the same thread
```

---

## 3. Flow B — arming (`/automate`), with the human gate

Hops 1–3 are **byte-identical** to Flow A. The adapter never looks at the text. The fork is hop 4.

```mermaid
sequenceDiagram
    autonumber
    actor H as Human
    participant SL as Slack
    participant RX as events-svc<br/>slack receiver
    participant RUN as cuga-core<br/>POST /run — THE DOOR
    participant CO as events-svc<br/>POST /api/concierge
    participant ST as Subscription store

    H->>SL: @cuga-app /automate every morning at 9 tell me IBM's price
    SL->>RX: POST /api/events/slack/events (signed)
    RX->>RUN: POST /run  — identical call to Flow A
    Note over RUN: _SLASH_VERBS matches<br/>(a leading @mention is tolerated)<br/>→ FORWARD. The agent is never invoked.
    RUN->>CO: POST /api/concierge {text, thread_id, channel, identity headers}
    CO->>CO: DRAFT — parse cadence, strip it from the prompt<br/>"every morning at 9 tell me IBM's price" → "Tell me IBM's price."
    CO->>ST: park the draft (10-min TTL)
    CO-->>RUN: {state: "confirm", reply: "<card>"}
    Note over RUN: thread added to _events_open_threads<br/>(in-memory routing hint, not state)
    RUN-->>RX: {answer: "<card>", routed_to: "events"}
    RX->>SL: post the card in-thread
    SL-->>H: Ready to arm — check this first…
    Note over H,ST: NOTHING IS ARMED YET

    H->>SL: yes
    Note over SL,RUN: no re-mention, no slash verb
    SL->>RX: POST (message event)
    RX->>RUN: POST /run
    Note over RUN: no slash verb — but the thread is OPEN<br/>→ FORWARD
    RUN->>CO: POST /api/concierge {text: "yes", thread_id}
    CO->>ST: ARM the subscription (deterministic, from the approved text)
    CO-->>RUN: {state: "armed", reply: "ARMED cron …"}
    Note over RUN: thread dropped from _events_open_threads
    RUN-->>RX: answer
    RX->>SL: post confirmation in-thread
```

**Why the gate exists.** The risky part of "turn this sentence into a standing job" is not the
schedule — it is the **prompt the agent is handed on every fire**, forever, with nobody watching.
So a human reads it first. Arming is deterministic from the approved text; there is no second LLM
pass free to arm something you never read.

---

## 4. Flow C — the fire (nobody is in the room)

A tick is already-decided work aimed at a known agent, and it needs delivery + run-logging, so it
goes through `/invoke` rather than the door.

```mermaid
sequenceDiagram
    autonumber
    participant SC as events-svc<br/>native scheduler
    participant ST as Subscription store
    participant IN as events-svc<br/>POST /invoke
    participant PS as poll_state.decide()
    participant RUN as cuga-core<br/>POST /run
    participant SUP as Supervisor
    participant SL as Slack

    loop every 10 s
        SC->>ST: which subscriptions are due?
    end
    ST-->>SC: cuga-043b82 (cron, every 5 min)
    SC->>IN: POST /invoke {agent, prompt, deliver: true, thread}
    IN->>RUN: POST /run — a FRESH thread_id
    Note over RUN: execution thread ≠ delivery thread<br/>tick #288 does not drag 287 prior turns
    RUN->>SUP: run
    SUP-->>RUN: answer
    RUN-->>IN: {"answer": …}
    opt poll mode only
        IN->>PS: decide(threshold / identity / fuzzy)
        PS-->>IN: changed? — if not, stay silent
    end
    IN->>SL: deliver into the ORIGINAL thread (from thread_id)
    IN->>ST: log the run
```

The delivery address rides on `thread_id` (`gw:<channel>:<native>#<locus>`), so a flow armed from
Slack fires back into that Slack thread without anyone passing a "reply to" anywhere.

---

## 5. Flow D — inbound webhook (an external system starts it)

```mermaid
sequenceDiagram
    autonumber
    participant EX as External system
    participant HK as events-svc<br/>POST /hook/:name
    participant ST as Subscription store
    participant RUN as cuga-core<br/>POST /run
    participant SUP as Supervisor
    participant CH as Delivery channel

    EX->>HK: POST /hook/alerts?agent=incident_triage
    HK->>HK: verify EVENTS_WEBHOOK_KEY
    HK->>ST: match watchers for (webhook, inbound)
    HK->>RUN: POST /run {query: payload summary, agent: incident_triage}
    Note over RUN: pinned sub-agent is a DIRECTIVE in the prompt, not a bypass — policies, tools and HITL unchanged
    RUN->>SUP: run (routed to incident_triage)
    SUP-->>RUN: "P2 · auth service · check the token refresh job"
    RUN-->>HK: answer
    HK->>CH: deliver
```

---

## 6. What happens when the eventing service is absent

CUGA is a microservice that works on its own. This is the property the whole split exists to
protect.

```mermaid
flowchart TD
    Q["utterance arrives at POST /run"] --> A{"EVENTS_API_URL set?"}
    A -->|no| C["plain chat — a slash verb is just text.<br/>Identical to upstream CUGA."]
    A -->|yes| B{"slash verb, or thread mid-dialogue?"}
    B -->|no| C
    B -->|yes| D["POST events/api/concierge"]
    D --> E{"reachable?"}
    E -->|yes| F["arming dialogue"]
    E -->|no| G["honest one-line error naming the URL.<br/>Chat is unaffected — it never calls out."]
```

| Scenario | What happens |
|---|---|
| Eventing never deployed (`EVENTS_API_URL` unset) | Forward disabled. `/run` and `/stream` behave exactly as upstream CUGA. |
| Eventing deployed but **down** | Chat unaffected. Arming returns an honest sentence naming the unreachable URL. No stack trace, no hang. |
| Eventing turned off | Same as never deployed — unset one variable. |

---

## 7. Deployment topology

Two Code Engine apps, one image, `min-scale 1` (the scheduler and channel loops are process-wide
singletons).

```mermaid
flowchart LR
    subgraph CE["IBM Code Engine — 2 apps, 1 image"]
        CORE["<b>cuga-core</b> :7860<br/>vanilla CUGA · THE DOOR<br/>supervisor + 8 sub-agents<br/>EVENTS_API_URL → events-svc"]
        EV["<b>cuga-events-svc</b> :8100<br/>adapters · concierge<br/>scheduler · /invoke"]
    end
    DB[("<b>cuga-events-pg</b><br/>Databases for PostgreSQL<br/>armed flows · runs · identity<br/><i>sslmode=verify-full</i>")]
    SLACK["Slack"] -->|"Request URL (inbound)"| EV
    TG["Telegram"] -.->|"long-poll (outbound)"| EV
    DC["Discord"] -.->|"Gateway ws (outbound)"| EV
    WEB["Studio /studio"] --> CORE
    EV -->|"/run — chat + every fire"| CORE
    CORE -->|"/api/concierge — arming only"| EV
    CORE --> MCP["MCP tool servers<br/>finance · web · geo · knowledge<br/>code · text · local"]
    EV --> DB
```

The database is reached **only by the eventing service** — `cuga-core` neither knows nor needs it,
which is the same one-directional coupling as everything else here. Local dev runs the identical
engine (`make pg`), so the storage path is exercised before it ships.

**Slack's Request URL points at `cuga-events-svc`, never at `cuga-core`.** "CUGA is the door"
describes where the *decision* happens — one hop later, invisible to Slack. The receiver did not
move.

### The events database — one engine everywhere

**`EVENTS_DB` takes a PostgreSQL URL**, and that is what local dev *and* Code Engine run:

```
EVENTS_DB=postgresql://cuga:…@host:5432/cuga_events   # local dev AND deployed — same engine
EVENTS_DB=/abs/path/events.db                          # SQLite, quickstart only
EVENTS_DB=:memory:                                     # SQLite, tests
```

**Why this changed.** Local dev used to be a SQLite file that survives everything, while Code Engine
ran SQLite on an ephemeral disk. Two different durability stories — and the fragile one was the only
one nobody exercised while developing. No local test could have caught the 2026-08-05 data loss,
because locally the failure mode did not exist. Same engine everywhere means local testing means
something.

| | |
|---|---|
| Start it locally | `make pg` — Postgres 16 in a container, prints the DSN |
| Test the deployed SQL path | `make test-pg` — 20 store tests against real Postgres |
| Inspect | `make pg-psql` · reset with `make pg-reset` |

SQLite is retained deliberately for the **hermetic offline suite** (360 tests that must not need a
database server) and for a zero-infrastructure quickstart. It is not the deployed configuration.
One seam — [`db.py`](../src/cuga/backend/events/db.py) — hides the three differences that matter:
`?` vs `%s` placeholders, `PRAGMA table_info` vs `information_schema`, and rows that must support
both `r["col"]` and `r[0]`. The SQL itself was already portable: `ON CONFLICT … DO UPDATE SET …
excluded.col` is Postgres syntax that SQLite adopted, and the schema uses only `TEXT`/`REAL`/`INTEGER`.

With Postgres, **durability is a property of the database** — no snapshot loop, no restore step, no
mount to configure. A new pod simply connects and the flows are there.

---

### Legacy: SQLite snapshot/restore (superseded by Postgres)

**The failure this fixes.** The container filesystem is ephemeral, and it is worse than "lost on
redeploy": Code Engine can replace the instance at any time — new revision, node drain, reschedule
— and the new pod starts with an empty disk. **No restart is recorded**; `ibmcloud ce app get`
still reads `Restarts: 0`, because that counter tracks crash-restarts of the *current* pod, not pod
replacement. Observed 2026-08-05: a cron armed from Slack at 11:12 was gone when a new pod started
at 11:24, and nothing in the logs said so.

**Why not simply put `EVENTS_DB` on the mounted volume.** Code Engine's persistent data store is
backed by a **COS bucket** — object storage, mounted via s3fs. SQLite on object storage is a
corruption hazard: POSIX advisory locking is not honoured, and a page write becomes a whole-object
rewrite. So the live database stays on local disk where locking behaves, and we copy consistent
snapshots to the mount.

```mermaid
flowchart LR
    subgraph POD["cuga-events-svc (pod)"]
        DB["EVENTS_DB<br/>/app/.cuga/events.db<br/><i>local disk — correct locking</i>"]
        SNAP["db_persist<br/>snapshot loop<br/><i>on change, every 15s</i>"]
    end
    MNT["EVENTS_DB_BACKUP<br/>/mnt/state/events.db<br/><i>COS-backed data store</i>"]
    DB -- "sqlite backup API<br/>temp → atomic replace" --> SNAP
    SNAP --> MNT
    MNT -- "restore() at boot,<br/>only if local is empty" --> DB
```

Snapshots use SQLite's **online backup API**, not a file copy — consistent even mid-write — and are
written temp-then-`os.replace`, so an interrupted snapshot can never replace a good backup with a
truncated one. Restore refuses to overwrite a local DB that already has data.

| | |
|---|---|
| Turn it on | `./deploy/ce/3_state_store.sh` then `EVENTS_STATE_STORE=cuga-events-state ./2_deploy.sh` |
| Check it | `GET /api/events/status` → `.durability` — `{"durable": true, "subscriptions_in_snapshot": N}` |
| Worst-case loss | one snapshot interval (15 s) of changes |
| Correct for | **exactly one writer** — which is what we deploy (`min-scale 1 / max-scale 1`) |

> **Superseded.** This whole mechanism exists only for the SQLite configuration. Point `EVENTS_DB`
> at a Postgres URL and none of it runs — no `EVENTS_DB_BACKUP`, no mount, no snapshot loop. Keep
> it only if you are deliberately running SQLite somewhere durable-ish.

---

## Sources

`src/cuga/backend/server/main.py` (`_SLASH_VERBS`, `_forwards_to_events`, `_forward_slash_to_events`,
`run_sync`, `/run/agents`) · `src/cuga/backend/events/cuga_door.py` ·
`src/cuga/backend/events/app.py` (channel receivers, `/invoke`, `/hook`) ·
`src/cuga/backend/events/concierge.py` · `src/cuga/backend/events/native_scheduler.py` ·
`src/cuga/backend/events/poll_state.py` · `deploy/ce/2_deploy.sh`
