# Three email use cases — step by step

---

# Part A — Actors and roles

## External actors (outside CUGA)

| Actor                  | Role                                                                              |
| ---------------------- | --------------------------------------------------------------------------------- |
| **User**               | Types utterances ("watch my inbox…") or one-shot requests. Recipient of outcomes. |
| **Admin**              | Does one-time external wiring (e.g., configures Gmail Pub/Sub to POST to CUGA).   |
| **External system**    | Gmail, GitHub, Linear, Slack — sources of events and targets of outcomes.         |

## CUGA-internal components

### Already exist in today's CUGA (request/response)

| Component             | Role                                                                            |
| --------------------- | ------------------------------------------------------------------------------- |
| **Chat UI / API**     | Surface where user submits an utterance                                         |
| **Routing agent**     | Parses utterances, classifies intent, delegates to specialist agents            |
| **Specialist agents** | Stateless async functions: prompt + tools → reasoning → tool calls → return     |
| **MCP tool clients**  | Outbound API clients for external systems (Gmail, Linear, …)                    |
| **Conversation state**| Thread-scoped working memory (per-`thread_id`)                                  |

### New for event-driven (added in this design)

| Component                | Role                                                                                                 | Example — how it comes into play                                                                                                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Data source**          | An underlying external system (Gmail, Slack, Box, Outlook, Postgres, local FS).                      | *"Watch my Outlook for invoices."* → data source = Outlook.                                                                                                                       |
| **Channel**              | A named, scoped, credentialed connection to a data source (e.g. `slack://acme-ws/#help`).            | *"Watch the `#help` Slack channel."* → channel = `slack://acme-ws/#help`. Stored once with credentials; referenced by name in subscriptions.                                      |
| **Adapter**              | Per-data-source code with two faces: **inbound** (emits Events) and **outbound** (exposed as MCP tools). | *"When a message arrives in `#help`, reply with status."* → Slack adapter inbound emits the Event, then the same Slack adapter outbound serves `slack.post` when the agent replies. |
| **Registry (SQLite)**    | Persistent tables: `subscriptions`, `agents`, `channels`, `poller_state`. Configuration only.        | *"Email me a daily HN digest."* → routing agent INSERTs one row into `subscriptions` with `trigger.kind=cron`. That row persists across restarts.                                 |
| **Subscription manager** | At startup & on registry change: arms APScheduler jobs, spawns poller tasks, registers agent inboxes.| The moment the daily-HN row is INSERTed, the subscription manager calls `scheduler.add_job(...)` so the 9am tomorrow fire is scheduled — without a daemon restart.                |
| **APScheduler**          | In-process timer. Fires cron expressions from `subscriptions` rows.                                  | *"Every Monday 9am, send me a status."* → APScheduler holds the `"0 9 * * 1"` cron; on Monday morning it builds the Event.                                                        |
| **`POST /events`**       | FastAPI endpoint. Single ingress for all push triggers. Builds an Event from HTTP body.              | *"When a PR opens in acme/api, triage it."* → GitHub POSTs the PR payload to `/events`; the endpoint builds the Event and hands it to the dispatcher.                             |
| **Push adapters**        | Sidecars that absorb non-HTTP push protocols (IMAP IDLE, Slack Socket Mode) and POST `/events`.      | *"Whenever an email arrives at `support@`, summarize it."* → IMAP-IDLE adapter holds the open socket; when Gmail pushes a new-message notification down it, the adapter POSTs `/events`. |
| **Pull poller task**     | One asyncio task per pull subscription. Runs `fetch → state-diff → emit` on its interval.            | *"Every 5 min, check for new starred emails and add them to Linear."* → poller hits Gmail, diffs against `seen_ids`, emits Events only for new ones.                              |
| **Event**                | Normalized envelope: `{ target: {kind, name, thread_id}, payload, source_subscription_id }`.         | Cron fire, GitHub webhook, and Gmail poll all converge into the *same* Event shape — downstream code doesn't know which producer made it.                                         |
| **Dispatcher**           | ~50 LOC router. Reads `ev.target.agent_name`, puts the Event in that agent's inbox.                  | *"Triage PRs"* event has `target_agent: triage_agent` → dispatcher drops it into `inboxes["triage_agent"]`. No business logic.                                                    |
| **Agent inbox**          | `asyncio.Queue`, one per registered agent. Buffer between producers and the Invoker.                 | If 20 PRs open in a burst, all 20 Events queue up in `inboxes["triage_agent"]`; the Invoker drains them one at a time. Backpressure visible per agent.                            |
| **Invoker loop**         | The daemon's `while True`: dequeues, locks `thread_id`, invokes the agent function.                  | When the triage Event is at the head of the inbox, the Invoker acquires `lock[thread_id="pr-acme-api-42"]` and calls `triage_agent(event)`. If a second event for the same PR arrives mid-run, it waits on the lock. |

