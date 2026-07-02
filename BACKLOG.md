# Backlog

## Async completion: move Future wait to top-level caller

Currently `FlowAgent.invoke()` creates an `asyncio.Future` and awaits it internally,
blocking until `complete_process` is called by the engine. This means FlowAgent sits
in the middle of the waiting chain: `run.py → invoke() → Future → complete_process`.

**Proposed change**: `invoke()` returns the Future instead of awaiting it. The caller
holds the wait directly: `run.py → await Future ← complete_process`. FlowAgent becomes
a pure initiator with no internal coordination state.

```python
# FlowAgent.invoke() — fire and forget
async def invoke(self, input_data, process_variables=None) -> asyncio.Future:
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    self._pending_completions[self.process_key] = future
    async with self.bridge.get_client() as c:
        await c.call_tool("run_process", {"process_key": ..., "initial_inputs": ...})
    return future

# Caller
handle = await flow_agent.invoke(input_data)
state = await handle
```

**What needs updating**: `run.py`, `loan_demo.py`, `cli/main.py` — all call sites of
`FlowAgent.invoke()` change from one `await` to two.
