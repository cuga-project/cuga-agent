# Durable Execution for CUGA (Temporal / DBOS) — Spec & Design

Status: proposal — outcome of a design brainstorm; nothing implemented
Tracks: epic #243 (Long-Running Task Execution)
Scope: new `src/cuga/backend/execution/`; touches `cuga_graph/entry_graph.py`,
`cuga_lite/adapter/{prepare_node,sandbox_node}.py`, `human_in_the_loop/followup_model.py`, `sdk.py`,
`server/main.py`, `server/run_routes.py`, `events/{app,runtime,native_scheduler}.py`

## Decisions taken in the brainstorm

| Topic | Decision |
|---|---|
| Run durations to support | All of them: minutes (one big run), hours (batch), days–weeks (waiting on people/events), recurring |
| Engine | Temporal preferred, not fixed → prototype **Temporal and DBOS** behind one interface, then pick |
| Shape | Pluggable `ExecutionBackend`; in-process stays the default |
| Primary audience | Enterprise self-host, one tenant per install |
| Tool side effects | Reads + internal writes (tickets, CRM, Slack) — a duplicate is bad but recoverable |
| Crash recovery | Resume from the last step; write tools protected by idempotency keys |
| Approvals | By *another role* (manager, finance, …); timeout behaviour configurable per policy |
| Data in engine history | IDs only — state, variables and tool output stay in the customer's Postgres / object store |
| Agent-callable durable tools | All four: `wait_until`, `wait_for_event`, `ask_human`, `schedule_followup` |
| Browser (Playwright) mode | Not in v1 |
| Python | 3.11+ acceptable for the durable extra (the recommended design does not need it — §2.1) |
| Agent config republished mid-run | The run finishes on the version it started with |
| Studio chat | Direct by default; durable is opt-in per run ("run in background"); automations and schedules always durable |
| PoC must show | Crash survival · 2-day approval · exactly-once cron across replicas · redeploy mid-run |

---

## Context — why

Today a CUGA run cannot outlive the process that started it:

- **A run is one HTTP request.** `/stream` runs the graph inside the SSE generator (`server/main.py`,
  `event_stream`). Closing the tab or restarting the pod ends the run, there is no detach/reattach, and
  stream events are persisted only once the final answer arrives (`server/main.py:1922`).
- **Checkpoints are in memory only.** Every graph compiles with `MemorySaver` (`entry_graph.py:111`; also
  `sdk.py`, `events/concierge.py`, `events/runtime.py`). Pending approvals live in RAM, and a draft
  save/publish rebuilds the graph with a fresh `MemorySaver` (`server/manage_routes/helpers.py:97-115`),
  dropping every thread's checkpoints.
- **Approvals wait forever, and only the requester can answer.** `FollowUpAction.timeout_seconds` exists
  (`human_in_the_loop/followup_model.py:54`) but nothing reads it.
- **The scheduler is single-replica and at-most-once.** `events/native_scheduler.py` is an in-process
  loop; a failed fire is not retried, and a second replica double-fires (the Code Engine deploy pins
  `--max-scale 1` for that reason).
- **Retries can duplicate runs.** `HttpRuntime` retries `POST /run` on transport errors, including read
  timeouts (`events/runtime.py:392-416`), but `/run` is not idempotent.
- **Long batches hit hard caps.** `cuga_lite_max_steps = 70` (`settings.toml:44`) and a recursion limit
  of 135 end a run regardless of how much useful work remains.

Issue #29 ("detached agent runs") is closed; as far as the code shows, what shipped is the events
service's bounded flows (`expires_at`), not a detached run primitive. Epic #243 is still open; this
document is a proposed design for it.

---

## 1. Current state — what a run depends on

### 1.1 Lifecycle

```
client ──POST /stream──▶ event_stream  (SSE generator, one uvicorn process)
                            │ load state: graph.get_state(thread)              ← MemorySaver
                            ▼
                         AgentLoop.run_stream → graph.astream(updates, subgraphs=True)
                            │  ChatAgent → EntryRouter → CugaLite{prepare → call_model ⇄ sandbox} → FinalAnswer
                            │  interrupt() → SSE "interrupt" → client POSTs ActionResponse → Command(resume)
                            ▼
                         on final Answer: write conversation history + stream events
```

