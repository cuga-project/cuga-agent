# Agent 0 — build brief

**For:** the team building agent 0.
**From:** CUGA FLO.
**Status:** specification for a service that does not exist yet.

## 1. What you are building

**One A2A server.** CUGA FLO is the client. It reaches you for two different purposes, but
you expose a single endpoint and a single agent card.

You are the Excel capability in a business process. CUGA FLO drives a BPMN workflow; when a
step needs a spreadsheet touched, it delegates the whole step to you. Separately, when the
process reaches a branch and the route depends on what the user wants, it asks you to find
out which way they prefer.

**You do not need to build:** an MCP server, any understanding of BPMN, or any CUGA FLO data
structures. Text in, text out.

## 2. Wire contract

| | |
|---|---|
| Protocol | A2A over JSON-RPC |
| Method | `message/send` |
| Agent card | `GET /.well-known/agent-card.json` |
| SDK | `a2a-sdk` — the same package CUGA FLO's client uses |

CUGA FLO sends a `Message` with a single `TextPart`. **Reply with a plain `Message` carrying
your final answer** — that is the whole contract, both capabilities, every call.

Implement with `AgentExecutor` (`execute`, `cancel`), served via `DefaultRequestHandler` and
the SDK's JSON-RPC and agent-card routes.

## 3. The agent card

Skill `name` and `description` are **not documentation** — they are injected into the prompt
of the CUGA FLO agent deciding whether to call you. Vague descriptions produce an agent that
never calls you, or one that calls you for everything.

```jsonc
{
  "name": "agent0",
  "description": "Performs retrieve, update and function/macro operations on Excel spreadsheets, asking the user for any operation parameters the request does not supply and handling dialogs those operations raise. Separately, reports the user's preference between alternative routes when the process reaches a decision point.",
  "version": "1.0.0",
  "capabilities": { "streaming": false, "push_notifications": false },
  "skills": [
    {
      "id": "fulfill_task",
      "name": "Perform a spreadsheet operation",
      "description": "Carry out a requested operation on an Excel workbook. Three kinds are supported, and the request statement determines which applies: RETRIEVE — read a table, cells, or a column across a row range, and report that it was retrieved; UPDATE — write values into cells or a column for identified rows, answering any confirmation or validation dialogs raised along the way; RUN FUNCTION — execute a named function, macro or bot routine and report its outcome. The statement may also carry a 'user escalation:' block naming parameters you must obtain from the user before proceeding.",
      "tags": ["fulfill_task"],
      "examples": [
        "Produce the table for line item $line_item on sheet ALL ACCOUNTS 1H, returning columns Modeled $ USD, Current Validated $ USD, Adjustment $ USD, Final Validated $ USD by Coverage Name",
        "For row $row_key change the column Adjustment to value $new_value, responding to dialog messages $messages with dialog responses $dialog_chosen",
        "Run function validate and submit the budget interlock bot"
      ]
    },
    {
      "id": "elicit_user_preference",
      "name": "Ask the user which way the process should go",
      "description": "Obtain the user's preference between the alternative routes available at a decision point in the process. Explains the alternatives, asks follow-up questions where the answer is ambiguous, and reports which one the user prefers — without deciding on their behalf. Used only at branch points; it does not gather operation parameters.",
      "tags": ["consultation"],
      "examples": [
        "Ask the user to choose one of the outgoing flows: update again, run function",
        "Ask whether to repeat the update for another row or proceed to the budget interlock function"
      ]
    }
  ]
}
```

### One service, two capabilities — not two services

**Everything above is one server, one endpoint, one card, one `AgentExecutor`.** The two
skills are *advertised* capabilities, not separate services to stand up. Nothing in an A2A
request names a skill — `Message` has no skill field, and neither does `RequestContext` — so
every call lands in the same `execute()` and you route internally:

```
GET /.well-known/agent-card.json  ──▶  the card above (advertises what you can do)

POST message/send ────────────────▶  AgentExecutor.execute(context, event_queue)
                                        │  read metadata["variables"]["role"]  (§4)
                                        ├─ "fulfill_task"           → do the work
                                        └─ "elicit_user_preference" → ask the user, report
```

**`role` is mandatory and always sent.** CUGA FLO sets it on every call — there is no code
path that omits it — so you can dispatch on it directly rather than inferring from the text:

| Called for | `role` value | You should |
|---|---|---|
| a delegated task | `"fulfill_task"` | perform the spreadsheet operation |
| a routing consultation | `"elicit_user_preference"` | ask the user which way, and report |

If it is absent or holds anything else, that is a caller bug rather than a case to handle
gracefully — fail with a clear message naming what arrived, so it surfaces immediately.

### Both skills may talk to the user — about different things

This is the distinction most likely to be got wrong, because "ask the user" appears in both:

| | `fulfill_task` | `elicit_user_preference` |
|---|---|---|
| Asks the user for | **operation parameters** — which line item, row, value | **which route the process takes** at a branch point |
| Why it asks | its instruction carries a `user escalation:` block naming what is missing | that *is* the request; there is nothing else to it |
| Then it | performs the operation and reports what it did | reports the preference; nothing is changed |

So a request for a line item or a row value arrives as `fulfill_task`, never as
`elicit_user_preference`. Consultation is only ever *"which of these ways should we go?"*.

**You also dispatch the operation type yourself.** Retrieve, update and run-function are not
separate skills either: a delegated task sends its whole instruction, and consultation passes
the card text as a tool description. Skills are descriptive, never addressable. So the
operation type is carried by the request statement and resolved by you — CUGA FLO sends a
goal, not a typed call. The three kinds are spelled out in the description and `examples` so
that a model reading the card still knows the full range of what you can do.

Corollary: **your intent parsing is load-bearing.** A statement that does not clearly indicate
retrieve, update or run-function should fail per §7 rather than be guessed at.

### Worked input and output

**`fulfill_task`** — you receive the task's instruction **verbatim**, escalation block
included, so you can code against exactly what arrives:

```
Produce the table for line item $line_item on sheet ALL ACCOUNTS 1H. It should return
the four columns Modeled $ USD, Current Validated $ USD, Adjustment $ USD, Final
Validated $ USD and the corresponding rows from the row label column Coverage Name.

user escalation: Which line item ($line_item)?
where:
$line_item : The identifier of the table (line item) to retrieve from the sheet.
```

You ask the user for `$line_item`, perform the retrieval, and reply with the **outcome**:

```
Retrieved line item 'Cloud Platform' from ALL ACCOUNTS 1H — 14 rows.
```

CUGA FLO needs to know the step succeeded, not what the data was. It does not parse your
reply for values, and nothing downstream consumes them. Keep it to one line that reads
sensibly in a process trace; do not return tables or row dumps.

**`elicit_user_preference`** — you receive a question the calling agent composed from what
the process needs to find out. It is close to the authored wording but not guaranteed
identical, so parse for intent rather than matching text:

```
Choose the outgoing flow based on the user's input.

user escalation: Ask user to choose any one of the possible outgoing flows:
update again, run function
```

You interview the user and report the preference — **never the routing decision**:

```
User prefers 'run function' over another update round.
```

## 4. Telling the two capabilities apart

Both arrive at the same `execute()` as free text. **The discriminator is in the request
metadata**, at `MessageSendParams.metadata`:

```jsonc
{ "variables": { "role": "fulfill_task" } }     // or "elicit_user_preference"
```

**This is not `Message.role`.** The name collision is an easy trap, and reaching for the
wrong one fails silently rather than loudly:

| | `Message.role` | our `role` |
|---|---|---|
| **What** | an A2A protocol field | our own convention, not part of A2A |
| **Values** | `ROLE_USER` / `ROLE_AGENT` | `fulfill_task` / `elicit_user_preference` |
| **Means** | *who is speaking* — client or agent | *which capability* is wanted |
| **Where** | on the `Message` | on `MessageSendParams.metadata`, one level up |

CUGA FLO sets `Message.role` to `user` on **every** call, so it distinguishes nothing.

Our `role` is **required**, exactly one of `fulfill_task` or `elicit_user_preference`, and
present on every request. Read it and dispatch; do not guess from the text.

**Both capabilities can involve talking to a person** — see §3, "Both skills may talk to the
user". What separates them is *what is asked* and *what results*: `fulfill_task` asks for
missing operation parameters and ends with work performed on the spreadsheet;
`elicit_user_preference` asks only which route to take and ends with a report, nothing
changed.

## 5. What each request looks like

§5.1–5.3 are all `fulfill_task` — one request type, three operations you distinguish from the
statement. §5.4–5.5 are `elicit_user_preference`.

### 5.1 Fulfilment may require asking the user first

A `fulfill_task` instruction can name parameters it does not supply, in a `user escalation:`
block. **Obtaining those from the user is your job**, before you can do the work:

```
for row $row_key change the column Adjustment to value $new_value, responding to
dialog messages $messages with dialog responses $dialog_chosen

user escalation: Which row ($row_key), value ($new_value), dialog responses
($dialog_chosen) and dialog messages ($messages)?
```

So fulfilment is not purely mechanical — it is *ask if the escalation block says to, then
act*. This is also why §7 says never to invent a parameter: an escalation block means ask,
and a missing parameter with no escalation block means fail.

### 5.2 Update the spreadsheet — including its dialogs
> For row 'Cloud Platform' change the column Adjustment to 45000.

Updates can raise **confirmation or validation dialogs**, and answering them is part of the
operation. The instruction may supply the expected messages and the responses to give
(`$messages`, `$dialog_chosen`); where it does not, report what appeared rather than
dismissing it.

Reply with the outcome, noting any dialog that appeared:
`Set Adjustment to 45,000 for row 'Cloud Platform'. One validation dialog ("Adjustment exceeds modeled by 12%") was confirmed.`

### 5.3 Retrieve from the spreadsheet
> Produce the table for line item 'Cloud Platform' on sheet ALL ACCOUNTS 1H.

Reply with the outcome, not the data:
`Retrieved line item 'Cloud Platform' from ALL ACCOUNTS 1H — 14 rows.`

### 5.4 Run a function
> Run function validate and submit the budget interlock bot.

A named function, macro or bot routine. Reply with the outcome, including failures:
`Budget interlock bot completed — validation passed, submission accepted (BI-2291).`

### 5.5 Consultation — routing preference
> Choose the outgoing flow based on the user's input.
> user escalation: Ask user to choose any one of the possible outgoing flows: update again, run function

**This is a conversation, not a form.** You may ask, clarify, and confirm:

> **agent0:** The update to 'Cloud Platform' is done. Would you like to update another row, or move on to running the budget interlock function?
> **User:** Just carry on.
> **agent0:** Running the function next, no further updates — correct?
> **User:** Correct.

Reply: `User prefers 'run function' over another update round.`

**Report the preference. Do not state a decision.** See §7.

### 5.6 Remembering parameters between calls — your choice

Every fulfil request carries its own `user escalation:` block, so the simplest agent0 is
**stateless**: ask for each named parameter every time it appears. That is always correct,
and a perfectly good first implementation.

You may instead be **stateful** — remember some parameters and skip re-asking when they
recur. We are not prescribing either. It is a judgement about how much repetition your users
will tolerate against how much staleness you are willing to risk, and you are better placed
to make it than we are.

