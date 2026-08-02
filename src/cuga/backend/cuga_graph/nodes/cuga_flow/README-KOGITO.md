# CUGA FLO — Integration with Apache KIE (Kogito)

The third `WorkflowEngine` backend, alongside LangGraph (in-process demo) and Flowable.
Kogito owns process execution and state; CUGA FLO contributes LLM reasoning at each
control point through the same `MCPFlowBridge` — no new MCP server, no changes to
`FlowAgent`, `DecisionAgent`, or `TaskAgent`.

Select it in an app's YAML:

```yaml
workflow_engine:
  type: kogito
  url: http://localhost:8081
  process_id: loan_approval
  callback_host: localhost      # host.docker.internal if Kogito runs in a container
  callback_port: 8090
```

**Kogito must already be running.** Nothing here builds or starts it, the same division
of responsibility as the Flowable integration, which does not start Flowable either.

---

## How it differs from Flowable

Kogito compiles BPMN into a Quarkus service **at build time**. There is no runtime
deployment endpoint, so `KogitoProxy` has no `deploy()` — a process must already be built
into the running service. This drives the whole app lifecycle below.

The hook mechanism is also simpler, though not by choice:

| | Flowable | Kogito |
|---|---|---|
| Hook shape | scriptTask + boundaryEvent + shared `Task_DynamicSkip` | one scriptTask |
| Signalling | throws `BpmnError('SKIP_TO')` | direct call |
| Redirect | `ChangeActivityStateBuilder().moveActivityIdTo(...)` | `FlowRedirect.to(kcontext, target)` |

Kogito **rejects boundary events on script tasks** at build time — *"Boundary events are
supported only on StateBasedNode, found node: ActionNode"* — so Flowable's triad cannot be
ported literally. Since the script task can perform the jump itself, the machinery turns
out to be unnecessary: all three hook actions collapse into one call, with `""` meaning
CONTINUE and the `complete_process` task id meaning TERMINATE.

---

## Components

### `KogitoProxy` — `backend/server/kogito/kogito_proxy.py`

Sync `httpx` client over Kogito's generated per-process REST API. Mirrors `FlowableProxy`'s
surface (`ping`, `start_process`, `get_variables`, `invoke_workflow`, `fetch_result`,
`complete_task`) minus two things, both deliberate:

- **No `deploy()`** — no runtime deployment in Kogito.
- **No REST redirect** — Flowable's `_change_process_state` exists but is unused at runtime
  because REST reads committed state and races the in-flight script task. Redirection must
  happen in-process, so this proxy offers no such path at all.

Config from `KOGITO_BASE_URL` / `KOGITO_TIMEOUT`, or the app's `workflow_engine.url`.

Confirmed REST contract:

| Call | Behaviour |
|---|---|
| `POST /{processId}` | Body is plain JSON variables — no Flowable `{name,value,type}` wrapping. Returns `id` plus terminal variables for an automated process. |
| `GET /{processId}` | Running instances only; `[]` once finished. |
| `GET /{processId}/{id}` | 404 after completion — a bare Kogito service keeps no history. |

### CUGA FLO Kogito runtime — `backend/server/kogito/*.java`

Compiled **into** each generated service, shared across all apps because both classes are
parameterised entirely through the arguments their BPMN script tasks pass:

- **`CugaFlo.java`** — MCP client for `execute_task`, `route_gateway`, `evaluate_hook`,
  `complete_process`. Ships the full process-variable map on every call and reads the MCP
  endpoint from the `cugaMcpUrl` process variable, injected by
  `MCPFlowBridge.register_kogito_engine`.
- **`FlowRedirect.java`** — the in-process jump. Cancels the calling node, then triggers the
  target. `redirectspike.bpmn` beside it is its verification model.

### `MCPFlowBridge.register_kogito_engine`

Registers `run_process`, starts the shared lazy uvicorn MCP listener, and injects
`cugaProcessKey` + `cugaMcpUrl` as start variables. Kogito is held in `_kogito_proxy`, not
`_proxy` — the latter gates the Flowable-only `_realize_hook_action` REST path.

---

## App lifecycle

An app is authored **entirely** under `docs/examples/flow_agent_app_inline/<app-name>/`:

```
<app-name>/
├── config/
│   ├── <app-name>_config.yaml          workflow_engine.type: kogito
│   ├── BPMNdiagram.bpmn                clean model — CUGA FLO parses this itself
│   └── *-kogito.bpmn                   Kogito model — script tasks calling CugaFlo
└── policies/                           engine-independent markdown
```

Then generate and build the service:

```bash
scripts/build_kogito_app.sh <app-name>            # --port N, --out DIR, --clean, --no-build
build/kogito/<app-name>/run.sh                    # generated; pins JAVA_HOME
python docs/examples/flow_agent_app_inline/run.py <app-name>
```