### 1.2 Process-local things a resumed run would need

| Thing | Where | Problem on another worker |
|---|---|---|
| Tool callables | `adapter._tools_context`, filled only by `prepare` (`prepare_node.py:261-596`), read by `sandbox` (`sandbox_node.py:144`) | A resume that starts at `call_model`/`sandbox` finds no tools |
| LLM, policy system, callbacks | Live objects in `config["configurable"]` (`utils/agent_loop.py:532-569`) | Not serializable; must be rebuilt from IDs |
| Todos, spawn futures | Closures per compiled graph (`cuga_lite_graph.py:201-227`) | Lost |
| Sandboxes | E2B / OpenSandbox caches per thread; local executor workspace on local disk | Recreated; local disk is not shared |
| Browser | One Playwright context per server process | Out of scope for v1 |

### 1.3 Side effects

- Code-act: one LLM-written block runs as a unit, with up to 100 tool calls per block
  (`settings.toml:62-64`). There are no idempotency keys, and a timeout cancels the block mid-flight.
- Tools are called through the registry (`/functions/call`) or in-process via
  `ActivityTracker.invoke_tool`. Every in-process callable is wrapped by `make_tool_awaitable`
  (`cuga_agent_core/execution/code_extraction.py:120`).
- MCP tool annotations (`readOnlyHint`, `idempotentHint`, `destructiveHint`) are not read anywhere
  today.

---

## 2. Design

### 2.1 Principle — LangGraph stays the brain; the engine is the shell

```
 Studio · SDK · /run · events service
          │  start(RunRequest) · respond · cancel · events(after=seq)
          ▼
 ExecutionBackend ── InProcessBackend   default; today's behaviour + detachable asyncio task
          │        ── TemporalBackend    cuga[temporal]
          │        ── DBOSBackend        cuga[dbos]
          ▼
 RunWorkflow  (engine code, ~100 lines, deterministic)
   loop: outcome = run_segment(run_id, resume)
         paused? → wait (answer / timer / event) per policy → resume
         done?   → deliver result → return
          │
          ▼  activity / step
 SegmentRunner  (engine-agnostic CUGA code)
   load RunRecord (pinned agent version) → rebuild graph with the Postgres checkpointer
   graph.astream(...) until interrupt | END | segment budget
   append StreamEvents to cuga_run_events · heartbeat · return Outcome
          │
          ▼
 Customer Postgres: LangGraph checkpoints · cuga_runs · cuga_run_events
                    cuga_pending_actions · cuga_tool_journal      (+ object store for large blobs)
```

Why this split:

- **IDs only in the engine.** Workflow inputs, activity payloads and signals carry the `run_id`, the
  segment number, the outcome kind and action IDs. State, variables, tool output and messages stay in
  Postgres. This satisfies the data decision and sidesteps Temporal's ~2 MB payload limit. That limit
  is the failure reported for the LangGraph plugin in
  [temporalio/sdk-python#1894](https://github.com/temporalio/sdk-python/issues/1894).
- **Step granularity comes from LangGraph, not the engine.** With a Postgres checkpointer and
  `durability="sync"`, LangGraph persists a checkpoint after every superstep. That includes the
  `call_model`/`sandbox` steps inside the CugaLite subgraph, because subgraphs inherit the parent
  checkpointer. A retried segment resumes from the latest checkpoint and repeats at most the one node
  that was in flight.
- **Engine-neutral.** The same `SegmentRunner` serves Temporal and DBOS. That keeps the bake-off cheap
  and keeps engine imports out of CUGA's graph code.
- **Tiny workflow code.** Determinism rules and workflow versioning apply only to the loop, not to the
  agent.

**Alternative considered: Temporal's official LangGraph plugin** (`temporalio[langgraph]`, temporalio ≥
1.27). It runs each graph node as an activity or inline in the workflow. It is not the primary path for
three reasons:

1. Node inputs and outputs travel through engine payloads. That conflicts with the IDs-only decision,
   and CUGA nodes return the full `state.model_dump()`.
2. It binds the graph to one engine, so there would be no DBOS comparison.
3. Its `interrupt()` support needs Python 3.11.

