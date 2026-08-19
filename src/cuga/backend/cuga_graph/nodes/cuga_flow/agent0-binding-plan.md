# Binding CUGA FLO's agents to a remote "agent 0" over A2A — plan

**Status: proposal, nothing implemented.**

## What this enables

Each of CUGA FLO's three wrapper agents holds a local `CugaAgent` and reasons with it. This
plan lets any of them reach a remote agent — "agent 0" — through a single uniform contract,
without changing who is responsible for what.

Agent 0 is a LangGraph agent, exposed as a service by its own team. It **answers free text by
default**; a caller may attach an `expected_output` schema, in which case agent 0 returns
matching JSON. Either way it never returns a flow id, a `HookResult`, or any CUGA FLO
structure — it supplies *input to* a decision, never the decision.

| Wrapper | Agent 0 returns | Who concludes |
|---|---|---|
| `TaskAgent` | free text → `output_mapping` | agent 0 — delegated work |
| `DecisionAgent` | text, or a typed preference | `DecisionAgent` picks the flow |
| `FlowAgent` | text, or a typed preference | `FlowAgent` forms the `HookResult` |

The optional schema exists because prose loses signal a policy may legitimately route on:
*"probably the cheaper option"* does not carry `certainty: ambiguous`. Keep it optional —
most consultations are lookups needing no envelope.

The existing parsers are untouched: `DecisionAgent` still parses `<flow_id>|<reason>` from
*its own* CugaAgent, `FlowAgent` still parses JSON from *its own* hook agent. Agent 0 is
never on those paths, so no external system can emit a routing decision or a hook action.

Two integration modes over one existing client:

```
  TaskAgent ──delegate──▶ ┌────────────────────────────┐
                          │ delegate_task_via_a2a_sdk  │──A2A──▶ agent 0
  DecisionAgent ──┐       │ (a2a_protocol.py, existing)│
  FlowAgent ──────┴─tool─▶└────────────────────────────┘
```

---

## Part A — CUGA FLO side

### A1. Two small functions — no client class

`cuga_supervisor/a2a_protocol.py` already exposes what is needed, **module-level and
supervisor-independent**:

- `fetch_agent_card(...)` — resolves the card
- `delegate_task_via_a2a_sdk(agent_card, task, auth=None, timeout=30.0, variables=None)`
  → `{"result": str, "variables": dict, "status": str}`

So there is no `Agent0Client` class to write. Cache the card once at config load — that is
the only state involved — and add two wrappers.

**Consultant role** — a LangChain `@tool`, since `CugaAgent` takes LangChain tools, not MCP
servers. Its docstring carries agent 0's card `description`, which is what the LLM reads to
decide whether to ask:

```python
@tool
async def ask_agent_0(question: str) -> str:
    """<agent 0's card description>"""
    return (await delegate_task_via_a2a_sdk(_card, question, timeout=90))["result"]
```

**Executor role** — a shim exposing `invoke()`, because `TaskAgent` is duck-typed
(`await self.agent.invoke(task_input)`) and its `_process_output` reads `output` or
`content`. `delegate_task_via_a2a_sdk` returns `result`, so an unmapped dict would fall
through to `str(result)` and store the whole repr as the task output. Rename the key:

```python
class _Agent0Task:  # ponytail: 3 lines beats a class hierarchy; TaskAgent only calls invoke()
    async def invoke(self, task_input):
        return {"output": (await delegate_task_via_a2a_sdk(_card, str(task_input)))["result"]}
```

**Pass `timeout` explicitly.** The default is `30.0` — the fourth timeout in the stack, and
below the 120s Kogito ceiling, so leaving it silently truncates a task agent that would
otherwise have finished.

**`a2a-sdk` is a soft dependency.** The module raises `ImportError` behind a `HAS_A2A_SDK`
guard. Catch that at config load, not mid-process, so a missing package fails the app rather
than a running instance.

#### The `goal` framing

`task` should read as a delegated objective, not a literal question — *"find out the user's
preference regarding this routing choice"*, not *"ask exactly this and return this field"*.
That distinction is why this is A2A rather than MCP elicitation: agent 0 decides **how** to
obtain the answer, and may explain options, ask clarifying questions, or resolve ambiguity
over several turns.

#### Structured responses need a decision first

