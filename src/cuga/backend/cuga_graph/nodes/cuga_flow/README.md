<img src="../../../../../../docs/images/cugaflo-logo.png" alt="CUGA FLO" width="280"/>

# CUGA FLO

**CUGA FLO** is a BPMN-compiled process orchestration harness for policy-aware, structurally-enforced agent workflows.

---

## Overview

Standard multi-agent systems leave routing decisions to the LLM at runtime — the model decides what to call next, which makes the execution path unpredictable and hard to audit. CUGA FLO inverts this: the process structure is defined upfront in a BPMN 2.0 diagram and compiled into a LangGraph at initialisation time. The graph topology is the ground truth.

Within that structure, three layers of policy-aware reasoning operate:

- **FlowAgent** oversees the process as a whole. At permitted interception points (hooks), it can adapt the flow — skipping nodes, jumping to a target, escalating, or halting — by reasoning against a hook-specific policy and the full current process state.
- **CugaAgent as DecisionAgent** reasons about gateway routing: given the condition evaluation result, the process state, and the gateway's policy, it selects which branch to follow.
- **CugaAgent as TaskAgent** fulfils individual tasks: it executes the task logic in accordance with the task's policy and writes results back into the shared process variable namespace.

This makes CUGA FLO suited for regulated, repeatable, or auditable processes — loan approvals, compliance workflows, onboarding pipelines — where the sequence of steps is structurally enforced but each step and permitted intervention is still governed by policy.

---

## Key Concepts

### FlowAgent

The meta-agent and single entry point for BPMN process execution. At initialisation it:

1. Parses the BPMN 2.0 XML into a `BPMNProcess` (elements + sequence flows)
2. Compiles each task, gateway, and hook into a LangGraph node
3. Wires nodes with edges that follow the BPMN sequence flows exactly

At runtime it oversees the process as a whole: manages the shared **process variable** namespace, evaluates hook policies to decide whether and how to adapt the flow at permitted interception points, and builds the final completion message from task results. A `FlowAgent` can be registered as a sub-agent inside a `CugaSupervisor`, where it is treated as a peer alongside `CugaAgent` instances.

```python
flow_agent = FlowAgent(
    bpmn_file="process.bpmn",
    task_agents={"Activity_id": task_agent_instance},
    task_policies={"Activity_id": policy_markdown},
    gateway_agents={"Gateway_id": DecisionAgent(gateway_id, policy)},
    flow_conditions={"Flow_id": "${variable} > threshold"},
    hooks=[Hook(id=..., hook_type=HookType.PRE_EDGE, location="Flow_id", policy=md)],
    process_variables={"amount": 0, "approved": False},
)
```

Alternatively, load from a YAML config file:

```python
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import load_flow_from_yaml

flow_agent = load_flow_from_yaml("config/process_config.yaml")
```

---

### TaskAgent

Wraps a `CugaAgent` for execution inside a single BPMN task element, scoping it to a specific task with its own policy. Each task in the diagram is bound to a `TaskAgent` instance that:

- Resolves task input from process variables via **input mapping**
- Executes the underlying `CugaAgent`
- Writes outputs back to process variables via **output mapping**
- Records the result in `FlowState` with `status: completed` or `status: failed`
- Fires optional `pre_execute` / `post_execute` hooks

```python
TaskAgent(
    task_id="Activity_0oydey5",
    task_name="Check Credit",
    agent=credit_checker_agent,       # CugaAgent instance
    input_mapping={"applicant": "applicant_name", "amount": "loan_amount"},
    output_mapping={"score": "credit_score"},
)
```

---

### DecisionAgent

A per-gateway routing agent that binds a `CugaAgent` to a specific gateway, giving it a routing policy and the responsibility to select the correct outgoing branch. Implemented as a two-node internal LangGraph:

**Node 1 — `eval_condition`** (deterministic, no LLM)
Substitutes `${variable}` tokens from process variables into the gateway condition expression and evaluates it safely without `eval()`. Produces `TRUE`, `FALSE`, or `UNKNOWN`.