It is kept as an optional spike (Phase 7).

### 2.2 The `ExecutionBackend` contract

```python
# src/cuga/backend/execution/backend.py  (sketch)
class RunRequest(BaseModel):
    run_id: str                 # idempotency key: same id → same run, never a second one
    agent_id: str
    agent_version: int          # pinned at start; the run finishes on it
    thread_id: str
    user_id: str | None
    input: str | None           # new task / message
    origin: dict | None         # where to deliver the result (channel, thread, webhook)
    budgets: RunBudgets         # max_wall_clock, max_segments, max_llm_tokens, max_tool_writes

class Pause(BaseModel):         # what a paused run is waiting for
    kind: Literal["approval", "ask_human", "wait_until", "wait_for_event"]
    action_id: str
    ref: str                    # row in cuga_pending_actions holding the details

class Outcome(BaseModel):
    kind: Literal["done", "paused", "segment_budget", "failed", "cancelled"]
    pause: Pause | None = None

class ExecutionBackend(Protocol):
    capabilities: BackendCapabilities   # durable, schedules, durable_waits
    async def start(self, req: RunRequest) -> RunHandle
    async def get(self, run_id: str) -> RunStatus
    async def respond(self, run_id: str, action_id: str, response_ref: str) -> None
    async def notify_event(self, correlation_key: str, event_ref: str) -> None
    async def cancel(self, run_id: str, reason: str) -> None
    async def schedule(self, spec: ScheduleSpec) -> str        # idempotent on spec.id
    async def unschedule(self, schedule_id: str) -> None
    def events(self, run_id: str, after_seq: int = 0) -> AsyncIterator[StreamEvent]
```

`InProcessBackend` implements the contract with one `asyncio` task per run, on the same Postgres/SQLite
tables:

- Runs are detachable (close the tab, reconnect) but do not survive a crash.
- `capabilities.durable_waits` is `False`, so the agent is never offered a tool the backend can't honour.

### 2.3 The run workflow

```python
async def run_workflow(run_id: str):
    resume = None
    while True:
        outcome = await run_activity(
            run_segment, run_id, resume,
            heartbeat_timeout=60s, start_to_close=segment_max_duration,
            retry=exponential(max_attempts=5, non_retryable=[ConfigError]),
        )
        if outcome.kind in ("done", "failed", "cancelled"):
            await run_activity(deliver_result, run_id)   # retried on its own; never re-runs the agent
            return outcome.kind
        if outcome.kind == "segment_budget":
            if over_run_budget(run_id):
                return await finish(run_id, "budget_exhausted")
            resume = Continue()                          # new segment, fresh step counter (§2.11)
        else:                                            # paused
            resume = await handle_pause(outcome.pause)   # §2.6 / §2.7
```

| Concept | Temporal | DBOS |
|---|---|---|
| Run | Workflow, `id = run_id` (a duplicate start is rejected) | `@DBOS.workflow`, workflow ID = `run_id` |
| `run_segment` | Activity; heartbeats from a background task every 10 s | `@DBOS.step` with retries |
| Answer / approval / event arrives | Update with a validator (synchronous accept/reject) | `DBOS.send` → `DBOS.recv(topic, timeout)` |
| `wait_until`, reminders, escalation | `workflow.sleep`, `wait_condition(timeout=…)` | `DBOS.sleep`, `recv` timeout |
| Schedules | Temporal Schedules (overlap and catch-up policies) | Scheduled workflow, deduplicated by schedule name + time |
| Cancel | Workflow cancel → activity sees it on heartbeat → sets the run's stop event | `DBOS.cancel_workflow` + the same stop event |
| Worker dies mid-segment | Heartbeat timeout → activity retried on another worker | Workflow recovered from its last completed step (multi-replica recovery to verify, §5) |
| Ops visibility | Temporal UI + search attributes (agent, user, status) | DBOS workflow tables / Conductor |

### 2.4 Recovery — "resume from the last step"

- Checkpointer: `AsyncPostgresSaver` (`langgraph-checkpoint-postgres`) on `storage.postgres_url`, with
  `durability="sync"` for durable runs.
- A retried `run_segment` calls `graph.astream(None, config)` on the run's thread, and LangGraph
  continues from the latest checkpoint.

