# Kogito → CUGA FLO — the runtime and model extensions

What has to exist **on the Kogito side** for a process to reach CUGA FLO for reasoning: two
Java classes compiled into the service, and a set of BPMN extensions that call them.

A clean BPMN model executes fine on Kogito on its own — it just never asks anything. Every
point where reasoning is wanted has to be made into a **script task that calls out**. This
document covers what those classes provide and how each kind of control point is wired.

---

## Part 1 — The runtime, compiled into the service

Two classes live in `backend/server/kogito/` and are copied into every generated project by
`scripts/build_kogito_app.sh`. They are **app-independent**: everything specific to a process
arrives as arguments from the BPMN, so no per-app Java is ever written.

```
build/kogito/<app>/src/main/java/org/cuga/
├── CugaFlo.java        the MCP client
└── FlowRedirect.java   the in-process jump
```

### `CugaFlo.java` — the MCP client

Four public methods, one per control point. Each builds a JSON-RPC `tools/call`, POSTs it to
CUGA FLO's MCP endpoint, and applies the answer to the running instance.

| Method | Calls | Returns |
|---|---|---|
| `executeTask(kcontext, taskId, taskName)` | `execute_task` | void — writes outputs back |
| `routeGateway(kcontext, gatewayId, gatewayName, flowsJson, outVar)` | `route_gateway` | void — writes the chosen flow id to `outVar` |
| `evaluateHook(kcontext, hookId, hookName, terminateTarget)` | `evaluate_hook` | the node id to jump to, or `""` |
| `completeProcess(kcontext)` | `complete_process` | void — hands the terminal state back |

**Every call ships the whole process-variable map.** `context()` serialises
`pi.getVariables()` into the `ControlPointFlowKnowledge` payload, so CUGA FLO always sees
complete state without the model declaring what to send:

```java
ctx.put("process_instance_id", pi.getStringId());
ctx.put("element_id", elementId);
ctx.put("element_name", elementName);
ctx.set("current_state", {"process_variables": <all variables>});
```

**The endpoint is a process variable, never a constant.** `call()` reads `cugaMcpUrl`, which
`MCPFlowBridge.register_kogito_engine` injects at start:

```java
Object url = instance(kcontext).getVariables().get("cugaMcpUrl");
if (url == null || String.valueOf(url).isBlank()) {
    throw new IllegalStateException("cugaMcpUrl process variable is not set");
}
```

That indirection matters because the right host differs by deployment — `localhost` when the
service runs on the host, `host.docker.internal` from a container. A model with a hardcoded
URL works in exactly one of those.

Four properties of the transport are load-bearing and easy to get wrong if reimplemented:

- **HTTP/1.1 is pinned.** Java's `HttpClient` defaults to HTTP/2 and opens with an h2c
  upgrade that uvicorn rejects — the symptom is `400 Invalid HTTP request received.` before
  the JSON is ever parsed.
- **`Accept: application/json, text/event-stream`.** FastMCP's stateless HTTP app answers
  with SSE framing, so `unwrapSse()` pulls the JSON off the `data:` line; a plain JSON body
  passes through untouched.
- **The result is nested twice.** The tool's return value is a JSON *string* inside
  `result.content[0].text`, so it is parsed again after the envelope.
- **120 s read timeout.** A control point is an LLM call of 10–60 s.

**Writing results back is type-aware.** Kogito's process model is generated and strongly
typed, so `coerce()` converts each incoming JSON value to the type the declared variable
already holds — Jackson reads a JSON number as `Double`, and assigning that to a variable
declared `Float` fails at runtime.

`executeTask` writes back **every** returned key the process declares:

```java
vars.fieldNames().forEachRemaining(name -> {
    if (declared.containsKey(name)) {
        pi.setVariable(name, coerce(vars.get(name), declared.get(name)));
    }
});
```

The consequence is worth internalising: **the `<bpmn2:property>` list is the output
mapping.** There is no per-task setter to write — a variable is written if and only if the
model declares it, and silently dropped otherwise.

