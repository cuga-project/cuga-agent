# Hook Transform Know-How: Flow Edge Hook → Flowable Script Task (MCP HTTP)

## Overview

A CUGA FLO hook is an intercept point attached to a sequence flow edge. When the process
reaches that edge, CUGA FLO is given the opportunity to evaluate a policy and decide an
action (CONTINUE, SKIP_TO, or TERMINATE).

In Flowable the hook is realised as a `<scriptTask>` inserted **on the flow** between the
upstream element (typically a gateway) and the original downstream task. The script calls
the `evaluate_hook` MCP tool via HTTP. CUGA FLO evaluates the configured hook policy and
responds synchronously.

- **CONTINUE**: Flowable naturally advances to the downstream task via the hook task's normal outgoing flow.
- **SKIP_TO**: The hook script sets `_hookAction = 'skip_to'`, `_hookSkipTarget`, and any `state_updates` variables, then throws a `BpmnError('SKIP_TO')`. A boundary error event on the hook task catches this and routes to a shared `Task_DynamicSkip` script task, which uses Flowable's internal Java API (`RuntimeService.createChangeActivityStateBuilder().moveActivityIdTo(...)`) to move the execution token to the target activity.
- **TERMINATE**: The hook script sets `_hookAction = 'terminate'` and `_haltReason`, then throws the same `BpmnError('SKIP_TO')`. `Task_DynamicSkip` detects `_hookAction == 'terminate'` and routes directly to the `complete_process` HTTP service task, bypassing the rest of the process. CUGA FLO marks the flow as halted.

Both SKIP_TO and TERMINATE share the same BpmnError code and boundary event. Routing diverges in `Task_DynamicSkip` based on `_hookAction`. The FlowableProxy performs no REST calls for either — everything happens within Flowable's internal Java API.

`_hookAction` and `_haltReason` are always present as process variables: FlowAgent.invoke() injects them as empty-string defaults before starting the Flowable process, so the `complete_process` EL expressions always resolve cleanly.

Unlike task and gateway script tasks, the hook script task:
- Uses the **flow ID** (not an element ID) as the `hook_id`
- Does **not** set a routing variable — Flowable advances automatically after the script returns (CONTINUE)
- For SKIP_TO / TERMINATE: sets `_hookAction` (and related variables) in-script before throwing `BpmnError`

---

## Source: Sequence Flow Edge (hook intercept point)

```xml
<sequenceFlow id="FLOW_ID" name="FLOW_NAME"
    sourceRef="UPSTREAM_ELEMENT" targetRef="DOWNSTREAM_TASK">
  <conditionExpression xsi:type="tFormalExpression">
    <![CDATA[${ROUTING_VAR == 'FLOW_ID'}]]>
  </conditionExpression>
</sequenceFlow>
```

The hook is configured in the YAML on this flow ID (`FLOW_ID`). No change is made to the
`<sequenceFlow>` element itself — only new elements are inserted between the two endpoints.

---

## Target: Script Task Inserted on the Flow

### 1. Insert the hook script task

Add a new `<scriptTask>` with a fresh ID (e.g. `Task_Hook_FLOW_ID`). Keep `name` descriptive:

```xml
<scriptTask id="Task_Hook_FLOW_ID" name="Notify CUGA FLO Hook"
    scriptFormat="javascript" flowable:autoStoreVariables="false">
  <script><![CDATA[
var allVars = execution.getVariables(); var varKeys = allVars.keySet().toArray(); var varParts = [];
for (var vi = 0; vi < varKeys.length; vi++) {
    var vk = varKeys[vi]; var vv = allVars.get(vk);
    if (vv === null || vv === undefined) { varParts.push('"' + vk + '":null'); }
    else { var sv = String(vv);
        if (sv.trim() !== '' && !isNaN(sv)) { varParts.push('"' + vk + '":' + sv); }
        else if (sv === 'true' || sv === 'false') { varParts.push('"' + vk + '":' + sv); }
        else { varParts.push('"' + vk + '":"' + sv.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '\\r') + '"'); }
    }
}
var processVarsJson = '{' + varParts.join(',') + '}';
var body = '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"evaluate_hook","arguments":{"hook_id":"FLOW_ID","ctx":{"process_instance_id":"' + execution.processInstanceId + '","element_id":"Task_Hook_FLOW_ID","element_name":"Notify CUGA FLO Hook","current_state":{"process_variables":' + processVarsJson + '},"execution_history":[],"process_model_summary":{}}}},"id":1}';
var url = new java.net.URL('http://host.docker.internal:8090/mcp');
var conn = url.openConnection();
conn.setRequestMethod('POST');
conn.setRequestProperty('Content-Type', 'application/json');
conn.setRequestProperty('Accept', 'application/json, text/event-stream');
conn.setReadTimeout(120000);
conn.setDoOutput(true);
var os = conn.getOutputStream();
os.write(new java.lang.String(body).getBytes('UTF-8'));
os.close();
var br = new java.io.BufferedReader(new java.io.InputStreamReader(conn.getInputStream(), 'UTF-8'));
var raw = '';
var line;
while ((line = br.readLine()) !== null) {
    if (line.indexOf('data:') === 0) { raw = line.substring(5).trim(); break; }
}
br.close();
var resp = JSON.parse(raw);
if (!resp.result) throw new Error('evaluate_hook error: ' + JSON.stringify(resp.error || resp));
var hookResult = JSON.parse(resp.result.content[0].text);
if (hookResult.action === 'skip_to') {
    execution.setVariable('_hookAction', 'skip_to');
    execution.setVariable('_hookSkipTarget', hookResult.skip_to_node);
    if (hookResult.state_updates) {
        for (var k in hookResult.state_updates) {
            if (hookResult.state_updates.hasOwnProperty(k)) { execution.setVariable(k, hookResult.state_updates[k]); }
        }
    }
    throw new org.flowable.engine.delegate.BpmnError('SKIP_TO');
} else if (hookResult.action === 'terminate') {
    execution.setVariable('_hookAction', 'terminate');
    execution.setVariable('_haltReason', hookResult.message || 'Hook terminated the process');
    throw new org.flowable.engine.delegate.BpmnError('SKIP_TO');
}
  ]]></script>
</scriptTask>
```

### 1b. Add boundary error event and shared dynamic-skip task

Add a boundary error event on `Task_Hook_FLOW_ID` to intercept SKIP_TO/TERMINATE, routing to the shared
`Task_DynamicSkip` task. If `Task_DynamicSkip` doesn't exist yet in the process, add it once:

```xml
<!-- error definition (add once per process, before <process>) -->
<error id="Error_SkipTo" name="SkipToError" errorCode="SKIP_TO"/>

<!-- boundary event (one per hook task) — catch-all: no errorRef needed -->
<boundaryEvent id="BoundaryError_FLOW_ID" name="action event" attachedToRef="Task_Hook_FLOW_ID" cancelActivity="true">
  <errorEventDefinition/>
</boundaryEvent>
<sequenceFlow id="Flow_BoundarySkip_FLOW_ID" sourceRef="BoundaryError_FLOW_ID" targetRef="Task_DynamicSkip"/>

<!-- shared generic skip/terminate task (add once per process) — no outgoing sequence flows -->
<scriptTask id="Task_DynamicSkip" name="hooks handling" scriptFormat="javascript" flowable:autoStoreVariables="false">
  <script><![CDATA[
var action = String(execution.getVariable('_hookAction') || 'skip_to');
var target = (action === 'terminate')
    ? 'COMPLETE_PROCESS_TASK_ID'
    : String(execution.getVariable('_hookSkipTarget'));
var runtimeService = org.flowable.engine.ProcessEngines.getDefaultProcessEngine().getRuntimeService();
runtimeService.createChangeActivityStateBuilder()
    .processInstanceId(execution.getProcessInstanceId())
    .moveActivityIdTo(execution.getCurrentActivityId(), target)
    .changeState();
  ]]></script>
</scriptTask>
```

`COMPLETE_PROCESS_TASK_ID` is the `id` of the `complete_process` HTTP service task in the process
(the terminal task that calls CUGA back). Replace with the actual element ID from the BPMN.

`Task_DynamicSkip` is shared across all hooks in the process — multiple boundary events can point to it.
Each hook script writes to `_hookAction` (and `_hookSkipTarget` for skip_to) immediately before
throwing, so there is no collision (only one hook executes at a time within a process instance).

### 1c. Update the `complete_process` HTTP service task body