---

# Part B — How the current system works (today)

CUGA today is **request/response only**. A user types something; an agent runs; a reply comes back; the conversation ends (or continues turn-by-turn). Nothing runs in the background. There are no triggers, no inboxes, no schedulers.

### Components in play today

```
User → Chat UI → Routing agent → Specialist agent → MCP tools → reply → User
                       │                  │
                       └────── conversation thread state ──────┘
```

### One-line flow

> User types → routing agent classifies + delegates → specialist agent runs (calls tools as needed) → returns text → shown to user.

### What's missing for event-driven work

| Capability needed                          | Exists today?  |
| ------------------------------------------ | -------------- |
| Persist "do this on a schedule"            | ❌              |
| Listen for external pushes (webhooks)      | ❌              |
| Poll a system and react only to changes    | ❌              |
| Queue work per agent                       | ❌              |
| Run a background daemon at all             | ❌ (request-bound) |

### What the new design adds on top

The existing pieces (routing agent, specialist agents, MCP tools, conversation state) **stay unchanged**. The event-driven additions sit **above** them:

```
                                                       ┌─ inbox: digest_agent ──┐
[CRON]  APScheduler ───────────► build Event ──────►   │  ev, ev, …             │ ──► Invoker ──► agent fn
                                                       └────────────────────────┘            │
[PUSH]  POST /events ──────────► build Event ──►  Dispatcher  ──►  per-agent inboxes ─►  ...  │
                                                                                              │
[PULL]  poller (fetch+diff) ───► build Event ──────►   ┌─ inbox: triage_agent ──┐             ▼
                                                       │  ev                    │       MCP tools
                                                       └────────────────────────┘       (Gmail, Linear, …)
```

The agent function itself is the same shape it always was — a stateless async fn that runs a prompt and calls tools. What changes is **who calls it**: now the Invoker calls it on behalf of a trigger, not the chat UI on behalf of a user turn.

---

# Part C — Channels, data sources, and adapters

So far the examples were Gmail-only. In reality CUGA has to read from and write to many places: Slack, Box, Outlook, a local directory, Postgres, S3. These all fit a single small abstraction.

## The abstraction in one picture

```
         ┌────────────────────────────────────────────────────────────┐
         │  User-facing concept:  CHANNEL                             │
         │  (named, scoped, credentialed reference)                   │
         │                                                            │
         │   slack://acme-ws/#help                                    │
         │   outlook://support@acme.com                               │
         │   box://acme/uploads                                       │
         │   file:///Users/anu/inbox                                  │
         │   postgres://prod/db/tickets                               │
         └────────────────────────────────────────────────────────────┘
                                  │
                  ┌───────────────┴───────────────┐
                  ▼                               ▼
        ┌──────────────────┐            ┌──────────────────┐
        │  Inbound face    │            │  Outbound face   │
        │  (of the adapter)│            │  (of the adapter)│
        │                  │            │                  │
        │  detects change  │            │  exposed as MCP  │
        │  emits Event     │            │  tool the agent  │
        │                  │            │  calls           │
        └──────────────────┘            └──────────────────┘
                  │                               ▲
                  ▼                               │
        ┌────────────────────────────────────────────────────┐
        │  ADAPTER  (per data source: Slack, Box, Gmail, …)  │
        │  Speaks the external protocol. Manages credentials.│
        └────────────────────────────────────────────────────┘
                  │                               │
                  ▼                               ▼
        ┌────────────────────────────────────────────────────┐
        │  DATA SOURCE  (the external system itself)          │
        └────────────────────────────────────────────────────┘
```