The plan's `expected_output` schema has no return channel today. `delegate_task_via_a2a_sdk`
accepts `variables` as **request** metadata, but every return path hardcodes
`"variables": {}` — nothing comes back through it. Two options:

- **Parse it out of the text** (`result`) with a local `json.loads` and validate. No changes
  to shared supervisor code; ugly but contained.
- **Extend the function to read response metadata.** Correct, but it is shared with the
  supervisor, so it widens the blast radius of this work.

Start with the parse; revisit if elicitation becomes common.

### A2. Declare agent 0 once

New top-level block in the app YAML, plus a matching model in `app_yaml_schema.py`:

```yaml
remote_agents:
  agent_0:
    protocol: a2a
    url: https://…
```

### A3. Wire the executor role — `TaskAgent`

`FlowConfig.create_task_agents()` is the single construction site. Add a `delegate_to`
branch that injects the A1 shim instead of a `CugaAgent`:

```yaml
tasks:
  - id: Activity_0oydey5
    agent:
      delegate_to: agent_0
```

Note that method currently catches per-task exceptions and logs rather than raising, so a
misconfigured `delegate_to` would yield a task with no agent bound and no loud failure.
Worth tightening while touching it.

### A4. Wire the consultant role — `DecisionAgent` and `FlowAgent`

Both build their `CugaAgent` lazily today with `special_instructions` and `model` only —
**neither passes `tools`**, and neither `GatewayConfig` nor `HookConfig` has a `tools`
field. Three small changes:

- add `tools: list[str]` to `GatewayConfig` and `HookConfig`
- resolve the names the way `create_task_agents` already resolves task tools
- pass them into `DecisionAgent._get_agent()` and `FlowAgent._get_hook_agent()`

```yaml
gateways:
  Gateway_09ad5fc:
    policy: ../policies/decision-credit_decision.md
    tools: [agent_0]

hooks:
  - id: Flow_0ybszcv
    policy: ../policies/hook-approval_intercept.md
    tools: [agent_0]
```

**One wrinkle:** `FlowAgent._hook_agent` is a **single instance shared by every hook in the
process**. Per-hook tools therefore need it split per hook, or the tool set becomes the
union across all hooks — a hook could then consult an agent its own policy never mentioned.

### A5. Record what was consulted

If agent 0's answer shaped a routing decision or a hook action, the audit trail must contain
it — otherwise the trace shows a conclusion whose basis lives only in a remote agent's logs.
`FlowState` already carries `graph_modifications` and `task_results`; consultations belong
alongside, and should also reach `ActivityTracker` so they appear in the UI trace.

This matters most for the consultant role, which is exactly where an external system
influences the process's structure while remaining invisible to it.

---

## Part B — Requirements on agent 0

Agent 0's team builds **one thing: an A2A server.** Not two. "As a tool" describes how CUGA
FLO presents agent 0 to a local `CugaAgent`; it is not a protocol agent 0 must speak, and no
MCP server is required.

Use `a2a-sdk` — the same package CUGA's client side already depends on, so both ends agree
by construction.

### B1. An agent card with two skills

`name`, `description`, `version`, `url`, `capabilities`, and at least two `skills`:

- **`fulfill_task`** — perform a delegated subtask, return free text. Backs `TaskAgent`.
- **`elicit_user_preference`** — obtain the user's preference across supplied options and
  return it structured. Backs the consultant role. Agent 0 owns the conversation: explaining
  alternatives, asking clarifying questions, resolving ambiguity.

The division to hold: **agent 0 determines *how* to obtain the preference; CUGA FLO
determines *what to do* with it.**

**This is the highest-leverage part.** `format_agent_card_for_prompt()` puts `description`
and `skills` **into the deciding LLM's prompt**, and the tool wrapper reuses them as the
tool description. That text is the entire basis on which a `DecisionAgent` judges whether
consulting agent 0 is warranted. A vague card yields an agent that never asks, or one that
asks every time.

### B2. An `AgentExecutor`

Two methods, `execute` and `cancel`. LangGraph maps directly:

```python
class Agent0Executor(AgentExecutor):
    async def execute(self, context, event_queue):
        result = await graph.ainvoke({"messages": [...]})
        # emit the text result
```

### B3. A served endpoint

`DefaultRequestHandler` plus the SDK's `jsonrpc_routes` / `agent_card_routes`, under
uvicorn. Follow the SDK's own server example for the assembly — the module layout differs
between versions and has not been verified here beyond confirming those pieces exist.

