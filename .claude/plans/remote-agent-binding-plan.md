# Generalised remote-agent binding for CUGA FLO

**Progress**

| Item | State |
|---|---|
| 1. Pydantic schemas | ✅ **Done and verified**, including the per-hook correction |
| 4a. `agent0-team-brief.md` | ✅ **Done** — all eight items applied; ready to send |
| 2, 3, 3a, 4, 5. Code | ✅ **Done** — `remote_agent.py`, flow_config wiring, audit trail, both wrappers, README. 9 checks pass in `test_remote_agent.py` |
| Demo app `excel_flows_kogito` | 🟡 Both blocking gaps closed (`workflow_engine:` added, `request_user_input` removed); YAML validates. Remaining: inert gateway `tools:`, empty policy files, placeholder agent0 URL |

Item 4b records which YAML field feeds each skill; it is the reference for implementing 2–4.

## Context

CUGA FLO's three wrapper agents each hold a locally-constructed `CugaAgent`. Two needs have
emerged that this cannot serve:

1. **Task fulfilment by a remote agent** — a task's work may belong to an external agent
   (a LangGraph service, a domain bot) rather than a local `CugaAgent`.
2. **Human-in-the-loop consultation** — a `DecisionAgent` routing a gateway, or the
   `FlowAgent` deliberating a hook, may need a preference or clarification that only a human
   can supply. A remote agent conducts that conversation and reports back.

Both must generalise to **any** remote agent reachable over A2A, named in the application
YAML — not a single hard-coded "agent 0".

**Authority moves in one case and stays put in the other** — that asymmetry is the design.

- **Task fulfilment** — the work is delegated to the remote agent *in place of* the local one,
  and it becomes the authority for that fulfilment.
- **Consultation** — the remote agent is bound as a **tool** on the `DecisionAgent` or
  `FlowAgent`, which remain the authority: they reason over the replied text and conclude the
  routing or the hook themselves.

Either way the reply needs no CUGA FLO structure — it is either the task's own result, or
input to a decision made locally.

Supersedes the agent-0-specific draft, kept as history alongside this file at
[`agent0-binding-plan.md`](agent0-binding-plan.md).

The concrete brief for the first remote agent — Excel operations plus user consultation —
is [`agent0-team-brief.md`](agent0-team-brief.md), written to be sent to that team as-is.

**Where this gets demonstrated.** Not in `loan_approval`. A new application,
`excel_flows_kogito`, is built *after* the enhancement lands, and exercises both bindings
against agent 0 on the Kogito engine. The existing loan-approval apps are the **regression
gate** — they must keep behaving exactly as they do today. See "Demonstration app" below.

## Decisions taken

- `agent_type` on `TaskAgentConfig` — already declared, never read — is **activated** and may
  name a remote agent. Default `cuga_agent` keeps every existing app unchanged.
- New `human_consultation:` on `GatewayConfig` (per gateway) and `HookConfig` (per hook).
- New top-level `remote_agents:` block mapping name → address.
- Agent cards fetched **once and cached**, never per invocation.
- Unreachable endpoint: **hard fail for task delegation, soft degrade for consultation.**

## Target YAML

```yaml
remote_agents:
  agent_0:
    url: "http://localhost:9000"
    timeout: 90            # optional; see "Timeouts" below
    auth: {type: bearer, token: "..."}   # optional
  report_bot:                          # a second entry, to keep nothing
    url: "http://reports:9001"         # hard-coded to a single agent

flow:
  name: "Excel Flows"
  id: "excel_flows"
  agent_type: cuga_agent          # existing — the FlowAgent's own reasoning agent

tasks:
  - id: "Activity_update"
    mode: task_agent
    agent:
      name: update_spreadsheet
      system_instruction: "..."
      agent_type: agent_0         # EXISTING field, now read. Omit -> cuga_agent
      tools: []

gateways:
  Gateway_repeat_or_macro:
    mode: decision_agent
    policy: "../policies/decision-repeat_or_macro.md"
    agent_type: cuga_agent        # existing field — the inner agent this
                                  # DecisionAgent uses to reason about routing
    human_consultation: agent_0   # NEW — remote agent bound as a tool for
                                  # this gateway only

hooks:
  - id: "Flow_before_update"
    type: edge
    location: "..."
    instruction: |                # NEW — what this hook is for, plus its
      Confirm the adjustment is within tolerance.
      user escalation: Ask whether to proceed given the variance.
    human_consultation: agent_0   # NEW — bound as a tool for THIS hook only;
                                  # a hook needing no human binds nothing
```

**The two fields are orthogonal roles, and every level carries both:**

| Field | Answers | Bound as | Default |
|---|---|---|---|
| `agent_type` | who *reasons* | the wrapper's inner agent | `cuga_agent` |
| `human_consultation` | who is *consulted* | a tool on that inner agent | none |

- On `tasks[].agent` — `agent_type` may name a remote agent, replacing `CugaAgent` outright,
  because the task's work *is* the delegation.
- On `gateways.<id>` and `hooks[]` — `human_consultation` adds a remote tool the local agent
  may call. **Routing and hook authority stay local by construction**: the remote agent
  supplies input, the local agent concludes. `agent_type` at the `flow:` and gateway levels
  stays restricted to local kinds, since naming a remote agent there would hand out that
  authority. A deliberate position — revisit only on an explicit decision.

