# Skill Hooks for Event-Driven CUGA

> **First time here?** Read [design_doc.md](design_doc.md) for the runtime
> vocabulary (parked agent loops, always-running producers, the dispatcher).
> This doc adds *one new producer category* on top of that.

Trigger agentic loops on **internal** events — specifically, when a CUGA
agent calls a skill / tool. Hooks let you add cross-cutting behavior (audit,
notification, escalation) without modifying the agent that does the work.

**Architectural note: this doc describes the only new architecture in the
configuration story.** Interactive (Mode 1) and Declarative (Mode 2) ride on
today's event-driven design unchanged. Hooks (Mode 3) introduce three new
pieces: a new `trigger.kind=hook`, a new `event.kind=skill_event`, and a new
producer — the `SkillHookDispatcher` — that wraps every CUGA tool call.

For the architecture-level framing, see
[configuration_architecture.md](configuration_architecture.md).
Parent overview: [configuration_modes.md](configuration_modes.md).
Sibling (tooling-only addition): [declarative_config.md](declarative_config.md).

---

## TL;DR

Hooks are a fourth `trigger.kind` (alongside push, pull, timed). They fire
when a named **skill / tool execution** happens inside any agent's turn:

```yaml
- id: audit-linear-tickets
  trigger:
    kind: hook
    on: post_skill                # also: pre_skill, on_skill_error
    skill: linear.create_ticket   # match by exact name OR glob
  target:
    agent: audit_logger
  prompt: |
    A Linear ticket was just created by agent {hook.invoking_agent}.
    Ticket: {hook.result.ticket_url}
    Log it to the audit table.
```

Same Event envelope, same dispatcher, same agent loops. The only new thing
is the **internal trigger** that wraps every skill call and emits an Event
to anyone subscribed.

---

## Why hooks (vs. just modifying the agent)

The naïve thing is to bake the cross-cutting behavior into the agent. *"When
I create a Linear ticket, also log to the audit table."* That works for one
agent, fails as a pattern:

- **N agents × M concerns = N×M places to change.** Five agents that touch
  Linear, three concerns (audit, slack notify, metrics)? Fifteen edits.
- **Behavior leaks.** Audit logic in the triage agent's prompt makes the
  triage agent's reasoning noisier and harder to debug.
- **No central control.** Want to add a new audit rule? You need to find
  and modify every agent that creates Linear tickets.

Hooks invert this: **the concern lives in one place, regardless of how many
agents do the underlying action.**

This is the same pattern as:

- **Git hooks** — `post-commit` runs once per commit, regardless of who made it
- **Claude Code's hook system** — `PostToolUse`, `UserPromptSubmit`, etc.
- **Kubernetes admission controllers** — webhook fires on any matching API call
- **Database triggers** — `AFTER INSERT` runs once per row, regardless of
  which query inserted it

The CUGA equivalent: *"After any agent calls Linear.create_ticket, run the
audit_logger agent."*

---

## Where hooks fire

A skill / tool call inside an agent turn has a natural lifecycle:

```
agent loop wakes
   ↓
classify intent / plan
   ↓
plan tool calls
   ↓
┌──────────────────────────────────────┐
│ for each tool call:                  │
│   ┌────────────────────────────────┐ │
│   │   ⮕ PRE_SKILL hook  fires     │ │  ← intercept input, validate, abort
│   │                                │ │
│   │   skill.execute(args)          │ │
│   │                                │ │
│   │   ⮕ POST_SKILL hook fires     │ │  ← react to result
│   │       OR                       │ │
│   │   ⮕ ON_SKILL_ERROR hook       │ │  ← react to failure
│   └────────────────────────────────┘ │
└──────────────────────────────────────┘
   ↓
compose response
   ↓
agent emits  ───────────────────┐
   ↓                            │
turn ends                       │
                                ▼
                       ⮕ ON_AGENT_EMIT hook fires
                          ⮕ ON_INBOX_DRAIN hook
                             (when inbox goes empty)
```

Five hook points cover every case I can think of from the events.md set:

