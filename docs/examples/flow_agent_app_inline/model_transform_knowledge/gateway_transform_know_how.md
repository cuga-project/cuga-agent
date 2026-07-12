# Gateway Transform Know-How: Plain Exclusive Gateway → CUGA FLO Decision Agent (MCP HTTP)

## Overview

A plain `<exclusiveGateway>` with hard-coded EL conditions (e.g. `${credit_score > 0.6}`)
is transformed so that a CUGA FLO decision agent chooses the outgoing flow at runtime.

The pattern inserts a new `<scriptTask>` **immediately before** the gateway. The script
calls the `route_gateway` MCP tool via HTTP, receives the chosen flow ID as a string, and
writes it to a Flowable process variable. The gateway conditions are replaced with equality
checks against that variable so Flowable routes deterministically based on the agent's choice.

---

## Source: Plain Exclusive Gateway

```xml
<exclusiveGateway id="GATEWAY_ID"></exclusiveGateway>

<sequenceFlow id="FLOW_IN" sourceRef="PREV_TASK" targetRef="GATEWAY_ID"/>
<sequenceFlow id="FLOW_A" name="yes" sourceRef="GATEWAY_ID" targetRef="TASK_A">
  <conditionExpression xsi:type="tFormalExpression"><![CDATA[${some_variable > 0.6}]]></conditionExpression>
</sequenceFlow>
<sequenceFlow id="FLOW_B" name="no" sourceRef="GATEWAY_ID" targetRef="TASK_B">
  <conditionExpression xsi:type="tFormalExpression"><![CDATA[${some_variable <= 0.6}]]></conditionExpression>
</sequenceFlow>
```

---

## Target: Script Task + Rewired Gateway

### 1. Insert a routing script task before the gateway

Add `<scriptTask id="Activity_RouteGATEWAY_ID" ...>` using the same Java HTTP pattern as
task script tasks, but calling `route_gateway` instead of `execute_task`.

```xml
<scriptTask id="Activity_RouteGATEWAY_ID" name="route gateway decision"
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
var body = '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"route_gateway","arguments":{"gateway_id":"GATEWAY_ID","ctx":{"process_instance_id":"' + execution.processInstanceId + '","element_id":"GATEWAY_ID","element_name":"GATEWAY_NAME","current_state":{"process_variables":' + processVarsJson + '},"execution_history":[],"process_model_summary":{},"available_flows":[AVAILABLE_FLOWS_JSON]}}},"id":1}';
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
if (!resp.result) throw new Error('route_gateway error: ' + JSON.stringify(resp.error || resp));
var flowId = resp.result.content[0].text.trim().replace(/^"|"$/g, '');
execution.setVariable('ROUTING_VAR', flowId);
  ]]></script>
</scriptTask>
```

### 2. Rewire the incoming flow

Change the sequence flow that used to go directly into the gateway so it instead targets the
new routing script task:

```xml
<!-- before -->
<sequenceFlow id="FLOW_IN" sourceRef="PREV_TASK" targetRef="GATEWAY_ID"/>

<!-- after -->
<sequenceFlow id="FLOW_IN" sourceRef="PREV_TASK" targetRef="Activity_RouteGATEWAY_ID"/>
<sequenceFlow id="FLOW_GW_NEW" sourceRef="Activity_RouteGATEWAY_ID" targetRef="GATEWAY_ID"/>
```

### 3. Replace gateway conditions with variable equality checks

```xml
<sequenceFlow id="FLOW_A" name="yes" sourceRef="GATEWAY_ID" targetRef="TASK_A">
  <conditionExpression xsi:type="tFormalExpression"><![CDATA[${ROUTING_VAR == 'FLOW_A'}]]></conditionExpression>
</sequenceFlow>
<sequenceFlow id="FLOW_B" name="no" sourceRef="GATEWAY_ID" targetRef="TASK_B">
  <conditionExpression xsi:type="tFormalExpression"><![CDATA[${ROUTING_VAR == 'FLOW_B'}]]></conditionExpression>
</sequenceFlow>
```

The value of `ROUTING_VAR` after the script task is always the exact flow ID string chosen by
the CUGA FLO agent (e.g. `"Flow_0ybszcv"`), so the equality check is a simple string
comparison in JUEL.

---

## Template Parameters

### Process Variables
All Flowable process variables are forwarded automatically using `execution.getVariables()` —
same dynamic snippet as task script tasks. No per-variable declarations needed.

