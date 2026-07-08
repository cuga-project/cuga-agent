# Task Transform Know-How: Plain BPMN Task → Flowable Script Task (MCP HTTP)

## Overview

A plain `<bpmn:task>` in a cugaflo BPMN diagram is transformed into a Flowable
`<scriptTask>` that calls the CUGA FLO MCP `execute_task` tool via HTTP and writes
the output back into Flowable process variables.

Flowable's Rhino/Nashorn engine exposes `java.*` to JavaScript, so a single script
task can sanitise the input, POST to MCP, parse the SSE response, and set variables —
no subprocess or helper tasks required.

---

## Source: Plain Task (BPMNdiagram.bpmn)

```xml
<bpmn:task id="TASK_ID" name="TASK_NAME">
  <bpmn:incoming>FLOW_IN</bpmn:incoming>
  <bpmn:outgoing>FLOW_OUT</bpmn:outgoing>
</bpmn:task>
```

Key attributes carried forward:
- `id` — reused as the script task's `id` and as `task_id` / `element_id` in the MCP call
- `name` — reused as `name` and `element_name` in the MCP call
- incoming / outgoing flow IDs — unchanged; no rewiring needed

---

## Target: Flowable Script Task

Replace the `<bpmn:task>` element with `<scriptTask>` keeping the **same `id` and `name`**.
No subprocess, no extra flows, no second BPMNDiagram block.

```xml
<scriptTask id="TASK_ID" name="TASK_NAME"
    scriptFormat="javascript" flowable:autoStoreVariables="false">
  <script><![CDATA[
var msg = execution.getVariable('_user_message') || '';
var safeMsg = msg.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '\\r');
PROCESS_VARIABLE_GETTERS
var body = '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"execute_task","arguments":{"task_id":"TASK_ID","ctx":{"process_instance_id":"' + execution.processInstanceId + '","element_id":"TASK_ID","element_name":"TASK_NAME","current_state":{"process_variables":{PROCESS_VARIABLES_JSON}},"execution_history":[],"process_model_summary":{}}}},"id":1}';
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
if (!resp.result) throw new Error('execute_task error: ' + JSON.stringify(resp.error || resp));
var vars = JSON.parse(resp.result.content[0].text).process_variables;
OUTPUT_VARIABLE_SETTERS
  ]]></script>
</scriptTask>
```

---

## Template Parameters

### `PROCESS_VARIABLE_GETTERS`
One `var` per process variable needed in the request body.
Variables starting with `_` cannot appear as bare EL identifiers in Flowable, so read
them via `execution.getVariable()` and store under a plain name (the sanitize block for
`_user_message` already handles this — it writes `safeMsg`):

```javascript
var creditScore = execution.getVariable('credit_score');
var decision    = execution.getVariable('decision');
var cugaKey     = execution.getVariable('cugaProcessKey');
```

### `PROCESS_VARIABLES_JSON`
Inline JSON fragment inside the body string — one entry per variable.
Numeric values are unquoted; strings are quoted with the JS variable reference inside:

```
"credit_score":' + creditScore + ',"decision":"' + decision + '","cugaProcessKey":"' + cugaKey + '","user_message":"' + safeMsg + '"
```

Rules:
- Always include `cugaProcessKey` (routing key injected at start)
- Always include `user_message` mapped to `safeMsg` (the escaped `_user_message`)
- Add one entry per variable in the YAML `variables:` block
- Numeric variables: `' + varName + '` (no surrounding quotes in the JSON string)
- String variables: `"' + varName + '"` (quoted)

### `OUTPUT_VARIABLE_SETTERS`
One block per entry in the YAML `output_mapping:` for this task:

```javascript
if (vars && vars.OUTPUT_VAR !== undefined) {
    execution.setVariable('OUTPUT_VAR', TYPE_CAST(vars.OUTPUT_VAR));
}
```

Type cast by YAML type:

| YAML type    | JS cast             |
|--------------|---------------------|
| `float`/`int`| `Number(vars.VAR)`  |
| `str`        | `String(vars.VAR)`  |
| `bool`       | `vars.VAR === true \|\| vars.VAR === 'true'` |

Example for `output_mapping: {credit_score: credit_score}`:
```javascript
if (vars && vars.credit_score !== undefined) {
    execution.setVariable('credit_score', Number(vars.credit_score));
}
```

---

## Fixed Constants

| Property | Value | Reason |
|----------|-------|--------|
| `scriptFormat` | `javascript` | Required; omitting it causes Flowable to skip execution silently |
| `flowable:autoStoreVariables` | `false` | Prevents Flowable auto-binding all script-local vars as process variables |
| `Accept` header | `application/json, text/event-stream` | FastMCP requires both; omitting `text/event-stream` returns HTTP 406 |
| `setReadTimeout` | `120000` | LLM tasks take 10–60 s; Flowable default 5 s causes IO exception |
| Response parsing | strip `data:` prefix before `JSON.parse` | FastMCP wraps its response in SSE framing (`data: {...}\n\n`) |
| Result path | `resp.result.content[0].text` → parse again | FastMCP nests the tool return value as a JSON string inside a content array |

---

## Diagram Changes

### Outer process `BPMNDiagram`
Replace the original task's `<BPMNShape>` with a script task shape at the **same bounds**:

```xml
<bpmndi:BPMNShape bpmnElement="TASK_ID" id="BPMNShape_TASK_ID">
  <omgdc:Bounds height="80.0" width="100.0" x="TASK_X" y="TASK_Y"/>
</bpmndi:BPMNShape>
```

No edge changes — flow IDs and waypoints are identical to the original task.

### No subprocess diagram needed
The single script task is a flat element; there is no second `BPMNDiagram` block.

---

## Transformation Algorithm

Given:
- `task_id`, `task_name` from the source BPMN element
- `variables` dict from the YAML config
- `output_mapping` dict for this task from the YAML config

Steps:
1. Replace `<bpmn:task id=task_id name=task_name>` with `<scriptTask id=task_id name=task_name scriptFormat="javascript" flowable:autoStoreVariables="false">`
2. Populate `PROCESS_VARIABLE_GETTERS` from the YAML `variables:` keys
3. Populate `PROCESS_VARIABLES_JSON` with a typed inline JSON fragment for each variable
4. Populate `OUTPUT_VARIABLE_SETTERS` from the YAML `output_mapping:` entries
5. In the `BPMNDiagram`: replace the `BPMNShape` element — change element type label if needed, keep bounds unchanged
6. Leave all incoming/outgoing `sequenceFlow` elements untouched
