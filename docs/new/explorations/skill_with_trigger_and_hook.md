# Skill + Trigger + Hook — end-to-end configuration

## Scenario

> "Install the **`support-triage`** skill. Use it whenever an email lands in `support@acme.com`. Also, whenever any agent using this skill marks a ticket as `urgent`, page me on Slack."

Three pieces:
- **Skill** — `support-triage` (bundle of prompt + tools the agent gets when handling support emails).
- **Trigger** — push, from `outlook://support@acme.com`, runs the skill-equipped agent.
- **Hook** — observes `linear.update_label(label="urgent")` calls made while the skill is active, fires a notify agent.

---

## Phase 1 — Install the skill (once)

### Step 1 — Drop the skill package on disk
- **What**: place `support-triage/` folder containing `SKILL.md`, optional scripts, manifest (lists tools the skill brings: `linear.create_issue`, `linear.update_label`, `gmail.fetch`).
- **Who**: User / admin (file copy or `cuga skills install support-triage`).
- **Component**: skills directory.

### Step 2 — Register the skill in the registry
- **What**: INSERT row into `skills` table with `name=support-triage`, manifest path, hash.
- **Who**: subscription manager (on startup scan) or `cuga skills install` CLI.
- **Component**: registry (`skills` table).

---

## Phase 2 — Configure the trigger (once)

### Step 3 — User types the utterance
- **What**: *"Whenever an email arrives at `support@acme.com`, use the `support-triage` skill to handle it."*
- **Who**: User.
- **Component**: chat UI.

### Step 4 — Routing agent registers the task
- **What**: classifies as `setup_standing`, calls `register_task(...)` which INSERTs:
  ```yaml
  id:           support-email-handler
  trigger:      { kind: push, channel: outlook://support@acme.com, event: message_received }
  target_agent: triage_agent
  skill:        support-triage          # ← skill reference
  prompt:       "Triage this email using the skill's playbook."
  ```
- **Who**: routing agent.
- **Component**: registry (`subscriptions`).

### Step 5 — Subscription manager wires the trigger
- **What**: tells Outlook adapter to watch `support@acme.com`; ensures `triage_agent` inbox exists; resolves `skill=support-triage` so the agent will load it at invocation.
- **Who**: subscription manager.
- **Component**: Outlook adapter (inbound face), agent inbox registry.

---

## Phase 3 — Configure the hook (once)

### Step 6 — User types the hook utterance
- **What**: *"Whenever any agent using the `support-triage` skill marks something urgent, page me on Slack."*
- **Who**: User.

### Step 7 — Routing agent registers the hook
- **What**: INSERTs:
  ```yaml
  id:           urgent-page
  trigger:
    kind:     hook
    channel:  skill://support-triage/linear.update_label
    filter:   args.label == "urgent"
  target_agent: notify_anu_agent
  outcomes:     [{ action: slack.post, channel: slack://acme-ws/@anu }]
  ```
- **Who**: routing agent.
- **Component**: registry (`subscriptions`).
- **Role**: hook channel scheme is `skill://<skill-or-agent>/<tool>`. The `filter` is evaluated against the tool args at hook time.