| Hook | When it fires | Hook receives |
|---|---|---|
| `pre_skill` | Before the agent's tool runtime executes a tool | `{ skill, args, invoking_agent, thread_id }` |
| `post_skill` | After the tool returns successfully | `{ skill, args, result, duration_ms, ... }` |
| `on_skill_error` | After the tool raises / returns an error | `{ skill, args, error, ... }` |
| `on_agent_emit` | When an agent calls `publish()` / `send_to()` / `reply_to()` | `{ source_agent, emission_target, payload }` |
| `on_inbox_drain` | When an agent loop processed an event AND its inbox is empty | `{ agent, last_event_id, idle_since }` |

`pre_skill` and `on_skill_error` are the spiciest; start without them and
add when needed. `post_skill` and `on_agent_emit` cover ~90% of real use
cases.

---

## How hook firing actually works

The implementation is small and **does not introduce a new primitive**.
It's just another producer.

### Mechanism

1. Every CUGA tool runtime call is wrapped in a `with skill_hook_dispatcher(...)`
   context manager.
2. `pre_skill` fires synchronously *before* the tool runs. If a hook returns
   `{abort: true, reason: "..."}`, the tool call is short-circuited with
   that reason. (Optional — many hooks just observe.)
3. The tool runs.
4. `post_skill` or `on_skill_error` fires *asynchronously* after — the
   dispatcher writes an `Event(kind="skill_event", source="hook:<skill>", ...)`
   into the bus, then returns control to the calling agent.
5. Subscribed agents drain those events on their own time, on their own
   inbox.

### The Event envelope for a hook fire

```python
Event(
    id          = str(uuid4()),
    kind        = "skill_event",
    source      = "hook:post_skill:linear.create_ticket",
    target      = Target(agent_name="audit_logger", thread_id=...),
    payload     = Payload(
        text    = "(hook auto-prompt — see below)",
        context = {
            "hook": {
                "on":             "post_skill",
                "skill":          "linear.create_ticket",
                "invoking_agent": "triage_agent",
                "invoking_thread": "slack:C123:t456",
                "args":           {...},
                "result":         {...},
                "duration_ms":    142,
            }
        }
    ),
    created_at  = ...,
)
```

Same envelope as everything else. The hook context lives in `payload.context.hook`
where the prompt template can reference it.

### Thread strategy

By default a hook fires into a **new thread per event** — the audit_logger
agent doesn't accumulate context across unrelated tickets. Override with
`target.thread_strategy: persistent` if you want one ongoing thread (useful
for stateful agents like a learning critic that wants to remember).

---

## Hook subscription schema

Hooks are declared in the same `subscriptions:` block as other rules. The
discriminator is `trigger.kind: hook`.

```yaml
- id: <stable id>
  description: <optional one-liner>
  enabled: true

  trigger:
    kind: hook
    on: post_skill                   # required
    skill: linear.create_ticket      # exact match
    # OR
    skill_glob: "linear.*"           # glob match
    # OR
    skill_re: "^(linear|jira)\\..*"  # regex (rare)

    filter:                          # optional: subset by skill outputs
      result.success: true           # JSONPath-like; AND-of-equalities
      args.team: support

    invoking_agent_in:               # optional: only when these agents fire
      - triage_agent
      - scout_agent

    invoking_agent_not_in:           # optional: opposite
      - audit_logger                 # avoid loops

    rate_limit:                      # optional: 1 per ticket within window
      key: "{args.team}:{args.title}"
      window: 60s

  target:
    agent: audit_logger
    thread_strategy: per_event       # default; or `persistent`

  prompt: |
    Skill {hook.skill} just ran successfully on behalf of
    {hook.invoking_agent} (thread {hook.invoking_thread}).

    Arguments: {hook.args | tojson}
    Result:    {hook.result | tojson}

    Log this to the audit table with a 1-line summary.

  outcomes:
    - emit_to: audit_db_sink
```

The `filter` block is critical for keeping hook traffic low. Without it, a
chatty agent calling `linear.create_ticket` 50 times in an hour fires the
hook 50 times. With `filter: { result.priority: "high" }`, the hook fires
only for the noteworthy ones.

---

## Common patterns

### 1. Audit / observability

> *"Log every Linear ticket created by any agent to a central audit DB."*

