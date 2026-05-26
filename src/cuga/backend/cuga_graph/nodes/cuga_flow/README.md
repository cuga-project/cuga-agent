<img src="../../../../../../docs/images/cugaflo-logo.png" alt="CUGA FLO" width="280"/>

# CUGA FLO

**CUGA FLO (FLow Orchestration)** is a process harness for policy-aware, structurally-enforced agent workflows. The architecture separates deterministic process execution from agentic reasoning and governance. CUGA FLO enables the integration with any workflow engine via MCP. A demo instantiation with LangGraph is included, but this may be replaced with other workflow engines in production settings. LLM reasoning is scoped to designated control points — task fulfillment, gateway routing, and hook-governed flow adaptations.

---

## Overview

CUGA FLO combines the deterministic structural guarantees of workflow engines with open-world, policy-bounded agentic reasoning without relinquishing process semantics to the LLM layer. Rather than allowing agents to bypass or redefine process behavior, adaptations—when permitted—must be explicitly enabled and remain policy-adherent within governed execution boundaries. Existing systems tend to choose one side of the tradeoff: classical BPM emphasizes structural conformance but limited runtime adaptability, whereas LLM-as-planner approaches favor adaptability at the cost of conformance guarantees and auditability. CUGA FLO seeks to reconcile these two extremes by providing a holistic process harness that can be configured to enforce both structural conformance and runtime adaptability.

Within that structure, three layers of policy-aware reasoning operate:

- **FlowAgent** oversees the process as a whole. At permitted interception points (hooks), it can adapt the flow — skipping nodes, jumping to a target, escalating, or halting — by reasoning against a hook-specific policy and the full current process state.
- **DecisionAgent** reasons about gateway routing: given the condition evaluation result, the process state, and the gateway's policy, it selects which branch to follow.
- **TaskAgent** fulfils individual tasks: it executes the task logic in accordance with the task's policy and writes results back into the shared process variable namespace.

**MCP as the integration bridge.** The FlowAgent harness does not execute the BPMN process graph itself at runtime — a `WorkflowEngine` does. The two are decoupled by `MCPFlowBridge`, a FastMCP server that mediates all communication between them. The FlowAgent exposes reasoning tools (`execute_task`, `route_gateway`, `evaluate_hook`, `get_static_config`) over MCP; the engine calls those tools at every control point. This makes the execution engine replaceable — the included LangGraph engine is the demo/current backend; enterprise-grade engines connect to the same MCP interface in production.

This makes CUGA FLO suited for regulated, repeatable, or auditable processes — loan approvals, compliance workflows, onboarding pipelines — where the sequence of steps is structurally enforced but each step and permitted intervention is still governed by policy.

---

## Key Concepts

### FlowAgent

The meta-agent and single entry point for the process harness. At initialisation it:

1. Parses the BPMN 2.0 XML into a `BPMNProcess` (elements + sequence flows)
2. Compiles each task, gateway, and hook definition into the corresponding agent/policy structure
3. Registers its reasoning capabilities (`execute_task`, `route_gateway`, `evaluate_hook`, `get_static_config`) on the shared `MCPFlowBridge`

At runtime, the `WorkflowEngine` drives execution. The FlowAgent responds only at MCP-mediated control points, each call carrying a `ControlPointContext` that embeds the full process state, execution history, model summary, and task instruction. The FlowAgent manages the shared **process variable** namespace, evaluates hook policies to decide whether and how to adapt the flow, and builds the final completion message from task results.

A `FlowAgent` can be registered as a sub-agent inside a `CugaSupervisor`, where it is treated as a peer alongside `CugaAgent` instances.

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

Hooks are annotations over BPMN sequence flow edges. When execution reaches an annotated transition, CUGA FLO intercepts it and reasons — against the current process state and the hook's policy — about how execution should proceed before the target node is entered. The BPMN diagram is never modified; hooks are declared separately and attached to flows by ID.