### `AVAILABLE_FLOWS_JSON`
Array of objects, one per outgoing flow from the gateway.
Each object maps to `BPMNFlow(id, source_ref, target_ref, name, condition)`:

```
[{"id":"FLOW_A","source_ref":"GATEWAY_ID","target_ref":"TASK_A","name":"yes","condition":null},
 {"id":"FLOW_B","source_ref":"GATEWAY_ID","target_ref":"TASK_B","name":"no","condition":null}]
```

Rules:
- `id`: the sequence flow ID (same value used in the gateway condition)
- `source_ref`: the gateway ID
- `target_ref`: the downstream task/event ID
- `name`: human-readable label (from the flow `name` attribute, if any)
- `condition`: always `null` — the agent uses the policy to decide, not the EL condition

### `ROUTING_VAR`
Any plain variable name (no leading `_`). Conventionally `gatewayDecision` for a single
decision gateway, or `<gatewayId>Decision` when multiple gateways are present in the process.

---

## Response Parsing Difference vs. `execute_task`

| Tool | `resp.result.content[0].text` is… | Parse step |
|------|-----------------------------------|------------|
| `execute_task` | a JSON string (FlowState dict) | `JSON.parse(...)` then read `.process_variables` |
| `route_gateway` | a raw flow ID string (`"Flow_0ybszcv"`) | `.trim().replace(/^"\|"$/g, '')` only |

`route_gateway` returns a plain Python `str`; FastMCP wraps it as text content directly
without an extra JSON layer. Strip leading/trailing quotes defensively in case the value
is ever JSON-serialized.

---

## YAML Config

Enable the gateway in the YAML `gateways:` block under the gateway ID:

```yaml
gateways:
  GATEWAY_ID:
    mode: decision_agent
    condition: "${some_variable} > 0.6"   # context for the LLM; not evaluated by Flowable
    policy: "../policies/decision-GATEWAY_NAME.md"
    flows:
      FLOW_A:
        decision: "Approve — condition met"
      FLOW_B:
        decision: "Reject — condition not met"
```

- `condition`: forwarded as context to the decision agent LLM (informational only for the
  Flowable path; the actual Flowable routing is driven by `ROUTING_VAR`)
- `flows`: maps each outgoing flow ID to a human-readable decision label used by the policy

---

## Diagram Changes

Shift all elements at and downstream of the gateway ~160 px to the right to make room for
the new script task. Place the routing script task at the original gateway position (same
x, y) and move the gateway further right:

```xml
<!-- original gateway position becomes routing script task -->
<bpmndi:BPMNShape bpmnElement="Activity_RouteGATEWAY_ID" ...>
  <omgdc:Bounds height="80.0" width="100.0" x="ORIG_GW_X" y="ORIG_GW_Y - 20"/>
</bpmndi:BPMNShape>

<!-- gateway moved right -->
<bpmndi:BPMNShape bpmnElement="GATEWAY_ID" ...>
  <omgdc:Bounds height="40.0" width="40.0" x="ORIG_GW_X + 160" y="ORIG_GW_Y"/>
</bpmndi:BPMNShape>
```

Add edges for the two new/updated flows (`FLOW_IN` now targets routing task; `FLOW_GW_NEW`
connects routing task to gateway). Update all downstream edge waypoints by adding the shift
amount to their x-coordinates.

---

## Transformation Algorithm

Given:
- `gateway_id`, `gateway_name` from the source BPMN element
- `outgoing_flows`: list of `(flow_id, name, target_ref)` for each outgoing flow
- `variables` dict from the YAML config
- `policy_file`, `flow_labels` from the YAML `gateways:` block

Steps:
1. Add `<scriptTask id="Activity_Route{gateway_id}" ...>` with the `route_gateway` script (dynamic variable snippet + static `available_flows`)
2. Change the incoming `<sequenceFlow>` targetRef from `gateway_id` to the new script task
3. Add a new `<sequenceFlow>` from the script task to `gateway_id`
4. Replace each outgoing flow's `<conditionExpression>` with `${ROUTING_VAR == 'FLOW_ID'}`
5. In the YAML: add `gateways.gateway_id.mode = decision_agent` with `flows` labels
6. In the BPMNDiagram: add shape for routing task; shift gateway and downstream shapes right;
   update all affected edge waypoints
