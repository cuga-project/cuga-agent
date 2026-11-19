# SDK tracing lifecycle

SDK Langfuse tracing is opt-in. Completed observations are exported by the
Langfuse client's batch processor. `invoke()` and `stream()` do not flush or
wait for exporter/network work at the end of each run.

For a short-lived application, flush once after all runs and caller-owned trace
contexts have finished. The synchronous helper below waits for the exporter and
logs export failures without replacing an application exception:

```python
import asyncio
from contextlib import aclosing

from cuga.backend.observability.langfuse_sdk import flush

async def main(agent):
    async with aclosing(agent.stream("Example request")) as updates:
        async for update in updates:
            print(update)
            # An early break is safe here: aclosing closes the stream first.

try:
    asyncio.run(main(agent))  # agent is your configured CugaAgent
finally:
    flush()
```

An async application's explicit shutdown boundary can instead use
`await asyncio.to_thread(flush)`, after its runs and trace contexts close.
This deliberately waits for export there, not on every run. Exporter timeouts
and retries remain the Langfuse client's responsibility; flushing is not a
server-ingestion acknowledgement or a guarantee of delivery after failure.

A bare `break` does not close a retained async iterator. Use `aclosing` as above
or `await updates.aclose()`. Early close preserves the last output and is not an
error. Cancellation is re-raised and classified as an error after graph/hook
cleanup. Caller-provided Langfuse callbacks or a `langfuse_trace_id` keep their
existing caller-task context; only SDK-owned traces use an isolated pull task.

This change does not decide the pending metadata/privacy policy in design
issue #1133. It does not change cuga-eval or require a flush per sample.

## Tool outcomes and caller callbacks

A ToolGuard denial is recorded at the actual guard verdict for direct, registry,
and MCP routes. It returns the same denial payload without executing the tool,
and local tracking consistently marks the call unsuccessful whether tracing is
on or off. Arbitrary tool-returned dictionaries are not interpreted as denials.

The SDK exports one tool observation when its own handler is in use. If a caller's
Langfuse handler receives the delegated tool callbacks, that handler owns the
observation and the SDK does not add a duplicate. This does not suppress local
tracking.

With the tested `langchain-core==1.6.12`, `BaseTool.arun` does not dispatch a terminal
callback for `asyncio.CancelledError`. A caller-owned tool span may therefore
remain incomplete. CUGA preserves cancellation and the local unsuccessful
outcome, without privately closing that span or creating a duplicate SDK span.
SDK-owned tool and run observations do record cancellation as an error.

Callback coverage for a guarded invocation is matched by tool name. Custom code
that calls a different same-named tracked tool inside that guarded invocation
can still share that coverage. The standard direct/registry/MCP provider routes
are covered by the regression tests; arbitrary same-name nesting is not claimed
to have invocation-identity-based deduplication.