> **LangGraph note:** In the included LangGraph engine, hooks are materialised as intermediate graph nodes inserted at compile time between the source and target of each annotated edge. This is a technical choice specific to LangGraph's compiled graph model and is not part of the general hook contract.

Each hook carries:

| Field | Purpose |
|---|---|
| `location` | BPMN flow ID to intercept — exactly one hook per edge |
| `hook_type` | `PRE_EDGE`, `POST_NODE`, `PRE_GATEWAY`, `POST_GATEWAY` |
| `condition` | Optional guard — hook is skipped if it returns false |
| `policy` | Markdown policy; when present, FlowAgent reasons with its LLM against it |

The `HookResult.action` determines what happens next:

| Action | Effect |
|---|---|
| `CONTINUE` | Proceed to the target node normally |
| `SKIP_NODE` | Skip the immediate next node |
| `SKIP_TO` | Jump directly to a named node via `Command(goto=)` |
| `SWAP_NODES` | Swap two nodes: redirect to `node_b` when `node_a` was next, or `node_a` when `node_b` was next |
| `REQUEST_USER_INPUT` | Soft-halt and surface a question to the user |
| `TERMINATE` | Hard-halt the process immediately |
| `REMOVE_NODE` | Remove a node from the process graph at runtime: the engine rewires its predecessor and successor flows to bypass it, recompiles the graph, and resumes |
| `ADD_NODE` | Insert a new task node into the process graph at runtime: the engine adds the node before the current target, wires flows through it, registers an MCP-backed task handler for it, recompiles, and resumes |

`REMOVE_NODE` and `ADD_NODE` trigger a **graph rebuild** and may only target nodes that have not yet executed. The hook routes to END; the engine modifies the live `BPMNProcess` model, recompiles the LangGraph, and resumes directly at the correct entry point — `new_node_id` for ADD_NODE, or the successor of the removed node for REMOVE_NODE — via a conditional START edge. No previously-executed nodes are replayed.

Hook reasoning is performed by the **FlowAgent** itself — not a separate agent — because hooks are a process-level concern. The FlowAgent holds the full process state and BPMN structure, and reasons against the hook's policy to decide what flow adaptation (if any) is warranted. Hooks are the only points in the process where the FlowAgent is permitted to deviate from the nominal BPMN path, and every such deviation is policy-governed and recorded in the audit log.

**Separation of concerns:** CUGA FLO issues the hook action instruction — it does not execute it. Carrying out the action is the responsibility of the workflow engine. The engine receives the `HookResult` via the MCP bridge and applies the corresponding structural intervention (routing, graph rebuild, halt, etc.) according to its own execution model.

Per-process `action_permissions` (declared in the YAML config) explicitly list which hook actions are permitted or prohibited for a given process, providing an additional governance layer over what adaptations the FlowAgent may apply.

---

### MCP Bridge

`MCPFlowBridge` (`cuga_flo_mcp/bridge.py`) is a FastMCP server that acts as the integration contract between the FlowAgent harness and any `WorkflowEngine`. The two sides register independently:

**FlowAgent side** — registers reasoning tools:

| MCP Tool | Called by engine when |
|---|---|
| `execute_task` | A BPMN task node is reached |
| `route_gateway` | A gateway node needs a routing decision |
| `evaluate_hook` | A hook intercept point fires |
| `get_static_config` | Engine fetches process metadata at startup |

**WorkflowEngine side** — registers:

| MCP Tool | Called by FlowAgent when |
|---|---|
| `run_process` | Starting a new process instance |

Every MCP call carries a `ControlPointContext` — a dataclass embedding the full process state, execution history, model summary, and task instruction — so each reasoning call is self-contained and stateless from the engine's perspective.

```python
from cuga.backend.server.cuga_flo_mcp.bridge import MCPFlowBridge

bridge = MCPFlowBridge()
bridge.register_flow_agent(flow_agent)
bridge.register_engine(engine, registry)

# Engine obtains an in-process MCP client; can be swapped for HTTP/SSE transport
client = bridge.get_client()
```

