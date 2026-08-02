# Hook Transform Know-How: Sequence Flow → Hook Script Task (with in-process redirect)

## Overview

A `<sequenceFlow>` listed in the app YAML under `hooks:` is intercepted by inserting a
script task on it. The task calls the MCP tool `evaluate_hook`; CUGA FLO's FlowAgent reasons
against the hook's policy and returns a `HookResult`, which the task realises immediately.

**This is the transform that differs most from Flowable.** The Flowable version needs three
elements — a hook script task, a `<boundaryEvent>` catching a thrown `BpmnError`, and a
shared `Task_DynamicSkip` calling `ChangeActivityStateBuilder`. In Kogito it is **one script
task**, because:

- Kogito **rejects boundary events on script tasks** at build time: *"Boundary events are
  supported only on StateBasedNode, found node: ActionNode"*. The Flowable shape is not
  portable, and making it legal would mean turning every hook into a service task purely to
  satisfy the restriction.
- `FlowRedirect.to(...)` performs the jump from inside the calling task, so no error, no
  boundary event and no shared handler node are needed.

All three hook actions collapse into one call, because a blank target is a no-op:

| `HookResult.action` | Target passed to `FlowRedirect.to` |
|---|---|
| `continue` | `""` — nominal outgoing flow runs |
| `skip_to` | `skip_to_node` from the result |
| `terminate` | `COMPLETE_PROCESS_TASK_ID` |

---

## Source: Annotated Sequence Flow (`BPMNdiagram.bpmn`)

```xml
<bpmn:sequenceFlow id="FLOW_ID" sourceRef="UPSTREAM" targetRef="DOWNSTREAM"/>
```

With a YAML entry:

```yaml
hooks:
  - id: "FLOW_ID"
    type: "edge"
    location: "FLOW_ID"
    policy: "../policies/hook-something.md"
```

The hook id **is** the flow id — that is how `FlowAgent` finds the policy at runtime.

---

## Target: Hook Script Task

The flow is split in two, with the hook task in the middle:

```xml
<bpmn2:scriptTask id="Task_Hook_FLOW_ID" name="CUGA FLO hook: HOOK_LABEL"
    scriptFormat="http://www.java.com/java">
  <bpmn2:incoming>…</bpmn2:incoming>
  <bpmn2:outgoing>…</bpmn2:outgoing>
  <bpmn2:script><![CDATA[FlowRedirect.to(kcontext, CugaFlo.evaluateHook(kcontext, "FLOW_ID", "HOOK_LABEL", "COMPLETE_PROCESS_TASK_ID"));]]></bpmn2:script>
</bpmn2:scriptTask>
```

**Which half keeps the original flow id depends on whether the flow is conditional.** Both
cases occur in the loan approval model, and the difference is not cosmetic.

### Case A — plain flow: original id goes on the *outgoing* half

The new flow is upstream; the downstream element's `<bpmn2:incoming>` needs no change.

```xml
<bpmn2:sequenceFlow id="Flow_StartToHook" sourceRef="StartEvent_0bpt8wp" targetRef="Task_Hook_Flow_1e5ztf6"/>
<bpmn2:sequenceFlow id="Flow_1e5ztf6"     sourceRef="Task_Hook_Flow_1e5ztf6" targetRef="Activity_0oydey5"/>
```

```xml
<bpmn2:scriptTask id="Task_Hook_Flow_1e5ztf6" name="CUGA FLO hook: pre credit check"
    scriptFormat="http://www.java.com/java">
  <bpmn2:incoming>Flow_StartToHook</bpmn2:incoming>
  <bpmn2:outgoing>Flow_1e5ztf6</bpmn2:outgoing>
  <bpmn2:script><![CDATA[FlowRedirect.to(kcontext, CugaFlo.evaluateHook(kcontext, "Flow_1e5ztf6", "pre credit check", "Task_CompleteProcess"));]]></bpmn2:script>
</bpmn2:scriptTask>
```

### Case B — gateway branch: original id stays on the *incoming* half

A hook on a decision gateway's outgoing branch must keep the original id **on the flow
leaving the gateway**, along with its `<conditionExpression>`. That id is what the
DecisionAgent returns and what the condition compares the routing variable against —
renaming it breaks routing silently, since no branch then matches.

```xml
<bpmn2:sequenceFlow id="Flow_0ybszcv" name="yes" sourceRef="Gateway_09ad5fc" targetRef="Task_Hook_Flow_0ybszcv">
  <bpmn2:conditionExpression xsi:type="bpmn2:tFormalExpression"
      language="http://www.java.com/java"><![CDATA[return "Flow_0ybszcv".equals(gatewayDecision);]]></bpmn2:conditionExpression>
</bpmn2:sequenceFlow>
<bpmn2:sequenceFlow id="Flow_HookToLoan" sourceRef="Task_Hook_Flow_0ybszcv" targetRef="Activity_1h9ix55"/>
```

