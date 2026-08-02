# CUGA FLO — Integration with Flowable

CUGA FLO is designed to be engine-agnostic: the `WorkflowEngine` abstraction and `MCPFlowBridge`
interface allow any BPMN runtime to replace the native LangGraph demo engine. This document
describes the **Flowable** integration — CUGA FLO running alongside a deployed Flowable instance
as its external AI reasoning layer.

In this mode:

- **Flowable** owns process state, persistence, audit history, and token routing.
- **CUGA FLO** contributes LLM reasoning at each control point (task, gateway, hook) via synchronous MCP calls that Flowable's script tasks initiate over HTTP.
- **Hook actions** (SKIP_TO, TERMINATE) are fully realised inside the BPMN by a dedicated routing task; CUGA FLO issues the instruction and Flowable carries it out without any further REST calls.

---

## Architecture

```
  User / CugaSupervisor
         │
         ▼
    FlowAgent.invoke()
         │  calls run_process via MCPFlowBridge
         ▼
    FlowableProxy.start_process()   ──────────────────────────────────────────────┐
         │  POST /runtime/process-instances                                        │
         ▼                                                                         │
  ┌──────────────────────────────────────────────────────────┐                    │
  │                  Flowable Engine                         │                    │
  │                                                          │                    │
  │  ScriptTask (task agent)  ──► POST /mcp execute_task ──►│──► FlowAgent       │
  │  ScriptTask (decision)    ──► POST /mcp route_gateway ──►│──► DecisionAgent  │
  │  ScriptTask (hook)        ──► POST /mcp evaluate_hook ──►│──► FlowAgent      │
  │                                                          │◄── HookResult      │
  │  HTTP ServiceTask         ──► POST /mcp complete_process►│──► FlowAgent      │
  └──────────────────────────────────────────────────────────┘  (signals done)   │
                                                                                  │
    FlowableProxy polls history until endTime is set ◄────────────────────────────┘
         │
         ▼
    FlowAgent.invoke() returns FlowState
```

The CUGA FLO MCP server listens at `http://host.docker.internal:8090/mcp` — the
`host.docker.internal` hostname resolves to the host machine from inside the Flowable
Docker container, so script tasks can reach the CUGA FLO process without any network
configuration beyond a port binding.

---

## Component 1 — FlowableProxy

`src/cuga/backend/server/flowable/flowable_proxy.py`

A thin synchronous HTTP client over the Flowable UI REST API
(`/flowable-ui/process-api`, basic auth). It handles the CUGA FLO side of the
process lifecycle:

| Method | Purpose |
|---|---|
| `deploy(bpmn_path)` | Upload a BPMN 2.0 model file to Flowable |
| `start_process(process_key, variables)` | Start an instance by definition key, injecting initial process variables |
| `invoke_workflow(process_key, variables)` | Start + poll history until `endTime` is set (or timeout) |
| `fetch_result(instance_id)` | Read historic process variables after the instance ends |
| `list_process_definitions()` | Enumerate deployed models |
| `get_variables(instance_id)` | Runtime variables of a running instance |
| `get_historic_variables(instance_id)` | Variables from history (works after instance ends) |

Configuration is read from environment variables (or `.env`):

| Variable | Default | Description |
|---|---|---|
| `FLOWABLE_BASE_URL` | `http://localhost:8080/flowable-ui/process-api` | REST API base |
| `FLOWABLE_USER` | `admin` | HTTP basic auth username |
| `FLOWABLE_PASSWORD` | `test` | HTTP basic auth password |
| `FLOWABLE_TIMEOUT` | `30.0` | Request timeout in seconds |

**Hook actions are not realised via FlowableProxy REST calls.** CONTINUE, SKIP_TO, and
TERMINATE are all handled entirely inside the Flowable BPMN by a shared `Task_DynamicSkip`
script task (see Component 2 below). `realize_hook_action()` exists on the proxy but is
a logging stub — it records the hook outcome without making any REST calls, because by the
time CUGA FLO returns the `HookResult`, the BPMN boundary-event mechanism has already
routed execution.

Quick CLI (against a running `flowable/flowable-ui:latest` container):

```bash
python -m cuga.backend.server.flowable.flowable_proxy deploy path/to/model.bpmn20.xml
python -m cuga.backend.server.flowable.flowable_proxy run    Process_1s3q83l
python -m cuga.backend.server.flowable.flowable_proxy result <instance_id>
```

---

## Component 2 — Augmented BPMN Model

A legacy BPMN model cannot drive CUGA FLO out of the box. The process file deployed to
Flowable must be extended with three types of control-point callbacks and a terminal
callback. The transformation procedure is described in the know-how files under
`docs/examples/flow_agent_app_inline/model_transform_knowledge/flowable/`; the loan approval
process (`docs/examples/flow_agent_app_inline/loan_approval/config/Loan-Approval-Process.bpmn20.xml`)
is the reference implementation.

