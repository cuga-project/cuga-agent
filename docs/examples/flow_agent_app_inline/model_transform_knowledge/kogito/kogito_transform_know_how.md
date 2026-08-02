# Kogito Transform Know-How: Full BPMN Transformation for CUGA FLO Integration

## Overview

End-to-end procedure for transforming a clean BPMN model (e.g.
`loan_approval_kogito/config/BPMNdiagram.bpmn`) into the Apache KIE (Kogito) model that CUGA
FLO drives (e.g. `loan_approval_kogito/config/Loan-Approval-Process-kogito.bpmn`).

Both files live side by side in the app's `config/`. They are **not** alternatives:

| File | Role |
|---|---|
| `BPMNdiagram.bpmn` | Clean model. CUGA FLO parses it for its own process knowledge (`bpmn_file:` in the YAML). Never gets script tasks. |
| `*-kogito.bpmn` | Execution model. Compiled into the Quarkus service by `scripts/build_kogito_app.sh`. |

**Which elements to transform comes from the app YAML**, not from diagram annotations:

| YAML section | Applies to | Transform |
|---|---|---|
| `tasks:` with `mode: task_agent` | a `<bpmn:task>` | `task_transform_know_how` |
| `gateways:` with `mode: decision_agent` | an `<exclusiveGateway>` | `gateway_transform_know_how` |
| `hooks:` | a `<sequenceFlow>` | `hook_transform_know_how` |

Anything not listed is carried across unchanged. (The Flowable know-hows describe a
`textAnnotation` scan instead; if a model does carry those labels they agree with the YAML,
but the YAML is authoritative because it is what CUGA FLO itself loads.)

---

## How Kogito differs from Flowable

Worth reading before the steps — three constraints shape every transform, and the third
removes machinery the Flowable procedure requires.

1. **Scripts cannot contain Java FQNs.** Kogito's validator parses script bodies and reads
   the leading package segment of `org.jbpm.Foo` as an undeclared variable, failing the
   build with *"uses unknown variable in the script: org"*. Every script is therefore a
   one-liner delegating to a helper class declared via `<drools:import>`.
2. **Every process variable must be declared** as a `<bpmn2:property>`. The process model is
   generated and strongly typed; undeclared names are silently dropped on write.
3. **Boundary events are illegal on script tasks** — *"Boundary events are supported only on
   StateBasedNode, found node: ActionNode"*. Flowable's hook shape (scriptTask +
   boundaryEvent + shared `Task_DynamicSkip`) **cannot be ported**. It is also unnecessary:
   `FlowRedirect` performs the jump from inside the calling script task.

The runtime helpers the scripts call — `CugaFlo` and `FlowRedirect` — live in
`src/cuga/backend/server/kogito/` and are copied into every generated service. They are
app-independent; nothing per-app needs writing in Java.

---

## Overall Transformation Procedure

### Step 1 — Convert the document shell

The clean model is Camunda-flavoured BPMN (`bpmn:` prefix). Kogito expects the jBPM
flavour. Rewrite the root element with the `drools` namespace and add item definitions:

```xml
<bpmn2:definitions
    xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
    xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
    xmlns:drools="http://www.jboss.org/drools"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    id="_yourProcess" targetNamespace="http://www.omg.org/bpmn20">
  <bpmn2:itemDefinition id="_stringItem"  structureRef="String"/>
  <bpmn2:itemDefinition id="_floatItem"   structureRef="Float"/>
  <bpmn2:itemDefinition id="_integerItem" structureRef="Integer"/>
```

Rename every `bpmn:` prefix to `bpmn2:` throughout.

On the `<bpmn2:process>`, set `drools:packageName="org.cuga"` and `isExecutable="true"`.
**The process `id` becomes the REST endpoint** (`POST /{id}`) and must match `process_id`
in the app YAML's `workflow_engine:` block.

### Step 2 — Declare imports and process variables

Inside `<bpmn2:process>`, before anything else:

```xml
<bpmn2:extensionElements>
  <drools:import name="org.cuga.CugaFlo"/>
  <drools:import name="org.cuga.FlowRedirect"/>
</bpmn2:extensionElements>
```

Then one `<bpmn2:property>` per variable. Take the union of:

- the app YAML `variables:` block,
- every key any task's `output_mapping` writes,
- the inputs the caller passes (`run.py` defaults, or `FlowAgent.invoke` arguments),
- the routing variable used by each decision gateway (e.g. `gatewayDecision`),
- the CUGA FLO control variables, always: `cugaProcessKey`, `cugaMcpUrl`, `_user_message`,
  `_hookAction`, `_haltReason`.

```xml
<bpmn2:property id="credit_score" itemSubjectRef="_floatItem"  name="credit_score"/>
<bpmn2:property id="decision"     itemSubjectRef="_stringItem" name="decision"/>
```