### `FlowRedirect.java` — the in-process jump

One public method. It is how a hook's decision becomes actual movement of the token:

```java
public static void to(KogitoProcessContext kcontext, String targetNodeId) {
    if (targetNodeId == null || targetNodeId.isBlank()) {
        return;                                   // CONTINUE — leave the nominal path alone
    }
    Node target = findNode(pi, targetNodeId);
    ((NodeInstance) kcontext.getNodeInstance()).cancel();
    pi.getNodeInstance(target).trigger(null, Node.CONNECTION_DEFAULT_TYPE);
}
```

Three things to know about it:

**The self-cancel is load-bearing.** Without it the redirect fires *and* the calling task's
own outgoing flow continues, so the process runs both paths. The spike that established this
pattern produced the trail `A,D,B,D` until the cancel was added.

**A blank target is a deliberate no-op.** That is what lets all three hook outcomes go
through one call site — CONTINUE simply passes `""`.

**It must run in-process.** Doing this over REST would read committed state and race the
still-executing script task that asked for it. Running inside the task's own action leaves
no such window.

Node lookup scans `getNodes()` comparing the `UniqueId` metadata, which holds the BPMN
element id. `NodeContainer.getNodeByUniqueId(String)` looks like the right API but throws
NPE, so the scan is deliberate rather than lazy.

`redirectspike.bpmn`, beside the class, is its verification model.

---

## Part 2 — What the model must declare

Before any control point works, the process itself needs three things.

### Imports

Kogito's validator parses script bodies and reads the leading segment of a fully-qualified
name as an undeclared variable — `org.cuga.CugaFlo.executeTask(...)` fails the build with
*"uses unknown variable in the script: org"*. Declare the classes instead, and keep every
script to short names:

```xml
<bpmn2:process id="loan_approval" drools:packageName="org.cuga" isExecutable="true">
  <bpmn2:extensionElements>
    <drools:import name="org.cuga.CugaFlo"/>
    <drools:import name="org.cuga.FlowRedirect"/>
  </bpmn2:extensionElements>
```

### Process variables

Every variable must be a `<bpmn2:property>` with an `<bpmn2:itemDefinition>`. Undeclared
names are dropped on write, with no error anywhere:

```xml
<bpmn2:itemDefinition id="_credit_scoreItem" structureRef="Float"/>
<bpmn2:property id="credit_score" itemSubjectRef="_credit_scoreItem" name="credit_score"/>
```

Take the union of: the app YAML `variables:` block, every key a task's `output_mapping`
writes, the caller's inputs, each gateway's routing variable, and the CUGA FLO control
variables — `cugaProcessKey`, `cugaMcpUrl`, `_user_message`, `_hookAction`, `_haltReason`.

`build_kogito_app.sh` warns when the YAML names a variable the model does not declare.

### Script format

Every calling task is a `<bpmn2:scriptTask>` with:

```xml
scriptFormat="http://www.java.com/java"
```

Not `javascript`, and not the bare string `java`. Note `kcontext` is a
`KogitoProcessContext` — a helper typed against `org.kie.api.runtime.process.ProcessContext`
fails codegen.

---

## Part 3 — Wiring each control point

### Task agent → `execute_task`

A task whose work is done by a CUGA FLO agent becomes a one-line script task. The `id` must
match the `tasks: - id:` entry in the app YAML, or CUGA FLO has no TaskAgent bound to it.

```xml
<bpmn2:scriptTask id="Activity_0oydey5" name="check credit"
                  scriptFormat="http://www.java.com/java">
  <bpmn2:incoming>Flow_1e5ztf6</bpmn2:incoming>
  <bpmn2:outgoing>Flow_1ji6b0i</bpmn2:outgoing>
  <bpmn2:script><![CDATA[CugaFlo.executeTask(kcontext, "Activity_0oydey5", "check credit");]]></bpmn2:script>
</bpmn2:scriptTask>
```