The terminal `complete_process` HTTP service task must use EL expressions for the halt fields so
TERMINATE is propagated to CUGA:

```json
{
  "process_key": "${cugaProcessKey}",
  "state": {
    "process_id": "${cugaProcessKey}",
    "process_name": "PROCESS_DISPLAY_NAME",
    "process_variables": { ... },
    "messages": [],
    "is_complete": false,
    "is_halted": ${_hookAction == 'terminate'},
    "halt_reason": "${_haltReason}"
  }
}
```

`${_hookAction == 'terminate'}` evaluates to the JSON boolean `true` / `false`.
`"${_haltReason}"` interpolates the string value (empty string when not halted).
Both variables are always present because FlowAgent.invoke() injects empty-string defaults
before starting the Flowable process.

### 2. Split the intercepted flow into two

```xml
<!-- original flow now targets the hook task instead of the downstream task -->
<sequenceFlow id="FLOW_ID" name="FLOW_NAME"
    sourceRef="UPSTREAM_ELEMENT" targetRef="Task_Hook_FLOW_ID">
  <conditionExpression xsi:type="tFormalExpression">
    <![CDATA[${ROUTING_VAR == 'FLOW_ID'}]]>
  </conditionExpression>
</sequenceFlow>

<!-- new flow from hook task to the original downstream task -->
<sequenceFlow id="Flow_HookTo_DOWNSTREAM_TASK"
    sourceRef="Task_Hook_FLOW_ID" targetRef="DOWNSTREAM_TASK"/>
```

The original flow ID (`FLOW_ID`) and its condition are preserved — the gateway still routes
to it as before. Only its `targetRef` changes to point at the hook task.

---

## Template Parameters

### Process Variables
All Flowable process variables are forwarded automatically using `execution.getVariables()` —
same dynamic snippet as task and gateway script tasks. This includes `_user_message`, which
hook policies can read directly to extract values (e.g. applicant name, applicant ID) without
requiring those values to be pre-extracted into separate process variables. Hook LLM policies
should always check `_user_message` as a fallback when a target field is absent or empty in
the formal process variables.

---

## Response Parsing

| Tool | `resp.result.content[0].text` is… | What the script does with it |
|------|-----------------------------------|------------------------------|
| `evaluate_hook` | JSON string: `{"action":"continue"}` or `{"action":"skip_to","skip_to_node":"...","state_updates":{...}}` or `{"action":"terminate","message":"..."}` | parsed; SKIP_TO sets `_hookAction`/`_hookSkipTarget`/state_updates + throws BpmnError; TERMINATE sets `_hookAction`/`_haltReason` + throws BpmnError; CONTINUE is a no-op |

The script throws on `!resp.result` (MCP error). For SKIP_TO and TERMINATE, all state is set
via `execution.setVariable()` in the same Flowable transaction before the BpmnError is thrown.
The FlowableProxy performs no REST calls for either action — routing is handled entirely within
Flowable via the boundary event and `Task_DynamicSkip`.

---

## YAML Config

### `action_permissions`
Declare which hook actions the process is allowed to use:

```yaml
action_permissions:
  permitted_actions:
    - continue
    - skip_to        # add when the policy needs to reroute execution
    - terminate      # add when the policy may terminate the process early
  prohibited_actions:
    - skip_node
    - swap_nodes
```

`terminate` must be in `permitted_actions` (not `prohibited_actions`) for the LangGraph hook
engine to pass it through. If absent from `permitted_actions`, the engine silently downgrades
`terminate` to `continue` and the process will not halt.

### `hooks`
Add one entry per hook, keyed by the flow edge ID that is intercepted:

```yaml
hooks:
  - id: "FLOW_ID"
    type: "edge"
    location: "FLOW_ID"
    policy: "../policies/hook-HOOK_NAME.md"
```

- `id` / `location`: both set to the flow ID — this is what the script task passes as `hook_id`
- `type`: always `edge` for a flow-level intercept
- `policy`: path to the markdown policy file governing what action to take

When `hooks:` is absent or the `id` is not found, CUGA FLO defaults to CONTINUE and logs a
warning — the process is not blocked.

---

## Fixed Constants

