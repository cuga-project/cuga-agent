# Agent 0 — build brief

**For:** the team building agent 0.
**From:** CUGA FLO.
**Status:** specification for a service that does not exist yet.

## 1. What you are building

**One A2A server.** CUGA FLO is the client. It reaches you for two different purposes, but
you expose a single endpoint and a single agent card.

You are the Excel capability in a business process. CUGA FLO drives a BPMN workflow; when a
step needs a spreadsheet touched, it delegates to you. When it needs a detail only a human
knows, it asks you to find out.

**You do not need to build:** an MCP server, any understanding of BPMN, or any CUGA FLO data
structures. Text in, text out.

## 2. Wire contract

| | |
|---|---|
| Protocol | A2A over JSON-RPC |
| Method | `message/send` |
| Agent card | `GET /.well-known/agent-card.json` |
| SDK | `a2a-sdk` — the same package CUGA FLO's client uses |

CUGA FLO sends a `Message` with a single `TextPart`. Reply with a `Message`, or a `Task`
whose `history` contains messages — both are handled; the text is extracted either way.

Implement with `AgentExecutor` (`execute`, `cancel`), served via `DefaultRequestHandler` and
the SDK's JSON-RPC and agent-card routes.

## 3. The agent card

Skill `name` and `description` are **not documentation** — they are injected into the prompt
of the CUGA FLO agent deciding whether to call you. Vague descriptions produce an agent that
never calls you, or one that calls you for everything.

```jsonc
{
  "name": "agent0",
  "description": "Performs retrieve, update and function/macro operations on Excel spreadsheets, handles any dialogs those operations raise, and interviews the user to obtain operation parameters and process preferences.",
  "version": "1.0.0",
  "capabilities": { "streaming": false, "push_notifications": false },
  "skills": [
    {
      "id": "fulfill_task",
      "name": "Perform a spreadsheet operation",
      "description": "Carry out a requested operation on an Excel workbook. Three kinds are supported, and the request statement determines which applies: RETRIEVE — read a table, cells, or a column across a row range, and report the values; UPDATE — write values into cells or a column for identified rows, answering any confirmation or validation dialogs raised along the way; RUN FUNCTION — execute a named function, macro or bot routine and report its outcome. The statement may also carry a 'user escalation:' block naming parameters you must obtain from the user before proceeding.",
      "tags": ["fulfill_task"],
      "examples": [
        "Produce the table for line item $line_item on sheet ALL ACCOUNTS 1H, returning columns Modeled $ USD, Current Validated $ USD, Adjustment $ USD, Final Validated $ USD by Coverage Name",
        "For row $row_key change the column Adjustment to value $new_value, responding to dialog messages $messages with dialog responses $dialog_chosen",
        "Run function validate and submit the budget interlock bot"
      ]
    },
    {
      "id": "elicit_user_preference",
      "name": "Ask the user",
      "description": "Interview the user to obtain information the process does not have: which line item, row, column or value to act on, or which of several process options to take next. Explains the alternatives, asks follow-up questions where the answer is ambiguous, and reports what the user said without deciding on their behalf.",
      "tags": ["consultation"],
      "examples": [
        "Which line item should the retrieval cover?",
        "Ask the user to choose one of the outgoing flows: update again, run function"
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

If that `role` is missing, fall back to reading the text — but note the degradation is
**silent**, not an error, so it is worth logging when it happens.

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

You ask the user for `$line_item`, perform the retrieval, and reply:

```
Retrieved line item 'Cloud Platform' from ALL ACCOUNTS 1H: 14 rows by Coverage Name,
columns Modeled $ USD / Current Validated $ USD / Adjustment $ USD / Final Validated $ USD.
<table>
```

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
User prefers 'run function' over another update round. Stated explicitly and confirmed.
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

Treat our `role` as a hint with a fallback: if it is absent, infer from the text.

**Both capabilities can involve talking to a person** — see §5.1. What separates them is the
*deliverable*: `fulfill_task` ends with work performed on the spreadsheet, while
`elicit_user_preference` ends with a report of what the user said and nothing changed.

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

Reply with what changed *and* what the spreadsheet asked:
`Set Adjustment to 45,000 for row 'Cloud Platform'. One validation dialog appeared ("Adjustment exceeds modeled by 12%") and was confirmed. Final Validated $ USD recalculated to 418,000.`

### 5.3 Retrieve from the spreadsheet
> Produce the table for line item 'Cloud Platform' on sheet ALL ACCOUNTS 1H.

Reply with the values and enough context to be checkable:
`Line item 'Cloud Platform', sheet ALL ACCOUNTS 1H: 14 rows by Coverage Name across Modeled $ USD, Current Validated $ USD, Adjustment $ USD, Final Validated $ USD. <table>`

### 5.4 Run a function
> Run function validate and submit the budget interlock bot.

A named function, macro or bot routine. Reply with the outcome, including failures:
`Budget interlock bot: validation passed on 312 rows, submission accepted, reference BI-2291.`

### 5.5 Consultation — routing preference
> Choose the outgoing flow based on the user's input.
> user escalation: Ask user to choose any one of the possible outgoing flows: update again, run function

**This is a conversation, not a form.** You may ask, clarify, and confirm:

> **agent0:** The update to 'Cloud Platform' is done. Would you like to update another row, or move on to running the budget interlock function?
> **User:** Just carry on.
> **agent0:** Running the function next, no further updates — correct?
> **User:** Correct.

Reply: `User prefers 'run function' over another update round. Stated explicitly and confirmed.`

**Report the preference. Do not state a decision.** See §7.

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

**Answer fast, or return `input-required`.** Your reply is awaited inside a blocking call from
a workflow engine, under a **120-second ceiling**. Exceeding it fails the process instance —
there is no retry. Spreadsheet operations should finish well inside that. **A conversation
with a human will not.** When you need to wait on a person, return A2A state
`input-required` immediately rather than holding the connection open.

**Be safe to repeat.** The process explicitly supports repeating an update, and a timeout may
cause a request to be re-sent. Make writes idempotent where you can, and always report what
you actually changed so CUGA FLO can detect a double application.

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
      always `user` — with text-based inference as fallback
- [ ] An ambiguous operation type fails explicitly rather than defaulting
- [ ] A `user escalation:` block in a `fulfill_task` instruction triggers asking the user
- [ ] A missing parameter with **no** escalation block fails explicitly, never a guess
- [ ] Update dialogs are answered and reported, never silently dismissed
- [ ] Human interaction returns `input-required` rather than blocking
- [ ] Consultation replies report preferences, never decisions
- [ ] Responses are plain text — nothing is returned in metadata
- [ ] Spreadsheet operations complete well inside 120 seconds
