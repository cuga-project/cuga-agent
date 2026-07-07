# 01 — NOW worker via `POST /invoke` (the agent seam)

The one seam every trigger (and any direct caller) uses to run an arbitrary agent. This is the
core of Phase 1: a normalized envelope in → the **worker does the hard work** (a **CUGA** agent by
default) → an answer out (and an optional delivery). Code:
[`app.py`](../../src/cuga/backend/events/app.py) `invoke`,
[`runtime.py`](../../src/cuga/backend/events/runtime.py) `CugaRuntime.run` (raises if no CUGA stack; react is opt-in).

```mermaid
sequenceDiagram
    autonumber
    participant C as Caller<br/>(AP HTTP step / curl)
    participant I as POST /invoke<br/>(app.py)
    participant RT as Worker runtime<br/>(CugaRuntime, default)
    participant AS as AgentStore<br/>(shared — storage+isolation)
    participant W as CUGA worker<br/>(DynamicAgentGraph)<br/>—raises if no CUGA stack (react opt-in)
    participant MCP as cuga-* MCP / CUGA tools
    participant Sink as Delivery sink<br/>(EA_CAPTURE_URL / channel)

    C->>I: {source,event,text,agent,deliver,scope} + X-Gateway-Token
    Note over I: verify token · Envelope.from_dict · new/keep trace_id
    I->>I: scope = env.scope OR resolve_principal(headers).scope
    I->>RT: get_agent(agent, scope)  (404 if unknown)
    RT->>AS: read spec (scope-keyed)
    I->>RT: run(agent, thread_id, worker_input, scope, deliver_to)
    alt full CUGA stack present (app_context)
        RT->>W: build DynamicAgentGraph (per agent_id) · ainvoke(text, thread_id)
        loop reason ↔ act (CUGA supervisor/policies/knowledge)
            W->>MCP: tool call
            MCP-->>W: result
        end
        W-->>RT: final answer
    else opted-in react (EVENTS_CUGA_FALLBACK_REACT=1)
        RT->>RT: raise — or react if EVENTS_CUGA_FALLBACK_REACT=1
    end
    RT-->>I: answer
    opt deliver = true
        I->>Sink: POST {agent, answer, thread_id, trace_id}
        Sink-->>I: 200
    end
    I-->>C: {ok, agent, answer, trace_id}
```

**Verified by:** the **CUGA** worker path is **live-verified** — `live_cuga_worker_check.py`
(full `.venv`) provisions a `backend=cuga` worker → builds a `DynamicAgentGraph` on demand →
runs it via CUGA's real `AgentLoop` → returns an answer (`executed via: CUGA DynamicAgentGraph`).
The **opt-in react** fallback path (EVENTS_CUGA_FALLBACK_REACT=1) is verified by `live_react_check.py` + `live_phase2_watchers.py`
(focused venv). Memory is per `thread_id` either way. (Next: attach the worker's MCP servers to
the CUGA graph so cuga workers get tools — see [../TODO.md](../TODO.md).)