| Property | Value | Reason |
|----------|-------|--------|
| `scriptFormat` | `javascript` | Required; omitting causes Flowable to skip execution silently |
| `flowable:autoStoreVariables` | `false` | Prevents Flowable auto-binding script-local vars as process variables |
| `Accept` header | `application/json, text/event-stream` | FastMCP requires both; omitting `text/event-stream` returns HTTP 406 |
| `setReadTimeout` | `120000` | Hook policy evaluation via LLM can take 10–60 s; default 5 s causes IO exception |
| Response parsing | strip `data:` prefix before `JSON.parse` | FastMCP wraps its response in SSE framing (`data: {...}\n\n`) |
| No routing variable | — | The hook does not affect gateway routing; Flowable advances to the downstream task automatically (CONTINUE) |
| `_hookAction` default | `""` (empty string) | Injected by FlowAgent.invoke() via setdefault; `complete_process` EL `${_hookAction == 'terminate'}` evaluates to false when not set |
| `_haltReason` default | `""` (empty string) | Injected by FlowAgent.invoke() via setdefault; safe to interpolate as empty string in `complete_process` body |

---

## Diagram Changes

Replace the direct edge between `UPSTREAM_ELEMENT` and `DOWNSTREAM_TASK` with a hook task
at the midpoint of the original arrow. Also add the boundary error event shape and an edge
to the shared `Task_DynamicSkip`. If `Task_DynamicSkip` doesn't have a shape yet, add it
once (e.g. below and to the right of the hook tasks, away from the main flow).

```xml
<!-- hook task shape — placed at midpoint between upstream and downstream -->
<bpmndi:BPMNShape bpmnElement="Task_Hook_FLOW_ID" id="BPMNShape_Task_Hook_FLOW_ID">
  <omgdc:Bounds height="80.0" width="100.0" x="HOOK_X" y="HOOK_Y"/>
</bpmndi:BPMNShape>

<!-- boundary event shape — bottom-centre of hook task (HOOK_X+35, HOOK_Y+65) -->
<bpmndi:BPMNShape bpmnElement="BoundaryError_FLOW_ID" id="BPMNShape_BoundaryError_FLOW_ID">
  <omgdc:Bounds height="30.0" width="30.0" x="HOOK_X+35" y="HOOK_Y+65"/>
</bpmndi:BPMNShape>

<!-- shared dynamic skip task — add once per process -->
<bpmndi:BPMNShape bpmnElement="Task_DynamicSkip" id="BPMNShape_Task_DynamicSkip">
  <omgdc:Bounds height="80.0" width="100.0" x="SKIP_X" y="SKIP_Y"/>
</bpmndi:BPMNShape>
```

Update the edge for `FLOW_ID` so its target waypoint lands at the hook task's left edge,
and add edges for the new flows. Route the boundary-event-to-skip edge to clear any tasks
that sit between the boundary event and `Task_DynamicSkip`:

```xml
<!-- updated original flow: now ends at hook task -->
<bpmndi:BPMNEdge bpmnElement="FLOW_ID" ...>
  <omgdi:waypoint x="UPSTREAM_RIGHT_X" y="UPSTREAM_Y"/>
  <omgdi:waypoint x="HOOK_X" y="HOOK_CENTRE_Y"/>
</bpmndi:BPMNEdge>

<!-- new flow: hook task to downstream task (CONTINUE path) -->
<bpmndi:BPMNEdge bpmnElement="Flow_HookTo_DOWNSTREAM_TASK" ...>
  <omgdi:waypoint x="HOOK_RIGHT_X" y="HOOK_CENTRE_Y"/>
  <omgdi:waypoint x="DOWNSTREAM_X" y="DOWNSTREAM_CENTRE_Y"/>
</bpmndi:BPMNEdge>

<!-- boundary event to Task_DynamicSkip (SKIP_TO / TERMINATE path) -->
<bpmndi:BPMNEdge bpmnElement="Flow_BoundarySkip_FLOW_ID" ...
    flowable:sourceDockerX="15.0" flowable:sourceDockerY="30.0"
    flowable:targetDockerX="50.0" flowable:targetDockerY="0.0">
  <!-- route around any intervening shapes -->
  <omgdi:waypoint x="BOUNDARY_CENTRE_X" y="BOUNDARY_BOTTOM_Y"/>
  <omgdi:waypoint x="SKIP_CENTRE_X" y="BOUNDARY_BOTTOM_Y"/>
  <omgdi:waypoint x="SKIP_CENTRE_X" y="SKIP_Y"/>
</bpmndi:BPMNEdge>
```

