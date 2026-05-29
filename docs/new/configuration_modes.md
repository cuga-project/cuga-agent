# Configuration Modes for Event-Driven CUGA

> **First time here?** Read [design_doc.md](design_doc.md) for the runtime
> mechanics — the agent loop is parked on its Inbox; producers wake it via
> the Dispatcher. The configuration story below sits on top of that.

How a standing intent gets created. **Two architectures, three surfaces.**

- **Architecture A — the existing one.** Carries two configuration surfaces:
  - **Mode 1 (Interactive):** user chats with the CUGA routing agent
  - **Mode 2 (Declarative):** engineer writes `rules.yaml`, runs `cuga apply`

  Both produce identical rows in the registry. Identical runtime path.
  Declarative is a **UX/tooling addition**, not a new architecture.

- **Architecture B — the new one.** Carries one surface:
  - **Mode 3 (Hooks):** subscriptions that fire when an internal skill
    executes (not on an external event)

  Hooks need a new producer (`SkillHookDispatcher` wrapping the agent's
  tool runtime), a new trigger kind, and a new event kind. **This is the
  only architectural addition** in the configuration story.

For the architecture-level summary and the implementation order, see
[configuration_architecture.md](configuration_architecture.md). The rest of
this doc explains how the three surfaces coexist day to day.

---

## The three surfaces at a glance

| Surface | Architecture | Who writes the standing intent | When it's set up | Audience |
|---|---|---|---|---|
| **Interactive** *(existing)* | A — existing | User → CUGA routing agent → setup tools | Synchronous chat turn, ad-hoc | End users, exploratory work |
| **Declarative** *(new tooling, same architecture A)* | A — existing | A `rules.yaml` (or `.json`) file in a repo, applied via CLI | At deploy / `cuga apply` time | DevOps, engineering, production rules |
| **Hook-based** *(new architecture B)* | B — new | A standing intent that fires *when an existing skill runs*, not on an external trigger | At rule-registration time; reacts to internal events | Cross-cutting concerns: audit, notifications, escalation |

All three end up as **rows in the same registry** (`subscriptions`, `agents`,
`pub_sinks`). The mode is just the surface through which a human or a config
file expresses the standing intent. **What's architecturally different is hooks** —
they add a new producer (`SkillHookDispatcher`) that turns internal skill
executions into Events. Interactive and declarative do not need this.

---

## Why we need more than the interactive mode

The supervisor + natural-language mode is great for:

- *"Every 10 days, find me a fresh lead."* — quick, conversational, one standing intent at a time
- Discovery — users don't need to know the schema
- Personal rules that belong to one user

But it falls short for:

- **Bulk setup** at deploy time. Imagine you're spinning up a CUGA tenant for a new customer with 40 standing intents. You don't want to type 40 chat messages.
- **Version control & review.** If a standing intent changes behavior, you want a PR, not a chat log buried in a database.
- **Reproducibility.** Re-applying the same YAML on a fresh CUGA install should give an identical system.
- **Cross-team contracts.** Engineering owns "when CI fails, page oncall." That belongs in a Git-tracked file, not a CUGA user's chat history.
- **Cross-cutting reactions.** *"Whenever any agent calls `linear.create_ticket`, log to audit."* — that's not a trigger the user invokes; it's a hook on existing skill execution. The interactive supervisor can't naturally express it.

The two new modes plug those gaps without rewriting the core. Same Event,
same Inbox, same routing agent for runtime intelligence — just different
surfaces for *creating* rules.

---

## How they coexist

```
                ┌──────────────────────────────────────────────┐
                │   subscriptions  +  agents  +  pub_sinks     │
                │           (SQLite registry)                  │
                └─────────────▲────────────▲────────────▲──────┘
                              │            │            │
        interactive setup ────┘            │            └──── declarative apply
        (CUGA routing agent                │                  (cuga apply rules.yaml)
         calls setup tools                 │
         after user utterance)             │
                                           │
                              hook-based subscription
                              (standing intent triggered by an
                               internal skill_event,
                               registered via either of
                               the other two modes)
```

The runtime path (when a trigger fires) is **identical** for all three. The
distinction is purely about *who created the row*.

---

## Decision guide

**Use interactive when:**
- A single user is setting up a personal standing intent
- The standing intent is exploratory or short-lived
- You don't yet know the exact wording — you want the supervisor to figure out the target_agent

**Use declarative when:**
- You're deploying a tenant or bootstrapping production
- The standing intent is owned by a team, not a person
- You want it under version control with code review
- You're going to have more than 5 of them

**Use hook-based when:**
- The trigger is "*some other agent just did X*" rather than "*external event happened*"
- You're adding cross-cutting behavior (audit, notification, escalation) without modifying the agent that does the work
- You want plugin-style extensibility — new hooks can be added later without rewiring existing agents

You can mix freely. A tenant could have:
- 30 declarative rules from `rules.yaml` (the baseline)
- 5 user-added interactive rules (personal automations)
- 4 hook-based rules cutting across both (audit, escalation, metrics)

All in the same `subscriptions` table, all routed by the same dispatcher.

---

## What's in this folder

| File | What it is |
|---|---|
| [design_doc.md](design_doc.md) | **Read first.** Runtime mechanics in plain English: what's always running, who builds Events, what wakes the agent. The vocabulary for everything else. |
| [runtime_mechanics.png](runtime_mechanics.png) | Visual companion to `design_doc.md` — process boundary, always-running tasks with STATE: badges, dispatcher, inboxes, wake chain. |
| [intent_classification.md](intent_classification.md) | **The first decision CUGA makes** when any utterance arrives: one-shot sync vs Set-up Stage vs modify-standing vs query-standings. |
| [configuration_architecture.md](configuration_architecture.md) | The architecture-level framing: which mode needs new architecture and which doesn't. |
| [configuration_modes.md](configuration_modes.md) | This file — the day-to-day overview of the three surfaces |
| [declarative_config.md](declarative_config.md) | Full design for YAML/JSON declarative configuration (Mode 2) |
| [skill_hooks.md](skill_hooks.md) | Full design for skill-execution hooks (Mode 3 — the new architecture) |
| [unified_architecture_part1.png](unified_architecture_part1.png) | **Part 1 — Architecture A.** Existing event-driven CUGA, with STATE: annotations on always-running tasks. |
| [unified_architecture_part2.png](unified_architecture_part2.png) | **Part 2 — A + additive Route B.** Same picture + three purple-dashed additions (Mode 3 surface, SkillHookDispatcher, new edges). |
| [flow_existing_architecture.png](flow_existing_architecture.png) | Two setup surfaces → same registry → one runtime. Drill-down for Modes 1 + 2. |
| [flow_mode3_hook.png](flow_mode3_hook.png) | Hook registration + wrapped tool runtime + hook event in the bus. Drill-down for Mode 3. |
| [producers_and_processors.png](producers_and_processors.png) | Reference: six producer types, the bus internals, four processor types. |

This folder is a self-contained extension proposal. It references the
existing design package but does not modify it.