**The contract is small:**

- A **channel** is a string URI plus stored credentials. Users name them in utterances. Subscriptions store them in `trigger.channel` and `outcomes[*].channel`.
- An **adapter** registers two things with CUGA: zero or more inbound watchers (push/pull behavior per channel) and zero or more outbound MCP tools.
- The dispatcher, inboxes, Invoker, and agent functions never see "Slack" or "Box" — only Events with `channel:` strings in their payload and tool names like `slack.post`.

## Three layers

### Layer 1 — Data source

The underlying system. Box, Slack, Outlook, the local filesystem, Postgres, S3. CUGA doesn't talk to these directly — adapters do.

### Layer 2 — Channel

A **configured connection** to a data source, with a scope and credentials. The thing a user actually names in an utterance.

| User says…                              | Channel                                              | Data source       |
| --------------------------------------- | ---------------------------------------------------- | ----------------- |
| "my `support@` mailbox"                 | `outlook://support@acme.com`                         | Outlook           |
| "the `#help` Slack channel"             | `slack://acme-ws/#help`                              | Slack             |
| "the `/uploads` Box folder"             | `box://acme/uploads`                                 | Box               |
| "the `~/inbox` directory"               | `file:///Users/anu/inbox`                            | local FS          |
| "the `tickets` table"                   | `postgres://prod/db/tickets`                         | Postgres          |

Each channel is registered once with credentials and a kind. Users reference them by name; adapters know how to talk to them.

### Layer 3 — Adapter

Code that knows the protocol of one data source. An adapter has two faces — most adapters implement both, some implement only one.

| Face        | Used as              | Called by              | Purpose                                                        |
| ----------- | -------------------- | ---------------------- | -------------------------------------------------------------- |
| **Inbound** | trigger / producer   | subscription manager   | Detects changes in the data source and emits Events            |
| **Outbound**| MCP tool             | agent function         | Performs writes/queries the agent invokes during reasoning     |

A single Slack adapter handles **both** "react to a new message in `#help`" (inbound) and "post a reply to `#help`" (outbound) — same credentials, same connection pool, two entry points.

## How channels appear in a subscription

Channels turn what used to be tangled config into one clear named reference:

```yaml
id:           help-channel-triage
trigger:      { kind: push, channel: slack://acme-ws/#help, event: message }
target_agent: triage_agent
tools:        [slack.post]            # tool will resolve channel from subscription context
outcomes:     [
  { action: slack.post, channel: slack://acme-ws/#triage-log },
  { action: outlook.send, channel: outlook://anu@acme.com, subject: "Triage summary" }
]
```

Three different channels, two data sources, one row. Setup didn't change — utterance still becomes a `subscriptions` row; what's new is that `channel:` is a first-class field.

## Inbound: how each adapter type plugs into CUGA

The three trigger sub-shapes from before map to three adapter behaviors. The choice is **per-channel**, made by the adapter author, transparent to the user:

| Adapter behavior         | Used when…                                  | Channel examples                              |
| ------------------------ | ------------------------------------------- | --------------------------------------------- |
| **Native webhook**       | data source can POST to `/events` directly  | GitHub, Stripe, Calendly, Linear              |
| **Bridged webhook**      | data source pushes via held socket; adapter converts to POST `/events` | Slack Socket Mode, IMAP IDLE, MQTT |
| **Filesystem watcher**   | data source is local files; adapter watches via inotify/FSEvents → POST `/events` | local directories, mounted drives |
| **Poll + state-diff**    | data source has no push API                 | Box (some events), legacy IMAP, Sheets, RSS   |

From the architecture's view: all of these end up dropping an Event into a per-agent inbox. The adapter absorbs the protocol mess.

## Outbound: how each adapter exposes itself as a tool

Every adapter that supports writes registers MCP tools named after the data source:

| Adapter   | Outbound tools (examples)                              |
| --------- | ------------------------------------------------------ |
| Gmail     | `gmail.send`, `gmail.label`, `gmail.archive`           |
| Outlook   | `outlook.send`, `outlook.move`, `outlook.flag`         |
| Slack     | `slack.post`, `slack.react`, `slack.upload`            |
| Box       | `box.upload`, `box.move`, `box.share`                  |
| Files     | `file.write`, `file.move`, `file.delete`               |
| Linear    | `linear.create_issue`, `linear.comment`                |

The agent's `tools:` list in the subscription references these by name. The adapter resolves which channel to use based on either the tool arguments or the subscription's `outcomes` config.

## Putting it together — flow for a multi-channel subscription

> *"When a customer DM lands in `#help` Slack, look up their order in Postgres, post a triage note back to `#triage-log`, and email me a daily roll-up."*

This actually needs **two subscriptions** (push + cron). The interesting one is the push:

```yaml
id:           help-dm-triage
trigger:      { kind: push, channel: slack://acme-ws/#help, event: message }
target_agent: triage_agent
tools:        [postgres.query, slack.post]
outcomes:     [{ action: slack.post, channel: slack://acme-ws/#triage-log }]
```

**Flow at runtime:**

1. Customer DMs into `#help` (Slack).
2. **Slack Socket Mode adapter** (bridged-webhook behavior) holds the WebSocket to Slack. Receives the message frame.
3. Adapter translates → `POST /events` (internal) with `{channel: slack://acme-ws/#help, message}`.
4. `/events` endpoint → builds Event → Dispatcher → `triage_agent` inbox.
5. Invoker → invokes `triage_agent(event)`.
6. Agent calls `postgres.query("SELECT * FROM orders WHERE customer_email=…")` via Postgres adapter (outbound).
7. Agent calls `slack.post(channel=slack://acme-ws/#triage-log, text=…)` via Slack adapter (outbound, **same adapter as step 2**, opposite face).
8. Returns.

One adapter (Slack) appearing on both ends. Two data sources touched (Slack, Postgres). The agent doesn't know about Socket Mode or connection pools — it just knows the tool names.

## What scales this cleanly

Adding **Box** support means writing one Box adapter with both faces (`box.watch_folder` inbound + `box.upload` outbound). After that:

- Users can say "watch the `/uploads` Box folder" and CUGA registers a Box channel.
- Subscriptions can target `box://...` channels for triggers.
- Agents can call `box.upload` as a tool.

No changes to the dispatcher, inboxes, Invoker, or any agent. The trigger and tool layers are the only places where new integrations land. **That's the abstraction's payoff.**

---

# Part D — Step-by-step flows

Each example below is broken into discrete steps. Every step names:

- **What** happens
- **Who** causes it (actor)
- **Which** component is involved
- **Role** — why this step exists

---

# Example 1 — CRON ("Daily 9am digest")

## Setup phase (happens once, when user creates the subscription)

### Step 1.1 — User types the utterance
- **What**: User says *"Every day at 9am, send me a summary of the top 5 most important emails from the last 24h."*
- **Who**: User
- **Component**: chat UI
- **Role**: kicks off the interactive set-up

### Step 1.2 — Routing agent classifies intent
- **What**: Receives the utterance, classifies it as `setup_standing` (not a one-shot)
- **Who**: Routing agent (LLM call)
- **Component**: routing agent
- **Role**: decides this should become a standing intent, not a one-time reply

### Step 1.3 — Routing agent writes the subscription row
- **What**: Calls `register_task(...)` → SQL INSERT
- **Who**: Routing agent
- **Component**: registry (`subscriptions` table)
- **Role**: persist the user's intent so the daemon can act on it later
- **Result**: row exists:
  ```yaml
  id: daily-email-digest
  trigger: { kind: cron, cron: "0 9 * * *" }
  target_agent: digest_agent
  prompt: "Read last 24h..."
  tools: [gmail.list, gmail.fetch]
  outcomes: [{ action: gmail.send, to: anu@… }]
  ```

