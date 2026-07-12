# Hook Transform Know-How: Flow Edge Hook → Flowable Script Task (MCP HTTP)

## Overview

A CUGA FLO hook is an intercept point attached to a sequence flow edge. When the process
reaches that edge, CUGA FLO is given the opportunity to evaluate a policy and decide an
action (e.g. CONTINUE, SKIP_TO).

In Flowable the hook is realised as a `<scriptTask>` inserted **on the flow** between the
upstream element (typically a gateway) and the original downstream task. The script calls
the `evaluate_hook` MCP tool via HTTP. CUGA FLO evaluates the configured hook policy and
responds synchronously.

- **CONTINUE**: Flowable naturally advances to the downstream task via the hook task's normal outgoing flow.
- **SKIP_TO**: The hook script sets `_hookSkipTarget` (and any `state_updates` variables), then throws a `BpmnError('SKIP_TO')`. A boundary error event on the hook task catches this and routes to a shared `Task_DynamicSkip` script task, which uses Flowable's internal Java API (`RuntimeService.createChangeActivityStateBuilder().moveActivityIdTo(...)`) to move the execution token to the target activity. This all happens within the same Flowable transaction, avoiding any race conditions. The FlowableProxy performs no REST calls for SKIP_TO.

Unlike task and gateway script tasks, the hook script task:
- Uses the **flow ID** (not an element ID) as the `hook_id`
- Does **not** set a routing variable — Flowable advances automatically after the script returns
- For SKIP_TO: sets `_hookSkipTarget` and state_updates variables in-script before throwing `BpmnError`

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
    execution.setVariable('_hookSkipTarget', hookResult.skip_to_node);
    if (hookResult.state_updates) {
        for (var k in hookResult.state_updates) {
            if (hookResult.state_updates.hasOwnProperty(k)) { execution.setVariable(k, hookResult.state_updates[k]); }
        }
    }
    throw new org.flowable.engine.delegate.BpmnError('SKIP_TO');
}
  ]]></script>
</scriptTask>
```

### 1b. Add boundary error event and shared dynamic-skip task

Add a boundary error event on `Task_Hook_FLOW_ID` to intercept SKIP_TO, routing to the shared
`Task_DynamicSkip` task. If `Task_DynamicSkip` doesn't exist yet in the process, add it once:

```xml
<!-- error definition (add once per process, before <process>) -->
<error id="Error_SkipTo" name="SkipToError" errorCode="SKIP_TO"/>

<!-- boundary event (one per hook task) — catch-all: no errorRef needed -->
<boundaryEvent id="BoundaryError_FLOW_ID" name="skip to" attachedToRef="Task_Hook_FLOW_ID" cancelActivity="true">
  <errorEventDefinition/>
</boundaryEvent>
<sequenceFlow id="Flow_BoundarySkip_FLOW_ID" sourceRef="BoundaryError_FLOW_ID" targetRef="Task_DynamicSkip"/>

<!-- shared generic skip task (add once per process) — no outgoing sequence flows -->
<scriptTask id="Task_DynamicSkip" name="dynamic skip" scriptFormat="javascript" flowable:autoStoreVariables="false">
  <script><![CDATA[
var target = String(execution.getVariable('_hookSkipTarget'));
var runtimeService = org.flowable.engine.ProcessEngines.getDefaultProcessEngine().getRuntimeService();
runtimeService.createChangeActivityStateBuilder()
    .processInstanceId(execution.getProcessInstanceId())
    .moveActivityIdTo(execution.getCurrentActivityId(), target)
    .changeState();
  ]]></script>
</scriptTask>
```

`Task_DynamicSkip` is shared across all hooks in the process — multiple boundary events can point to it.
Each hook script writes to `_hookSkipTarget` immediately before throwing, so there is no collision
(only one hook executes at a time within a process instance).

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
hook policies can read directly to extract values (e.g. `applicant_id`) without requiring
that value to be pre-extracted into a separate process variable.

---

## Response Parsing

| Tool | `resp.result.content[0].text` is… | What the script does with it |
|------|-----------------------------------|------------------------------|
| `evaluate_hook` | JSON string (`{"action": "continue", ...}` or `{"action": "skip_to", "target_node": "...", "state_updates": {...}}`) | parsed; SKIP_TO sets variables + throws BpmnError; CONTINUE is a no-op |

The script throws on `!resp.result` (MCP error). For SKIP_TO, all state (`_hookSkipTarget`,
`state_updates` keys) is set via `execution.setVariable()` in the same Flowable transaction
before the BpmnError is thrown. The FlowableProxy performs no REST calls for SKIP_TO — routing
is handled entirely within Flowable via the boundary event and `Task_DynamicSkip`.

---

## YAML Config

### `action_permissions`
Declare which hook actions the process is allowed to use. Start with only `continue`:

```yaml
action_permissions:
  permitted_actions:
    - continue
    - skip_to        # add when the policy needs to reroute execution
  prohibited_actions:
    - skip_node
    - terminate
    - swap_nodes
```

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
| No routing variable | — | The hook does not affect gateway routing; Flowable advances to the downstream task automatically |

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

<!-- boundary event to Task_DynamicSkip (SKIP_TO path) -->
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

---

## Transformation Algorithm

Given:
- `flow_id` — the sequence flow ID that is the hook intercept point
- `upstream_element_id`, `downstream_task_id` — the flow's current `sourceRef` / `targetRef`
- `variables` available at the intercept point (from YAML config)
- `hook_policy_file` from the YAML `hooks:` entry

Steps:
1. Add `<scriptTask id="Task_Hook_{flow_id}" ...>` with the `evaluate_hook` script (dynamic variable snippet + SKIP_TO block that sets `_hookSkipTarget` / `state_updates` and throws `BpmnError('SKIP_TO')`)
2. If not already present: add `<error id="Error_SkipTo" .../>` before `<process>` and add `<scriptTask id="Task_DynamicSkip" ...>` (shared, no outgoing flows, uses internal Flowable Java API)
3. Add `<boundaryEvent id="BoundaryError_{flow_id}" attachedToRef="Task_Hook_{flow_id}" ...>` + `<sequenceFlow ... sourceRef="BoundaryError_{flow_id}" targetRef="Task_DynamicSkip"/>`
4. Change `<sequenceFlow id=flow_id>` `targetRef` from `downstream_task_id` to `Task_Hook_{flow_id}`
5. Add a new `<sequenceFlow id="Flow_HookTo_{downstream_task_id}" sourceRef="Task_Hook_{flow_id}" targetRef=downstream_task_id/>`
6. In the YAML: add `hooks:` entry with `id: flow_id`, `type: edge`, `location: flow_id`, `policy: hook_policy_file`
7. In the YAML: ensure `action_permissions.permitted_actions` contains at least `continue`; add `skip_to` if the policy may reroute
8. In the BPMNDiagram: add shape for hook task; add shape for boundary event (bottom-centre of hook task); add shape for `Task_DynamicSkip` (once); update edge waypoints for `FLOW_ID`; add edge for the new connecting flow; add edge from boundary event to `Task_DynamicSkip`