**`human_consultation` sits on the element that reasons**, never above it. `DecisionAgent` is
already constructed **per gateway** (`flow_config.py:409`, each with its own lazy
`self._agent`); hooks get the same treatment, so each declares its own consultation rather
than inheriting one from the process. `FlowAgent`'s single `_hook_agent` must therefore become
one per hook — see 4b row 3.

## Files to change

### 1. `docs/examples/flow_agent_app_inline/schemas/app_yaml_schema.py` — ✅ DONE

Documentation-only — **nothing imports it** (confirmed: the sole reference is a prose pointer
in `flow_config.py:26`), so this changes no runtime behaviour on its own. The runtime still
ignores every field below until item 3 lands.

- ✅ New `RemoteAgentConfig`: `url: str`, `protocol: Literal["a2a"] = "a2a"`,
  `timeout: Optional[float]`, `auth: Optional[dict[str, str]]`.
- ✅ `AppYaml.remote_agents: dict[str, RemoteAgentConfig] = Field(default_factory=dict)`.
- ✅ `GatewayConfig` keeps `agent_type` and adds `human_consultation: Optional[str] = None`.
- ✅ `TaskAgentConfig.agent_type` widened to `str`; `GatewayConfig.agent_type` and
  `FlowBlock.agent_type` stay `AgentType`.

**✅ Corrected — consultation is per hook, not process-wide.**
`human_consultation` was briefly on `FlowBlock`; it now lives on **`HookConfig`**, so each
hook declares whether it consults, exactly as each gateway does:

- ✅ **Removed** `human_consultation` from `FlowBlock`. It keeps `agent_type` — that is the
  FlowAgent's own reasoning agent, which is genuinely process-wide.
- ✅ **Added** `human_consultation: Optional[str] = None` to `HookConfig`.
- ✅ **Added** `instruction: Optional[str] = None` to `HookConfig` — the hook's prose instruction
  and `user escalation:` block. `condition` (a guard expression), `message` (the
  static-fallback `HookResult` text) and `policy` (a markdown path) are all already spoken
  for, so a hook has nowhere else to state what it wants asked.

The result is one consistent rule across all three levels: **the element that reasons declares
its own consultation.** Task → `agent_type`, gateway → `human_consultation`, hook →
`human_consultation`.

**The authority boundary turned out to be enforced, not merely documented.** Because
`AgentType` stayed a `Literal`, `GatewayConfig(agent_type="agent_0")` raises a validation
error — a remote agent structurally cannot be declared as a gateway's decider. Verified
alongside the regression check that an existing app YAML with none of these fields still
validates with all new defaults inert.

### 2. `src/cuga/backend/cuga_graph/nodes/cuga_flow/remote_agent.py` (new)

The only genuinely new code, and it is thin because `a2a_protocol.py` already provides the
client. **Reuse, do not rebuild:**

- `fetch_agent_card(endpoint, auth, timeout)` — `a2a_protocol.py:89`
- `delegate_task_via_a2a_sdk(card, task, auth, timeout, variables)` → `{"result", "variables", "status"}` — `a2a_protocol.py:111`
- `_agent_card_description(card)` — `a2a_protocol.py:41`

Contents:

- `RemoteAgentRegistry` — holds the parsed `remote_agents` block; resolves a name to a card,
  fetching **once** and caching. Async, because `fetch_agent_card` is.
- `make_consultation_tool(name, registry) -> BaseTool` — a factory closing over the registry,
  since the card is not known at import time. Set `description` from
  `_agent_card_description(card)` rather than a literal docstring: the remote team then
  controls when their agent gets consulted without a CUGA FLO code change.
- `RemoteTaskExecutor` — satisfies `TaskAgent`'s duck-typed contract. `TaskAgent` calls
  `await self.agent.invoke(task_input)` (`task_agent.py:105`) and `_process_output` reads
  `output` or `content`, else `str(result)` (`task_agent.py:163`). Since
  `delegate_task_via_a2a_sdk` returns `result`, an unmapped dict would be stringified whole
  into the task output — **rename the key**:

```python
class RemoteTaskExecutor:  # ponytail: TaskAgent only calls invoke(); no base class needed
    async def invoke(self, task_input):
        card = await self._registry.card(self._name)          # cached
        out = await delegate_task_via_a2a_sdk(
            card, str(task_input), timeout=self._timeout,
            variables={"role": "fulfill_task"},               # see below
        )
        return {"output": out["result"]}
```

**Send a `role` discriminator on every call.** Both bindings reach the same remote endpoint
with free text, so the remote agent cannot otherwise tell a delegated task from a
consultation — and the two are answered by completely different means (its own tools versus
asking a human). Pass `variables={"role": "fulfill_task"}` from `RemoteTaskExecutor` and
`variables={"role": "elicit_user_preference"}` from the consultation tool.
`delegate_task_via_a2a_sdk` already forwards `variables` into
`MessageSendParams.metadata` (`a2a_protocol.py:130-131`), so this costs one argument per call
site — but it must actually be passed, and the team brief specifies the remote side reads it.

### 3. `src/cuga/backend/cuga_graph/nodes/cuga_flow/flow_config.py`