### Step 1.4 — Subscription manager arms the cron job
- **What**: Notices the new row, calls `scheduler.add_job(cron="0 9 * * *", fn=fire)`
- **Who**: Subscription manager (in-process task in CUGA loop)
- **Component**: APScheduler
- **Role**: register the cron expression so the timer will fire at 9am

### Step 1.5 — Routing agent confirms to user
- **What**: Replies *"Got it, you'll get a digest at 9am tomorrow."*
- **Who**: Routing agent
- **Role**: close the conversation

## Runtime phase (happens every day at 9am)

### Step 1.6 — Timer fires
- **What**: System clock reaches 9:00:00
- **Who**: APScheduler (wall clock)
- **Component**: APScheduler
- **Role**: trigger source — the thing that decides "now's the time"

### Step 1.7 — Build the Event
- **What**: APScheduler's callback constructs:
  ```python
  Event(
    target=Target(kind="agent", name="digest_agent", thread_id="cron-2026-05-29"),
    payload={"trigger_time": "2026-05-29T09:00:00"},
    source_subscription_id="daily-email-digest",
  )
  ```
- **Who**: APScheduler callback
- **Component**: APScheduler
- **Role**: normalize the trigger into the standard Event shape so downstream doesn't care it was cron

### Step 1.8 — Hand to dispatcher
- **What**: Calls `dispatcher.dispatch(event)`
- **Who**: APScheduler callback
- **Component**: dispatcher
- **Role**: forward to the routing layer

### Step 1.9 — Dispatcher routes to inbox
- **What**: Reads `ev.target.agent_name == "digest_agent"`, calls `inboxes["digest_agent"].put(event)`
- **Who**: Dispatcher
- **Component**: `digest_agent` inbox (asyncio.Queue)
- **Role**: pick the right per-agent queue, enqueue