All script tasks use Flowable's **Nashorn/Rhino JavaScript engine**. HTTP calls are made
via `java.net.URL` and `java.io.*` — no external libraries required. Every script task
dynamically serialises all current process variables and forwards them in the MCP call body,
so CUGA FLO always receives the full process state.

---

### Extension 1 — Task Agent (ScriptTask)

Each BPMN task annotated as a Task Agent is replaced by a `<scriptTask>` that:

1. Serialises all process variables into a JSON object via `execution.getVariables()`
2. POSTs a `tools/call` MCP request to `execute_task` at `http://host.docker.internal:8090/mcp`
3. Parses the SSE-wrapped JSON response (`data: {...}`)
4. Reads `resp.result.content[0].text` → parses it as a `FlowState` JSON → extracts `process_variables`
5. Writes declared output variables back via `execution.setVariable()`

```xml
<scriptTask id="TASK_ID" name="TASK_NAME"
    scriptFormat="javascript" flowable:autoStoreVariables="false">
  <script><![CDATA[
/* ... dynamic variable serialisation ... */
var body = '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"execute_task",'
         + '"arguments":{"task_id":"TASK_ID","ctx":{...,"process_variables":' + processVarsJson + '}}},"id":1}';
/* ... HTTP POST ... */
var vars = JSON.parse(resp.result.content[0].text).process_variables;
if (vars && vars.OUTPUT_VAR !== undefined) {
    execution.setVariable('OUTPUT_VAR', TYPE_CAST(vars.OUTPUT_VAR));
}
  ]]></script>
</scriptTask>
```

The `scriptFormat="javascript"` attribute and `flowable:autoStoreVariables="false"` are
required on every script task. The read timeout is set to 120 000 ms to accommodate LLM
latency (10–60 s per call).

---

### Extension 2 — Decision Agent (ScriptTask + adapted gateway)

An exclusive gateway annotated as a Decision Agent is preceded by a new routing
`<scriptTask>` and its outgoing conditions are rewritten:

**New routing script task** (`Activity_Route{GATEWAY_ID}`):

1. Serialises process variables and the list of available outgoing flows
2. POSTs to `route_gateway` MCP — CUGA FLO's `DecisionAgent` selects a flow ID
3. `resp.result.content[0].text` is the raw flow ID string (no extra JSON layer)
4. Writes the chosen flow ID to a process variable (e.g. `gatewayDecision`)

**Rewired flows:**

- The flow that previously entered the gateway now targets the routing script task
- A new flow connects the routing script task to the gateway

**Adapted gateway conditions** — each outgoing flow's `<conditionExpression>` is replaced
with a variable equality check:

```xml
<!-- before: hard-coded EL condition -->
<conditionExpression><![CDATA[${credit_score > 0.6}]]></conditionExpression>

<!-- after: equality check against the decision variable -->
<conditionExpression><![CDATA[${gatewayDecision == 'FLOW_ID'}]]></conditionExpression>
```

Flowable routes deterministically based on the string the DecisionAgent returned; the
routing variable is the only channel between the two elements.

---

### Extension 3 — Hook (ScriptTask + boundary event + Task_DynamicSkip)

A flow edge annotated as a Hook is intercepted by inserting a `<scriptTask>` on the flow
and attaching the boundary-event machinery:

#### Hook script task

Inserted between the upstream element and the original downstream task:

1. Serialises process variables and POSTs to `evaluate_hook` MCP
2. `resp.result.content[0].text` is a JSON `HookResult`: `{"action": "...", ...}`
3. Branches on `hookResult.action`:

| Action | Script behaviour |
|---|---|
| `continue` | Script returns normally; Flowable advances to the downstream task |
| `skip_to` | Sets `_hookAction = 'skip_to'` and `_hookSkipTarget = <node_id>`; throws `BpmnError('SKIP_TO')` |
| `terminate` | Sets `_hookAction = 'terminate'` and `_haltReason = <message>`; throws `BpmnError('SKIP_TO')` |

Both non-continue cases use the **same** `BpmnError` code. The `state_updates` dict from a
`skip_to` result is also applied via `execution.setVariable()` before the error is thrown.

#### Boundary event

A `<boundaryEvent>` with a catch-all `<errorEventDefinition/>` is attached to the hook
script task. It catches the `BpmnError('SKIP_TO')` thrown for both SKIP_TO and TERMINATE
and routes execution to `Task_DynamicSkip`.

```xml
<boundaryEvent id="BoundaryError_FLOW_ID" attachedToRef="Task_Hook_FLOW_ID" cancelActivity="true">
  <errorEventDefinition/>
</boundaryEvent>
<sequenceFlow sourceRef="BoundaryError_FLOW_ID" targetRef="Task_DynamicSkip"/>
```