The script combines the app's `*-kogito.bpmn` with the shared runtime and the
`pom.xml` / `application.properties` templates from `backend/server/kogito/`. Port comes
from `--port`, else the app's `workflow_engine.url`, else 8081.

`loan_approval_kogito` is the worked reference.

---

## Writing the Kogito model

Four constraints, each found by hitting it:

- **No Java FQNs inside `<bpmn2:script>`.** The validator reads the leading package segment
  as an undeclared variable (*"uses unknown variable in the script: org"*). Declare
  `<drools:import name="org.cuga.CugaFlo"/>` in the process `extensionElements` and keep
  scripts to one-liners delegating to the runtime classes.
- **Declare every process variable** as a `<bpmn2:property>`. The model is generated and
  strongly typed, so undeclared names are silently dropped and JSON numbers must be coerced
  to the declared type (`CugaFlo.coerce`).
- **`kcontext` is `KogitoProcessContext`**, not `org.kie.api.runtime.process.ProcessContext`.
- **Gateway conditions** are equality checks against the routing variable the
  DecisionAgent wrote, exactly as in the Flowable model.

Every terminal branch must reach the `complete_process` task, or `FlowAgent.invoke()` waits
on its future forever.

---

## Monitoring a run

Generated services embed **Data Index** (`kogito-addons-quarkus-data-index-inmemory`), which
records every instance and exposes it over GraphQL at `/graphql`. Without it there is
nothing to inspect: a bare Kogito service finishes an automated process inside the start
call and then 404s the instance.