Omitting one does not error — the value is dropped silently at runtime, which surfaces much
later as an agent reasoning over missing state. `cugaMcpUrl` in particular is injected by
`MCPFlowBridge.register_kogito_engine`; without it every control point fails with
*"cugaMcpUrl process variable is not set"*.

### Step 3 — Transform task agents

For each entry in YAML `tasks:` with `mode: task_agent`, apply `task_transform_know_how`.
Independent of the other transforms; do these first.

### Step 4 — Transform decision agents

For each entry in YAML `gateways:` with `mode: decision_agent`, apply
`gateway_transform_know_how`. This inserts a routing script task before the gateway and
rewrites the outgoing conditions, so it must run after tasks and before hooks — a hook may
sit on a flow this step rewires.

### Step 5 — Transform hooks

For each entry in YAML `hooks:`, apply `hook_transform_know_how`. One script task inserted
on the annotated flow. No boundary event, no `Task_DynamicSkip`.

### Step 6 — Add the `complete_process` callback

Always required. `FlowAgent.invoke()` blocks on a future that only `complete_process`
resolves, so **every terminal branch must reach it** or the call hangs indefinitely.

- Route all terminal branches into a merge `<exclusiveGateway>` (the clean model usually
  already has one).
- After the merge, add a script task — a plain script task, not Flowable's HTTP service
  task, because `CugaFlo` makes the call from Java:

```xml
<bpmn2:scriptTask id="Task_CompleteProcess" name="CUGA FLO complete process"
    scriptFormat="http://www.java.com/java">
  <bpmn2:incoming>Flow_MergeToComplete</bpmn2:incoming>
  <bpmn2:outgoing>Flow_CompleteToEnd</bpmn2:outgoing>
  <bpmn2:script><![CDATA[CugaFlo.completeProcess(kcontext);]]></bpmn2:script>
</bpmn2:scriptTask>
```

- Note its id as `COMPLETE_PROCESS_TASK_ID`: hooks use it as their TERMINATE target.
- Keep an `<bpmn2:endEvent>` after it.

`completeProcess` ships the whole variable map plus `is_halted` / `halt_reason` derived from
`_haltReason`, so no per-variable wiring is needed here.

### Step 7 — Rebuild the BPMNDI diagram

Kogito requires a `<bpmndi:BPMNDiagram>` with a `<bpmndi:BPMNShape>` for every flow node and
a `<bpmndi:BPMNEdge>` for every sequence flow. Carry bounds over from the clean model where
elements are unchanged, and place the inserted nodes (hook tasks, routing task, completion
task) in the gaps. Coordinates only affect rendering — but a missing shape is a build error.

Note `<di:waypoint xsi:type="dc:Point" .../>`, not Camunda's `<di:waypoint>` without a type.

### Step 8 — Update the app YAML

- `workflow_engine:` → `type: kogito`, `url`, `process_id` (the BPMN process id),
  `callback_host`, `callback_port`.
- `tasks:` / `gateways:` / `hooks:` — unchanged from the Flowable app if porting; these are
  engine-independent, as are the `policies/` markdown files.
- `action_permissions.permitted_actions:` — `skip_to` and `terminate` where hooks use them.

### Step 9 — Build and verify

```bash
scripts/build_kogito_app.sh <app-name>
build/kogito/<app-name>/run.sh
```

Then check, in order:

1. Startup logs show `process id <your-process-id>` and no `Invalid process` error.
2. `curl -s -o /dev/null -w '%{http_code}' http://localhost:8081/<process_id>` → 200.
3. A real run: `python docs/examples/flow_agent_app_inline/run.py <app-name>`.
4. The node trail: query `/graphql` for `ProcessInstances { state nodes { name } }` and
   confirm every control point appears and the state is `COMPLETED`.

---

## Build-Time Error Reference

Each of these is a specific mistake, not a mystery:

| Message | Cause |
|---|---|
| `uses unknown variable in the script: org` | A Java FQN inside `<bpmn2:script>`. Add a `<drools:import>` and call the short name. |
| `Boundary events are supported only on StateBasedNode, found node: ActionNode` | A boundary event attached to a script task. Not portable — use `FlowRedirect` instead. |
| `KogitoProcessContext cannot be converted to ProcessContext` | A helper typed against `org.kie.api.runtime.process.ProcessContext`. `kcontext` is `KogitoProcessContext` in 10.x. |
| `Invalid process ... Found error: {}` | Malformed BPMN — usually a dangling `sourceRef`/`targetRef` or a flow node with no BPMNDI shape. |
| `basePath ... is not a prefix to the resource sourcePath` | The model is outside `src/main/resources` (a symlink counts). The build script copies it in for this reason. |

Runtime failures at a control point are covered by `README-KOGITO.md`; the most common by
far is a missing `<bpmn2:property>` declaration.