**Node 2 — `decide`** (CugaAgent)
Reads the condition result, the full process state, and the gateway's markdown policy, then selects exactly one flow ID — the branch to activate — in adherence to that policy.

Gateways with a single outgoing flow, or configured as `mode: tool`, are routed inline by `FlowAgent` using condition evaluation directly — no `DecisionAgent` is instantiated for them.

```yaml
gateways:
  Gateway_09ad5fc:
    mode: decision_agent
    condition: "${credit_score} > 0.6"
    policy: "policies/decision-credit_decision.md"
    flows:
      Flow_approve: { decision: "Approve — credit score sufficient" }
      Flow_reject:  { decision: "Reject — credit score insufficient" }
```

---

### Hook

Hooks intercept execution at specific BPMN sequence flow edges before the target node is entered. They are compiled into intermediate graph nodes at build time, so the BPMN diagram never needs to be modified to add cross-cutting behaviour like auditing, compliance checks, or escalation.

Each hook carries:

| Field | Purpose |
|---|---|
| `location` | BPMN flow ID to intercept |
| `hook_type` | `PRE_EDGE`, `POST_NODE`, `PRE_GATEWAY`, `POST_GATEWAY` |
| `condition` | Optional guard — hook is skipped if it returns false |
| `policy` | Markdown policy; when present, FlowAgent reasons with its LLM against it |
| `priority` | Execution order when multiple hooks share an edge |

The `HookResult.action` determines what happens next:

| Action | Effect |
|---|---|
| `CONTINUE` | Proceed to the target node normally |
| `SKIP_NODE` | Skip the immediate next node |
| `SKIP_TO` | Jump directly to a named node via `Command(goto=)` |
| `SWAP_NODES` | Swap two nodes: redirect to `node_b` when `node_a` was next, or `node_a` when `node_b` was next |
| `REQUEST_USER_INPUT` | Soft-halt and surface a question to the user |
| `TERMINATE` | Hard-halt the process immediately |

Hook reasoning is performed by the **FlowAgent** itself — not a separate agent — because hooks are a process-level concern. The FlowAgent holds the full process state and BPMN structure, and reasons against the hook's policy to decide what flow adaptation (if any) is warranted. Hooks are the only points in the process where the FlowAgent is permitted to deviate from the nominal BPMN path, and every such deviation is policy-governed and recorded in the audit log.

---

### FlowState

`FlowState` extends `AgentState` with process-specific fields:

| Field | Description |
|---|---|
| `process_variables` | Shared dict readable and writable by all nodes |
| `execution_path` | Ordered list of node IDs traversed so far |
| `gateway_decisions` | Record of each gateway's chosen flow |
| `hook_evaluations` | Audit log of every hook invocation and its outcome |
| `task_results` | Dict of `task_id → result` for all completed tasks |

Process variables are the primary inter-node communication channel. Task agents write outputs into them; gateway conditions read from them; hook policies inspect them.

---

### ConditionEvaluator

A module-level utility (`eval_condition`) that evaluates BPMN condition expressions without `eval()`. It:

1. Substitutes `${variable}` tokens with values from `process_variables`
2. Parses the resulting expression into a binary comparison
3. Applies the operator safely using Python's `operator` module

Used by `DecisionAgent` (Node 1) and by `FlowAgent` directly for tool-mode gateways.

---

## Module Structure

```
cuga_flow/
├── flow_agent.py        # FlowAgent — compiles BPMN → LangGraph, meta-agent
├── flow_agent_state.py  # FlowState — process-aware agent state
├── flow_config.py       # FlowConfig — YAML-based instantiation
├── bpmn_parser.py       # BPMN 2.0 XML parser → BPMNProcess
├── task_agent.py        # TaskAgent — CugaAgent wrapper for task nodes
├── decision_agent.py    # DecisionAgent — two-node gateway router
└── hook_manager.py      # Hook, HookManager, HookAction, HookResult
```