**Load (`__init__`, lines 30–49).** Add `self.remote_agents = config_dict.get("remote_agents", {})`
and build the registry. Unknown top-level keys are currently ignored silently — there is no
`extra="forbid"` — so adding the block breaks nothing.

**Validate names at load, reachability at first use.** `FlowConfig.__init__` and
`create_task_agents` are synchronous; `fetch_agent_card` is async. Rather than force an event
loop at import, split the two failure classes:

- **Name validation is synchronous and hard** — every `agent_type` / `human_consultation`
  value that is not a built-in must be a key of `remote_agents`, else raise at load. This
  catches typos, the common error.
- **Reachability is checked on first use and cached.** A dead endpoint surfaces at first
  consultation rather than at startup.

This deviates slightly from "fetch at config load" but preserves what that choice was for:
**exactly one fetch per process, never one per invocation** — unlike the supervisor, which
re-fetches every card on every invoke (`prepare_agents_and_prompt.py:85`).

**Task binding (`create_task_agents`, lines 200–215).** Read `agent_type`; when it names a
remote agent, inject `RemoteTaskExecutor` in place of `CugaAgent` — `TaskAgent` already
accepts an injected agent, so `task_agent.py` needs no change. Hard-fail an unreachable
delegate target: a task that cannot execute is a broken app.

Also fix the pre-existing bug on the same lines: `tools_config` is `list[str]` from YAML and
is passed straight into `CugaAgent(tools=...)`, which expects `List[BaseTool]`. This is not
silent — `DirectLangChainToolsProvider._validate_tools` raises
`ValueError("Tool at index {i} is not a valid LangChain tool")`
(`cuga_lite/providers/langchain.py:52-58`). It has never fired only because every shipped app
declares `tools: []`. Resolve names to tools before construction.

**Gateway binding (`create_decision_agents`, ~line 409).** Read `human_consultation` per
gateway; pass the resolved tool into the `DecisionAgent` constructor.

**The existing `try/except` will swallow the hard fail.** `create_task_agents` wraps each task
in a handler that logs and continues (`flow_config.py:200`, `except ... logger` at ~217). A
delegation failure raised inside it would be caught, leaving a task with **no agent bound and
no loud failure** — the opposite of the decision taken above. Let remote-binding errors
propagate, or re-raise them explicitly, rather than sharing the per-task handler.

**`a2a-sdk` is a soft dependency.** `a2a_protocol.py` gates its imports behind `HAS_A2A_SDK`
and raises `ImportError` when absent. Check it during name validation, so a missing package
fails the app at load rather than a running process instance.

### 3a. Audit — record what was consulted

If a remote agent's answer shaped a routing decision or a hook action, the trace must contain
it; otherwise the audit shows a conclusion whose basis lives only in another system's logs.
`FlowState` already carries `graph_modifications` and `task_results` — consultations belong
alongside, and should reach `ActivityTracker` so they appear in the UI trace (`DecisionAgent`
already tracks its routing decisions there, `decision_agent.py:257`).

This matters most for consultation, which is precisely where an external system influences the
process's structure while remaining invisible to it.

**Beware the re-parse.** `to_flow_agent()` → `bridge.load_flow()` → `ProcessRegistry.register_flow`
constructs a **second** `FlowConfig` from the path (`process_registry.py:55-61`), and
`FlowAgent.__init__` reads its bindings from *that* instance (`flow_agent.py:82-92`) — not the
one `to_flow_agent()` was called on. Consumers must therefore be **methods on `FlowConfig`**,
never state injected into an instance from outside, or they are dropped on re-parse.

### 4. `decision_agent.py` and `flow_agent.py`

Both create their `CugaAgent` lazily with `special_instructions` and `model` only, passing no
tools. Accept an optional tool list and forward it:

- `DecisionAgent.__init__` gains `consultation_tool=None`; `_get_agent()` (line 270) passes it
  as `tools=[...]`. Already per-gateway, so nothing else changes.
- `FlowAgent` reads each hook's `human_consultation` from its `Hook` and **caches one agent
  per hook**: `self._hook_agent` (line 99) becomes `self._hook_agents: Dict[str, CugaAgent]`,
  and `_get_hook_agent(hook)` (line 215) keys on `hook.id`, passing that hook's tool as
  `tools=[...]`. Both call sites (lines 300 and 388) already have the `hook` in scope.

Their result parsing is untouched — `DecisionAgent` still parses `<flow_id>|<reason>` and
`FlowAgent` still parses its `HookResult` JSON, both from their **own** local agent. The
remote agent is never on those paths, so it cannot emit a routing decision or a hook action.

Note the two call conventions differ: `DecisionAgent` uses `invoke(prompt)` positionally
(`decision_agent.py:227`), `FlowAgent` uses `invoke(message=prompt)` (`flow_agent.py:300`).

### 4b. What each skill is invoked with — the YAML → A2A mapping

Three invocations, from existing YAML fields. **They are not symmetric** — delegation bypasses
the local LLM, consultation goes through it:

| # | Skill | Input | Source | Caller |
|---|---|---|---|---|
| 1 | `fulfill_task` | the task's `system_instruction`, **verbatim** | `tasks[].agent.system_instruction` | `RemoteTaskExecutor`, via `agent_type: <remote>` |
| 2 | `elicit_user_preference` | a question the LLM composes from the gateway's `condition` | `gateways.<id>.condition` | `DecisionAgent`'s CugaAgent calling the tool, via gateway `human_consultation:` |
| 3 | `elicit_user_preference` | a question the LLM composes from the hook's **`instruction`** | `hooks[].instruction` — **a new field, see below** | `FlowAgent`'s hook agent calling the tool, via the hook's `human_consultation:` |

**Row 1 is literal.** `RemoteTaskExecutor.invoke()` forwards the instruction as the task string
— no local agent sits in between, so the `user escalation:` block reaches the remote agent
exactly as authored.

**Rows 2 and 3 pass through the local LLM**, because consultation is bound as a *tool*. The
agent decides whether to call it and fills in the `question` argument itself. The YAML field
is what the agent reasons *from*, not a payload copied to the wire.

**Row 3 — per hook, and it forces one code change.** `FlowAgent` holds a **single**
`_hook_agent` (`flow_agent.py:99`, built lazily at `:215`, used from both `:300` and `:388`)
shared by every hook. With consultation declared per hook, one shared agent would carry the
union of every hook's tools — a hook could reach a remote agent its own config never named.
So `_hook_agent` becomes a **dict keyed by hook id**, each entry built lazily with that hook's
own tool. The existing lazy-construction shape carries over unchanged; only the cache key is
new. `DecisionAgent` already works this way, one per gateway.

A hook's question comes from its *instruction*, the same way a task's comes from
`system_instruction` and a gateway's from `condition`. No existing field can carry it:

- **`condition`** is a guard expression evaluated *before* the hook fires
  (`_create_condition_function`) — it decides whether the hook runs, not what to ask.
- **`message`** is already taken: `flow_config.py:344` passes it to
  `_create_hook_handler(action, message)`, baking it into the **static fallback `HookResult`**
  used when no policy is present. Overloading it would collide with static hooks.
- **`policy`** is a *path* to markdown reasoning guidance, not an authored instruction, and
  the app's hooks would then need a policy file purely to hold one sentence.

So add **`instruction: Optional[str]`** to `HookConfig` — a name already established in this
schema by `TaskConfig.instruction`. It carries the hook's prose instruction plus its
`user escalation:` block, matching how every element in the demo app's BPMN documentation is
authored. The hook agent quotes it into the prompt alongside the policy text, and reaches for
the consultation tool when the escalation block calls for it.

**Confirm the key against the generator first.** These models come from an external tool —
`targetNamespace="http://mira-miner/export"` — which writes `instruction:` and
`user escalation:` into `<bpmn:documentation>` and from there into `agent.system_instruction`
(tasks) and `gateways.<id>.condition`. The demo app contains **no hooks**, so there is no
evidence of how that tool emits a hook's annotation. The generator is upstream of this
schema: if it already writes the hook instruction under some key, match it rather than
renaming. One export of a hook-annotated model settles it.

Each hook carries its **own** `human_consultation`, so a hook that needs no human input binds
no tool at all — the same granularity gateways already have.

**Settled: free-form.** `consult_user(question: str)` — the LLM composes the question, with
the YAML field quoted into its prompt so the exact wording is available to it. The
alternative (no argument, always send the configured text) was rejected as too rigid for a
tool the agent decides when to call.

**Fulfilment carries its own escalation contract.** A `system_instruction` states the work and
then names the parameters the remote agent must obtain from the user, in a `user escalation:`
block:

> Produce the table for line item `$line_item` on sheet ALL ACCOUNTS 1H …
> **user escalation:** Which line item (`$line_item`)?
> **where:** `$line_item` : The identifier of the table to retrieve from the sheet.

So **the remote agent owns that interaction** and replies once the task is done. This settles
the question raised under "Demonstration app": eliciting inside `fulfill_task` is intended,
not an accident of authoring. The consequence is that `RemoteTaskExecutor` needs no parameter
plumbing — but see the timeout note below, which now applies to fulfilment as well as
consultation.

**A consulting gateway drops node 1 entirely.** `DecisionAgent._build_graph` omits the
`eval_condition` node when `human_consultation:` is set: there is no expression to evaluate,
and the decide agent gets what it needs by calling `consult_user`. Without consultation the
original two-node path is unchanged. The gateway's `condition` is then prose describing what
to ask:

> Choose the outgoing flow based on the user's input.
> **user escalation:** Ask user to choose any one of the possible outgoing flows: update
> again, run function

The remote agent returns what the user preferred; the `DecisionAgent` then reasons with its
own local agent and picks from `flows:`. Prose is therefore the right content for a `condition`
on a gateway carrying `human_consultation:`, and it never reaches the analytic evaluator —
node 1 is not in the graph at all.

**Hooks keep the tool without the skip.** A hook's job is open-ended — assess the state and
choose among several structural actions — so whether a human needs asking is part of what its
policy decides. `FlowAgent` therefore keeps one agent per hook (`_hook_agents`, keyed by hook
id), since each may bind a different tool.

