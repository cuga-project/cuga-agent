# SDK tool observation ownership

This contract is separate from #1133 metadata/privacy changes.

## Decision

Separate trace ancestry from who writes **tool** observations. For strict complete
tool outcomes, recommend an explicitly constructed CUGA-managed Langfuse handler.
Do not silently convert, mutate, or substitute an existing caller handler.

| Mode | Root / chain / generation owner | Tool observation owner | Terminal contract |
|---|---|---|---|
| Existing SDK tracing | SDK root + SDK Langfuse callbacks | CUGA recorder | One completed tool observation for each completed/interrupted known CUGA tool invocation |
| Existing supplied stock handler | Caller | Caller callbacks, with existing CUGA fallback for unrepresented routes. For a LangChain tool that CUGA calls inside its own wrapper (MCP and runtime tools through `ActivityTracker.invoke_tool`, ToolGuard), CUGA does not record the call only when the caller's handler received both the start and the end (result or error) of the inner `tool.ainvoke`. Argument errors before the call, timeouts and cancellation are recorded by CUGA. An inner `tool.ainvoke` after an outer one does not reach the handler again | Compatibility unchanged; returned provider errors may look successful, cancellation may be unfinished |
| Explicit `create_sdk_tool_handler()` | Caller root; inherited public chain/generation callbacks | CUGA recorder only | Same CUGA terminal contract without a duplicate callback tool span |

## Explicit opt-in API

```python
from cuga.backend.observability.langfuse_sdk import create_sdk_tool_handler

# trace_id is required with langfuse_trace_id: it must be the same id. Without it, the
# handler writes chain and model observations to a new trace and CUGA writes tool
# observations to known_trace_id, so one run becomes two traces. CUGA logs a warning
# when the two ids differ.
handler = create_sdk_tool_handler(trace_id=known_trace_id)
# Supply to CugaAgent(callbacks=[handler]) or this invocation only.
result = await agent.invoke(
    message,
    config={
        "callbacks": [handler],
        "configurable": {"langfuse_trace_id": known_trace_id},
    },
)
```

Alternatively invoke within a caller-owned active Langfuse root. Root ownership
is not transferred to CUGA. A configured trace ID is an ancestry link, not proof
that a backend has stored a root. The new tool writer does not inspect private
handler context/run maps to discover tool ancestry. Existing callback-reuse
logic is unchanged; this is not a claim that all legacy callback code avoids
private fields. The factory's `trace_id` alone is not tool ancestry: pass the
same id as `configurable.langfuse_trace_id`, or run inside an active root.

The factory uses only public `on_tool_start`, `on_tool_end`, `on_tool_error`
overrides. All chain/model methods are inherited unchanged. It creates a **new**
handler, not an adapter around arbitrary live callback state. `ignore_agent` is
a read-only, broader LangChain property, not a mutable per-run tool switch in
Langfuse4.5.1, and is not used.

## Compatibility and preconditions

- Existing configurations and original handler objects are untouched by default.
- Use exactly one Langfuse tool ownership policy in a run. A stock handler beside
  a managed handler violates the strict guarantee; CUGA emits a fixed
  diagnostic at the start of `invoke`, the supervisor and `stream` (SDK-owned and
  caller-owned) and preserves supplied callbacks rather than silently deleting one.
- A managed handler suppresses all its tool callbacks, including non-CUGA tools.
  Dedicate it to CUGA-managed runs; do not reuse it as the sole callback for
  arbitrary LangChain tools outside CUGA instrumentation.
- Tracing requires enabled settings and keys. No keys means no tool observations,
  never a hidden remote-client creation. Missing active root and explicit trace
  ID emits a diagnostic at that same boundary (invoke, supervisor and both stream
  paths); never fabricate a new unrelated trace.
- Preserve original tool returns, exceptions, denial payloads, arguments, prompt
  messages, callback objects, and invocation result shape. Do not raise a fake
  exception to make telemetry appear failed.
- Guarantee is scoped to instrumented direct, ToolGuard, registry and MCP routes,
  not arbitrary third-party code that bypasses CUGA's recording boundary.

## Terminal state

Observation ownership is decided before execution. The authoritative recorder
captures success, exception, real guard denial or provider error, timeout and
cancellation. Tracked aliases are matched by exact delegated callable. Guard
fallback coverage still uses tool names; arbitrary same-name nesting is not
guaranteed to have invocation-identity deduplication. Arbitrary returned `error`
dictionaries are not reinterpreted as failure verdicts. The public callback tool span never starts in managed mode,
so cancellation cannot leave an orphan of that kind.

Cancellation is cooperative. SDK-owned stream cleanup grants a bounded caller
wait (500ms, or immediate on second cancel), not forced thread/task termination.
A cancellation-suppressing worker can outlive the caller and delay process exit.
When it does, the SDK keeps a strong reference to the detached driver task until
it finishes and ends the root `cuga.stream` observation at once with
`level=ERROR`, so the trace does not stay open while the worker runs.
Normal close waits. Caller-owned stream context stays in the calling task.

## Validation and remaining scope

Synthetic Mac tests use Langfuse4.5.1 and langchain-core1.6.12, real SDK preparation
and real OTLP export to loopback. Models and registry/MCP transport are stubs.
No credentials, external endpoints, live provider or production data are used.
Real Langfuse backend readback is unavailable (container daemons not running).
Explicit flush only after run/context closure; no per-run network flush.

The original four stock-handler failure probes remain strict expected failures,
while corresponding managed-handler probes must pass. No diagnostic-event
machinery or new outcome metadata is introduced.