Two ways to read it — [GraphQL directly](#graphql-directly) for scripting and CI, or the
[Management Console UI](#management-console-ui) for browsing. Both read the same store.

### GraphQL directly

```bash
curl -s -X POST http://localhost:8081/graphql -H 'Content-Type: application/json' \
  -d '{"query":"{ ProcessInstances { id state start end nodes { name type enter exit } variables } }"}'
```

A completed loan approval returns its full node trail:

```
84522e81  COMPLETED  10 nodes
    ActionNode  CUGA FLO hook: pre credit check
    ActionNode  check credit
    ActionNode  route gateway decision
    Split       credit decision
    ActionNode  CUGA FLO hook: approval intercept
    ActionNode  give loan of 1000 USD
    Join        merge
    ActionNode  CUGA FLO complete process
    EndNode     End
    StartNode   Start
```

`variables` carries the terminal state (`credit_score`, `gatewayDecision`, `decision`),
which makes this the practical way to see what a hook or gateway actually decided.
Filter with `ProcessInstances(where: {state: {equal: ERROR}})`.

#### Reading the timeline

Two properties of this data surprise people, and both are visible above — note `Start`
listed last.

**`enter` has millisecond granularity, and adjacent deterministic nodes tie.** In a real
run, `give loan`, `merge`, and `complete process` all recorded `enter=12:11:05.849`. Nodes
sharing a timestamp cannot be ordered from the data, so any display order among them —
in the console or anywhere else — is arbitrary. `merge` appearing before `give loan` is
this, not a routing anomaly. `Start` ties with the first node for the same reason.

**`exit` is the process end time on every node**, not each node's own. It is the same value
for all ten. Per-node duration therefore cannot be read from `exit`.

Real durations come from the gaps between consecutive `enter` values:

```
hook: pre credit check    →  4.0s   LLM
check credit              →  9.0s   LLM
route gateway decision    →  4.0s   LLM
credit decision (gateway) →  0ms    deterministic
hook: approval intercept  →  3.1s   LLM
give loan → merge → end   →  4ms    deterministic
```

20.1s total, of which all but ~4ms is the four LLM control points. The engine's own work is
below the resolution of the timestamps — which is the expected shape here, and precisely
why three consecutive nodes collapse into a single millisecond.

Four things worth knowing:

- **The BPMN source must stay readable at its build-time absolute path.** Data Index reads
  it at startup to register the definition and fails hard if it has moved — which is what
  the otherwise-harmless `Not source found for process id` warning is about. Building
  under `build/kogito/` keeps this true; building into `/tmp` broke it.
- **An embedded PostgreSQL starts with the service**, adding a few seconds to boot. The
  addon is "in-memory" in the sense of needing no external Data Index, not no database.
- **`ERROR` instances record zero nodes** when they fail on the first node, so a failed run
  tells you its state but not always where it stopped.
- **OIDC has to be disabled explicitly** — Data Index pulls it in and refuses to start
  without an auth server URL. The generated `application.properties` does this.

`kie-addons-quarkus-events-process` is deliberately absent: Data Index needs no help from
it, and it drags in reactive-messaging whose metric decorator fails on a missing
`org.eclipse.microprofile.metrics.MetricRegistry`. `kie-addons-quarkus-process-svg` is also
out — it serves a `.svg` exported from the KIE BPMN editor rather than rendering from the
BPMNDI, so it does nothing until a model ships one.

### Management Console UI

The Kogito Management Console is a browser UI over the **same** embedded Data Index — it
adds no data, it renders what `/graphql` already returns. Optional: the queries above stay
the source of truth, and the console is the convenient view of them.

There is no separate Data Index service to deploy. The console talks straight to the app.

```
browser ──▶ Management Console (:8280)  ──▶  /graphql on the Kogito app (:8081)
                  static React app              embedded Data Index
```

Because the console is a static app, **the browser makes the GraphQL calls, not the
container** — which is why the runtime URL is `localhost:8081` (as the browser sees it) and
why CORS matters.

**Prerequisites** — all three are already handled by `build_kogito_app.sh`, but they are
what makes it work:

1. `kogito-addons-quarkus-data-index-inmemory` in the build, or there is no history at all.
2. `quarkus.http.cors=true` in `application.properties`, or the browser silently drops
   every response.
3. `kogito.service.url` set, or the console can list instances but cannot open one.
4. The app running and having executed at least one process.

**Steps**

```bash
# 1. app running, with at least one process executed
./scripts/build_kogito_app.sh loan_approval_kogito
build/kogito/loan_approval_kogito/run.sh

# 2. console
docker run -d --name kogito-mc -p 8280:8080 \
  apache/incubator-kie-kogito-management-console:10.2.0
```

Open http://localhost:8280 and enter the runtime URL `http://localhost:8081`.

The 10.x console takes that **through the UI**, as a route param — there is no env var for
it. `KOGITO_DATAINDEX_HTTP_URL` is silently ignored; the only supported variables are
`RUNTIME_TOOLS_MANAGEMENT_CONSOLE_APP_NAME` and two OIDC client settings.

From there it lists process instances with their state and timings, and drills into one to
show the node path — the same trail the `nodes { }` query returns, including both CUGA FLO
hooks and the gateway split.

**Troubleshooting**

| Symptom | Cause |
|---|---|
| *"Could not communicate with runtime"* | CORS. Rebuild so `quarkus.http.cors=true` is in `application.properties`, and restart the app. Confirm with a preflight (below). |
| *"missing the kogito.service.url property"*, or *"Error fetching data"* on opening an instance | `kogito.service.url` unset, so instances record `serviceUrl: null` and the console has no base URL to call back on. Rebuild and restart. |
| Connects, but no instances | Data Index is per-service-lifetime — a restart clears history. Run a process, then reload. |
| Timeline order looks wrong | Deterministic nodes tie at millisecond `enter` granularity and cannot be ordered — see [Reading the timeline](#reading-the-timeline). |
| Console very slow | The image is amd64 only; on Apple Silicon it runs under emulation. |

Listing instances only needs `/graphql`; opening one, and every action on it, goes back to
the runtime at its recorded `serviceUrl`. That is why the list can work while the detail
view fails. Check it with:

```bash
curl -s -X POST http://localhost:8081/graphql -H 'Content-Type: application/json' \
  -d '{"query":"{ ProcessInstances { id serviceUrl endpoint } }"}'
# serviceUrl must not be null
```

```bash
# a working runtime answers a preflight with an allow-origin header
curl -s -i -X OPTIONS http://localhost:8081/graphql \
  -H "Origin: http://localhost:8280" \
  -H "Access-Control-Request-Method: POST" | grep -i access-control
```

> Verified from the shell: the image runs, the embedded Data Index answers the console's own
> query shape, and CORS preflight succeeds. The rendered UI itself has not been checked in a
> browser.

```bash
docker logs -f kogito-mc     # if the UI looks empty
docker rm -f kogito-mc       # stop it
```

### No VS Code extension does this

The Apache KIE Kogito Bundle is an **authoring** extension — BPMN/DMN/scesim editors — with
no runtime view. For an in-editor alternative to the console, point a generic GraphQL client
at `/graphql`: the *GraphQL: Language Feature Support* extension, or *REST Client* with a
`.http` file holding the queries above.

---

## Known gaps

- `execution_path`, `gateway_decisions`, and `task_results` come back empty in the
  `FlowState`: `complete_process` only ships `process_variables`. Flowable has the same gap,
  so this is parity rather than a regression — and the Data Index trace above covers what
  the audit log does not.
- **Two `FlowAgent`s in one Python process collide on MCP callback port 8090** —
  `_ensure_http_server` guards per-bridge, not per-port. Fine for one flow per process;
  consecutive runs need a few seconds for the port to be released.
- Quarkus and the Flowable demo container both default to **8080**. Kogito apps are pinned
  to 8081; a 404 carrying a Tomcat error page means a request reached Flowable instead.
- **JDK 17+ is required.** The Homebrew `openjdk@17` formula is keg-only and invisible to
  `/usr/libexec/java_home`, so `JAVA_HOME` must be set explicitly — the build script probes
  the usual locations and bakes the result into the generated `run.sh`.