**Timeout consequence, now unavoidable.** Both calls can involve a human, so both run a
conversation inside a blocking control point under the 120s `CugaFlo.java` ceiling. This is no
longer a risk confined to consultation — it is the normal path for three of the four
interactions in the demo app, and the strongest argument yet for the parked BPMN user-task
work.

### 4a. `agent0-team-brief.md` — items 1–7 ✅ done, item 8 outstanding

The brief was written before the demo app existed and before structured schemas were ruled
out. Corrections 1–7 are applied and verified. **Item 8 — making clear that the two skills are
one service, not two — is still to do.**

| # | Brief currently says | Should say |
|---|---|---|
| 1 | §6: honour an `expected_output` schema and return matching JSON | **Free text only.** This plan puts structured schemas out of scope — there is no return channel. The brief instructs work we have decided not to send |
| 2 | "RUN MACRO — execute a named **VBA macro**" | The app's third task is `run_function`: *"run function validate and submit the budget interlock bot"*. Broaden to a named function, macro or bot routine |
| 3 | *(nothing)* | **Dialogs.** The app's update instruction says *"responding to dialog messages `$messages` with dialog responses `$dialog_chosen`"* — an update may raise confirmation or validation dialogs agent 0 must answer and report. A whole capability the team would not know to build |
| 4 | card `"name": "agent_0"` | The app's `remote_agents:` key is `agent0`. Align them to avoid two spellings in circulation |
| 5 | Examples use `Q3-loans.xlsx`, `Status`, `Amount` | The real domain: sheet `ALL ACCOUNTS 1H`, row labels in `Coverage Name`, the `Adjustment` column, line items, the budget interlock function. Concrete examples from the actual app are worth more than invented ones |

#1 is the one that matters most — it is a contradiction between two documents we are handing
out, not merely a stale detail. #3 is the largest omission by build effort.

**Plus two additions from 4b:**

**6 — Worked input/output examples on each card skill.** Add to the brief, using the real app
strings rather than invented ones, so the team can code against exactly what arrives:

```jsonc
// fulfill_task — input is the task's system_instruction, verbatim
"Produce the table for line item $line_item on sheet ALL ACCOUNTS 1H. It should return
 the four columns Modeled $ USD, Current Validated $ USD, Adjustment $ USD, Final
 Validated $ USD and the corresponding rows from the row label column Coverage Name.

 user escalation: Which line item ($line_item)?
 where:
 $line_item : The identifier of the table (line item) to retrieve from the sheet."

// -> agent 0 asks the user for $line_item, performs the retrieval, and replies:
"Retrieved line item 'Cloud Platform' from ALL ACCOUNTS 1H: 14 rows by Coverage Name,
 columns Modeled $ USD / Current Validated $ USD / Adjustment $ USD / Final Validated
 $ USD.  <table>"
```

```jsonc
// elicit_user_preference — input is the gateway's condition, verbatim
"Choose the outgoing flow based on the user's input.

 user escalation: Ask user to choose any one of the possible outgoing flows:
 update again, run function"

// -> agent 0 interviews the user and reports the preference, never the routing:
"User prefers 'run function' over another update round. Stated explicitly and confirmed."
```

**7 — Correct the role framing.** The brief currently implies fulfilment is answered purely
from the spreadsheet and only consultation talks to a human. Per 4b, `fulfill_task` **also**
interviews the user, for any parameter its `user escalation:` block names. §7's "never invent
parameters" stays — it just resolves to *ask*, not *fail*, when an escalation block says so.

### 8 — One service, two capabilities ✅ done

The brief lists two skills without saying how they are served, which reads as though the team
must build **two services**. They must not. Verified against the installed SDK:

- `AgentExecutor` has exactly two methods, `execute` and `cancel`.
- **No skill selector exists in the request.** `Message` carries `message_id`, `context_id`,
  `task_id`, `role`, `parts`, `metadata`, `extensions`, `reference_task_ids` — nothing names
  a skill. `RequestContext` exposes no skill either.

So every call lands in the same `execute()`, and the card **advertises** capability rather
than **addressing** it. Add to §3, right after the card JSON:

```
message/send ──▶ AgentExecutor.execute(context, event_queue)      ← one entry point
                   │
                   ├─ role == "fulfill_task"           → do the spreadsheet work
                   └─ role == "elicit_user_preference" → interview the user, report back
```

State plainly: one server, one endpoint, one card, one executor — two capabilities reached
through it, distinguished by the `role` metadata of §4. Also flag the failure mode: if `role`
is absent the agent falls back to reading the text, which degrades silently rather than
erroring.

Note `Message.role` is the A2A sender role (`user`/`agent`) and is **unrelated** — worth
saying so in the brief, since the name collision is an easy trap.

### 5. Docs

`agent0-binding-plan.md` sits alongside this file as history, already carrying a "superseded
by" pointer — it is not to be rewritten. What remains is a short section in
`cuga_flow/README.md` describing the `remote_agents:` block and the two binding fields, since
that README is the document someone reads before writing an app YAML.

## What a remote agent's team must provide

Hand this to whoever owns the remote agent. It applies to any of them, not one named agent.

**One A2A server. No MCP.** "As a tool" describes how CUGA FLO presents the agent to a local
`CugaAgent`; it is not a protocol the remote side speaks. Use `a2a-sdk` — the same package
CUGA's client already depends on, so both ends agree by construction.

