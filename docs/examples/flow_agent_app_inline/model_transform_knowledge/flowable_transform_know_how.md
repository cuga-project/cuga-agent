# Flowable Transform Know-How: Full BPMN Transformation for CUGA FLO Integration

## Overview

This know-how describes the **end-to-end procedure** for transforming a legacy BPMN model
(e.g. `BPMNdiagram.bpmn`) into a Flowable-specific model that is driven by CUGA FLO agents.

The input is a legacy BPMN file with **text annotation labels** marking which elements to
transform and how:

| Annotation label | Attached to | Transform to apply |
|---|---|---|
| `Task Agent` | a `<bpmn:task>` | CUGA FLO MCP script task (`task_transform_know_how`) |
| `Decision Agent` | an `<exclusiveGateway>` | routing script task + rewired conditions (`gateway_transform_know_how`) |
| `Hook` | a `<sequenceFlow>` | hook script task inserted on the flow (`hook_transform_know_how`) |

Any element **not** annotated is kept as-is.  
Process-level additions (merge gateway + `complete_process` callback) are always required
and are described in the second half of this file.

---

## Overall Transformation Procedure

### Step 1 — Scan the legacy BPMN for transformation annotations

For each `<textAnnotation>` in the `<process>`, read its `<text>` value and find the
`<association>` that references it. The `sourceRef` of the association identifies the
target element:

```
"Task Agent"     → sourceRef is a <bpmn:task> ID
"Decision Agent" → sourceRef is a <bpmn:exclusiveGateway> ID
"Hook"           → sourceRef is a <bpmn:sequenceFlow> ID
```

Build three lists from this scan:
- `task_agents`: list of task IDs
- `decision_agents`: list of gateway IDs
- `hooks`: list of flow IDs

### Step 2 — Transform task agents

For each task ID in `task_agents`, apply `task_transform_know_how`:
- Replace `<bpmn:task>` with `<scriptTask>` (same `id`, same incoming/outgoing flows)
- Populate `OUTPUT_VARIABLE_SETTERS` from the YAML `output_mapping` for that task
- Add "Task Agent" `<textAnnotation>` + `<association>` (preserve or create)
- Update `BPMNDiagram`: replace `BPMNShape`; add annotation shape + edge

Task transforms are independent of each other and of gateway/hook transforms — do them first.

### Step 3 — Transform decision agents

For each gateway ID in `decision_agents`, apply `gateway_transform_know_how`:
- Insert `<scriptTask id="Activity_Route{gateway_id}">` immediately before the gateway
- Rewire the incoming flow to target the new routing script task
- Add a new flow from the routing script task to the gateway
- Replace each outgoing flow's `<conditionExpression>` with `${ROUTING_VAR == 'FLOW_ID'}`
- Add "Decision Agent" `<textAnnotation>` + `<association>` above the routing script task
- Update `BPMNDiagram`: shift gateway and downstream shapes right; add routing task shape;
  add annotation shape + edge; update all affected edge waypoints

Do gateway transforms **after** task transforms so that shifted waypoints don't conflict
with task shape positions already placed.

### Step 4 — Transform hooks

For each flow ID in `hooks`, apply `hook_transform_know_how`:
- Insert `<scriptTask id="Task_Hook_{flow_id}">` on the flow between its `sourceRef` and `targetRef`
- Change the original flow's `targetRef` to the new hook task
- Add a new flow from the hook task to the original `targetRef`
- Add `<boundaryEvent>` on the hook task + flow to `Task_DynamicSkip`
- If `Task_DynamicSkip` does not exist yet: add it once (shared across all hooks)
- If `<error id="Error_SkipTo">` does not exist yet: add it once before `<process>`
- Add "Hook" `<textAnnotation>` + `<association>` above the hook task
- Update `BPMNDiagram`: add hook task shape; add boundary event shape; add annotation shape + edge;
  update edge waypoints for the split flow; add edge for the new connecting flow

Do hook transforms **after** gateway transforms: hooks reference flow IDs that may have been
rewired in Step 3 (the incoming flow to a decision-agent gateway may itself be a hook point).

### Step 5 — Add the `complete_process` callback (this file)

