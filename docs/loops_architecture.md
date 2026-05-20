## CUGA Loops — Architecture

![CUGA Loops architecture](images/loops_architecture.svg)

```mermaid
flowchart TB
    subgraph Agent["CUGA Agent (per thread)"]
        Invoke["agent.invoke(prompt, thread_id)"]
        LLM["LLM + LangChain tools"]
        Invoke --> LLM
    end

    subgraph CtxVars["contextvars (set around invoke)"]
        CV["current_agent_name<br/>current_thread_id<br/>current_app_name<br/>current_loop_id"]
    end
    Invoke -.sets.-> CV

    subgraph Tools["loops/tools.py — @tool surface"]
        T1["schedule_recurring"]
        T2["schedule_wakeup"]
        T3["list_my_loops"]
        T4["cancel_loop"]
    end
    LLM -- calls --> Tools
    Tools -- _identity() --> CV

    subgraph Service["LoopsService (singleton)"]
        Reg["agent registry<br/>name → invoke_fn"]
        Arm["_arm_loop()"]
        Sched[("APScheduler<br/>AsyncIOScheduler")]
        Arm --> Sched
    end
    Agent -- register_agent(name, invoke_fn) --> Reg
    Tools -- register_loop --> Service

    subgraph Persist["loops/registry.py"]
        DB[("Loop rows +<br/>LoopRun history")]
    end
    Service <--> Persist

    subgraph Parse["cron_parser.py"]
        P["'5m' • '0 9 * * *'<br/>'daily' • 'weekday'"]
    end
    Tools --> Parse

    Sched -- fire(loop_id) --> Runner["runner.fire_loop()"]
    Runner -- lookup --> Reg
    Runner -- sets current_loop_id --> CV
    Runner -- invoke_fn(prompt, thread_id) --> Invoke
    Runner -- write LoopRun --> Persist

    subgraph Surface["loops/api.py + ui.html"]
        API["HTTP endpoints"]
        UI["Inspector UI"]
    end
    Surface <--> Service
    Surface <--> Persist

    Boot(["app startup"]) -- start() --> Service
    Service -- list_active_for_revival<br/>+ re-arm --> Sched
```

### Lifecycle

```mermaid
stateDiagram-v2
    [*] --> ACTIVE: schedule_recurring / schedule_wakeup
    ACTIVE --> PAUSED: pause_loop
    PAUSED --> ACTIVE: resume_loop
    ACTIVE --> EXPIRED: expires_at reached
    ACTIVE --> CANCELLED: cancel_loop
    ACTIVE --> ORPHANED: fired but owning agent<br/>not registered
    CANCELLED --> [*]: delete_loop
    EXPIRED --> [*]: delete_loop
```

### Fire sequence

```mermaid
sequenceDiagram
    participant APS as APScheduler
    participant R as runner.fire_loop
    participant S as LoopsService
    participant Reg as Registry (DB)
    participant A as Agent.invoke

    APS->>R: trigger(loop_id)
    R->>Reg: get(loop_id)
    R->>S: get_agent(loop.agent_name)
    alt agent missing
        R->>Reg: status = ORPHANED
    else agent registered
        R->>R: set current_loop_id ctxvar
        R->>A: invoke(loop.prompt, loop.thread_id)
        A-->>R: answer
        R->>Reg: append LoopRun (OK / ERROR)
        R->>Reg: bump fire_count, last_fire_at
    end
```

### Key design points

- **Singleton service** ([service.py](../src/cuga/backend/loops/service.py)) wraps a single `AsyncIOScheduler` on the app's event loop.
- **Identity via contextvars** ([service.py:28-42](../src/cuga/backend/loops/service.py#L28-L42)) — tools auto-bind the loop to the calling agent + thread; no explicit args.
- **Three trigger kinds** map to APScheduler triggers: `DELAY → DateTrigger`, `INTERVAL → IntervalTrigger`, `CRON → CronTrigger`.
- **Re-arm on startup** — `start()` reloads ACTIVE loops from the registry and refreshes each `next_fire_at`.
- **Orphan handling** — registry rows survive restarts; if no agent claims them at fire time, they're flagged ORPHANED but stay visible in the UI.
- **Default `expires_in_days=7`** keeps stray loops from accumulating cost.