1. **An agent card** — `name`, `description`, `version`, `url`, `capabilities`, `skills`.
   **The highest-leverage item.** The card's description becomes the tool description
   (`_agent_card_description`, `a2a_protocol.py:41`), which is the entire basis on which a
   `DecisionAgent` judges whether consulting is warranted. Vague card → an agent that never
   asks, or asks every time.
2. **Two capabilities, advertised as two skills — but one service:**
   - `fulfill_task` — perform a delegated subtask, return the result. Backs `agent_type:`.
   - `elicit_user_preference` — obtain a human's preference and report it. Backs
     `human_consultation:`.
3. **One `AgentExecutor`** (`execute`, `cancel`) wrapping their graph, served via
   `DefaultRequestHandler` and the SDK's JSON-RPC / agent-card routes.

**Both capabilities arrive at the same `execute()`.** Nothing in an A2A request names a skill
— `Message` has no skill field and neither does `RequestContext` — so the card *advertises*
capability rather than *addressing* it. CUGA FLO distinguishes the two with its own
discriminator:

```
message/send ──▶ AgentExecutor.execute(context, event_queue)      ← one entry point
                   │   reads MessageSendParams.metadata["variables"]["role"]
                   ├─ "fulfill_task"           → perform the work
                   └─ "elicit_user_preference" → interview the user, report back
```

**Not `Message.role`** — that is the A2A sender field, values `ROLE_USER` / `ROLE_AGENT`, and
`a2a_protocol.py:128` hardcodes it to `user` on every call, so it distinguishes nothing. The
name collision is an easy trap; say so explicitly.

**Behavioural requirements — these are CUGA FLO's constraints, not A2A's:**

- **Text in, text out.** No knowledge of BPMN, process variables, or CUGA FLO.
- **Send a goal, not a script.** CUGA FLO delegates *"find out the user's preference regarding
  this routing choice"*, not *"ask exactly this question and return exactly this field"*. That
  distinction is what makes this A2A delegation rather than MCP elicitation: the remote agent
  decides **how** to obtain the answer, and may explain options or ask clarifying questions.
- **Fulfilment may also need the user.** A task instruction can carry a `user escalation:`
  block naming parameters the remote agent must obtain before acting. Fulfilment is *ask if
  told to, then act* — not purely mechanical.
- **On consultation, report — don't rule.** Return preferences and facts, never verdicts:
  *"user prefers to run the function"*, not *"run the function next"*. This applies to
  consultation specifically. Under `agent_type:` delegation the remote agent **is** the
  authority for the work itself; it is only routing and hook decisions that stay local.
- **Reply with a plain `Message`, never a `Task`.** The client flattens a Task by joining
  every message in its `history`, so an interview would arrive as a whole transcript rather
  than a conclusion. Message-only also means there is no `input-required` to signal: the
  120s ceiling is the entire budget, with no park-and-resume.

## Timeouts — pass explicitly

`delegate_task_via_a2a_sdk` defaults to `timeout=30.0`, which sits **below** the ceilings it
runs inside. The chain, for Kogito:

| Hop | Limit | Source |
|---|---|---|
| Kogito script task → MCP bridge | **120s** | `CugaFlo.java:203` |
| CUGA → Kogito, whole process | 600s (`KOGITO_TIMEOUT`) | `kogito_proxy.py:69` |

A consultation or delegated task runs *inside* the 120s hop, and exceeding it makes
`CugaFlo.java` rethrow — the script task throws and the **process instance fails**, with no
CONTINUE fallback. Take the per-agent `timeout` from `remote_agents`, defaulting to something
under the 120s ceiling.

**Consequence worth stating plainly:** only fast, non-interactive consultation fits in-line.
A genuine human conversation cannot run inside a blocking control point at any timeout value —
it needs a modelled BPMN user task where the engine owns the wait
(`KogitoProxy.complete_task` exists; nothing demonstrates it yet). **That user-task path is
out of scope here** and is the larger follow-on piece. Until it exists there is no
park-and-resume at all: remote agents reply with a plain `Message`, which carries no task
state to park, so the 120s ceiling is the whole budget.

## Demonstration app — `excel_flows_kogito`

**The app now exists** at `docs/examples/flow_agent_app_inline/excel_flows_kogito/`, authored
separately. Process: retrieve → update → gateway `consult user` → {update again | run
function} → end.

### What is already correct

- **Both BPMN models present**, correctly named: `excel_flows_kogito.bpmn` (clean, referenced
  by `flow.bpmn_file`) and `excel_flows_kogito-kogito.bpmn` (ported).
- **The ported model is properly wired** — `drools:packageName="org.cuga"`,
  `isExecutable="true"`, both `drools:import`s, the infra properties (`cugaProcessKey`,
  `cugaMcpUrl`, `_user_message`, `_hookAction`, `_haltReason`, `gatewayDecision`), three
  `CugaFlo.executeTask` calls, one `routeGateway`, and `completeProcess`. Script-task IDs
  match the YAML task IDs and the gateway ID exactly.
- **It already uses the new schema fields**: `remote_agents: {agent0: ...}`,
  `agent_type: agent0` on all three tasks, and `human_consultation: agent0` on the gateway —
  the exact pair this plan introduces.