No output-variable setters are needed — declaring the variable is what makes the write land.

### Decision agent → `route_gateway`

A gateway whose branch is chosen by reasoning needs **two** changes: a routing script task
inserted before it, and its outgoing conditions rewritten to read the routing variable.

The gateway itself stays an ordinary exclusive gateway, and never evaluates business data —
the decision happens entirely inside the policy-governed agent.

```xml
<bpmn2:scriptTask id="Activity_RouteGateway" name="route gateway decision"
                  scriptFormat="http://www.java.com/java">
  <bpmn2:script><![CDATA[CugaFlo.routeGateway(kcontext, "Gateway_09ad5fc", "credit decision",
    "[{\"id\":\"Flow_0ybszcv\",\"source_ref\":\"Gateway_09ad5fc\",\"target_ref\":\"Task_Hook_Flow_0ybszcv\",\"name\":\"yes\",\"condition\":null},
      {\"id\":\"Flow_1jgea85\",\"source_ref\":\"Gateway_09ad5fc\",\"target_ref\":\"Activity_131ar38\",\"name\":\"no\",\"condition\":null}]",
    "gatewayDecision");]]></bpmn2:script>
</bpmn2:scriptTask>
```

Then each outgoing flow compares the routing variable, in Kogito's **Java** condition
dialect:

```xml
<bpmn2:sequenceFlow id="Flow_0ybszcv" name="yes"
                    sourceRef="Gateway_09ad5fc" targetRef="Task_Hook_Flow_0ybszcv">
  <bpmn2:conditionExpression xsi:type="bpmn2:tFormalExpression"
      language="http://www.java.com/java"><![CDATA[return "Flow_0ybszcv".equals(gatewayDecision);]]></bpmn2:conditionExpression>
</bpmn2:sequenceFlow>
```

Four details decide whether this works:

- **`available_flows` is a Java string literal**, so every inner quote is escaped `\"`.
- **`target_ref` must be the target after all rewiring** — if a hook was inserted on that
  flow, it is the hook task's id. This is what the DecisionAgent sees as its options, so a
  stale value makes it reason about a topology that no longer exists.
- **The condition body is a Java statement** returning a boolean — with `return`, and with
  the literal first so a null variable cannot NPE.
- **`route_gateway` returns a bare flow id**, not JSON. `routeGateway` trims surrounding
  quotes before storing it.

The routing variable must be declared, and one per gateway — two gateways sharing one would
race, the second overwriting the first's decision.

### Hook → `evaluate_hook` + `FlowRedirect`

A hook intercepts a *transition*. Insert a script task on the annotated flow; it evaluates
the hook and immediately applies the outcome:

```xml
<bpmn2:scriptTask id="Task_Hook_Flow_1e5ztf6" name="CUGA FLO hook: pre credit check"
                  scriptFormat="http://www.java.com/java">
  <bpmn2:script><![CDATA[FlowRedirect.to(kcontext,
    CugaFlo.evaluateHook(kcontext, "Flow_1e5ztf6", "pre credit check", "Task_CompleteProcess"));]]></bpmn2:script>
</bpmn2:scriptTask>
```

That single composed line handles all three outcomes, because `evaluateHook` translates the
action into a target and `FlowRedirect` treats blank as a no-op:

| `HookResult.action` | Target returned | Effect |
|---|---|---|
| `continue` | `""` | nominal outgoing flow runs |
| `skip_to` | `skip_to_node` | jumps to that node |
| `terminate` | the `terminateTarget` argument | jumps to the completion task |

`evaluateHook` also applies the result's `state_updates` before returning, and on
`terminate` sets `_haltReason` from the result message.

**`hook_id` is the flow id**, not the task id — that is how `FlowAgent` finds the policy.
Passing the task id yields *"Hook … not found"* and a silent CONTINUE.

**Which half of the split keeps the original flow id depends on the flow.** A plain flow
keeps it on the outgoing side, so the downstream element needs no change:

```xml
<bpmn2:sequenceFlow id="Flow_StartToHook" sourceRef="StartEvent_0bpt8wp" targetRef="Task_Hook_Flow_1e5ztf6"/>
<bpmn2:sequenceFlow id="Flow_1e5ztf6"     sourceRef="Task_Hook_Flow_1e5ztf6" targetRef="Activity_0oydey5"/>
```

A hook on a **gateway branch** must keep the original id and its `<conditionExpression>` on
the flow *leaving the gateway* — that id is what the DecisionAgent returns and what the
condition compares against. Renaming it breaks routing silently, with no branch matching:

```xml
<bpmn2:sequenceFlow id="Flow_0ybszcv" sourceRef="Gateway_09ad5fc" targetRef="Task_Hook_Flow_0ybszcv">
  <bpmn2:conditionExpression …>return "Flow_0ybszcv".equals(gatewayDecision);</bpmn2:conditionExpression>
</bpmn2:sequenceFlow>
<bpmn2:sequenceFlow id="Flow_HookToLoan" sourceRef="Task_Hook_Flow_0ybszcv" targetRef="Activity_1h9ix55"/>
```

Hooks need **no boundary event and no shared handler node**. Kogito rejects boundary events
on script tasks at build time (*"Boundary events are supported only on StateBasedNode, found
node: ActionNode"*), and `FlowRedirect` makes them unnecessary anyway.

### Completion → `complete_process`

Always required, and the one whose absence is hardest to diagnose. `FlowAgent.invoke()`
blocks on a future that only this call resolves, so **every terminal branch must reach it**
or the caller hangs forever.

Route all terminal branches into a merge gateway, then:

```xml
<bpmn2:scriptTask id="Task_CompleteProcess" name="CUGA FLO complete process"
                  scriptFormat="http://www.java.com/java">
  <bpmn2:script><![CDATA[CugaFlo.completeProcess(kcontext);]]></bpmn2:script>
</bpmn2:scriptTask>
```

`completeProcess` ships the whole variable map plus `is_halted` / `halt_reason` derived from
`_haltReason`, so nothing per-variable is wired here. Note its id is also what hooks pass as
their `terminateTarget`.

---

## Part 4 — The shape of a wired process

The loan approval reference, with the inserted elements marked:

```
Start
  ↓
Task_Hook_Flow_1e5ztf6        ← inserted   evaluate_hook + FlowRedirect
  ↓
Activity_0oydey5              ← rewritten  execute_task
  ↓
Activity_RouteGateway         ← inserted   route_gateway
  ↓
Gateway_09ad5fc               ← conditions rewritten to read gatewayDecision
  ├─ yes → Task_Hook_Flow_0ybszcv  ← inserted → give loan
  └─ no  → send rejection letter
  ↓
merge
  ↓
Task_CompleteProcess          ← inserted   complete_process
  ↓
End
```

Ordinary script tasks that need no reasoning stay ordinary — `give loan` is just
`kcontext.setVariable("decision", "give loan");`.

---

## Build-time error reference

Each of these is a specific mistake, not a mystery:

| Message | Cause |
|---|---|
| `uses unknown variable in the script: org` | A Java FQN inside `<bpmn2:script>`. Add a `<drools:import>` and call the short name. |
| `Boundary events are supported only on StateBasedNode, found node: ActionNode` | A boundary event on a script task. Use `FlowRedirect` instead. |
| `KogitoProcessContext cannot be converted to ProcessContext` | A helper typed against the KIE `ProcessContext`. |
| `Invalid process … Found error: {}` | Malformed BPMN — usually a dangling `sourceRef`/`targetRef`, or a flow node with no BPMNDI shape. |
| `Node '…' has no incoming connection` | A disconnected node. Kogito rejects those. |
| `basePath … is not a prefix to the resource sourcePath` | The model is outside `src/main/resources` — the build script copies it in for this reason. |

Runtime failures at a control point are covered in `README-KOGITO.md`; by far the most common
is a missing `<bpmn2:property>` declaration.