### Step 8 — Subscription manager arms the hook
- **What**: tells SkillHookDispatcher to start matching tool calls against this subscription.
- **Who**: subscription manager.
- **Component**: SkillHookDispatcher (the wrapper around the agent's tool-call boundary).

---

## Phase 4 — Runtime (every incoming email)

### Step 9 — Email arrives
- **What**: Customer emails `support@acme.com` with subject "URGENT: outage".
- **Component**: Outlook (external system).

### Step 10 — Outlook adapter (inbound) wakes
- **What**: held connection receives notification → builds POST body → `POST /events`.
- **Component**: Outlook adapter (inbound face).

### Step 11 — `/events` endpoint builds Event
- **What**: validates, builds Event `{ target: triage_agent, payload: {message_id, body, …} }`.
- **Component**: `POST /events` (FastAPI).

### Step 12 — Dispatcher → Inbox
- **What**: routes to `inboxes["triage_agent"]`.
- **Component**: Dispatcher, `triage_agent` inbox.

### Step 13 — Invoker dequeues
- **What**: acquires `lock[thread_id]`, calls `triage_agent(event)`.
- **Component**: Invoker.

### Step 14 — Agent loads the skill
- **What**: agent reads its config from registry, sees `skill=support-triage`, merges the skill's prompt and tools into its context.
- **Component**: agent fn + registry (`skills` table).
- **Role**: this is where skills *come into play* — they're loaded into the agent's working set at invocation time.

### Step 15 — Agent reasons and calls tools
- **What**: LLM call with skill-augmented context → decides to mark the ticket as `urgent`.
- **Component**: agent fn → Linear adapter (outbound face).

### Step 16 — SkillHookDispatcher intercepts
- **What**: as the agent calls `linear.update_label(label="urgent")`, the wrapper:
  1. checks subscriptions matching `skill://support-triage/linear.update_label`.
  2. evaluates filter `args.label == "urgent"` → matches.
  3. builds a hook Event `{ target: notify_anu_agent, payload: {tool_args, source_thread} }`.
  4. hands it to the **same Dispatcher**.
  5. then forwards the original tool call to the Linear adapter as if nothing happened.
- **Component**: SkillHookDispatcher, Dispatcher.
- **Role**: hooks *come into play* here — observed mid-turn, emitted as a normal Event.

### Step 17 — Hook Event flows the normal path
- **What**: Dispatcher → `inboxes["notify_anu_agent"]` → Invoker (separate task) → `notify_anu_agent(event)`.
- **Component**: same Dispatcher / inbox / Invoker as any other Event. No special-case path.

### Step 18 — notify_anu_agent runs outcome
- **What**: calls `slack.post(channel="slack://acme-ws/@anu", text="Urgent ticket marked: …")`.
- **Component**: Slack adapter (outbound face).

### Step 19 — Original `triage_agent` continues
- **What**: the original `linear.update_label` call completes (the hook didn't block it). triage_agent finishes the email, returns.
- **Component**: agent fn → Invoker (releases lock).

---

## Architecture pieces that came into play

| Component                    | Phase used in       | Role here                                                                 |
| ---------------------------- | ------------------- | ------------------------------------------------------------------------- |
| **Skills directory**         | install             | filesystem source of skill packages                                       |
| **Registry: `skills`**       | install             | catalog of installed skills                                               |
| **Registry: `subscriptions`**| trigger + hook setup| stores the two rows (one trigger, one hook)                               |
| **Registry: `channels`**     | setup               | Outlook + Slack channel rows with credentials                             |
| **Subscription manager**     | every setup step    | arms producers, wires hooks, ensures inboxes exist                        |
| **Outlook adapter (in)**     | runtime             | bridges Outlook → `POST /events`                                          |
| **`POST /events`**           | runtime             | single ingress for push triggers                                          |
| **Dispatcher**               | runtime ×2          | routes the trigger Event AND the hook Event                               |
| **Inboxes**                  | runtime ×2          | `triage_agent` (trigger), `notify_anu_agent` (hook)                       |
| **Invoker**                  | runtime ×2          | runs both agents (separate invocations)                                   |
| **Agent fn (`triage_agent`)**| runtime             | loads skill, reasons, calls tools                                         |
| **SkillHookDispatcher**      | runtime             | intercepts tool calls, emits hook Events, forwards original call          |
| **Agent fn (`notify_anu`)**  | runtime             | the hook-triggered agent                                                  |
| **Linear adapter (out)**     | runtime             | actually marks the ticket urgent                                          |
| **Slack adapter (out)**      | runtime             | sends the notification                                                    |

---

## What unifies this

- Skills, triggers, and hooks all configure via **subscriptions table** rows (+ a `skills` table for installed packages).
- The trigger is a normal **push producer** event; the hook is a normal **hook producer** event — same Event shape, same Dispatcher, same inboxes, same Invoker.
- Skills inject prompt + tools into the agent at invocation time. They don't fire events themselves.
- Hooks observe tool calls and emit events without modifying the observed agent.
- The agent's outcome is unchanged from the binary diamond: write to a channel **or** emit an Event. The hook subscription's Event goes through the second exit (emit), but the original agent doesn't know.