| Node in flight when the worker died | What repeats | Cost |
|---|---|---|
| `call_model` | One LLM call | Tokens only — no tool has run yet |
| `sandbox` | The same code block (it was checkpointed when `call_model` finished) | Tool calls repeat → the §2.5 journal turns writes into no-ops |
| `prepare` / `FinalAnswerAgent` | Tool loading / one LLM call | Idempotent / tokens |

Prerequisite: every node must be runnable from *checkpoint + config IDs* alone (§2.8).

### 2.5 Tool-call journal — no duplicate writes

1. **Classify** each tool as `read`, `idempotent` or `write`:
   - MCP: `readOnlyHint` → read; `idempotentHint` → idempotent.
   - OpenAPI: `GET`/`HEAD` → read; every other method → write.
   - An override per tool in the agent config or policy wins.
   - Anything unclassified → write.
2. **Key** every write as `(run_id, checkpoint_id of the sandbox step, tool_name, ordinal)`, where
   `ordinal` counts write calls within the block. Store a hash of the arguments next to the key.
3. **On each call** (write tools only, durable runs only):
   - If a journal row is `done`, return the stored result without calling.
   - If the row is `started` or missing: mark it `started`, call the downstream with an `Idempotency-Key`
     header (OpenAPI) or `_meta` (MCP), store the result, then mark it `done`.
   - If the key matches but the argument hash differs, the replayed block diverged (time-dependent code,
     or read results that changed). Do not call. Pause the run for a human decision; policy may
     override this.
4. **Where it hooks in:**
   - In-process tools: at the `make_tool_awaitable` seam.
   - E2B `call_api` stubs: at the registry's `/functions/call`, with the run ID and step passed as
     headers.

Residual risk: a crash after the downstream write but before the `done` commit causes a duplicate, but
only if the downstream ignores idempotency keys. That is accepted for "reads + internal writes" and
documented per tool.

### 2.6 Approvals by another role, with deadlines

`FollowUpAction` gains:

```python
assignee: str | None = None      # "user:<id>" | "role:<name>" | "group:<name>"; None = requester (today)
timeout_seconds: int | None      # exists today, unused — finally wired
on_timeout: Literal["wait", "remind", "escalate", "reject"] = "wait"
reminders_seconds: list[int] = []
escalate_to: str | None = None
```

- **Who and when.** The `tool_approval` policy sets these fields per tool, for example: "discount >
  15% → `role:sales_manager`, remind at 24 h, escalate to `role:vp_sales` at 48 h".
- **Notify and wait.** On pause, `handle_pause` runs a `notify` activity through events-service
  delivery (Slack DM, email or the Studio inbox), then waits against those deadlines. Every reminder and
  escalation is a durable timer.
- **Answering.** Answers arrive via `POST /runs/{run_id}/actions/{action_id}` from the Studio inbox, a
  Slack button or an email link. The API checks the caller's OIDC roles against `assignee` before
  calling `backend.respond`. A Temporal Update validator also rejects stale or duplicate answers.
- **Audit.** One row per decision (action, decision, approver, time, comment) in `cuga_run_events`.

### 2.7 Durable tools the agent can call

`prepare` offers these only when `backend.capabilities.durable_waits` is true:

| Tool | Engine primitive | Guardrail |
|---|---|---|
| `wait_until(when)` | Durable sleep | Maximum total wait per run |
| `wait_for_event(source, match, timeout)` | Correlation row + signal from the events service (webhook, Slack reply, poll) | Sources limited to configured channels |
| `ask_human(to, question, options, timeout, escalate_to)` | Same path as §2.6; the answer is returned to the agent | The assignee must exist |
| `schedule_followup(when \| cron, task)` | `backend.schedule()` | Needs approval by default; capped per agent |

These tools are called *inside* a code block, and a running block cannot be suspended across
processes. They work like this:

1. Each tool records its request and raises `DurablePause`.
2. `sandbox` lets that exception through instead of turning it into output text, and routes to the
   existing `interrupt()`.
3. The segment returns `paused`, and the workflow waits.
4. The resume value comes back as the block's result (for example "event received: …").

Code after the call in that block does not run, so the prompt says a durable tool must be the block's
last statement. Variables assigned earlier in the block persist as they do today.