If there is insufficient horizontal space between `UPSTREAM_ELEMENT` and `DOWNSTREAM_TASK`,
shift `DOWNSTREAM_TASK` and all elements further right to accommodate the hook task (100 px
wide) plus spacing (≥ 30 px gap on each side).

### Text annotation

Add a **"Hook"** label above the hook script task. In the `<process>` block:

```xml
<textAnnotation id="TextAnnotation_Hook_FLOW_ID">
  <text>Hook</text>
</textAnnotation>
<association id="Association_Hook_FLOW_ID" sourceRef="Task_Hook_FLOW_ID"
    targetRef="TextAnnotation_Hook_FLOW_ID" associationDirection="None"/>
```

In the `BPMNDiagram` block (annotation is ~45 px above the hook task, same width):

```xml
<bpmndi:BPMNShape bpmnElement="TextAnnotation_Hook_FLOW_ID" id="BPMNShape_TextAnnotation_Hook_FLOW_ID">
  <omgdc:Bounds height="30.0" width="100.0" x="HOOK_X" y="HOOK_Y - 45"/>
</bpmndi:BPMNShape>

<bpmndi:BPMNEdge bpmnElement="Association_Hook_FLOW_ID" id="BPMNEdge_Association_Hook_FLOW_ID"
    flowable:sourceDockerX="50.0" flowable:sourceDockerY="0.0"
    flowable:targetDockerX="1.0" flowable:targetDockerY="15.0">
  <omgdi:waypoint x="HOOK_X + 50" y="HOOK_Y"/>
  <omgdi:waypoint x="HOOK_X + 10" y="HOOK_Y - 15"/>
</bpmndi:BPMNEdge>
```

---

## Transformation Algorithm

Given:
- `flow_id` — the sequence flow ID that is the hook intercept point
- `upstream_element_id`, `downstream_task_id` — the flow's current `sourceRef` / `targetRef`
- `complete_process_task_id` — the ID of the terminal HTTP service task that calls CUGA back
- `variables` available at the intercept point (from YAML config)
- `hook_policy_file` from the YAML `hooks:` entry

Steps:
1. Add `<scriptTask id="Task_Hook_{flow_id}" ...>` with the `evaluate_hook` script (dynamic variable snippet + SKIP_TO block that sets `_hookAction`/`_hookSkipTarget`/`state_updates` and throws `BpmnError('SKIP_TO')` + TERMINATE block that sets `_hookAction`/`_haltReason` and throws the same `BpmnError('SKIP_TO')`)
2. If not already present: add `<error id="Error_SkipTo" .../>` before `<process>` and add `<scriptTask id="Task_DynamicSkip" ...>` (shared, no outgoing flows; reads `_hookAction` to choose between `COMPLETE_PROCESS_TASK_ID` and `_hookSkipTarget`)
3. Add `<boundaryEvent id="BoundaryError_{flow_id}" attachedToRef="Task_Hook_{flow_id}" ...>` + `<sequenceFlow ... sourceRef="BoundaryError_{flow_id}" targetRef="Task_DynamicSkip"/>`
4. Change `<sequenceFlow id=flow_id>` `targetRef` from `downstream_task_id` to `Task_Hook_{flow_id}`
5. Add a new `<sequenceFlow id="Flow_HookTo_{downstream_task_id}" sourceRef="Task_Hook_{flow_id}" targetRef=downstream_task_id/>`
6. Update the `complete_process` HTTP service task body to use `${_hookAction == 'terminate'}` for `is_halted` and `"${_haltReason}"` for `halt_reason`
7. In the YAML: add `hooks:` entry with `id: flow_id`, `type: edge`, `location: flow_id`, `policy: hook_policy_file`
8. In the YAML: ensure `action_permissions.permitted_actions` contains `continue`; add `skip_to` if the policy may reroute; add `terminate` if the policy may halt the process early (must NOT be in `prohibited_actions`)
9. In the BPMNDiagram: add shape for hook task; add shape for boundary event (bottom-centre of hook task); add shape for `Task_DynamicSkip` (once); update edge waypoints for `FLOW_ID`; add edge for the new connecting flow; add edge from boundary event to `Task_DynamicSkip`
