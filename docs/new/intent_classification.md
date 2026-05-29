# Intent Classification — How CUGA Routes Incoming Utterances

The first decision CUGA makes when any utterance arrives. Before picking
*which specialist agent* to delegate to, the routing agent picks *which
mode*: one-shot sync work, set-up of a standing intent, modification of an existing standing intent, or a query about standing intents.

> **First time here?** Read [design_doc.md](design_doc.md) for the runtime
> mechanics. This doc adds CUGA's *first* turn — the classifier — on top
> of that.

---

## TL;DR

```
USER utterance arrives
     │
     ▼
CUGA Routing Agent — INTENT CLASSIFICATION  ◄── new first step
   match utterance against:
     one_shot | setup_standing | modify_standing | query_standings
     │
     ├─► one_shot      → regular sync CUGA call
     │                   delegate_to_specialist, run turn, return answer
     │
     ├─► setup_standing    → Set-up Stage
     │                   parse trigger / target / outcomes
     │                   delegate_to_* picks target_agent
     │                   INSERT subscription row · confirm to user
     │
     ├─► modify_standing   → fetch subscription, UPSERT / DELETE, confirm
     │
     └─► query_standings   → read registry, format, reply
```

Same routing agent. Same `delegate_to_*` mechanism. Same registry. The
classifier just adds a quick *"which lane?"* turn before the existing
behavior runs.

---

## The four intents

| Intent | Example utterances | What CUGA does after classification |
|---|---|---|
| **`one_shot`** | "Find me a hotel in Paris" · "Summarize this doc" · "What's the weather?" · "Send an email to John" | Regular sync CUGA call. `delegate_to_specialist`, run a normal turn, return the answer. Nothing written to the registry. |
| **`setup_standing`** | "Every Monday 9am post HN digest" · "When a customer emails support, file a Linear ticket" · "Watch the OpenAI changelog every 6h" | **Set-up Stage.** Parse trigger/target/prompt/outcomes; routing agent's `delegate_to_*` picks the right `target_agent`; INSERT subscription, agent, and pub_sink rows; confirm back. **At Run-time**, the actual work happens later when the trigger fires. |
| **`modify_standing`** | "Stop the Monday digest" · "Change my arxiv watch to daily" · "Pause the support triage rule" | Find the matching subscription row, UPSERT or DELETE, re-arm trigger if needed, confirm. |
| **`query_standings`** | "What loops do I have running?" · "Which rules are arming the audit logger?" · "What's scheduled today?" | Read registry, format the matching rows, reply inline. No state change. |

The `one_shot` path **is** today's CUGA — it's the regular synchronous
flow. The other three intents land in the event-driven world.

---

## Signals the classifier looks for

The classifier is a single LLM turn with a structured-output prompt. It
looks for these signals:

### Strong signals for `setup_standing`

| Signal category | Words / phrases |
|---|---|
| **Temporal recurrence** | every, daily, weekly, hourly, monthly, each, every Monday, every 6h, twice a day |
| **Reactive conditional** | when, whenever, if X happens, on every, watch for, monitor, alert me when |
| **Forever-implication** | from now on, going forward, always, never miss |
| **Trigger-source nouns** | email, webhook, mention, message, ticket, PR, push notification (paired with an action) |
| **One-shot delay** | in 2 hours, after this finishes, tomorrow morning |

### Strong signals for `one_shot`

- Imperative-now verbs: *find me*, *show me*, *summarize*, *translate*, *generate*
- No temporal qualifier
- Past or present continuous: *what's*, *who is*, *how do I*

### Strong signals for `modify_standing`

- Reference to an existing standing intent by name, ID, or unambiguous description
- Verbs: stop, pause, resume, delete, change, update, edit, cancel

### Strong signals for `query_standings`

- Question words about subscriptions: *what loops*, *which rules*, *what's scheduled*, *list my…*
- No action verb implying new work

---

## The ambiguous cases (and the right behavior)

Three classes of utterance trip up pure pattern matching. The classifier
should **ask the user** when confidence is low (< 0.8).

### Case 1 — "Find me a lead today"

Looks like temporal language but means **one-shot, constrained to today**.

> CUGA: *"Did you want me to find a lead now (one-shot), or set up a daily
> rule to find a fresh lead each day?"*

### Case 2 — "Send an email when this finishes"

"When" can be a within-turn condition OR a standing-intent trigger.

> CUGA: *"Got it. Should I just send the email once after this current
> task finishes? Or set up a standing intent that sends an email every time this
> task type completes from now on?"*

