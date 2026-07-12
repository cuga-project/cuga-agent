# Hook Transform Know-How: Flow Edge Hook → Flowable Script Task (MCP HTTP)

## Overview

A CUGA FLO hook is an intercept point attached to a sequence flow edge. When the process
reaches that edge, CUGA FLO is given the opportunity to evaluate a policy and decide an
action (e.g. CONTINUE, TERMINATE, SKIP_TO).

In Flowable the hook is realised as a `<scriptTask>` inserted **on the flow** between the
upstream element (typically a gateway) and the original downstream task. The script calls
the `evaluate_hook` MCP tool via HTTP. CUGA FLO evaluates the configured hook policy,
delegates the action to the Flowable proxy for realisation, and responds synchronously.
Flowable receives the response and the script task completes — because the action is CONTINUE,
the process naturally advances to the downstream task with no additional REST calls.

Unlike task and gateway script tasks, the hook script task:
- Uses the **flow ID** (not an element ID) as the `hook_id`
- Does **not** extract output variables from the response — the process state is unchanged
- Does **not** set a routing variable — Flowable advances automatically after the script returns

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
PROCESS_VARIABLE_GETTERS
var body = '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"evaluate_hook","arguments":{"hook_id":"FLOW_ID","ctx":{"process_instance_id":"' + execution.processInstanceId + '","element_id":"Task_Hook_FLOW_ID","element_name":"Notify CUGA FLO Hook","current_state":{"process_variables":{PROCESS_VARIABLES_JSON}},"execution_history":[],"process_model_summary":{}}}},"id":1}';
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
  ]]></script>
</scriptTask>
```

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

### `PROCESS_VARIABLE_GETTERS`
One `var` per process variable needed as context for the hook policy. Typically the same
variables available at the intercept point (credit score, decision, routing key):

```javascript
var creditScore = execution.getVariable('credit_score');
var decision    = execution.getVariable('decision');
var cugaKey     = execution.getVariable('cugaProcessKey');
```

Variables with a leading `_` cannot appear as bare EL identifiers; read them via
`execution.getVariable()` and assign to a plain local name as needed.

### `PROCESS_VARIABLES_JSON`
Inline JSON fragment inside the body string — same rules as task and gateway script tasks
(numeric values unquoted, string values quoted):

```
"credit_score":' + creditScore + ',"decision":"' + decision + '","cugaProcessKey":"' + cugaKey + '"
```

Include every variable that the hook policy needs to reason about.

---

## Response Parsing

The hook script task only checks that the call succeeded — it does not extract variables:

| Tool | `resp.result.content[0].text` is… | What the script does with it |
|------|-----------------------------------|------------------------------|
| `evaluate_hook` | a JSON string (`{"action": "continue", "message": "..."}`) | ignored — success is enough |

The script throws on `!resp.result` (MCP error) so Flowable can surface the failure.
No `execution.setVariable` calls are needed: process state is unchanged by a CONTINUE action,
and CUGA FLO's proxy handles any REST-level realisation before responding.

---

## YAML Config

### `action_permissions`
Declare which hook actions the process is allowed to use. Start with only `continue`:

```yaml
action_permissions:
  permitted_actions:
    - continue
  prohibited_actions:
    - skip_to
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
at the midpoint of the original arrow:

```xml
<!-- hook task shape — placed at midpoint between upstream and downstream -->
<bpmndi:BPMNShape bpmnElement="Task_Hook_FLOW_ID" id="BPMNShape_Task_Hook_FLOW_ID">
  <omgdc:Bounds height="80.0" width="100.0" x="HOOK_X" y="HOOK_Y"/>
</bpmndi:BPMNShape>
```

Update the edge for `FLOW_ID` so its target waypoint lands at the hook task's left edge,
and add a new edge from the hook task's right edge to `DOWNSTREAM_TASK`:

```xml
<!-- updated original flow: now ends at hook task -->
<bpmndi:BPMNEdge bpmnElement="FLOW_ID" ...>
  <omgdi:waypoint x="UPSTREAM_RIGHT_X" y="UPSTREAM_Y"/>
  <omgdi:waypoint x="HOOK_X" y="HOOK_CENTRE_Y"/>
</bpmndi:BPMNEdge>

<!-- new flow: hook task to downstream task -->
<bpmndi:BPMNEdge bpmnElement="Flow_HookTo_DOWNSTREAM_TASK" ...>
  <omgdi:waypoint x="HOOK_RIGHT_X" y="HOOK_CENTRE_Y"/>
  <omgdi:waypoint x="DOWNSTREAM_X" y="DOWNSTREAM_CENTRE_Y"/>
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
1. Add `<scriptTask id="Task_Hook_{flow_id}" name="Notify CUGA FLO Hook" ...>` with the `evaluate_hook` script, using `flow_id` as `hook_id`
2. Change `<sequenceFlow id=flow_id>` `targetRef` from `downstream_task_id` to `Task_Hook_{flow_id}`
3. Add a new `<sequenceFlow id="Flow_HookTo_{downstream_task_id}" sourceRef="Task_Hook_{flow_id}" targetRef=downstream_task_id/>`
4. In the YAML: add `hooks:` entry with `id: flow_id`, `type: edge`, `location: flow_id`, `policy: hook_policy_file`
5. In the YAML: ensure `action_permissions.permitted_actions` contains at least `continue`
6. In the BPMNDiagram: add shape for hook task between the two endpoints; update edge waypoints for `FLOW_ID`; add edge for the new connecting flow