```yaml
- id: audit-linear-tickets
  trigger: { kind: hook, on: post_skill, skill: linear.create_ticket }
  target:  { agent: audit_logger, thread_strategy: per_event }
  prompt:  "Log this ticket creation."
  outcomes: [{ emit_to: audit_db }]
```

### 2. Cross-team notification

> *"When any agent files a P0 ticket, ping the on-call channel."*

```yaml
- id: notify-p0-tickets
  trigger:
    kind: hook
    on: post_skill
    skill: linear.create_ticket
    filter: { args.priority: "p0" }
  target:  { agent: oncall_notifier }
  prompt:  "Announce this P0 ticket in #oncall with context."
  outcomes: [{ emit_to: oncall_slack }]
```

### 3. Approval gate (pre_skill)

> *"Before any agent sends an email to a customer, require human approval."*

```yaml
- id: gate-customer-email
  trigger:
    kind: hook
    on: pre_skill
    skill: email.send
    filter: { args.to_external: true }
  target:  { agent: approval_broker }
  prompt: |
    Agent {hook.invoking_agent} wants to send this email.
    Post to #email-approvals with approve / deny buttons.
    Return { approved: true } or { approved: false, reason: "..." }.
  outcomes: [{ emit_to: approvals_slack }]
  # Special: pre_skill hooks can abort the underlying call by returning
  # { abort: true, reason: "rejected by approver" }
```

When `on: pre_skill`, the hook is **awaited synchronously** by the tool
runtime. This is the one place where the bus is on the critical path of a
turn. Use sparingly — it adds latency.

### 4. Self-improvement / critic loop

> *"After every scout_agent run, have the critic_agent review and provide feedback."*

```yaml
- id: critic-after-scout
  trigger:
    kind: hook
    on: on_inbox_drain
    invoking_agent_in: [scout_agent]
  target:  { agent: critic_agent }
  prompt: |
    Review scout_agent's last turn. Was the answer good? If not, write
    a feedback note that will be appended to scout's next prompt.
  outcomes: [{ emit_to: scout_feedback_log }]
```

### 5. Cascading workflows

> *"When the triage_agent finishes any turn that filed a ticket, kick off the prep_doc_agent."*

```yaml
- id: prep-doc-after-triage
  trigger:
    kind: hook
    on: on_agent_emit
    invoking_agent_in: [triage_agent]
    filter: { emission_target.kind: sink, emission_target.name: linear }
  target:  { agent: prep_doc_agent }
  prompt:  "A new triage ticket was filed: {hook.payload.ticket_url}. Draft the prep doc."
  outcomes: [{ emit_to: docs_drive }]
```

That's a pipeline expressed entirely as a hook — no need to teach the
triage_agent about the prep_doc_agent.

---

## Hook + declarative config = composable system

You can build sophisticated behaviors by **stacking declarative subscriptions
and hooks**:

```
External trigger (push: email)
   ↓ subscription "support-email-triage"
triage_agent runs
   ↓ calls linear.create_ticket
   │   ⮕ HOOK "audit-linear-tickets" fires → audit_logger
   │   ⮕ HOOK "notify-p0-tickets" fires (if filter matches) → oncall_notifier
   ↓ replies to customer
   ↓ turn ends
       ⮕ HOOK "prep-doc-after-triage" fires → prep_doc_agent
                                                   ↓ calls drive.create_doc
                                                   │   ⮕ HOOK "audit-docs" fires → audit_logger
                                                   ↓ done
```

One external event triggers a chain of agentic work, with every step
auditable and each concern (audit, notification, downstream prep) in its
own declarative file. No agent had to know about any other.

---

## Avoiding hook loops

The hook system fires Events. Events go through the dispatcher. The agent
that consumes a hook event might itself call skills → which might fire more
hooks. Worst case: `audit_logger` writes to a Linear ticket somehow, which
triggers `audit-linear-tickets`, which writes another ticket, which...

Three guardrails:

1. **`invoking_agent_not_in: [audit_logger]`** — explicit exclusion. Most
   hooks should exclude themselves.