### B4. Behavioural requirements

These are CUGA FLO constraints rather than A2A ones, and matter more than the plumbing:

- **Goal in, answer out.** No knowledge of BPMN, process variables, or CUGA FLO.
- **Honour `expected_output` when supplied.** Return JSON matching it, including any
  ambiguity or no-preference cases the schema allows, rather than prose approximating it.
- **Report, don't rule.** Return facts and preferences, not verdicts. Nothing enforces
  this technically, so state it in the card: an agent 0 that answers *"reject the loan"*
  rather than *"three prior defaults on record"* quietly moves authority out of the harness.
- **Say `input-required` rather than hold the connection.** A consultation runs *inside*
  `route_gateway` or `evaluate_hook`, which blocks the workflow engine. Only sub-second
  lookups can complete in-line; anything involving a human must return `input-required` and
  let the escalation branch take over. See the constraint below — for
  `elicit_user_preference` this is the normal path, not an edge case.

---

## Cross-cutting constraint — the synchronous window

A consultation happens inside a control point, which is inside a blocking call from the
workflow engine. On Kogito, `POST /{processId}` is synchronous for the whole process; this
is what produced the duplicate-instance bug when a 32-second call exceeded a 30-second
timeout.

- **Lookups (seconds):** fine as a tool, in-line.
- **Anything conversational (minutes to days):** no protocol fixes this. It needs the
  escalation branch — a modelled BPMN user task where the *engine* owns the wait, with agent
  0 driving the conversation and completing the task. `KogitoProxy.complete_task` already
  exists; no know-how covers user tasks yet.

**`elicit_user_preference` is the second regime.** A multi-turn dialogue — explain the
options, ask a clarifying question, confirm — cannot run inside a blocking control point, so
the in-line tool path only ever suits `fulfill_task` and fast lookups. Modelling that user
task is therefore a prerequisite for preference elicitation, not a later refinement, and is
the largest piece of work this plan implies.

Decide per hook and per gateway which regime applies, because the two cost very differently.

---

## Out of scope, deliberately

- **Agent 0 deciding a flow or a hook action.** Mechanically possible — `DecisionAgent`
  already validates the returned id against `available_flows` and falls back safely, so a
  remote decider could not invent a branch. Excluded because free-text-only keeps agent 0's
  contract uniform and leaves all structural authority local.
- **MCP.** It fits a thin form/UI surface — *"ask exactly this question, return exactly this
  field"*. Agent 0 is an autonomous elicitation agent instead, which is delegation, so A2A
  is the right relationship. `CugaAgent` cannot consume MCP directly in any case.
- **A repair loop.** The PDF proposes re-prompting agent 0 to fix a response that fails
  validation. Deferred: treat a validation failure as a failed consultation and let the
  policy proceed without it. Add the loop once a real agent 0 is observed returning
  malformed artifacts — it is a retry state machine, against one JSON check today.

## Related defect to fix first

`action_permissions` is enforced **only** in `langgraph_engine.py:621`. `FlowAgent` stores
`_permitted_actions` / `_prohibited_actions` and never reads them, so on Flowable and Kogito
a hook result is applied unchecked. Independent of this plan, but it is the guard that
should bound hook outcomes on every engine — worth moving into `FlowAgent._handle_hook`
before external input starts shaping those decisions.

---

## Verification

1. **Wrappers in isolation** — both against a stub A2A server: the card resolves, the tool
   returns text, and the shim returns a dict keyed `output` (not `result`, which
   `_process_output` would stringify wholesale).
2. **Executor role** — a task with `delegate_to: agent_0` completes and its output lands via
   `output_mapping`.
3. **Consultant role** — a gateway with `tools: [agent_0]` where the policy needs an
   external fact: assert the tool was called, the answer appears in the decide prompt, and
   the chosen flow is still one of `available_flows`.
   With an `expected_output` schema, assert valid JSON is parsed and malformed JSON degrades
   to a failed consultation rather than an exception.
4. **Not-called path** — a decision resolving on its condition alone makes no A2A call, so
   the round trip is genuinely conditional.
5. **Audit** — the consultation appears in the trace, not only in agent 0's logs.
6. **Regression** — an app with no `remote_agents:` behaves exactly as today.