### 2.8 Required refactor — rehydratable nodes

- Add `ensure_runtime(state, config)` at the start of `call_model` and `sandbox`. If this process has no
  tools for `(thread_id, agent_version)`, it runs the tool-loading half of `prepare`: no policy checks,
  and no prompt rebuild, since the prompt is already in state.
- Key tools, todos and spawn futures **by thread**, not per compiled graph. This also removes §6.1.
- Pass IDs in `configurable` (agent ID, version, LLM config ref). Resolve the live objects in the worker
  via `LLMManager` and `config_store`.
- Keep one checkpointer instance across graph rebuilds, instead of creating a new `MemorySaver` per
  build.

### 2.9 Streaming and reattach

- `SegmentRunner` appends every `StreamEvent` to `cuga_run_events(run_id, seq, ts, type, payload)` as
  it is produced.
- `GET /runs/{run_id}/events?after=<seq>` is an SSE tail (LISTEN/NOTIFY, with a polling fallback).
  Studio's background-run view and the SDK's `RunHandle.stream()` reconnect from the last `seq`.
- `/stream` chat stays as it is (opt-in decision). On completion, a durable run writes conversation
  history exactly as chat does.

### 2.10 Versioning

- `RunRecord.agent_version` is pinned at start. Graphs are cached by `(agent_id, version)`, and every
  segment builds from the pinned version.
- The engine code (the loop) changes rarely. When it does, use Temporal worker versioning or the DBOS
  application version.
- Graph changes across CUGA releases: a checkpoint written by release N must resume on release N+1.
  Add checkpoint fixtures from the previous release to CI, and keep an alias for any renamed node for
  one release.

### 2.11 Budgets, continuation, cancel

