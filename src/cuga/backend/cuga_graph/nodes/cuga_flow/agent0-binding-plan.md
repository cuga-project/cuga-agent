# Binding CUGA FLO's agents to a remote "agent 0" over A2A — plan

**Status: proposal, nothing implemented.**

## What this enables

Each of CUGA FLO's three wrapper agents holds a local `CugaAgent` and reasons with it. This
plan lets any of them reach a remote agent — "agent 0" — through a single uniform contract,
without changing who is responsible for what.

Agent 0 is a LangGraph agent, exposed as a service by its own team. It **always answers free
text**. It never returns a flow id, a `HookResult`, or any CUGA FLO structure.

| Wrapper | Agent 0's text becomes | Who concludes |
|---|---|---|
| `TaskAgent` | the task result → `output_mapping` | agent 0 — delegated work |
| `DecisionAgent` | a section in the decide prompt | `DecisionAgent` picks the flow |
| `FlowAgent` | a section in the hook prompt | `FlowAgent` forms the `HookResult` |

The existing parsers are untouched: `DecisionAgent` still parses `<flow_id>|<reason>` from
*its own* CugaAgent, `FlowAgent` still parses JSON from *its own* hook agent. Agent 0 is
never on those paths, so no external system can emit a routing decision or a hook action.

Two integration modes, one adapter:

```
  TaskAgent ──delegate──▶ ┌──────────────┐
                          │ Agent0Client │──A2A──▶ agent 0
  DecisionAgent ──┐       │ ask(q) → str │
  FlowAgent ──────┴─tool─▶└──────────────┘
```

---

## Part A — CUGA FLO side

### A1. The adapter — new, the only substantial code

`backend/server/agent0/` (or alongside the existing A2A client), one class:

```python
class Agent0Client:
    async def ask(self, question: str) -> str: ...
```

Reuses `cuga_supervisor/a2a_protocol.py`, which already wraps `a2a-sdk`
(`A2ACardResolver`, `A2AClient`) — the client side exists and should not be rebuilt.

Two façades over it:

- **`invoke(x) -> obj with .answer`** — satisfies what `TaskAgent` already calls, so
  `TaskAgent` itself needs no change (it is duck-typed: `await self.agent.invoke(...)`).
- **a LangChain `@tool`** — `CugaAgent` takes LangChain tools, not MCP servers, so the tool
  wrapper is required and belongs here. Its docstring should carry agent 0's card
  `description`, since that text is what the LLM reads to decide whether to ask.

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
branch that builds the adapter instead of a `CugaAgent`:

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

### B1. An agent card

`name`, `description`, `version`, `url`, `capabilities`, `skills`.

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

- **Free text in, free text out.** No knowledge of BPMN, process variables, or CUGA FLO.
- **Answer in seconds.** A consultation runs *inside* `route_gateway` or `evaluate_hook`,
  which blocks the workflow engine and counts against `KOGITO_TIMEOUT`. See the constraint
  below.
- **Report, don't rule.** As a consultant, return facts, not verdicts. Nothing enforces
  this technically, so state it in the card: an agent 0 that answers *"reject the loan"*
  rather than *"three prior defaults on record"* quietly moves authority out of the harness.
- **Say `input-required` rather than block.** If a human is involved and may take longer
  than seconds, return that state instead of holding the connection.

---

## Cross-cutting constraint — the synchronous window

A consultation happens inside a control point, which is inside a blocking call from the
workflow engine. On Kogito, `POST /{processId}` is synchronous for the whole process; this
is what produced the duplicate-instance bug when a 32-second call exceeded a 30-second
timeout.

- **Lookups (seconds):** fine as a tool.
- **Human approval (minutes to days):** no protocol fixes this. It needs the escalation
  branch — a modelled BPMN user task where the *engine* owns the wait, with agent 0 driving
  the conversation and completing the task. `KogitoProxy.complete_task` already exists; no
  know-how covers user tasks yet.

Decide per hook and per gateway which regime applies, because the two cost very differently.

---

## Out of scope, deliberately

- **Agent 0 deciding a flow or a hook action.** Mechanically possible — `DecisionAgent`
  already validates the returned id against `available_flows` and falls back safely, so a
  remote decider could not invent a branch. Excluded because free-text-only keeps agent 0's
  contract uniform and leaves all structural authority local.
- **MCP.** Only worth it if agent 0 later offers several distinct typed operations. Even
  then `CugaAgent` cannot consume MCP directly, so it would need its own adapter.

## Related defect to fix first

`action_permissions` is enforced **only** in `langgraph_engine.py:621`. `FlowAgent` stores
`_permitted_actions` / `_prohibited_actions` and never reads them, so on Flowable and Kogito
a hook result is applied unchecked. Independent of this plan, but it is the guard that
should bound hook outcomes on every engine — worth moving into `FlowAgent._handle_hook`
before external input starts shaping those decisions.

---

## Verification

1. **Adapter in isolation** — `Agent0Client.ask()` against a stub A2A server; assert the
   card is resolved and text returns.
2. **Executor role** — a task with `delegate_to: agent_0` completes and its output lands via
   `output_mapping`.
3. **Consultant role** — a gateway with `tools: [agent_0]` where the policy needs an
   external fact: assert the tool was called, the text appears in the decide prompt, and the
   chosen flow is still one of `available_flows`.
4. **Not-called path** — a decision resolving on its condition alone makes no A2A call, so
   the round trip is genuinely conditional.
5. **Audit** — the consultation appears in the trace, not only in agent 0's logs.
6. **Regression** — an app with no `remote_agents:` behaves exactly as today.