Always required regardless of which elements were annotated:
- Identify all terminal tasks (tasks with no outgoing flow in the **transformed** model,
  excluding `Task_DynamicSkip` which has no outgoing flow by design)
- Redirect their outgoing flows to a new merge `<exclusiveGateway id="Gateway_Merge">`
- Add the `<serviceTask>` `complete_process` after the merge gateway (see below)
- Note its `id` as `COMPLETE_PROCESS_TASK_ID` and update `Task_DynamicSkip` to target it
  for TERMINATE routing
- Add a final `<endEvent>` after `complete_process`
- Update `BPMNDiagram` with shapes and edges for the new elements

### Step 6 — Update the YAML config

Aggregate all YAML entries produced by the individual transforms:
- `tasks:` — one entry per task agent (MCP tool, output mapping, policy)
- `gateways:` — one entry per decision agent (mode, condition, flows, policy)
- `hooks:` — one entry per hook (id, type, location, policy)
- `action_permissions.permitted_actions:` — add `skip_to` if any hook may reroute;
  `terminate` if any hook may halt
- `variables:` — declare `_hookAction: {type: str, default: ""}` and
  `_haltReason: {type: str, default: ""}` whenever any hook is present

### Step 7 — Verify

Check the transformed model for:
- Every annotated element has been replaced or supplemented
- `Task_DynamicSkip` targets the correct `COMPLETE_PROCESS_TASK_ID`
- All sequence flow `sourceRef`/`targetRef` pairs are consistent (no dangling references)
- `complete_process` body EL expressions match the declared `variables:` names
- `BPMNDiagram` has a shape for every element in `<process>` and an edge for every flow

---

## Example: Loan Approval Process

Input: `BPMNdiagram.bpmn`

Annotation scan results:
- `task_agents`: `["Activity_0oydey5"]` (check credit)
- `decision_agents`: `["Gateway_09ad5fc"]` (credit decision gateway)
- `hooks`: `["Flow_1e5ztf6", "Flow_0ybszcv"]` (pre-credit screen; approval intercept)

Terminal tasks in the legacy model: `Activity_1h9ix55` (give loan), `Activity_131ar38`
(send rejection). Both already feed into merge gateway `Gateway_15y9k7z` → `endEvent`.

Transformation applied:
1. `Activity_0oydey5` → script task calling `execute_task` MCP tool
2. `Gateway_09ad5fc` → `Activity_RouteGateway` routing script task inserted before it;
   conditions replaced with `${gatewayDecision == 'FLOW_ID'}`
3. `Flow_1e5ztf6` → `Task_PreCreditHook` inserted; `Task_DynamicSkip` added once
4. `Flow_0ybszcv` → `Task_HookHttp` inserted
5. Merge gateway already present (`Gateway_15y9k7z`); end event replaced with
   `sid-41B1E1F3-9EAE-4B1D-9C4B-09D98F5E1EF2` (`CUGA FLO complete process`) → new end event

Output: `Loan-Approval-Process.bpmn20.xml`

---

## `complete_process` Service Task (Process-Level Addition)

When transforming a legacy BPMN process into a CUGA FLO–driven Flowable process, beyond
converting individual tasks and gateways, two process-level elements must be added:

1. A **merge gateway** that collects all terminal paths into a single point.
2. A **`complete_process` HTTP service task** that calls CUGA FLO back when the process ends,
   delivering the final process state (variables, halt status, halt reason).

Without the `complete_process` task, CUGA FLO never receives the outcome and the flow hangs
waiting for a response that never arrives.

---

## Source: Legacy Process Terminal Pattern

A legacy BPMN typically ends with one or more terminal tasks leading directly into `<endEvent>`
elements — one per outcome branch:

```xml
<endEvent id="EndEvent_Approve"/>
<endEvent id="EndEvent_Reject"/>
```

---

## Target: Merge Gateway + `complete_process` Service Task

### 1. Replace per-branch end events with a shared merge gateway

Remove the per-branch `<endEvent>` elements. Instead, have all terminal tasks flow into a
single merge `<exclusiveGateway>`, then continue to `complete_process`:

```xml
<exclusiveGateway id="Gateway_Merge"/>

<!-- one sequenceFlow per terminal task -->
<sequenceFlow id="Flow_ApproveToMerge" sourceRef="TERMINAL_TASK_A" targetRef="Gateway_Merge"/>
<sequenceFlow id="Flow_RejectToMerge"  sourceRef="TERMINAL_TASK_B" targetRef="Gateway_Merge"/>

<sequenceFlow id="Flow_MergeToComplete" sourceRef="Gateway_Merge" targetRef="COMPLETE_PROCESS_TASK_ID"/>
```

`Task_DynamicSkip` (the hook routing task) also targets `COMPLETE_PROCESS_TASK_ID` directly
via `moveActivityIdTo` for the TERMINATE action — it bypasses the merge gateway.

### 2. Add the `complete_process` HTTP service task

This is a Flowable **HTTP service task** (`flowable:type="http"`), not a script task.
`flowable:parallelInSameTransaction="false"` is required so the HTTP call executes outside
the active Flowable DB transaction.

```xml
<serviceTask id="COMPLETE_PROCESS_TASK_ID" name="CUGA FLO complete process"
    flowable:parallelInSameTransaction="false" flowable:type="http">
  <extensionElements>
    <flowable:field name="requestMethod">
      <flowable:string><![CDATA[POST]]></flowable:string>
    </flowable:field>
    <flowable:field name="requestUrl">
      <flowable:string><![CDATA[http://host.docker.internal:8090/mcp]]></flowable:string>
    </flowable:field>
    <flowable:field name="requestHeaders">
      <flowable:string><![CDATA[Content-Type: application/json
Accept: application/json, text/event-stream]]></flowable:string>
    </flowable:field>
    <flowable:field name="requestBody">
      <flowable:expression><![CDATA[{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "complete_process",
    "arguments": {
      "process_key": "${cugaProcessKey}",
      "state": {
        "process_id": "${cugaProcessKey}",
        "process_name": "PROCESS_DISPLAY_NAME",
        "process_variables": {
          OUTPUT_VARIABLE_EL_EXPRESSIONS
        },
        "messages": [],
        "is_complete": false,
        "is_halted": ${_hookAction == 'terminate'},
        "halt_reason": "${_haltReason}"
      }
    }
  },
  "id": 1
}]]></flowable:expression>
    </flowable:field>
    <flowable:field name="ignoreException">
      <flowable:string><![CDATA[true]]></flowable:string>
    </flowable:field>
  </extensionElements>
</serviceTask>
```

### 3. Add the end event

```xml
<sequenceFlow id="Flow_CompleteToEnd" sourceRef="COMPLETE_PROCESS_TASK_ID" targetRef="END_EVENT_ID"/>
<endEvent id="END_EVENT_ID"/>
```

---

## Template Parameters

### `PROCESS_DISPLAY_NAME`
The human-readable process name shown in CUGA FLO (e.g. `"Loan Approval"`). Not an EL
expression — a plain string literal in the JSON body.

### `OUTPUT_VARIABLE_EL_EXPRESSIONS`
One JSON key-value pair per process output variable, using Flowable EL expressions:

```
"VARIABLE_NAME": ${VARIABLE_NAME}        <!-- for numbers and booleans -->
"VARIABLE_NAME": "${VARIABLE_NAME}"      <!-- for strings -->
```

Example for a process that outputs `credit_score` (number) and `decision` (string):

```json
"credit_score": ${credit_score},
"decision": "${decision}"
```

Only include variables that are meaningful output for the CUGA FLO caller. Internal routing
variables (`gatewayDecision`, `_hookAction`, etc.) should be omitted.

### `cugaProcessKey`
Injected automatically by FlowAgent when starting the Flowable process instance. Do not
declare it in the YAML `variables:` block — it is always present.

### `_hookAction` / `_haltReason`
Injected as empty-string defaults by FlowAgent before the process starts. The EL expressions
`${_hookAction == 'terminate'}` and `"${_haltReason}"` therefore always resolve cleanly,
even when no hook has fired.

### `ignoreException`
Set to `true` so a transient CUGA FLO connectivity failure does not block the process from
reaching the end event. The Flowable process always completes; CUGA FLO may log a warning.

---

## YAML Config