### Case 3 — "I want to track competitor pricing"

Vague — could be a one-shot lookup OR a standing watch.

> CUGA: *"Should I pull pricing once now, or set up a daily watch?"*

The principle: **a single LLM disambiguation question is much cheaper than
incorrectly setting up a recurring standing intent that fires 52 times unintentionally.**

---

## Decision rules in pseudo-code

```python
async def classify(utterance: str) -> Classification:
    """The routing agent's first turn — before delegate_to_* runs."""
    result = await llm.structured_call(
        model="haiku-4.5",           # tiny model is enough
        prompt=CLASSIFIER_PROMPT,
        utterance=utterance,
        schema=ClassificationSchema,
    )

    if result.confidence < 0.8:
        # Don't proceed silently — ask the user
        return Classification(
            intent="needs_disambiguation",
            disambiguation_question=result.suggested_question,
        )

    return result   # {intent, confidence, optional intent_id, ...}


async def handle(utterance: str):
    c = await classify(utterance)

    match c.intent:
        case "one_shot":
            return await regular_cuga_flow(utterance)   # today's behavior

        case "setup_standing":
            # Set-up Stage begins here
            target = await routing_agent.delegate_to(utterance)
            sub    = parse_subscription(utterance, target)
            await registry.insert_subscription(sub)
            return f"Done. {sub.summary()}"

        case "modify_standing":
            sub = await find_subscription(c.intent_id, utterance)
            return await modify(sub, utterance)

        case "query_standings":
            return await list_rules(filter=c.filter)

        case "needs_disambiguation":
            return ask_user(c.disambiguation_question)
```

---

## Where this sits in the architecture

```
Set-up Stage
   ▲
   │
   │ (this branch)
   │
   │      ╔════════════════════════════════════════╗
   │      ║ CUGA Routing Agent                     ║
USER ────►║   1. INTENT CLASSIFICATION  ◄─── new   ║───► one_shot ────► Run-time
          ║   2. delegate_to_* (if needed)         ║      (today's CUGA)
          ║   3. call setup tools (if setup_standing)  ║
          ╚════════════════════════════════════════╝
```

**The classifier is step 1 of the routing agent's first turn.** It's not a
separate component; it's a prompt-level discriminator on the LLM that
already does `delegate_to_*`. Same model, same context, two decisions in
sequence (or one call with both decisions returned together).

This is why the classifier doesn't add new architecture — it's tooling
inside the routing agent.

---

## Implementation cost

- **Prompt + structured output schema**: ~150 LOC including tests
- **Disambiguation dialog state machine**: ~100 LOC (handle the
  "user answered the question, now finish classification")
- **Two new setup tools**: `delete_subscription`, `pause_subscription` for
  the `modify_standing` branch (≈30 LOC each)
- **Query tool**: `list_subscriptions(filter)` (~50 LOC) — likely already
  exists as `list_standing_intents`

**Total: ~400 LOC.** No runtime changes. Drops into Phase 1 (declarative
tooling) alongside the `cuga apply` work because both touch the
registry-config layer.

---

## How this affects the configuration story

The classifier is the **entry point that ties regular CUGA and
event-driven CUGA into one user-facing experience**:

| User-facing claim | How it works under the hood |
|---|---|
| *"Just talk to CUGA the same way you always have."* | The classifier accepts the same kinds of utterances; routes the one-shot ones to the existing sync flow. |
| *"CUGA learns standing intents from natural language."* | `setup_standing` intent → Set-up Stage → routing agent picks target → registry write. |
| *"CUGA can pause, modify, or list any standing intent you set."* | `modify_standing` and `query_standings` intents handle these without you remembering rule IDs. |
| *"You never have to learn a new command."* | The same routing agent's `delegate_to_*` mechanism handles all four intents; the classifier just decides which lane to enter. |

There are still two architectures (A — existing, B — additive for hooks).
The classifier sits at the entrance to Route A's Set-up Stage. Route B
(hooks) doesn't need it — hooks are set up the same way regular standing
rules are.

---

## See also

- [design_doc.md](design_doc.md) — runtime mechanics (after classification finishes)
- [configuration_architecture.md](configuration_architecture.md) — two-architecture framing
- [configuration_modes.md](configuration_modes.md) — three setup surfaces (interactive uses this classifier; declarative bypasses it)
- [declarative_config.md](declarative_config.md) — Mode 2: YAML setup that skips the classifier entirely (engineer already decided)