- **Continuation.** For durable runs, `segment_budget` replaces "max steps reached → fail". While the
  run budget allows, the workflow starts another segment with a continuation message, so a 6-hour batch
  becomes many bounded segments. Context summarization (#96) keeps each segment's context small.
- **Cancel.** `/runs/{id}/cancel` → engine cancel → the existing stop event.
- **Pause/resume (#243).** A `pause` signal stops scheduling new segments once the current one ends.

### 2.12 Events service

- **`/invoke`.** With a durable backend, `/invoke` calls `backend.start(run_id=<dedup key or
  sched:{sub}:{tick}>)` and returns at once. Delivery is the workflow's `deliver_result` activity.
  Retrying `/invoke` or `/run` can then no longer start a second run.
- **Schedules.** Subscriptions with `backend='native'` become engine schedules when armed and are
  removed when disarmed. The in-process loop stays the default when no durable backend is configured.
  Today's "fire once after downtime" behaviour maps to a catch-up window plus a skip-overlap policy;
  verify the exact Temporal semantics in the spike.
- **Out of scope.** Channel long-poll loops (Telegram, Discord) remain single-owner.

### 2.13 Configuration and processes

```toml
[execution]
backend = "inprocess"          # "inprocess" | "temporal" | "dbos"
checkpointer = "memory"        # "memory" | "sqlite" | "postgres"; durable backends require "postgres"
segment_max_duration = "2h"
heartbeat_seconds = 10
default_max_wall_clock = "14d"
time_scale = 1                 # demo/test only: 1440 makes a day pass in a minute for timers and deadlines

[execution.temporal]
target = "localhost:7233"
namespace = "default"
task_queue = "cuga-runs"
payload_codec = ""             # optional encryption codec (defence in depth; payloads are IDs anyway)

[execution.dbos]
system_database_url = ""       # empty = storage.postgres_url
```

- **New process.** `cuga worker` runs the Temporal worker or DBOS executor. For development,
  `cuga start demo --durable` runs the server and a worker in one process.
- **Helm.** A `worker` Deployment with N replicas; the API server scales independently.
- **Extras.** `cuga[temporal]` and `cuga[dbos]`, each with its own Python marker (precedent: the `wxo`
  extra is `python_version>='3.11'`).

---

## 3. Demo use case — "Renewal Desk"

A good demo exercises every durable feature with side effects the audience can **see and count**, and
runs on tools CUGA already ships. Two existing demo servers fit:

- **The CRM demo** (`demo_tools/crm`): accounts, contacts and opportunities with value, stage,
  probability and close date. It is plain REST, so GET is a read and POST/PUT is a write.
- **The email MCP and its local SMTP sink** (`demo_tools/email_mcp`): every sent mail lands as a
  `.json` file.

**Story.** Every Monday, the Renewal Desk agent reviews all opportunities that close this quarter and
have had no activity for 14 days. For each one it reads the account and its contacts, emails the
customer and updates the opportunity. A discount above 15% needs the sales manager's approval. After
emailing, the agent waits for the customer's reply and follows up if none comes.

| Step | What the audience sees | Proves |
|---|---|---|
| 3 workers running; the Monday 08:45 tick fires | Exactly **one** run starts | Exactly-once cron across replicas |
| Batch over ~150 seeded opportunities | Live progress in Studio; close the tab, reopen it, progress continues | Hours-long run, detach/reattach |
| `kill -9` the worker at opportunity 60 | Another worker carries on; the mail sink holds exactly one mail per opportunity | Crash survival, journal, no duplicate writes |
| A 20% discount on a large deal | Approval request to `role:sales_manager` in Slack / the Studio inbox; with time compressed, a reminder, then escalation to `role:vp_sales`; the VP approves "two days later" | Another-role approval, deadlines, long wait |
| A customer reply dropped into the mail sink | The waiting run wakes and moves the deal to `negotiation` | `wait_for_event` |
| No reply within 3 days | A follow-up mail, then `schedule_followup` for next Monday | `wait_until`, `schedule_followup` |
| Agent v2 (new email tone) published mid-run | The in-flight run keeps v1; next Monday's run uses v2 | Version pinning |
| The same script on DBOS | The same results, with only Postgres | Bake-off |

Days have to pass in minutes:

- Live demo: durable timers and approval deadlines read `execution.time_scale`.
- Automated tests: Temporal's time-skipping test environment.

**Alternatives considered:**

- Employee onboarding: multiple approvers and systems, the classic Temporal demo, but it needs new HR/IT
  mocks.
- Invoice-exception handling: needs finance mocks.

Renewal Desk wins because everything it needs already exists except the reply trigger, which is a poll
subscription on the mail sink.

---

## 4. Rollout — independently shippable PRs

| Phase | PR | Ships | Useful without an engine? |
|---|---|---|---|
| 0a | Persistent checkpointer | `[execution] checkpointer` (sqlite/postgres); one instance across graph rebuilds | Yes — approvals survive restarts and publishes |
| 0b | Rehydratable nodes | §2.8; per-thread tools context (fixes §6.1) | Yes |
| 0c | Incremental run events | `cuga_run_events` written during the run, not at the end | Yes — crash forensics |
| 0d | Measure state size | Checkpoint bytes per step; move the largest fields to delta updates | Yes |
| 1 | `ExecutionBackend` + `InProcessBackend` | `/runs` API, `RunHandle` in the SDK (`CugaAgent.start()`), Studio "run in background" | Yes — detachable runs |
| 2 | `TemporalBackend` + `DBOSBackend` | §2.3 workflow, segment activity, cancel, budgets | — |
| 3 | Tool journal | §2.5 classification and journal | — |
| 4 | Role approvals with deadlines | §2.6, Studio approvals inbox | Partly (assignee and notify work in-process) |
| 5 | Events service on the backend | §2.12 schedules + idempotent `/invoke` | — |
| 6 | Agent durable tools | §2.7 | — |
| 7 | Spikes | Temporal LangGraph plugin (per-node activities); supervisor delegations and `spawn_agent` as child workflows | — |

Phases 0–1 are worth doing even if neither engine is adopted.

---

## 5. Verification

PoC acceptance: each scenario runs against both engines, using the Renewal Desk agent, a scripted fake
LLM, and the CRM and email demo servers.

1. **Crash survival.**
   - SIGKILL the worker during a sandbox block after 3 of its 5 writes, then restart it.
   - Assert the run completes.
   - Assert each write reached the CRM / mail sink exactly once.
   - Assert the final answer matches a run that did not crash.
2. **Long approval.**
   - Pause on a `role:sales_manager` approval.
   - Advance time to 24 h: a reminder is sent. Advance to 48 h: the approval is escalated.
   - Approve as a `vp_sales` user and assert the run resumes.
   - Assert an answer from a user without the role is rejected (403).
   - Time control: Temporal's time-skipping environment; DBOS uses `time_scale`.
3. **Exactly-once cron.**
   - Run 3 workers with a 1-minute schedule for 10 minutes, restarting one worker partway through.
   - Assert exactly 10 runs with unique run IDs.
4. **Redeploy mid-run.**
   - Start a run on agent v3, publish v4, then restart the workers on a new build.
   - Assert every segment ran on v3 and the next run uses v4.

Bake-off scorecard (to fill in from the PoC):

| Criterion | Temporal | DBOS |
|---|---|---|
| Self-host footprint | Temporal server + its database (Postgres works) + UI | A library + the Postgres CUGA already uses |
| Ops UI | Temporal Web UI (bundled) | DBOS Conductor — check licensing for self-host |
| Recovery from a permanently lost replica | Built in (heartbeat timeout → another worker) | Verify the behaviour without Conductor |
| Durable timers / waits with a timeout | Yes | Yes |
| Dynamic per-subscription schedules | Native Schedules API | Verify; otherwise one scheduled tick workflow + deduplicated IDs |
| Test time-skipping | Yes | Verify; otherwise `time_scale` |
| License | MIT | MIT (Transact) |
| Customer already runs it | Common in large enterprises | Nothing extra to run |

---

## 6. Adjacent findings (surfaced, not fixed here)

1. **Thread-bound callables in a graph-shared dict (likely; not reproduced).** Two concurrent runs on
   the same agent, with runtime filesystem/shell tools enabled, could swap workspaces between `prepare`
   and `sandbox`. The chain:
   - `prepare` builds the filesystem/shell callables for the current thread's workspace
     (`prepare_node.py:461-467`) and writes them into `adapter._tools_context` (`:468`).
   - That dict is created once per compiled CugaLite graph (`cuga_lite_graph.py:205`).
   - The entry graph compiles one CugaLite subgraph (`entry_graph.py:209-217`).
   - The server caches one entry graph per `(agent_id, use_draft)` across all users and threads
     (`server/main.py:2587`).

   Phase 0b removes the shared dict; a targeted concurrency test should come first.
2. **`HttpRuntime` can run an agent twice** on a read timeout (see Context). Phase 5 fixes this; a
   `run_id` idempotency key on `/run` would fix it sooner.
3. **The native scheduler gives up before the run does.** Its HTTP timeout is 200 s
   (`native_scheduler.py:117`). `HttpRuntime` waits up to 300 s per attempt, with 2 retries
   (`events/runtime.py:249`). A slow fire is therefore logged as failed while the run continues.

---

## 7. Related issues and PRs

- #243 — Epic: long-running task execution (open). This document proposes its design.
- #29 — Detached agent runs (closed; shipped as bounded event flows rather than a run primitive).
- #96 — Context summarization (closed); needed for multi-segment runs.
- #751 — Isolate sub-agent checkpoints across users (open). Same class of problem as §6.1. Land it
  before Phase 0a so that persisted checkpoint keys are already scoped correctly.
- #560 — Tool-call caps per block, run and thread; reuse them for `max_tool_writes`.

## 8. Open questions

1. Which approver notification channels for v1: Slack, email, the Studio inbox, or all three?
2. Is the OIDC roles claim enough to resolve `role:<name>`, or is a directory lookup (SCIM/LDAP) needed?
3. How long must run records, events, journal rows and checkpoints be retained for audit?
4. In durable mode, should the local filesystem workspace be refused, or should it require a shared
   volume?
5. When a durable backend is configured, should `/run` from the events service default to durable?

## References

- Temporal — Python LangGraph integration: https://docs.temporal.io/develop/python/integrations/langgraph
- Temporal blog — LangGraph plugin: https://temporal.io/blog/temporal-langgraph-plugin-durable-execution
- Large LangGraph state vs. payload limits: https://github.com/temporalio/sdk-python/issues/1894
- DBOS — scheduled workflows: https://docs.dbos.dev/python/tutorials/scheduled-workflows