2. **`source.startswith("hook:")` check at the dispatcher** — track the
   "hook depth" of an event chain; reject events with depth > N (default 3).
   The dispatcher silently drops these and increments a metric.
3. **Rate limits in the hook config** — `rate_limit: { key, window }` collapses
   bursts.

In practice, loops are rare if hooks are written sensibly. The depth limit
is a safety net.

---

## Performance

Hooks are an extension of the existing bus, so the cost model is the same:

| Hook type | Cost per fire |
|---|---|
| `post_skill`, `on_skill_error`, `on_agent_emit`, `on_inbox_drain` | One `Event` allocation + one `inbox.put()`. Sub-millisecond. |
| `pre_skill` (synchronous, abortable) | One round-trip to the hook target agent. Adds the target agent's turn latency to the original tool call. **Use sparingly.** |

If you have N hooks matching one skill call, that's N Events written into N
inboxes. Fan-out scales linearly with hook count, not with consumer count.

---

## Hook lifecycle vs. subscription lifecycle

A hook subscription is a row in `subscriptions` like any other. It can be:

- Disabled (`enabled: false`) without deletion
- Hot-swapped via `cuga apply` (when defined in YAML)
- Created at runtime via the supervisor's `subscribe_hook` tool

Hooks don't have to be defined in YAML — they're equally valid via the
interactive mode:

> User: *"Whenever any agent files a Linear ticket marked P0, ping me on Slack."*
> Supervisor: *(calls `subscribe_hook(skill='linear.create_ticket', filter={args.priority: 'p0'}, target='anu_notifier', prompt='...')`)*
> *Done. You'll be pinged on P0 tickets.*

---

## Open questions

- **`pre_skill` ergonomics:** the abort path is powerful but adds latency.
  Should we limit `pre_skill` to specific high-stakes skills (declared in
  agent config) so it can't accidentally be applied to chatty tools?
- **Hook observability:** the explorer UI should have a "hooks fired" panel
  per skill call, showing the chain. Worth designing.
- **Replay:** can we replay a stored event chain through the same hook set
  to debug "why did this happen"? Probably yes once events are durable
  (Phase 5).
- **Hook tests:** how does a developer test "hook X should fire when Y
  happens"? Need a `cuga simulate` command that drives the bus without real
  external triggers.

None of these block the v1 design; they're "after we use it for a quarter,
what hurts" questions.

---

## What this unlocks (in events.md terms)

Re-reading events.md with hooks in mind, several utterances become much
cleaner:

> *"[sub+pub] When a customer emails support, classify; if bug → Linear, if sales → #sales."*

Today (without hooks): the triage agent has to know about both Linear and
Slack. With hooks: the triage agent just classifies and emits a tagged
event. Separate hooks handle the bug branch and the sales branch
independently.

> *"Every Monday, scout leads, draft emails, post the top 3 to Slack for human approval."*

Today: the scout agent has to know about the human-approval pattern. With
hooks: a `pre_skill` hook on `email.send` enforces approval globally —
*any* agent trying to send an email to an external recipient gets gated,
not just the scout.

> *"Watch this PR every 30 minutes — when CI turns green, merge it and stop."*

Today: explicit timed loop with state-diff. With hooks: a `post_skill` hook
on `github.poll_ci` could fire when `result.status == 'success'` AND the
loop could self-cancel via the same hook. Cleaner separation.

---

## Summary

| Trigger kind | What fires it | Already in core design? |
|---|---|---|
| `timed` | Cron / interval / delay | Yes (loops) |
| `push` | External webhook / gateway delivers | Yes (M4) |
| `pull` | CUGA polls a source + state-diff | Yes (M5) |
| **`hook`** | **An internal skill execution matches a standing intent** | **New — this doc** |

Hooks add the fourth kind. Everything else — Event envelope, Inbox,
Dispatcher, routing agent, declarative config — is unchanged. Hooks are an
adapter that turns internal lifecycle events into the same Event shape as
external ones.

Recommended milestone: **M3.5 or M4** — after `publish()` and webhooks
exist, hooks are a natural extension because the plumbing is identical.
Implementation is ~200 lines of Python (a `SkillHookDispatcher` wrapping
the tool runtime, plus the schema additions to subscriptions).
