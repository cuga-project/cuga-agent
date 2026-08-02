# Task Transform Know-How: Plain BPMN Task → Kogito Script Task (MCP over HTTP)

## Overview

A plain `<bpmn:task>` in the clean model becomes a Kogito `<bpmn2:scriptTask>` that calls
the CUGA FLO MCP tool `execute_task` and writes the returned outputs back into process
variables.

Unlike Flowable's Nashorn script — which inlines variable serialisation, the HTTP call and
SSE parsing — the Kogito script is **one line**. All of that lives in `org.cuga.CugaFlo`,
because Kogito's validator rejects Java FQNs inside a script body (*"uses unknown variable
in the script: org"*). The helper is shared across every app; nothing per-task is written
in Java.

---

## Source: Plain Task (`BPMNdiagram.bpmn`)

```xml
<bpmn:task id="TASK_ID" name="TASK_NAME">
  <bpmn:incoming>FLOW_IN</bpmn:incoming>
  <bpmn:outgoing>FLOW_OUT</bpmn:outgoing>
</bpmn:task>
```

Carried forward unchanged:

- `id` — reused as the script task id, and passed as `task_id` / `element_id` to MCP. It
  **must** match the `tasks: - id:` entry in the app YAML, or CUGA FLO has no TaskAgent for it.
- `name` — reused as `name` and passed as `element_name`.
- incoming / outgoing flow ids — no rewiring.

---

## Target: Kogito Script Task

```xml
<bpmn2:scriptTask id="TASK_ID" name="TASK_NAME" scriptFormat="http://www.java.com/java">
  <bpmn2:incoming>FLOW_IN</bpmn2:incoming>
  <bpmn2:outgoing>FLOW_OUT</bpmn2:outgoing>
  <bpmn2:script><![CDATA[CugaFlo.executeTask(kcontext, "TASK_ID", "TASK_NAME");]]></bpmn2:script>
</bpmn2:scriptTask>
```

Worked example from `Loan-Approval-Process-kogito.bpmn`:

```xml
<bpmn2:scriptTask id="Activity_0oydey5" name="check credit" scriptFormat="http://www.java.com/java">
  <bpmn2:incoming>Flow_1e5ztf6</bpmn2:incoming>
  <bpmn2:outgoing>Flow_1ji6b0i</bpmn2:outgoing>
  <bpmn2:script><![CDATA[CugaFlo.executeTask(kcontext, "Activity_0oydey5", "check credit");]]></bpmn2:script>
</bpmn2:scriptTask>
```

---

## Prerequisites

Both belong to the document-level transform (`kogito_transform_know_how`, steps 1–2), but a
task will fail without them:

```xml
<bpmn2:extensionElements>
  <drools:import name="org.cuga.CugaFlo"/>
</bpmn2:extensionElements>
```

Every variable the task's `output_mapping` writes must be declared:

```xml
<bpmn2:property id="credit_score" itemSubjectRef="_floatItem" name="credit_score"/>
```

---

## No `OUTPUT_VARIABLE_SETTERS`

Flowable's template needs one generated setter block per `output_mapping` entry.
Kogito needs none: `CugaFlo.executeTask` writes back **every** key in the returned
`process_variables` that the process already declares.

That makes the declaration in step 2 the actual output mapping. A variable the model does
not declare is silently ignored — a missing `<bpmn2:property>` is the usual reason an output
"disappears" between the agent and the gateway that reads it.

Types are handled by `CugaFlo.coerce`, which converts each incoming JSON value to the type
the declared variable currently holds. This matters: Jackson reads a JSON number as `Double`,
and assigning that to a variable declared `Float` fails at runtime.

| Declared `itemSubjectRef` | JSON in | Stored as |
|---|---|---|
| `_floatItem` | `0.91` | `Float` |
| `_integerItem` | `1000` | `Integer` |
| `_stringItem` | `"give loan"` | `String` |

---

## Fixed Constants

| Property | Value | Reason |
|---|---|---|
| `scriptFormat` | `http://www.java.com/java` | Kogito's Java dialect. Not `javascript`, and not the bare string `java`. |
| Script body | one call, no FQNs | The validator reads `org.…` as an undeclared variable and fails the build. |
| `kcontext` type | `KogitoProcessContext` | Not `org.kie.api.runtime.process.ProcessContext` — a helper typed against the latter fails codegen. |
| MCP endpoint | `cugaMcpUrl` process variable | Injected by `MCPFlowBridge.register_kogito_engine`; never hardcode a host, since it differs between `quarkus:dev` on the host and a container. |
| HTTP version | HTTP/1.1, pinned in `CugaFlo` | Java's `HttpClient` defaults to HTTP/2 and opens with an h2c upgrade that uvicorn rejects — a 400 *"Invalid HTTP request received."* before the JSON is read. |
| Read timeout | 120 s, in `CugaFlo` | LLM tasks take 10–60 s. |
| Response parsing | strip `data:` then parse `result.content[0].text` | FastMCP answers with SSE framing and nests the tool result as a JSON string. Handled in `CugaFlo`. |

Only the first three are the model author's concern; the rest are properties of the shared
helper, listed so failures are recognisable.

---

## Diagram Changes

One shape, same bounds as the original task:

```xml
<bpmndi:BPMNShape id="shape_TASK_ID" bpmnElement="TASK_ID">
  <dc:Bounds height="102.0" width="154.0" x="TASK_X" y="TASK_Y"/>
</bpmndi:BPMNShape>
```

No edge changes — flow ids and endpoints are unchanged. A flow node with no shape is a
build error, so do not skip it even though coordinates only affect rendering.

Kogito does not require the "Task Agent" `<textAnnotation>` the Flowable transform adds;
the YAML `tasks:` entry is what binds the agent.

---

## Transformation Algorithm

Given `task_id` and `task_name` from the source element, plus the app YAML:

1. Confirm the YAML has a `tasks:` entry with `mode: task_agent` and this `id`. If not, the
   task is not an agent — carry it across unchanged.
2. Replace `<bpmn:task>` with `<bpmn2:scriptTask …  scriptFormat="http://www.java.com/java">`,
   keeping `id`, `name`, and both flow references.
3. Set the script to `CugaFlo.executeTask(kcontext, "<id>", "<name>");`.
4. Declare every `output_mapping` target as a `<bpmn2:property>` with a matching item
   definition.
5. Copy the `BPMNShape` across with its bounds.
6. Leave all sequence flows untouched.