**One constraint to know before choosing: we send no correlation identifier.** Each request
is an independent A2A message with a fresh `message_id`; `context_id` and `task_id` are not
set, and the metadata carries only `role`. Nothing on the wire tells you that two requests
belong to the same process run, or which run. So any memory has to key on something *you*
control — your own session with the user — rather than on anything in the request. If that
turns out to be the blocker, tell us: sending a stable identifier is a small change on our
side, and we would rather add it than have you infer one.

Two rules either way:

- **A stale remembered value is as bad as an invented one.** §7 forbids guessing a
  parameter; reusing one the user set for a different purpose is the same failure with extra
  steps. When you reuse, confirm rather than assume.
- **Do not carry values across a repeat.** The process can loop back and update again, and
  "again" normally means a different row and a different value. Re-ask on each pass.

## 6. Responses

**Free text. Always.** There is no structured-response contract: CUGA FLO's client discards
response metadata, so nothing you put there is read, and no schema is sent to you to fill in.
Write for a reader — the receiving agent parses prose perfectly well.

When the answer is uncertain, say so in the text — "the user was ambiguous; best reading is
X" is far more useful than a confident guess, because CUGA FLO can route on that.

## 7. Behavioural requirements

These are CUGA FLO's constraints, and they matter more than the plumbing.

**Report, don't rule.** You supply input to a decision; CUGA FLO makes it. On a routing
consultation, report *"user prefers to run the function"* — never *"run the function next"*.
The distinction keeps process authority inside the workflow, and it is the single thing most
likely to be got wrong.

**Never invent parameters — ask, or fail.** If an instruction lacks a line item, row or
value, do not guess and do not pick a default. Guessing writes wrong data into real
spreadsheets. Two cases:

- The instruction has a **`user escalation:` block** naming the parameter → **ask the user**
  for it, then proceed (§5.1).
- No escalation block covers it → **fail** with a clear message saying what was missing.

**Report the outcome, not the payload.** CUGA FLO records your reply in the process trace and
needs to know whether the step succeeded; it does not parse it for values, and no later step
consumes them. One clear sentence beats a table.

**Never dismiss a dialog silently.** A confirmation or validation dialog is information the
process may need. Answer it as instructed where `$dialog_chosen` is supplied, and always
report which dialogs appeared and how they were answered.

**Fail loudly and specifically.** "Sheet ALL ACCOUNTS 1H not found" and "budget interlock bot
raised: type mismatch on line 40" are both actionable. "Operation failed" is not.

## 8. Checklist

- [ ] `GET /.well-known/agent-card.json` returns the card in §3
- [ ] `message/send` accepts a text message and returns text
- [ ] Both capabilities are served by **one** `AgentExecutor.execute()` — no second service,
      no per-skill endpoint
- [ ] `fulfill_task` correctly dispatches all three operation types from the request
      statement alone
- [ ] `role` is read from `MessageSendParams.metadata` — **not** `Message.role`, which is
      always `user` — and dispatches to the right capability
- [ ] A missing or unrecognised `role` fails loudly rather than being guessed at
- [ ] An ambiguous operation type fails explicitly rather than defaulting
- [ ] A `user escalation:` block in a `fulfill_task` instruction triggers asking the user
- [ ] A missing parameter with **no** escalation block fails explicitly, never a guess
- [ ] Update dialogs are answered and reported, never silently dismissed
- [ ] Every reply is a plain `Message` carrying the final answer
- [ ] Interviews are short enough to complete, end to end, inside 120 seconds
- [ ] Consultation is treated as a routing question only — a request for an
      operation parameter arrives as `fulfill_task`, not as a consultation
- [ ] Fulfilment replies state the outcome in a line — no tables or row dumps
- [ ] Stateless or stateful is a deliberate choice (§5.6); if stateful, reused values
      are confirmed rather than assumed, and never carried across a repeat
- [ ] Consultation replies report preferences, never decisions
- [ ] Responses are plain text — nothing is returned in metadata
- [ ] Spreadsheet operations complete well inside 120 seconds
