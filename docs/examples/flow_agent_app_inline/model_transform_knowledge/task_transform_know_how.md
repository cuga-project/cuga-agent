# Task Transform Know-How: Plain BPMN Task → Flowable Composite Subprocess

## Overview

A plain `<bpmn:task>` in a cugaflo BPMN diagram must be transformed into a Flowable
**subprocess** when the task is to be executed by a CUGA FLO task agent via MCP HTTP callback.
The subprocess wraps three steps: sanitise the user message, call the MCP `execute_task` tool,
and parse the result back into Flowable process variables.

---

## Source: Plain Task (BPMNdiagram.bpmn)

```xml
<bpmn:task id="Activity_0oydey5" name="check credit">
  <bpmn:incoming>Flow_1e5ztf6</bpmn:incoming>
  <bpmn:outgoing>Flow_1ji6b0i</bpmn:outgoing>
</bpmn:task>
```

Key attributes:
- `id` — element ID, reused as `task_id` in the MCP call
- `name` — human-readable label, reused as `element_name` in the MCP call
- `incoming` / `outgoing` — flows that must be reconnected to the subprocess

---

## Target: Flowable Composite Subprocess

The plain task is **replaced** by a `<subProcess>` at the outer process level.
The original task `id` and `name` are preserved but move to the HTTP service task **inside** the subprocess.

### Outer-level replacement

```xml
<!-- BEFORE -->
<bpmn:task id="TASK_ID" name="TASK_NAME"> ... </bpmn:task>

<!-- AFTER -->
<subProcess id="SUB_ID" name="TASK_NAME">
  ... (see inner elements below)
</subProcess>
```

- `SUB_ID` — new UUID (e.g. generated as `sid-<uuid>`); does NOT reuse `TASK_ID`
- All `sourceRef` / `targetRef` on the original task's flows are updated from `TASK_ID` to `SUB_ID`

### Inner elements (fixed structure, 5 elements + 4 flows)

```
[start] → [sanitize script] → [HTTP execute_task] → [parse script] → [end]
```

#### 1. Start event
```xml
<startEvent id="SUB_START_ID" flowable:formFieldValidation="true"></startEvent>
```

#### 2. Sanitize script task (identical for every task)
Escapes `_user_message` into a JSON-safe `userMessage` variable.
`_user_message` cannot be referenced directly in Flowable EL (leading underscore restriction),
so `execution.getVariable()` is used and the result is stored under a plain-named variable.

```xml
<scriptTask id="SUB_SANITIZE_ID" name="sanitize user message"
    scriptFormat="javascript" flowable:autoStoreVariables="false">
  <script><![CDATA[var msg = execution.getVariable('_user_message') || '';
execution.setVariable('userMessage', msg.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '\\r'));]]></script>
</scriptTask>
```

#### 3. HTTP service task — calls MCP `execute_task`
Uses the **original task's `id`** and `name`.

```xml
<serviceTask id="TASK_ID" name="TASK_NAME"
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
    "name": "execute_task",
    "arguments": {
      "task_id": "TASK_ID",
      "ctx": {
        "process_instance_id": "${execution.processInstanceId}",
        "element_id": "TASK_ID",
        "element_name": "TASK_NAME",
        "current_state": {
          "process_variables": {
            PROCESS_VARIABLES_BLOCK
          }
        },
        "execution_history": [],
        "process_model_summary": {}
      }
    }
  },
  "id": 1
}]]></flowable:expression>
    </flowable:field>
    <flowable:field name="requestTimeout">
      <flowable:string><![CDATA[120000]]></flowable:string>
    </flowable:field>
    <flowable:field name="responseVariableName">
      <flowable:string><![CDATA[executeTaskResponse]]></flowable:string>
    </flowable:field>
    <flowable:field name="ignoreException">
      <flowable:string><![CDATA[false]]></flowable:string>
    </flowable:field>
  </extensionElements>
</serviceTask>
```

**`PROCESS_VARIABLES_BLOCK`** — list every Flowable process variable the agent needs as context,
plus `userMessage`. All numeric variables are unquoted; all string variables are quoted.
Example for the loan-approval task:

```json
"credit_score": ${credit_score},
"decision": "${decision}",
"cugaProcessKey": "${cugaProcessKey}",
"user_message": "${userMessage}"
```

Rules:
- `cugaProcessKey` is always included (routing key injected at process start)
- `user_message` always maps to `${userMessage}` (the sanitized form)
- Add one entry per variable declared in the YAML `variables:` block
- Variables starting with `_` **cannot** appear as bare EL identifiers; use
  `execution.getVariable('_name')` and store under a plain name in the sanitize step instead

**`Accept: application/json, text/event-stream`** — both values are required; FastMCP's
stateless HTTP transport rejects requests that do not accept both.  
The response body is SSE-framed (`data: {...}\n\n`), not plain JSON — see parse script below.

