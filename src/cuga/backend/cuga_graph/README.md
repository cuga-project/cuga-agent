# Cuga Agent Graph

The server entry graph is **`CugaEntryGraph`** ([`entry_graph.py`](entry_graph.py)): a thin router over three runtimes.

```
ChatAgent → EntryRouter → CugaLite | CugaSupervisor | CugaBrowser → FinalAnswerAgent
```

| Runtime | Module | Role |
|---------|--------|------|
| **CugaLite** | `nodes/cuga_lite/` | Single-agent code execution loop |
| **CugaSupervisor** | `nodes/cuga_supervisor/` | Multi-agent orchestration |
| **CugaBrowser** | `nodes/cuga_browser/` + `nodes/browser/` | Web automation subgraph |

Shared infrastructure lives under `nodes/cuga_agent_core/`, `policy/`, `state/`, and `nodes/shared/`.

The legacy full graph (`DynamicAgentGraph` with task decomposition, API planner pipeline, and save/reuse) has been removed. `DynamicAgentGraph` is a backwards-compatible alias for `CugaEntryGraph`.