### Step 1.10 — Invoker wakes up
- **What**: Invoker loop was `await`ing on `digest_agent` inbox; the `.put` wakes it
- **Who**: Invoker loop (the daemon's `while True`)
- **Component**: Invoker
- **Role**: the central scheduler that picks events off inboxes

### Step 1.11 — Invoker acquires per-thread lock
- **What**: `async with thread_locks[ev.thread_id]:`
- **Who**: Invoker
- **Component**: thread lock table (in-memory dict)
- **Role**: prevent two events for the same conversation from running concurrently

### Step 1.12 — Invoker calls the agent function
- **What**: `await digest_agent(event)` — passes Event as the input
- **Who**: Invoker
- **Component**: agent function
- **Role**: actually run the work

### Step 1.13 — Agent calls `gmail.list`
- **What**: HTTP GET to Gmail API with `q="newer_than:1d"`
- **Who**: Agent function
- **Component**: Gmail MCP tool
- **Role**: fetch candidate emails

### Step 1.14 — Agent ranks + summarizes via LLM
- **What**: Prompts the LLM with the email list, gets back top-5 + summaries
- **Who**: Agent function
- **Component**: LLM client
- **Role**: do the actual reasoning task

### Step 1.15 — Agent calls `gmail.send`
- **What**: HTTP POST to Gmail API with the digest body
- **Who**: Agent function
- **Component**: Gmail MCP tool
- **Role**: deliver the outcome defined in the subscription

### Step 1.16 — Agent returns
- **What**: Function returns normally
- **Who**: Agent function
- **Component**: Invoker (receives the return)
- **Role**: signal "done"

### Step 1.17 — Invoker releases lock, awaits next event
- **What**: Lock released; loop returns to `await any_inbox.get()`
- **Who**: Invoker
- **Component**: Invoker
- **Role**: be ready for the next event (next day at 9am)

---

# Example 2 — PUSH ("Triage every incoming email")

## Setup phase

### Step 2.0 — One-time external wiring (done by admin, not CUGA)
- **What**: In Google Cloud Console, configure Gmail API to publish mailbox-change notifications to a Pub/Sub topic; set Pub/Sub to POST to `https://my-cuga.example.com/events`
- **Who**: Admin (human, outside CUGA)
- **Role**: tell Gmail where to deliver pushes

### Step 2.1 — User types the utterance
- **What**: *"Whenever an email arrives at `support@acme.com`, classify it as bug/billing/other and label it."*
- **Who**: User

### Step 2.2 — Routing agent classifies + writes row
- **What**: Classifies as `setup_standing`; INSERTs into `subscriptions`
- **Who**: Routing agent
- **Component**: registry
- **Result**:
  ```yaml
  id: support-triage
  trigger: { kind: push, source: gmail-pubsub, mailbox: support@acme.com }
  target_agent: triage_agent
  tools: [gmail.label]
  ```

### Step 2.3 — Nothing else happens at setup
- **What**: `POST /events` endpoint is **already running** (started with the daemon). No new listener to spin up.
- **Who**: nobody (no-op)
- **Component**: `POST /events` endpoint
- **Role**: this is the architectural point — push setup only writes the registry row; the listener is shared and always-on

## Runtime phase (happens on every incoming email)

### Step 2.4 — Customer sends an email
- **What**: Customer's email client → Gmail SMTP
- **Who**: Customer
- **Role**: the real-world event

### Step 2.5 — Gmail publishes to Pub/Sub
- **What**: Gmail detects mailbox change for `support@acme.com`, publishes a notification to the configured Pub/Sub topic
- **Who**: Gmail server
- **Component**: Gmail API + Google Pub/Sub
- **Role**: external system fans out the change

### Step 2.6 — Pub/Sub POSTs to CUGA
- **What**: `POST https://my-cuga.example.com/events` with JSON body `{mailbox, message_id, …}`
- **Who**: Google Pub/Sub
- **Component**: HTTP transport
- **Role**: deliver the push to CUGA

### Step 2.7 — `/events` endpoint receives the POST
- **What**: FastAPI handler validates signature, parses body
- **Who**: `POST /events` endpoint
- **Component**: FastAPI
- **Role**: ingress point — accept and authenticate the push

### Step 2.8 — Endpoint looks up matching subscription
- **What**: `SELECT * FROM subscriptions WHERE trigger.source='gmail-pubsub' AND mailbox='support@acme.com'`
- **Who**: `POST /events` handler
- **Component**: registry
- **Role**: find which agent should handle this push (registry feeds the routing decision)

### Step 2.9 — Endpoint builds Event
- **What**:
  ```python
  Event(
    target=Target(kind="agent", name="triage_agent", thread_id=f"gmail:{message_id}"),
    payload={"mailbox": "support@...", "message_id": "abc123", "snippet": "..."},
  )
  ```
- **Who**: `POST /events` handler
- **Component**: Event model
- **Role**: normalize HTTP body into the standard Event shape

### Step 2.10 — Endpoint hands to dispatcher
- **What**: `dispatcher.dispatch(event)`
- **Who**: `POST /events` handler

### Step 2.11 — Dispatcher routes to inbox
- **What**: `inboxes["triage_agent"].put(event)`
- **Who**: Dispatcher
- **Component**: `triage_agent` inbox

### Step 2.12 — `/events` endpoint returns 200
- **What**: HTTP 200 OK back to Pub/Sub (so Pub/Sub doesn't retry)
- **Who**: FastAPI handler
- **Role**: ack the push *before* agent runs — keeps ingress fast

### Steps 2.13–2.19 — Invoker → agent (identical to 1.10–1.17)
- Invoker wakes, locks `thread_id`, invokes `triage_agent(event)`.
- Agent calls LLM to classify, calls `gmail.label(message_id, "bug")`, returns.
- Invoker releases lock, awaits next event.

---

# Example 3 — PULL ("Watch starred emails every 5 min")

## Setup phase

### Step 3.1 — User types the utterance
- **What**: *"Every 5 minutes, check for new emails I've starred and add them to my Linear backlog."*

### Step 3.2 — Routing agent writes the subscription
- **What**: INSERT into `subscriptions`
- **Result**:
  ```yaml
  id: starred-to-linear
  trigger:
    kind: pull
    source: gmail
    query: "is:starred newer_than:1d"
    interval: 5m
    state_key: gmail-starred-anu
  target_agent: linear_create_agent
  tools: [linear.create_issue]
  ```

### Step 3.3 — Subscription manager spawns a poller task
- **What**: Starts `asyncio.create_task(pull_poller(sub))` — one task per pull subscription
- **Who**: Subscription manager
- **Component**: pull poller task (new asyncio task in the CUGA loop)
- **Role**: become the trigger source for this subscription

### Step 3.4 — Initial poller_state row
- **What**: INSERT empty state row if not present
- **Component**: registry (`poller_state` table)
- **Role**: somewhere to remember "what we've seen" across restarts

## Runtime phase (happens every 5 min)

### Step 3.5 — Timer fires inside poller task
- **What**: `await asyncio.sleep(5*60)` returns
- **Who**: pull poller task
- **Component**: pull poller
- **Role**: trigger source — daemon-initiated tick

### Step 3.6 — Read cursor / seen_ids
- **What**: `SELECT seen_ids FROM poller_state WHERE state_key='gmail-starred-anu'`
- **Who**: pull poller
- **Component**: registry (`poller_state`)
- **Role**: load what was already processed

### Step 3.7 — Fetch from Gmail
- **What**: HTTP GET to Gmail API with `q="is:starred newer_than:1d"`
- **Who**: pull poller
- **Component**: Gmail API (via MCP)
- **Role**: ask the external system "what's there now?"

### Step 3.8 — Compute diff
- **What**: `new_msgs = [m for m in fetched if m.id not in seen_ids]`
- **Who**: pull poller
- **Role**: **THIS** is what makes pull a trigger instead of dumb polling — only new items emit

### Step 3.9 — Short-circuit if nothing new
- **What**: If `new_msgs` is empty: go back to Step 3.5 (sleep)
- **Who**: pull poller
- **Role**: avoid emitting duplicate events

### Step 3.10 — For each new message: build Event
- **What**:
  ```python
  Event(
    target=Target(kind="agent", name="linear_create_agent",
                  thread_id=f"gmail:{msg.id}"),
    payload={"subject": ..., "snippet": ..., "link": ...},
  )
  ```
- **Who**: pull poller

### Step 3.11 — Hand each Event to dispatcher
- **What**: `dispatcher.dispatch(event)` per new message
- **Who**: pull poller

### Step 3.12 — Update poller_state
- **What**: `UPDATE poller_state SET seen_ids = seen_ids ∪ new_msg_ids`
- **Who**: pull poller
- **Component**: registry (`poller_state`)
- **Role**: persist so the next tick doesn't re-emit these (and restart-safe)

### Step 3.13 — Dispatcher routes to inbox
- **What**: `inboxes["linear_create_agent"].put(event)` for each
- **Who**: Dispatcher

### Steps 3.14–3.20 — Invoker → agent (identical pattern)
- Invoker wakes for each Event, locks `thread_id`, invokes `linear_create_agent(event)`.
- Agent calls `linear.create_issue(title=msg.subject, body=msg.snippet)`.
- Returns. Invoker releases lock, awaits next event.

### Step 3.21 — Poller goes back to sleep
- **What**: Back to Step 3.5
- **Role**: ready for the next 5-min tick

---

# The pattern across all three

Read down each column:

| Phase                  | CRON                    | PUSH                            | PULL                                    |
| ---------------------- | ----------------------- | ------------------------------- | --------------------------------------- |
| **Who watches?**       | APScheduler             | `POST /events` endpoint         | pull poller task                        |
| **Who initiates?**     | wall clock              | external system                 | daemon timer                            |
| **What's persisted?**  | cron expression         | nothing (registry only matches) | poller_state cursor                     |
| **Builds Event in...** | APScheduler callback    | `/events` handler               | poller task                             |
| **Dispatcher's job**   | route to agent inbox    | route to agent inbox            | route to agent inbox                    |
| **Invoker's job**      | dequeue, lock, invoke   | dequeue, lock, invoke           | dequeue, lock, invoke                   |
| **Agent's job**        | run prompt, call tools  | run prompt, call tools          | run prompt, call tools                  |

**The trigger sources differ. Everything from the Event onward is identical.** That's the architectural point.