The in-process transport (`FastMCPTransport`) is used by the demo LangGraph engine. A remote transport (HTTP/SSE) can be substituted without changing any FlowAgent or engine logic — enabling cross-process or cross-host deployment.

---

### WorkflowEngine

`WorkflowEngine` (`workflow_engine.py`) is the abstract execution backend. It holds the process model and instance state, drives execution node-by-node, and communicates with the FlowAgent exclusively through the MCP bridge. The single abstract method is:

```python
async def _run_via_mcp(
    self,
    process: BPMNProcess,
    initial_inputs: dict,
    mcp_server: MCPFlowBridge,
) -> FlowState:
    ...
```

**LangGraphWorkflowEngine** (`langgraph_engine.py`) is the concrete demo engine included with CUGA FLO. It:

1. Fetches static process config from the FlowAgent via the `get_static_config` MCP tool
2. Builds a `_ControlOverlay` — a set of MCP-backed handlers for tasks, gateways, and hooks
3. Compiles the BPMN topology into a LangGraph using `_build_graph` and `_add_edges_with_hooks`
4. At each control point (task node, gateway node, hook node), calls the corresponding FlowAgent MCP tool with a `ControlPointContext`

The LangGraph engine is the **current/demo backend**. Enterprise-grade workflow engines (with their own persistence, audit trails, and compliance guarantees) connect to the same `WorkflowEngine` interface and MCP bridge in production — no changes to the FlowAgent harness are required.

---

### ProcessRegistry

`ProcessRegistry` (`process_registry.py`) is a catalog of BPMN process definitions. It maps short process keys to `ProcessDefinition` objects (pairing a parsed `BPMNProcess` with a `FlowConfig`) and caches parsed results for reuse across invocations.

`register_from_directory()` auto-discovers process definitions from a directory using the `flow_agent_app_inline/` layout convention: each subdirectory containing a YAML file with a `flow:` key is registered as a named process.

```python
registry = ProcessRegistry()
registry.register_from_directory("docs/examples/flow_agent_app_inline/")

# Lookup returns (BPMNProcess, FlowConfig) — cached after first parse
process, config = registry.get("loan_approval")
```

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

## Demo Apps

Three inline demo processes are included under `docs/examples/flow_agent_app_inline/`:

| App | Description |
|---|---|
| `loan_approval` | Multi-step loan processing with credit check, compliance, and approval gateways |
| `receive_order` | Order intake flow with inventory check and fulfilment routing |
| `trip_planner` | Travel planning flow with itinerary assembly and booking steps |

Start any demo with:

```bash
cuga start flow_agent_inline <app_name>

# Examples:
cuga start flow_agent_inline loan_approval
cuga start flow_agent_inline receive_order
cuga start flow_agent_inline trip_planner
```

Each app directory follows the same layout: a BPMN file, a `flow_config.yaml` referencing it, agent definitions, and per-task/gateway policy markdown files under `policies/`.

---

## Module Structure

```
cuga_flow/
├── flow_agent.py          # FlowAgent — process harness meta-agent
├── flow_agent_state.py    # FlowState — process-aware agent state
├── flow_config.py         # FlowConfig — YAML-based instantiation
├── bpmn_parser.py         # BPMN 2.0 XML parser → BPMNProcess
├── task_agent.py          # TaskAgent — CugaAgent wrapper for task nodes
├── decision_agent.py      # DecisionAgent — two-node gateway router
├── hook_manager.py        # Hook, HookManager, HookAction, HookResult
├── workflow_engine.py     # WorkflowEngine ABC + ControlPointContext
├── langgraph_engine.py    # LangGraphWorkflowEngine — demo/current engine
└── process_registry.py    # ProcessRegistry — multi-process catalog

cuga_flo_mcp/
└── bridge.py              # MCPFlowBridge — FastMCP integration contract
```