No YAML entry is needed for the `complete_process` task itself — it is transparent to the
CUGA FLO configuration layer. Ensure the YAML `variables:` block declares any process
variables that appear in `OUTPUT_VARIABLE_EL_EXPRESSIONS`:

```yaml
variables:
  credit_score:
    type: float
    default: 0.0
  decision:
    type: str
    default: ""
  _hookAction:
    type: str
    default: ""
  _haltReason:
    type: str
    default: ""
```

`_hookAction` and `_haltReason` must be declared so FlowAgent initialises them to `""`
before the process starts, which makes the `complete_process` EL expressions safe.

---

## Fixed Constants

| Property | Value | Reason |
|----------|-------|--------|
| `flowable:type` | `http` | Makes this a Flowable HTTP service task — no script needed |
| `flowable:parallelInSameTransaction` | `false` | Runs HTTP call outside the active Flowable DB transaction; omitting this can cause deadlocks or `OptimisticLockingException` |
| `requestUrl` | `http://host.docker.internal:8090/mcp` | FlowableProxy MCP endpoint; `host.docker.internal` resolves the host from inside the Flowable Docker container |
| `Accept` header | `application/json, text/event-stream` | FastMCP requires both; omitting `text/event-stream` returns HTTP 406 |
| `ignoreException` | `true` | Process must complete even if CUGA FLO is temporarily unreachable |
| `is_complete` | `false` | Flowable signals completion; CUGA FLO derives finality from receiving the callback, not from this flag |

---

## Diagram Changes

Add the merge gateway and `complete_process` service task to the right of the last terminal
tasks, with the end event further right:

```xml
<!-- merge gateway: centred vertically on the main flow lane -->
<bpmndi:BPMNShape bpmnElement="Gateway_Merge" id="BPMNShape_Gateway_Merge">
  <omgdc:Bounds height="40.0" width="40.0" x="MERGE_X" y="MAIN_Y + 20"/>
</bpmndi:BPMNShape>

<!-- complete_process service task: 100×80, same y as other tasks -->
<bpmndi:BPMNShape bpmnElement="COMPLETE_PROCESS_TASK_ID" id="BPMNShape_COMPLETE_PROCESS_TASK_ID">
  <omgdc:Bounds height="80.0" width="100.0" x="COMPLETE_X" y="MAIN_Y"/>
</bpmndi:BPMNShape>

<!-- end event: 28×28 -->
<bpmndi:BPMNShape bpmnElement="END_EVENT_ID" id="BPMNShape_END_EVENT_ID">
  <omgdc:Bounds height="28.0" width="28.0" x="END_X" y="MAIN_Y + 26"/>
</bpmndi:BPMNShape>
```

Add edges from each terminal task to the merge gateway, from the merge gateway to
`complete_process`, and from `complete_process` to the end event. Typical spacing:
~30 px gap between elements.

---

## Transformation Algorithm

Given:
- `terminal_task_ids`: list of all tasks that have no outgoing flow in the legacy model
- `output_variables`: dict of `{variable_name: type}` from the YAML `output_mapping`
- `process_display_name`: human-readable name for the CUGA FLO callback

Steps:
1. Remove all `<endEvent>` elements from terminal branches
2. Add `<exclusiveGateway id="Gateway_Merge"/>` after the rightmost terminal task
3. For each terminal task: change its outgoing flow `targetRef` to `Gateway_Merge`
4. Add `<sequenceFlow ... sourceRef="Gateway_Merge" targetRef="COMPLETE_PROCESS_TASK_ID"/>`
5. Add the `<serviceTask>` element with the `complete_process` body (see above); replace
   `OUTPUT_VARIABLE_EL_EXPRESSIONS` with one EL entry per output variable
6. Add `<sequenceFlow ... sourceRef="COMPLETE_PROCESS_TASK_ID" targetRef="END_EVENT_ID"/>` and `<endEvent id="END_EVENT_ID"/>`
7. Note the `COMPLETE_PROCESS_TASK_ID` — it is referenced by `Task_DynamicSkip` for TERMINATE routing
8. In the YAML: declare `_hookAction` and `_haltReason` in `variables:` with `default: ""`
9. In the BPMNDiagram: add shapes for merge gateway, `complete_process` task, and end event;
   add edges for all new flows; update existing terminal-task edge waypoints to target the merge gateway