**`requestTimeout: 120000`** — LLM-backed tasks take 10–60 s; Flowable's default 5 s timeout
causes an IO exception without this override.

#### 4. Parse script task — reads output variables from MCP response
The response is an SSE envelope. The script strips the `data:` prefix, parses the JSON-RPC
result, then writes each output variable back to the Flowable execution.

```xml
<scriptTask id="SUB_PARSE_ID" name="fetch OUTPUT_VARIABLE_NAME"
    scriptFormat="javascript" flowable:autoStoreVariables="false">
  <script><![CDATA[var raw = executeTaskResponse;
var dataLine = null;
var lines = raw.split('\n');
for (var i = 0; i < lines.length; i++) {
    if (lines[i].indexOf('data:') === 0) { dataLine = lines[i].substring(5).trim(); break; }
}
var resp = JSON.parse(dataLine || raw);
if (!resp.result) {
    throw new Error("execute_task MCP error: " + JSON.stringify(resp.error || resp));
}
var content = JSON.parse(resp.result.content[0].text);
var vars = content.process_variables;
OUTPUT_VARIABLE_SETTERS]]></script>
</scriptTask>
```

**`OUTPUT_VARIABLE_SETTERS`** — one block per variable in the YAML `output_mapping:`.
Cast to the appropriate JS type:

| YAML type | JS cast |
|-----------|---------|
| `float` / `int` | `Number(vars.VAR)` |
| `str` | `String(vars.VAR)` |
| `bool` | `vars.VAR === true \|\| vars.VAR === 'true'` |

Example for `output_mapping: {credit_score: credit_score}`:
```javascript
if (vars && vars.credit_score !== undefined) {
    execution.setVariable("credit_score", Number(vars.credit_score));
}
```

#### 5. End event
```xml
<endEvent id="SUB_END_ID"></endEvent>
```

#### Sequence flows inside the subprocess
```xml
<sequenceFlow id="SUB_FLOW_1" sourceRef="SUB_START_ID"    targetRef="SUB_SANITIZE_ID"/>
<sequenceFlow id="SUB_FLOW_2" sourceRef="SUB_SANITIZE_ID" targetRef="TASK_ID"/>
<sequenceFlow id="SUB_FLOW_3" sourceRef="TASK_ID"         targetRef="SUB_PARSE_ID"/>
<sequenceFlow id="SUB_FLOW_4" sourceRef="SUB_PARSE_ID"    targetRef="SUB_END_ID"/>
```

---

## Diagram (BPMNdi) Changes

### Outer process diagram
Replace the original task's `<BPMNShape>` with a collapsed subprocess shape (same bounds):

```xml
<bpmndi:BPMNShape bpmnElement="SUB_ID" id="BPMNShape_SUB_ID" isExpanded="false">
  <omgdc:Bounds height="80.0" width="100.0" x="TASK_X" y="TASK_Y"/>
</bpmndi:BPMNShape>
```

No edge changes are needed in the outer diagram — the flow IDs are unchanged; only the
`sourceRef`/`targetRef` attributes on the `<sequenceFlow>` elements change.

### Subprocess diagram (separate `<BPMNDiagram>` block)
Add a second `<BPMNDiagram>` whose `bpmnElement` matches `SUB_ID`.
Lay the five inner shapes out left-to-right at y ≈ 165 (tasks) / y ≈ 190 (events):

| Element | x | y | w | h |
|---------|---|---|---|---|
| Start event | 60 | 190 | 30 | 30 |
| Sanitize task | 140 | 165 | 100 | 80 |
| HTTP task | 300 | 165 | 100 | 80 |
| Parse task | 460 | 165 | 100 | 80 |
| End event | 620 | 191 | 28 | 28 |

Edges connect left-edge to right-edge of adjacent elements along y = 205.

---

## Transformation Summary (algorithm)

Given:
- Source: `task_id`, `task_name`, list of incoming/outgoing flow IDs
- YAML config: `variables` dict, `output_mapping` dict for this task

Steps:
1. Generate IDs: `sub_id`, `sub_start`, `sub_sanitize`, `sub_parse`, `sub_end`, `sub_flow_1..4`
2. Remove original `<task>` element
3. Insert `<subProcess id=sub_id name=task_name>` with the 5 inner elements
4. For every flow with `sourceRef=task_id` → set `sourceRef=sub_id`
5. For every flow with `targetRef=task_id` → set `targetRef=sub_id`
6. In the outer `BPMNDiagram`: replace `BPMNShape` for `task_id` with one for `sub_id` (same bounds, `isExpanded="false"`)
7. Append a new `BPMNDiagram` block for the subprocess interior with the 5 shapes and 4 edges