### Gaps to close before it can run

| # | Gap | Effect |
|---|---|---|
| 1 | **No `workflow_engine:` block** | `type` defaults to `langgraph` — the app silently does *not* use Kogito |
| 2 | **`request_user_input` in `permitted_actions`** | Not a `HookAction`; the YAML **fails schema validation**, and the string appears nowhere in `src/` |
| 3 | `tools: [decision-eval]` on the gateway | `GatewayConfig` has no `tools` field; pydantic ignores it silently and nothing resolves the name |
| 4 | All four policy files are **0 bytes** | Task and gateway policies load empty. Harmless for the delegated tasks, which carry their instruction in the YAML; the gateway still has its `condition` |

For #1, note the `process_id` must be **`n_playout`** — the `bpmn2:process` id in the ported
model — not `excel_flows`:

```yaml
workflow_engine:
  type: kogito
  url: http://localhost:8081
  process_id: n_playout
  callback_host: localhost
```

### Two things that look like gaps and are not

**`variables: {}` is correct.** The `$line_item`, `$row_key`, `$new_value`, `$dialog_chosen`
and `$messages` placeholders are **the remote agent's business, not the process's**. It
obtains them from the user per the instruction's `user escalation:` block, uses them, and
returns a result. CUGA FLO neither supplies nor stores them, so there is nothing to declare —
no `variables:` entry and no `<bpmn2:property>`. A process variable would only be needed if a
value had to survive *between* tasks, which nothing here requires.

**A prose `condition:` on the consultation gateway is correct.** `condition` is what the
`DecisionAgent` reasons from when deciding what to ask; prose is exactly right there. It would
only need to be an evaluable expression on a gateway that resolves locally without
consultation.

### The one real tension: `request_user_input`

Gap #2 is not a typo. It is an action the author wants and the runtime does not have — the
HITL gap. Everything else in this app routes human interaction through the remote agent, but
a **hook** has no such path today: `HookResult` can continue, skip, jump or terminate, and
none of those means *"go ask someone"*.

Two coherent resolutions:

- **Drop it for now.** Nothing in this app declares a hook, so the permission is unused.
  Removing it makes the YAML valid immediately, and it is the smaller step.
- **Route hook consultation through the remote agent too** — 4b row 3, needing
  `HookConfig.instruction` and `HookConfig.human_consultation`. Only worth building once an app
  actually declares a hook.

Engine: **Kogito**, so the demo also proves the binding across the MCP bridge rather than only
in-process on LangGraph.

### Layout as authored

```
excel_flows_kogito/
  config/
    excel_flows_kogito_config.yaml
    excel_flows_kogito.bpmn           # clean model, referenced by flow.bpmn_file
    excel_flows_kogito-kogito.bpmn    # ported: script tasks + CugaFlo callbacks
    supervisor_excel_flows_kogito.yaml
  policies/
    task-retrieve.md  task-update.md  task-run_function.md  decision-consult_user.md
```

### What the process exercises

| Element | Binding | Brief § |
|---|---|---|
| Task `retrieve` | `agent_type: agent0` | 5.3 |
| Task `update` | `agent_type: agent0` | 5.2 |
| Gateway `consult user` — update again, or run function? | `human_consultation: agent0` | 5.5 |
| Task `run_function` | `agent_type: agent0` | 5.4 |

**No hooks, and therefore no hook consultation.** An earlier draft of this plan
assumed a pre-update hook would elicit the operation parameters. The app instead puts a
`user escalation:` block inside each task's `system_instruction`, making the remote agent
responsible for asking — see 4b row 1. Both are coherent; this is the one that was built.

Consequences of that choice, worth being explicit about:

- **`variables: {}` is consistent, not an oversight.** With no hook writing parameters into
  process state, there is nothing to declare. Gap #4 is therefore only a problem if a
  parameter must survive *between* tasks — which nothing in this process currently requires.
- **The hook consultation path (4b row 3) is unexercised by this app.** It needs
  `HookConfig.instruction` and a hook to be worth implementing. Consider deferring row 3
  until an app actually uses it, rather than building it blind.

### Kogito specifics — from `README-KOGITO.md`, not re-derivable

- Name the ported model `<name>-kogito.bpmn`; the clean model stays `BPMNdiagram.bpmn`.
- **Every YAML variable needs a matching `<bpmn2:property>`** in the BPMN, plus its
  `itemSubjectRef` item definition. `build_kogito_app.sh` warns when one is missing; a missing
  property means the variable silently stays unset.
- `<drools:import name="org.cuga.CugaFlo"/>` and `FlowRedirect` at the process level —
  Java FQNs inside script bodies are rejected by the codegen.
- Build and run, per `CLAUDE.md`:
  ```bash
  ./scripts/build_kogito_app.sh excel_flows_kogito
  build/kogito/excel_flows_kogito/run.sh          # leave running
  cuga start flow_agent_inline excel_flows_kogito # other terminal
  ```
  Only step 1 repeats after a BPMN change — YAML and policies are read live.

### The limitation this demo will expose

The gateway consultation in the table above is **a conversation with a human, inside the 120s
`CugaFlo.java` hop**. If the user takes two minutes to answer, the script task throws and the
process instance fails.