#### Task_DynamicSkip (shared, one per process)

A single shared `<scriptTask>` that reads `_hookAction` and calls Flowable's internal Java
`ChangeActivityStateBuilder` API to move the execution token:

```javascript
var action = String(execution.getVariable('_hookAction') || 'skip_to');
var target = (action === 'terminate')
    ? 'COMPLETE_PROCESS_TASK_ID'           // route to terminal callback
    : String(execution.getVariable('_hookSkipTarget'));  // route to named node
var runtimeService = org.flowable.engine.ProcessEngines
    .getDefaultProcessEngine().getRuntimeService();
runtimeService.createChangeActivityStateBuilder()
    .processInstanceId(execution.getProcessInstanceId())
    .moveActivityIdTo(execution.getCurrentActivityId(), target)
    .changeState();
```

`Task_DynamicSkip` has no outgoing sequence flows. All routing is performed by the Java
API call. Multiple hooks in the same process share this single task; there is no collision
because only one hook executes at a time within a process instance.

---

## Terminal Callback — `complete_process` HTTP Service Task

At the end of the process (after the merge gateway that collects all terminal branches),
an HTTP service task (`flowable:type="http"`) calls the CUGA FLO `complete_process` MCP
tool to signal completion and deliver the final state:

```xml
<serviceTask flowable:type="http" flowable:parallelInSameTransaction="false">
  <extensionElements>
    <flowable:field name="requestMethod"><flowable:string>POST</flowable:string></flowable:field>
    <flowable:field name="requestUrl">
      <flowable:string>http://host.docker.internal:8090/mcp</flowable:string>
    </flowable:field>
    <flowable:field name="requestBody">
      <flowable:expression><![CDATA[{
  "jsonrpc": "2.0", "method": "tools/call",
  "params": { "name": "complete_process", "arguments": {
    "process_key": "${cugaProcessKey}",
    "state": {
      "process_variables": { "credit_score": ${credit_score}, "decision": "${decision}" },
      "is_halted": ${_hookAction == 'terminate'},
      "halt_reason": "${_haltReason}"
    }
  }}, "id": 1
}]]></flowable:expression>
    </flowable:field>
    <flowable:field name="ignoreException"><flowable:string>true</flowable:string></flowable:field>
  </extensionElements>
</serviceTask>
```

`flowable:parallelInSameTransaction="false"` is required so the HTTP call executes outside
Flowable's active DB transaction (avoiding deadlocks). `ignoreException="true"` ensures the
process reaches its end event even if CUGA FLO is transiently unreachable.

`_hookAction` and `_haltReason` are injected as empty-string defaults by `FlowAgent.invoke()`
before the process starts, so the EL expressions always resolve cleanly regardless of
whether any hook fired.

On receipt of `complete_process`, `FlowAgent.invoke()` resolves the `asyncio.Future` it is
waiting on, builds the final `FlowState`, and returns it to the caller.

---

## Process Variable Lifecycle

| Stage | Who writes | Who reads |
|---|---|---|
| Before start | `FlowAgent.invoke()` injects `_user_message`, `_hookAction=""`, `_haltReason=""`, and all YAML `variables:` defaults | — |
| Task execution | TaskAgent via `execute_task` → script task writes output vars | Subsequent tasks, gateways, hooks |
| Gateway routing | DecisionAgent writes `gatewayDecision` | Gateway `conditionExpression` checks |
| Hook SKIP_TO | Hook script writes `_hookAction`, `_hookSkipTarget`, and any `state_updates` | `Task_DynamicSkip` reads `_hookAction` / `_hookSkipTarget` |
| Hook TERMINATE | Hook script writes `_hookAction = 'terminate'`, `_haltReason` | `Task_DynamicSkip` routes to `complete_process`; EL in service task body reads both |
| Process end | `complete_process` service task serialises declared output vars | `FlowAgent.invoke()` receives them in `FlowState` |

---

## BPMN Transformation Know-Hows

The full procedure for transforming a legacy BPMN into a Flowable-ready model is documented
in the know-how files at
`docs/examples/flow_agent_app_inline/model_transform_knowledge/flowable/`:

| Know-how | Covers |
|---|---|
| `flowable_transform_know_how.md` | End-to-end transformation procedure; annotation scanning; transformation order; `complete_process` terminal task |
| `task_transform_know_how.md` | Task Agent ScriptTask: full script template, output variable setters, diagram changes |
| `gateway_transform_know_how.md` | Decision Agent ScriptTask + gateway condition rewrite; diagram shift |
| `hook_transform_know_how.md` | Hook ScriptTask + boundary event + `Task_DynamicSkip`; SKIP_TO and TERMINATE branches; `complete_process` EL expressions |