```xml
<bpmn2:scriptTask id="Task_Hook_Flow_0ybszcv" name="CUGA FLO hook: approval intercept"
    scriptFormat="http://www.java.com/java">
  <bpmn2:incoming>Flow_0ybszcv</bpmn2:incoming>
  <bpmn2:outgoing>Flow_HookToLoan</bpmn2:outgoing>
  <bpmn2:script><![CDATA[FlowRedirect.to(kcontext, CugaFlo.evaluateHook(kcontext, "Flow_0ybszcv", "approval intercept", "Task_CompleteProcess"));]]></bpmn2:script>
</bpmn2:scriptTask>
```

In both cases the MCP `hook_id` stays the **original flow id** from the YAML, regardless of
which half now carries it. Also remember the gateway's `available_flows` payload must give
`target_ref` as the hook task — see `gateway_transform_know_how`.

---

## How the redirect works

`FlowRedirect.to` is the Kogito counterpart of Flowable's
`RuntimeService.createChangeActivityStateBuilder().moveActivityIdTo(...)`:

```java
((NodeInstance) kcontext.getNodeInstance()).cancel();               // suppress nominal flow
pi.getNodeInstance(target).trigger(null, CONNECTION_DEFAULT_TYPE);  // jump
```

**The self-cancel is load-bearing.** Without it the redirect fires *and* the calling task's
own outgoing flow continues, so the process runs both paths — the spike that established
this pattern produced the trail `A,D,B,D` before the cancel was added.

It must run in-process. Doing it over REST reads committed state and races the still-executing
script task that requested it — the same reason Flowable's `_change_process_state` exists but
is unused at runtime. There is no such race here: the redirect runs synchronously on the
script task's own thread after the HTTP call returns.

Targets are resolved by scanning nodes for the `UniqueId` metadata, which holds the BPMN
element id. (`NodeContainer.getNodeByUniqueId(String)` looks like the right API but throws
NPE.) So a `skip_to_node` returned by a policy must be a **BPMN element id** in this model —
including ids created by the transform, such as another hook's `Task_Hook_*`.

---

## State updates and TERMINATE

`CugaFlo.evaluateHook` applies the result's `state_updates` before returning, writing only
keys the process declares — the usual reason an update silently vanishes is a missing
`<bpmn2:property>`.

On `terminate` it also sets `_haltReason` from the result message, which
`CugaFlo.completeProcess` turns into `is_halted` / `halt_reason` in the `FlowState` returned
to the caller. Declare both control variables:

```xml
<bpmn2:property id="_hookAction"  itemSubjectRef="_stringItem" name="_hookAction"/>
<bpmn2:property id="_haltReason"  itemSubjectRef="_stringItem" name="_haltReason"/>
```

`_hookAction` is not used for routing here — it exists for parity with the Flowable model,
where `Task_DynamicSkip` reads it — but `FlowAgent.invoke()` injects it at start, so it must
be declared or the injection is dropped.

---

## Fixed Constants

| Property | Value | Reason |
|---|---|---|
| `scriptFormat` | `http://www.java.com/java` | Java dialect. |
| Hook task id | `Task_Hook_{FLOW_ID}` | Convention; must be unique and stable, since it can itself be a `skip_to` target. |
| MCP `hook_id` | the **flow id** | How `FlowAgent` looks up the policy. Passing the task id instead yields *"Hook … not found"* and a silent CONTINUE. |
| TERMINATE target | `COMPLETE_PROCESS_TASK_ID` | Terminating must still reach `complete_process`, or `FlowAgent.invoke()` waits on its future forever. |
| Boundary event | **none** | Illegal on a script task in Kogito. |
| `Task_DynamicSkip` | **none** | Flowable-only; `FlowRedirect` replaces it. |

---

## Diagram Changes

- Add a `<bpmndi:BPMNShape>` for the hook task, between its upstream and downstream shapes.
- Add a `<bpmndi:BPMNEdge>` for the new upstream flow.
- Update the original flow's edge to start at the hook task.

No boundary-event shape and no handler-task shape — the two elements the Flowable transform
adds do not exist here.

---

## Transformation Algorithm

Given a `hooks:` entry with `location: FLOW_ID`:

1. Find `<sequenceFlow id="FLOW_ID">` and note its `sourceRef`, `targetRef`, and whether it
   carries a `<conditionExpression>`.
2. Create `Task_Hook_{FLOW_ID}` with the one-line script, using the **flow id** as `hook_id`
   and `COMPLETE_PROCESS_TASK_ID` as the terminate target.
3. Split the flow, choosing by case:
   - **No condition (Case A):** add a new flow `UPSTREAM → Task_Hook_{FLOW_ID}`, and change
     the original flow to `sourceRef=Task_Hook_{FLOW_ID}`, keeping its id and `targetRef`.
   - **Has a condition (Case B):** change the original flow to
     `targetRef=Task_Hook_{FLOW_ID}`, keeping its id, `sourceRef` and the condition; add a
     new flow `Task_Hook_{FLOW_ID} → DOWNSTREAM`.
4. Declare `_hookAction` and `_haltReason` if not already present.
5. Add the hook task shape and the new edge; update the original flow's edge.
6. Ensure `action_permissions.permitted_actions` in the YAML allows the actions the policy
   can return (`skip_to`, `terminate`).