This is not a reason to change the demo — it is the clearest possible motivation for the
BPMN user-task follow-on listed under Timeouts. Build it, show it working with a prompt
user, and let it be the concrete argument for the parked work. Note the exposure in the app's
README rather than hiding it behind a generous timeout.

## Verification

**Stage 1 — code changes, before the demo app exists.** No stub server, and no network: patch
`fetch_agent_card` and `delegate_task_via_a2a_sdk` with fakes. Everything below is about *our*
wiring — key names, caching, metadata, failure routing — and a real server would mostly
exercise `a2a-sdk` against itself. Interoperation with a real A2A server is what stage 2
proves. None of this waits on the remote team.

1. **Unit, no network** — card resolves once for N calls; the consultation tool returns text;
   `RemoteTaskExecutor.invoke()` returns a dict keyed `output` (not `result`, which
   `_process_output` would stringify wholesale); both call sites send their `role` metadata.
2. **Regression, and the gate that matters most** — `loan_approval` and
   `loan_approval_kogito`, which have no `remote_agents:` block, behave exactly as today.
   These apps are **not** modified to demonstrate the feature; that is `excel_flows_kogito`'s
   job.
3. **Name validation** — `agent_type: typo_bot` and `human_consultation: typo_bot` each raise
   at load, naming the offending key.
4. **Both fields coexist** — a gateway carrying `agent_type: cuga_agent` *and*
   `human_consultation: <remote>` builds a local `CugaAgent` that holds the remote tool;
   assert the decider is local and the tool is attached.
5. **Failure split** — point `url` at a closed port, which exercises the real connection-error
   path without any server: `agent_type: <remote>` fails **loudly** (specifically: confirm the
   per-task `try/except` does not swallow it into a silently agent-less task), while
   `human_consultation: <remote>` returns an "unavailable" string as tool output, so the
   agent routes on its policy alone. Note the tool cannot be *omitted* at load, since
   reachability is only known on first use — degrading happens at call time instead.
6. **Not-called path** — a gateway resolving on its condition alone makes no A2A call.
7. **Multiple agents** — two entries in `remote_agents:` to confirm nothing is hard-coded to a
   single agent. One fake serves both names.

**Stage 2 — `excel_flows_kogito` end to end.** Blocked on two things outside this task: the
app being seeded with its BPMN models and YAMLs, and a real agent 0 being reachable. Stage 1
is the completion bar for the code work; stage 2 is the later demonstration.

8. **Build and start** — `./scripts/build_kogito_app.sh excel_flows_kogito` completes with no
   missing-`<bpmn2:property>` warnings, and `run.sh` serves the process.
9. **All three fulfilment operations** — retrieve, update and macro each complete through
   `agent_type: agent_0`, and agent 0 dispatched the right one from the statement alone.
10. **Both consultation points** — the pre-update hook supplies workbook/columns/rows into
    process variables, and the gateway consultation returns a preference while the **chosen
    flow is still one of `available_flows`**. The decision must be traceable to the local
    agent, not to agent 0.
11. **Audit** — both consultations appear in the trace and in the Kogito Management Console
    instance view, not only in agent 0's logs.
12. **The 120s exposure** — deliberately delay the human answer past the ceiling and confirm
    the failure is the expected `CugaFlo.java` rethrow. Document it in the app README; do not
    paper over it by raising the timeout.

## Out of scope

- **MCP.** Fits a thin form/UI surface; a remote agent with autonomy over *how* it elicits is
  delegation, which is A2A. `CugaAgent` cannot consume MCP directly in any case.
- **Structured response schemas.** `delegate_task_via_a2a_sdk` accepts `variables` as
  *request* metadata but hardcodes `"variables": {}` on every return path — there is no return
  channel. Free text only until a use case forces extending that shared function.
- **The BPMN user task for long human waits** — the real HITL path, noted above.
- **`Task` replies.** `delegate_task_via_a2a_sdk` handles them poorly: it flattens a Task by
  joining **every** message in `history` — handing the deciding agent a transcript rather
  than a conclusion — and a Task with empty history falls through to `str(result_obj)`. It
  also never inspects `Task.status`, so `input-required` would read as a finished answer.
  The brief therefore specifies plain `Message` replies only, which sidesteps all three.
  Reading task status becomes a prerequisite for the BPMN user-task path above, since that
  is where a parked conversation would resume from.
- **A repair loop** for malformed responses. Treat a bad response as a failed consultation.
- **Sending a correlation identifier.** `delegate_task_via_a2a_sdk` builds each `Message`
  with a fresh `message_id` and sets neither `context_id` nor `task_id`, so a remote agent
  cannot tell that two requests belong to the same process run. That rules out any
  server-side memory keyed on the run — the team brief §5.6 leaves stateless-vs-stateful to
  the remote team but tells them to key on their own user session instead. `cugaProcessKey`
  already identifies a run internally and would be the obvious thing to pass; add it when a
  remote agent actually needs it.
- **`action_permissions` enforcement** — currently applied only in `langgraph_engine.py:621`;
  `FlowAgent` stores `_permitted_actions`/`_prohibited_actions` and never reads them, so
  Flowable and Kogito apply hook results unchecked. Independent of this work, but it is the
  guard that should bound hook outcomes on every engine.

