# Gateway Transform Know-How: Exclusive Gateway → Routing Script Task + Adapted Gateway

## Overview

An `<exclusiveGateway>` listed in the app YAML under `gateways:` with `mode: decision_agent`
is transformed in two parts:

1. A **routing script task** inserted immediately before it, calling the MCP tool
   `route_gateway`. CUGA FLO's DecisionAgent returns the id of the flow to take, which the
   task stores in a routing variable.
2. The gateway's **outgoing conditions rewritten** to equality checks against that variable.

The gateway itself stays an ordinary exclusive gateway. The routing variable is the only
channel between the agent's decision and BPMN routing — the gateway never evaluates business
data, so the decision stays entirely inside the policy-governed agent.

Structurally this matches the Flowable transform. The differences are the script dialect,
the condition language, and that the routing task is one line rather than an inlined HTTP
client.

---

## Source: Exclusive Gateway (`BPMNdiagram.bpmn`)

```xml
<bpmn:exclusiveGateway id="GATEWAY_ID">
  <bpmn:incoming>FLOW_IN</bpmn:incoming>
  <bpmn:outgoing>FLOW_A</bpmn:outgoing>
  <bpmn:outgoing>FLOW_B</bpmn:outgoing>
</bpmn:exclusiveGateway>

<bpmn:sequenceFlow id="FLOW_IN" sourceRef="UPSTREAM" targetRef="GATEWAY_ID"/>
<bpmn:sequenceFlow id="FLOW_A" name="yes" sourceRef="GATEWAY_ID" targetRef="TARGET_A"/>
<bpmn:sequenceFlow id="FLOW_B" name="no"  sourceRef="GATEWAY_ID" targetRef="TARGET_B"/>
```

The clean model may carry a human-readable condition as a `<textAnnotation>` (e.g.
*"credit score > 0.6"*). That is documentation. The real condition lives in the YAML
`gateways: <id>: condition:` and is evaluated by CUGA FLO, not by the engine.

---

## Target

### 1. Routing script task

Inserted between the upstream element and the gateway:

```xml
<bpmn2:scriptTask id="Activity_RouteGateway" name="route gateway decision"
    scriptFormat="http://www.java.com/java">
  <bpmn2:incoming>FLOW_IN</bpmn2:incoming>
  <bpmn2:outgoing>Flow_RouteToGateway</bpmn2:outgoing>
  <bpmn2:script><![CDATA[CugaFlo.routeGateway(kcontext, "GATEWAY_ID", "GATEWAY_NAME", "AVAILABLE_FLOWS_JSON", "ROUTING_VAR");]]></bpmn2:script>
</bpmn2:scriptTask>
```

### 2. Rewired flows

- `FLOW_IN` — change `targetRef` from the gateway to the routing task.
- Add `Flow_RouteToGateway` from the routing task to the gateway.

```xml
<bpmn2:sequenceFlow id="FLOW_IN" sourceRef="UPSTREAM" targetRef="Activity_RouteGateway"/>
<bpmn2:sequenceFlow id="Flow_RouteToGateway" sourceRef="Activity_RouteGateway" targetRef="GATEWAY_ID"/>
```

### 3. Adapted gateway

```xml
<bpmn2:exclusiveGateway id="GATEWAY_ID" name="GATEWAY_NAME" gatewayDirection="Diverging">
  <bpmn2:incoming>Flow_RouteToGateway</bpmn2:incoming>
  <bpmn2:outgoing>FLOW_A</bpmn2:outgoing>
  <bpmn2:outgoing>FLOW_B</bpmn2:outgoing>
</bpmn2:exclusiveGateway>

<bpmn2:sequenceFlow id="FLOW_A" name="yes" sourceRef="GATEWAY_ID" targetRef="TARGET_A">
  <bpmn2:conditionExpression xsi:type="bpmn2:tFormalExpression"
      language="http://www.java.com/java"><![CDATA[return "FLOW_A".equals(ROUTING_VAR);]]></bpmn2:conditionExpression>
</bpmn2:sequenceFlow>
```

`gatewayDirection` is `Diverging` on a split and `Converging` on a merge.

---

## Worked Example

From `Loan-Approval-Process-kogito.bpmn`:

```xml
<bpmn2:scriptTask id="Activity_RouteGateway" name="route gateway decision"
    scriptFormat="http://www.java.com/java">
  <bpmn2:script><![CDATA[CugaFlo.routeGateway(kcontext, "Gateway_09ad5fc", "credit decision", "[{\"id\":\"Flow_0ybszcv\",\"source_ref\":\"Gateway_09ad5fc\",\"target_ref\":\"Activity_1h9ix55\",\"name\":\"yes\",\"condition\":null},{\"id\":\"Flow_1jgea85\",\"source_ref\":\"Gateway_09ad5fc\",\"target_ref\":\"Activity_131ar38\",\"name\":\"no\",\"condition\":null}]", "gatewayDecision");]]></bpmn2:script>
</bpmn2:scriptTask>

<bpmn2:sequenceFlow id="Flow_0ybszcv" name="yes" sourceRef="Gateway_09ad5fc" targetRef="Task_Hook_Flow_0ybszcv">
  <bpmn2:conditionExpression xsi:type="bpmn2:tFormalExpression"
      language="http://www.java.com/java"><![CDATA[return "Flow_0ybszcv".equals(gatewayDecision);]]></bpmn2:conditionExpression>
</bpmn2:sequenceFlow>
```

Note `Flow_0ybszcv` targets a **hook task**, not the loan task directly — this gateway's
approve branch also carries a hook, transformed separately (`hook_transform_know_how`).
Apply the gateway transform first, then the hook, so the hook sees the rewired flow.

---

## Template Parameters

### `AVAILABLE_FLOWS_JSON`

A JSON array of the gateway's outgoing flows, passed as a **Java string literal**, so every
inner quote is escaped `\"`. One object per outgoing flow:

```json
{"id": "FLOW_ID", "source_ref": "GATEWAY_ID", "target_ref": "TARGET_ID",
 "name": "FLOW_LABEL", "condition": null}
```

`target_ref` must be the target **after** all rewiring — if a hook was inserted on this
flow, it is the hook task id. This is what the DecisionAgent sees as its choices, so a
stale value means it reasons about a topology that no longer exists.

`condition` stays `null`: the condition lives in the YAML policy, not the model.

### `ROUTING_VAR`

Conventionally `gatewayDecision`. Must be declared:

```xml
<bpmn2:property id="gatewayDecision" itemSubjectRef="_stringItem" name="gatewayDecision"/>
```

One variable per gateway if a process has several — two gateways sharing one variable would
race, since the second overwrites the first's decision.

### `GATEWAY_NAME`

Passed to MCP as `element_name` and shown in the DecisionAgent's prompt. Use the business
label (*"credit decision"*), not the id.

---

## Fixed Constants

| Property | Value | Reason |
|---|---|---|
| `scriptFormat` | `http://www.java.com/java` | Java dialect. |
| Condition `language` | `http://www.java.com/java` | Kogito's Java condition dialect. Not Camunda's `${...}` EL and not Flowable's `${gatewayDecision == 'FLOW_ID'}`. |
| Condition body | `return "…".equals(VAR);` | Must be a Java statement returning a boolean — with `return`, and with the literal first so a null variable cannot NPE. |
| `route_gateway` return | a bare flow id string | Not JSON. `CugaFlo.routeGateway` trims surrounding quotes before storing it. |
| Routing task placement | before the gateway | The gateway must not be reached until the variable is set; an unset variable matches no condition and the instance fails with no outgoing flow. |

---

## Diagram Changes

- Add a `<bpmndi:BPMNShape>` for the routing script task, in the gap before the gateway.
  Shift the gateway and everything downstream right if there is no room — rendering only,
  but a missing shape is a build error.
- Update the `<bpmndi:BPMNEdge>` for `FLOW_IN` to end at the routing task.
- Add an edge for `Flow_RouteToGateway`.
- Leave the outgoing flow edges alone; only their conditions changed.

---

## Transformation Algorithm

Given `gateway_id` from the YAML `gateways:` block:

1. Read `mode`. Only `decision_agent` gets this transform; `native` gateways (a merge, or a
   single-outgoing gateway) are carried across unchanged apart from the `bpmn2:` prefix and
   `gatewayDirection`.
2. Find the single incoming flow and retarget it to a new `Activity_Route{gateway_id}`.
3. Add a flow from the routing task to the gateway.
4. Build `AVAILABLE_FLOWS_JSON` from the outgoing flows, using post-rewiring targets.
5. Emit the routing script task with the escaped JSON literal and the routing variable name.
6. Replace each outgoing `<conditionExpression>` with the Java equality check.
7. Declare the routing variable as a `<bpmn2:property>`.
8. Add the routing task shape and update the two affected edges.
