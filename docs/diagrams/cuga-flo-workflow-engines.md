# CUGA FLO — Workflow Engine Architecture

How CUGA FLO drives three interchangeable BPMN runtimes through one MCP server. The
reasoning layer (`FlowAgent`, `TaskAgent`, `DecisionAgent`) is identical in all three
configurations — only the engine adapter and the runtime differ.

```mermaid
flowchart LR
    APP["Application<br/>CugaSupervisor / run.py"]

    subgraph CUGA["CUGA FLO — reasoning layer, identical for every engine"]
        FA["FlowAgent<br/>hooks, process state"]
        TA["TaskAgent<br/>per task"]
        DA["DecisionAgent<br/>per gateway"]
        FA --> TA
        FA --> DA
    end

    BRIDGE{{"MCPFlowBridge<br/>single FastMCP server<br/><br/>execute_task<br/>route_gateway<br/>evaluate_hook<br/>complete_process<br/>run_process"}}

    LGE["LangGraphWorkflowEngine<br/>WorkflowEngine ABC"]
    FP["FlowableProxy<br/>httpx REST client"]
    KP["KogitoProxy<br/>httpx REST client"]

    LGR["LangGraph StateGraph<br/>same Python process"]
    FR["Flowable container :8080<br/>Nashorn script tasks"]
    KR["Kogito / Quarkus :8081<br/>Java script tasks<br/>→ CugaFlo.java"]

    APP -->|"invoke()"| FA
    FA <==>|"MCP tool calls"| BRIDGE

    BRIDGE <==>|"in-process<br/>FastMCPTransport"| LGE
    BRIDGE ==>|"run_process"| FP
    BRIDGE ==>|"run_process"| KP

    LGE <--> LGR
    FP -->|"POST /runtime/process-instances"| FR
    KP -->|"POST /processId"| KR

    FR -.->|"callback HTTP :8090 /mcp"| BRIDGE
    KR -.->|"callback HTTP :8090 /mcp"| BRIDGE

    classDef cuga fill:#e6f2ff,stroke:#3b7dd8,color:#123
    classDef mcp fill:#fff4e0,stroke:#d8913b,color:#123
    classDef adapter fill:#efe9fb,stroke:#7d5bbe,color:#123
    classDef ext fill:#e9f7ec,stroke:#3ba55d,color:#123
    class FA,TA,DA cuga
    class BRIDGE mcp
    class LGE,FP,KP adapter
    class LGR,FR,KR ext
```

**Solid arrows are outbound** (CUGA FLO starting a process). **Dotted arrows are callbacks**
— the external runtimes reaching back into CUGA FLO for reasoning at each control point.

---

## The seam

Everything crosses one contract: **`MCPFlowBridge`**, a single FastMCP server. There is no
per-engine bridge and no second MCP server.

| Direction | Tool | Registered by | Called by |
|---|---|---|---|
| CUGA FLO → engine | `run_process` | the selected adapter | `FlowAgent.invoke()` |
| engine → CUGA FLO | `execute_task` | FlowAgent | a task control point |
| engine → CUGA FLO | `route_gateway` | FlowAgent | a gateway control point |
| engine → CUGA FLO | `evaluate_hook` | FlowAgent | a hook control point |
| engine → CUGA FLO | `complete_process` | FlowAgent | the process terminating |
| — | `register_flow`, `get_bpmn_process`, `get_flow_annotations` | ProcessRegistry | engine / config load |

`run_process` is **fire-and-forget**: it returns `{}` immediately. `FlowAgent.invoke()` then
blocks on an `asyncio.Future` that only `complete_process` resolves. This is why a ported
model must reach its completion task on *every* terminal branch — miss one and the call
never returns.

---

## Two transports, one contract

| | LangGraph | Flowable | Kogito |
|---|---|---|---|
| Adapter | `LangGraphWorkflowEngine` | `FlowableProxy` | `KogitoProxy` |
| Subclasses `WorkflowEngine` ABC | yes | no | no |
| Transport | in-process `FastMCPTransport` | HTTP | HTTP |
| Runtime location | same Python process | Docker container | Quarkus service |
| Callback mechanism | direct MCP client | Nashorn JS in script tasks | `CugaFlo.java` in script tasks |
| BPMN deployed at | compile of the StateGraph | runtime, via REST | **build time** — no runtime deploy |
| Hook redirect | graph recompile | `BpmnError` + boundary event + `Task_DynamicSkip` | `FlowRedirect.to()`, in-process |

The `WorkflowEngine` ABC is **not** what unifies these — only LangGraph implements it. The
real contract is the `run_process` MCP tool, which is why the two proxies need no common
base class.

For the external engines the bridge additionally starts a **uvicorn HTTP listener on
`callback_port` (8090)**, lazily on first `run_process`, so script tasks running inside the
container or JVM can call back in. The MCP URL is passed to the runtime as a process
variable (`cugaMcpUrl`) rather than hardcoded, because the right host differs between a
container (`host.docker.internal`) and a service on the host (`localhost`).

---

## What stays identical across engines

Selecting an engine is a YAML change:

```yaml
workflow_engine:
  type: langgraph | flowable | kogito
```

Unchanged in all three: the `policies/` markdown, the `tasks:` / `gateways:` / `hooks:`
config, the clean `BPMNdiagram.bpmn` CUGA FLO parses, and every agent class. What changes is
the engine-specific BPMN model — `*.bpmn20.xml` for Flowable, `*-kogito.bpmn` for Kogito,
none for LangGraph, which compiles the clean model directly.

See `README-FLOWABLE.md`, `README-KOGITO.md`, and the per-element procedures under
`docs/examples/flow_agent_app_inline/model_transform_knowledge/`.
