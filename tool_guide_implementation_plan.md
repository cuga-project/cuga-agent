# ToolGuard Implementation Plan

This document breaks [`toolguard_integration.md`](toolguard_integration.md) into small, ordered implementation tasks.

The goal is to implement ToolGuard as a provider-level decorator around [`ToolProviderInterface`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/base.py), so runtime enforcement works for:

- direct SDK LangChain tools
- registry/API tools
- tracker/runtime tools
- future wrapped providers

> Note: the file name currently remains `tool_guide_implementation_plan.md` for continuity, but the feature being implemented is **ToolGuard**, not ToolGuide.

---

## Phase 0: Pre-implementation cleanup, baseline, and guardrails

### Task 0.1: Fix obvious existing code issues

Make the small correctness fixes already identified in [`toolguard_integration.md`](toolguard_integration.md):

- fix the missing comma in [`policy/__init__.py`](src/cuga/backend/cuga_graph/policy/__init__.py)
- restore final newline at EOF where missing
- make [`filesystem_sync.py`](src/cuga/backend/cuga_graph/policy/filesystem_sync.py) robust when serializing `tool_guards`
- review whether [`ToolGuide.tool_guards`](src/cuga/backend/cuga_graph/policy/models.py) should move from optional to `default_factory=dict`

### Task 0.2: Establish current baseline

Run a baseline syntax/lint check before implementation so pre-existing failures are separated from ToolGuard changes.

Suggested checks:

```bash
uv run ruff check src/cuga/backend/cuga_graph/nodes/ src/cuga/backend/cuga_graph/policy/ src/cuga/sdk.py
python -m py_compile src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/combined.py
python -m py_compile src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/registry.py
python -m py_compile src/cuga/backend/cuga_graph/policy/__init__.py
python -m py_compile src/cuga/sdk.py
```

Deliverable:

- short note of current failures, if any
- classify failures as pre-existing cleanup vs implementation work

### Task 0.3: Confirm current provider construction points

Identify all places where providers are constructed and note which ones must be wrapped by `ToolGuardingToolProvider`.

Likely places to inspect:

- [`CugaAgent.__init__`](src/cuga/sdk.py)
- [`DynamicAgentGraph`](src/cuga/backend/cuga_graph/graph.py)
- [`server/main.py`](src/cuga/backend/server/main.py)
- [`supervisor_config.py`](src/cuga/supervisor_utils/supervisor_config.py)
- chat-agent provider construction, if in scope
- any explicit `CombinedToolProvider(...)` construction
- any custom provider construction paths that should remain unchanged for now

Deliverable:

- short list of provider construction sites to update during implementation
- short list of provider construction sites intentionally left unchanged

### Task 0.4: Remove current partial ToolGuard leakage from raw providers

The current working tree has partial ToolGuard parameters added directly to raw provider/factory code. Remove those early to avoid competing integration seams.

Clean up [`CombinedToolProvider`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/combined.py):

- remove `enable_toolguard_policies`
- remove `toolguard_policy_storage`
- remove pass-through arguments to `create_tool_from_api_dict()`
- fix indentation/formatting around `create_tool_from_api_dict()` calls

Clean up [`create_tool_from_api_dict()`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/registry.py):

- remove ToolGuard-specific parameters
- keep registry tool creation raw and focused on API invocation

Deliverable:

- ToolGuard logic no longer leaks into raw providers/factories
- raw providers remain responsible only for producing tools

---

## Phase 1: Introduce the provider decorator skeleton

### Task 1.1: Create [`toolguard.py`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/toolguard.py)

Create a new provider wrapper module:

- file: [`toolguard.py`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/toolguard.py)
- class: `ToolGuardingToolProvider`

Initial responsibilities only:

- store `base_provider`
- store `policy_storage`
- store `cuga_folder`
- store `enabled`
- initialize runtime/cache fields

Suggested fields:

- `base_provider`
- `policy_storage`
- `cuga_folder`
- `enabled`
- `_runtime`
- `_runtime_initialized`
- `_runtime_lock`
- `_guarded_tools_cache`

### Task 1.2: Implement transparent provider delegation

Implement the basic provider methods as pass-throughs:

- `initialize()`
- `get_apps()`
- `reset()` if supported by the wrapped provider

The wrapper should expose `initialized` consistently. Since SDK code checks:

```python
if not hasattr(self.tool_provider, "initialized") or not self.tool_provider.initialized:
    await self.tool_provider.initialize()
```

the wrapper must not break this behavior.

### Task 1.3: Add optional method forwarding

Forward common optional provider methods where present:

- `reset()`
- `add_tool()`
- `add_tools()`
- any provider-specific operation required by current SDK code

The forwarding methods should also invalidate the ToolGuard runtime/wrapper cache when tools change.

Deliverable:

- wrapper can stand in front of a provider without changing normal behavior
- SDK initialization and optional provider methods continue to work

### Task 1.4: Add provider package exports if needed

If provider modules are exported through:

```text
src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/__init__.py
```

then export `ToolGuardingToolProvider` there as well.

Deliverable:

- import style remains consistent with the rest of the provider package

---

## Phase 2: Add raw-tool access contract

### Task 2.1: Add raw tool APIs to the wrapper

Implement:

- `get_raw_tools(app_name)`
- `get_all_raw_tools()`

These must delegate directly to the wrapped provider and must not wrap tools.

Suggested behavior:

```python
async def get_raw_tools(self, app_name):
    return await self.base_provider.get_tools(app_name)
```

```python
async def get_all_raw_tools(self):
    apps = await self.base_provider.get_apps()
    tools = []
    for app in apps:
        tools.extend(await self.base_provider.get_tools(app.name))
    return tools
```

### Task 2.2: Update [`ToolGuardInvoker`](src/cuga/backend/cuga_graph/policy/tool_guard/tool_invoker.py)

Change `ToolGuardInvoker` so it prefers raw tools:

```python
if hasattr(self.tool_provider, "get_all_raw_tools"):
    tools_list = await self.tool_provider.get_all_raw_tools()
else:
    tools_list = await self.tool_provider.get_all_tools()
```

Deliverable:

- ToolGuard delegate calls use raw tools and avoid recursive guard wrapping

---

## Phase 3: Add guarded tool wrapping

### Task 3.1: Implement wrapper cache strategy

Define and implement the guarded tool cache in [`ToolGuardingToolProvider`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/toolguard.py).

Recommended cache key:

- `(app_name, tool.name, id(tool))`

Alternatively:

- cache by `(app_name, tool.name)`
- store the raw tool reference alongside the wrapper
- recreate wrapper if the raw tool object identity changes

Avoid using only `(app_name, tool.name)` without object identity checks because dynamic tools, registry refreshes, and include-filter resets can replace tools while preserving names.

### Task 3.2: Implement `_wrap_tool()`

Add a private method that takes:

- raw tool
- app name

and returns a guarded wrapper preserving:

- `name`
- `description`
- `args_schema`
- `_app_name`
- `_operation_id`
- `_param_constraints`
- `_response_schemas`
- any metadata needed by Lite tracking, prompt generation, and response handling

### Task 3.3: Preserve invocation compatibility

Ensure the wrapped tool still behaves like a LangChain tool in the ways Cuga expects.

At minimum:

- async invocation path works
- sync `@tool` functions work
- wrapper calls the original tool only after guard approval
- wrapper preserves metadata used by tracking and prompt generation

The wrapper should prefer:

```python
await original_tool.ainvoke(all_kwargs)
```

If that is unavailable or unsupported, use the equivalent LangChain sync invocation path safely.

This must be tested with sync tools because [`test_toolguard_flights.py`](test_toolguard_flights.py) defines sync `@tool` functions.

Deliverable:

- wrapped tools are drop-in replacements for raw LangChain tools
- both sync and async tools remain callable

---

## Phase 4: Add ToolGuard runtime lifecycle to the wrapper

### Task 4.1: Add lazy runtime initialization

Implement a private method such as:

- `_get_or_create_toolguard_runtime()`

Behavior:

- if disabled, return `None`
- if policy system is disabled, return `None`
- if already initialized, reuse cached runtime
- otherwise initialize once under lock

Runtime should be created with:

- `tool_provider=self`
- configured `policy_storage`
- configured `cuga_folder`

Important:

- create `ToolGuardRuntime(tool_provider=self, ...)`, not with `base_provider`
- this lets `ToolGuardInvoker` discover `get_all_raw_tools()` on the wrapper and avoid recursive guard wrapping

### Task 4.2: Add runtime invalidation APIs

Implement:

- `set_policy_storage(policy_storage)`
- `invalidate_toolguard_runtime()`

Invalidation must:

- clear runtime reference
- reset initialized flag
- clear guarded wrapper cache
- clear or refresh invoker tool caches when applicable

If an existing runtime has cleanup/shutdown behavior, call it before dropping the reference.

If cleanup requires awaiting, consider either:

- making invalidation async, or
- providing both sync invalidation and async cleanup paths

Deliverable:

- wrapper can refresh runtime after policy or tool changes
- stale runtime state is not reused after policy updates

### Task 4.3: Define disabled-policy behavior

If ToolGuard wrapper is disabled or global policy support is disabled:

- do not initialize `ToolGuardRuntime`
- return raw tools or transparent wrappers
- never block calls because of missing ToolGuard runtime

Deliverable:

- policy-disabled systems remain transparent and backwards compatible

---

## Phase 5: Enforce guards in wrapped tools

### Task 5.1: Normalize tool call arguments

Inside the guarded wrapper function:

- merge args and kwargs using [`merge_tool_call_args()`](src/cuga/backend/cuga_graph/nodes/cuga_lite/tracking/arguments.py)

The wrapper needs `param_names`. Derive them from `tool.args_schema`:

- Pydantic v2: `tool.args_schema.model_fields`
- Pydantic v1 compatibility if needed: `tool.args_schema.__fields__`
- fallback to `kwargs.keys()` if schema is unavailable

### Task 5.2: Call [`guard_tool_call()`](src/cuga/backend/cuga_graph/policy/tool_guard/tool_guard_runtime.py)

Before invoking the original tool:

- get runtime from `_get_or_create_toolguard_runtime()`
- if runtime exists, call `guard_tool_call()`

Inputs:

- `app_name`
- `function_name=tool.name`
- merged arguments

### Task 5.3: Define blocked-call payload

If guard returns an error, return a stable structured payload:

```python
{
    "error": "...",
    "blocked_by_policy": True,
    "policy_violation": True,
    "tool": tool_name,
    "app": app_name,
}
```

The payload should be stable enough for tests and understandable enough for the agent loop to summarize.

### Task 5.4: Preserve normal execution when allowed

If:

- ToolGuard is disabled
- no applicable guard exists
- guard passes

then invoke the original tool normally.

Deliverable:

- runtime enforcement exists at the actual tool execution boundary
- blocked tools are not executed

---

## Phase 6: Add runtime fail-closed semantics

### Task 6.1: Update [`ToolGuardRuntime.guard_tool_call()`](src/cuga/backend/cuga_graph/policy/tool_guard/tool_guard_runtime.py)

Revise behavior so that:

- no applicable guards → return `None`
- applicable guards + runtime available + pass → return `None`
- applicable guards + violation → return error string
- applicable guards + runtime/domain load failure → return blocking error string

This change should stay centralized in `guard_tool_call()`, not duplicated in the wrapper.

Recommended behavior when applicable guards exist but runtime cannot load:

```python
return (
    f"ToolGuard runtime unavailable for '{function_name}' in app '{app_name}'. "
    "Tool call blocked because an applicable guard policy exists but could not be loaded."
)
```

### Task 6.2: Verify no fail-closed behavior when no guard applies

Fail-closed should only happen after confirming that the tool has applicable guard policies for the requested app.

If no guard applies, `guard_tool_call()` should allow the call.

Deliverable:

- fail-closed semantics are correct and scoped only to active/applicable guards

---

## Phase 7: Make build-time and runtime folder handling consistent

### Task 7.1: Add `cuga_folder` to [`ToolGuardManager`](src/cuga/backend/cuga_graph/policy/tool_guard/manager.py)

Ensure generated domain files are saved under:

```text
{cuga_folder}/toolguard/domain/
```

instead of assuming default folder behavior indirectly.

### Task 7.2: Add `cuga_folder` to [`ToolGuardRuntime`](src/cuga/backend/cuga_graph/policy/tool_guard/tool_guard_runtime.py)

Update runtime domain loading so it uses configured `cuga_folder` instead of hard-coded `.cuga`.

Current hard-coded behavior to replace:

```python
Path.cwd() / ".cuga" / "toolguard" / "domain"
```

### Task 7.3: Verify runtime/build-time path symmetry

Confirm build-time save path and runtime load path are identical for both:

- default `.cuga`
- custom configured folder

Deliverable:

- no domain lookup mismatch between generation and enforcement

---

## Phase 8: Wire wrapper into SDK direct tools

### Task 8.1: Update [`CugaAgent.__init__`](src/cuga/sdk.py)

Change provider construction so:

- create base provider first
- wrap it with [`ToolGuardingToolProvider`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/toolguard.py)

This must cover:

- `tool_provider` explicitly passed in
- `tools=[...]`
- empty direct provider fallback

Avoid double-wrapping:

- if the provided `tool_provider` is already a `ToolGuardingToolProvider`, reuse it
- update its `policy_storage`, `cuga_folder`, and `enabled` settings if needed

### Task 8.2: Update lazy policy initialization path

In [`PoliciesManager._ensure_policy_system()`](src/cuga/sdk.py), after policy system is ready:

- if tool provider supports `set_policy_storage()`, pass storage into wrapper

Example:

```python
if hasattr(self._agent.tool_provider, "set_policy_storage"):
    self._agent.tool_provider.set_policy_storage(self._agent._policy_system.storage)
```

### Task 8.3: Preserve knowledge tool auto-injection

Current SDK knowledge injection checks:

```python
isinstance(self.tool_provider, DirectLangChainToolsProvider)
```

After wrapping, this will no longer be true.

Update SDK knowledge injection so it works with the wrapper.

Options:

- inspect the wrapper's `base_provider`
- add `get_base_provider()` / `unwrap()` helper
- expose `add_tool()` / `add_tools()` on the wrapper and delegate to the base provider

After adding knowledge tools:

- invalidate ToolGuard runtime
- clear guarded wrapper cache

### Task 8.4: Preserve dynamic tool addition

Update `CugaAgent.add_tool()` so it works when the direct provider is wrapped.

The wrapper should either:

- implement `add_tool()` and delegate to base provider if available
- or SDK should unwrap the base provider

After adding a dynamic tool:

- invalidate ToolGuard runtime
- clear guarded wrapper cache
- reset `_graph` / `_compiled_graph` as before

Deliverable:

- direct SDK LangChain tools are covered by runtime enforcement
- knowledge injection continues working
- dynamic tool addition continues working

---

## Phase 9: Wire wrapper into graph/server registry paths

### Task 9.1: Identify graph/server provider construction sites

Find where [`CombinedToolProvider`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/combined.py) is created for server or graph flows.

Likely places:

- [`server/main.py`](src/cuga/backend/server/main.py)
- [`backend/cuga_graph/graph.py`](src/cuga/backend/cuga_graph/graph.py)
- [`supervisor_config.py`](src/cuga/supervisor_utils/supervisor_config.py)
- chat-agent provider construction if in scope

### Task 9.2: Wrap [`CombinedToolProvider`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/combined.py)

At selected construction sites:

- create raw `CombinedToolProvider`
- wrap it with `ToolGuardingToolProvider`

Example:

```python
base_provider = CombinedToolProvider(...)
tool_provider = ToolGuardingToolProvider(
    base_provider,
    policy_storage=policy_system.storage if policy_system else None,
    cuga_folder=settings.policy.cuga_folder,
    enabled=settings.policy.enabled,
)
```

Avoid double-wrapping if the provider is already wrapped.

Deliverable:

- registry/API and tracker/runtime tools are covered by the same enforcement seam

---

## Phase 10: Fix policy mutation and merge semantics

### Task 10.1: Change [`update_tool_guard()`](src/cuga/sdk.py) to merge semantics

Revise `PoliciesManager.update_tool_guard()` so it:

- starts from existing `tool_guards`
- updates only provided tool entries
- preserves omitted tool guards
- preserves omitted fields for each existing tool guard

Current replace behavior should be avoided:

```python
tool_guards=tool_guards_obj
```

### Task 10.2: Invalidate runtime after policy mutations

After relevant policy mutations, call:

```python
invalidate_toolguard_runtime()
```

if available.

At minimum after:

- `update_tool_guard()`
- `add_tool_guide()`
- policy deletion
- policy clearing
- folder load/sync
- dynamic tool addition

### Task 10.3: Decide whether policy mutations require graph reset

Determine whether policy mutation requires resetting:

- `_graph`
- `_compiled_graph`

Runtime invalidation may be enough if tools are fetched dynamically during prepare nodes. But if wrappers are already bound into the compiled graph/model, graph reset may be needed.

At minimum, after `update_tool_guard()` on SDK agent:

- invalidate ToolGuard runtime
- verify whether existing graph/tool binding sees the updated runtime
- reset graph only if needed

Deliverable:

- runtime does not become stale after policy changes
- graph/tool binding behavior is verified

---

## Phase 11: Fix app-name consistency for direct SDK tools

### Task 11.1: Confirm direct provider app name

Verify the direct SDK provider uses:

```python
app_name="runtime_tools"
```

### Task 11.2: Align guard code generation

Ensure direct SDK tool guard generation uses the same app name.

Short-term:

- require `app_name="runtime_tools"` when generating code for direct SDK tools

Long-term:

- auto-detect app name when omitted and unambiguous

### Task 11.3: Update flight example/test

Update [`test_toolguard_flights.py`](test_toolguard_flights.py) to use the direct provider app name unless the provider app name is intentionally changed.

Current likely mismatch:

```python
app_name="cuga_app"
```

should become:

```python
app_name="runtime_tools"
```

or the provider default should be changed consistently.

Deliverable:

- generated domain files match runtime lookup for direct SDK tools
- the example test reflects the actual runtime app name

---

## Phase 12: Add target-tool validation and app-name autodetection

### Task 12.1: Validate target tool coverage

In `generate_tool_guard_examples()`, `generate_tool_guard_code()`, and `update_tool_guard()` where applicable, verify:

- policy is `ToolGuide`
- target tool is covered by `policy.target_tools`, unless `target_tools` contains `"*"`

### Task 12.2: Auto-detect app name when possible

When `app_name` is omitted in `generate_tool_guard_code()`:

- inspect provider apps/tools
- find apps containing `target_tool`
- if exactly one app contains the tool, use that app name
- if multiple apps contain the tool, require explicit `app_name`
- if no app contains the tool, raise a helpful error

Deliverable:

- generation is safer and less error-prone
- direct SDK examples can omit `app_name` in the common case once autodetection is implemented

---

## Phase 13: Add tests in small layers

### Task 13.1: Unit tests for [`ToolGuardingToolProvider`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/toolguard.py)

Add tests for:

- wrapper delegates `initialize()` and `get_apps()`
- no runtime/policies
- wrapper disabled
- runtime exists but no guard
- guard passes
- guard blocks
- runtime load fails with applicable guard
- `get_all_raw_tools()` returns unwrapped tools
- wrapper preserves metadata
- sync `@tool` invocation works
- async tool invocation works
- cache invalidates when raw tool identity changes

### Task 13.2: Unit tests for [`ToolGuardInvoker`](src/cuga/backend/cuga_graph/policy/tool_guard/tool_invoker.py)

Add tests proving:

- raw tools are preferred when available
- fallback to guarded `get_all_tools()` still works when raw API absent
- duplicate tool detection still behaves as expected

### Task 13.3: Direct SDK integration test

Mirror [`test_toolguard_flights.py`](test_toolguard_flights.py) for:

- `CugaAgent(tools=[...])`
- violating request blocked by runtime enforcement

Test instrumentation should prove the tool was not called.

Example:

```python
calls = []

@tool
def book_flight(...):
    calls.append(...)
    return "..."
```

Assert:

```python
assert calls == []
```

Also assert final answer does not claim booking succeeded and mentions the policy restriction/block.

### Task 13.4: Registry/provider integration test

Verify:

- wrapped registry tool is blocked
- raw `call_api()` is not reached when blocked
- registry tool executes normally when guard passes or no guard applies

### Task 13.5: Invalidation regression test

Verify:

- first invoke without active guard
- update guard code
- second invoke enforces new runtime behavior

### Task 13.6: Delegate recursion test

Verify:

- guard code invoking helper tools uses raw tools
- no recursive guard wrapping occurs

### Task 13.7: SDK compatibility tests

Add tests for wrapper compatibility with existing SDK behaviors:

- knowledge auto-injection still works
- `CugaAgent.add_tool()` still works
- explicit custom provider can be wrapped
- already wrapped provider is not double-wrapped

Deliverable:

- enforcement is proven across direct SDK and provider-backed flows
- existing SDK behavior is preserved

---

## Phase 14: Final validation and cleanup

### Task 14.1: Run formatting

Run formatting on touched files.

Suggested command:

```bash
uv run ruff format src/cuga/backend/cuga_graph/nodes/ src/cuga/backend/cuga_graph/policy/ src/cuga/sdk.py
```

### Task 14.2: Run lint checks

Run lint checks on touched files.

Suggested command:

```bash
uv run ruff check src/cuga/backend/cuga_graph/nodes/ src/cuga/backend/cuga_graph/policy/ src/cuga/sdk.py
```

### Task 14.3: Run targeted tests

Run:

- provider tests
- policy tests
- direct SDK integration tests
- registry/provider integration tests

### Task 14.4: Run broader regression checks if needed

If targeted tests pass, run broader relevant suites.

Deliverable:

- implementation is validated and ready for review

---

## Suggested Implementation Order Summary

1. Fix baseline syntax/lint issues and obvious cleanup.
2. Remove current partial ToolGuard leakage from `CombinedToolProvider` / `registry.py`.
3. Add `ToolGuardingToolProvider` skeleton.
4. Add transparent delegation and optional method forwarding.
5. Add raw-tool APIs.
6. Update `ToolGuardInvoker`.
7. Add guarded wrapper creation and metadata preservation.
8. Add sync/async invocation compatibility.
9. Add lazy runtime lifecycle, cleanup, and invalidation.
10. Add guard enforcement in wrapped tools.
11. Make runtime fail closed for applicable guards.
12. Add `cuga_folder` to `ToolGuardManager` and `ToolGuardRuntime`.
13. Wire wrapper into `CugaAgent`, including knowledge injection and `add_tool()` compatibility.
14. Wire wrapper into selected graph/server `CombinedToolProvider` construction sites.
15. Fix `update_tool_guard()` merge semantics and invalidation behavior.
16. Fix app-name consistency and update the flight example/test.
17. Add target-tool validation and app-name autodetection.
18. Add unit/integration/regression tests.
19. Run formatting, lint, and test validation.

---

## Notes for Implementation

- Keep ToolGuard logic out of raw providers like [`CombinedToolProvider`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/combined.py).
- Keep ToolGuard logic out of raw registry tool creation in [`registry.py`](src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/registry.py).
- Centralize enforcement in the provider wrapper plus [`ToolGuardRuntime`](src/cuga/backend/cuga_graph/policy/tool_guard/tool_guard_runtime.py).
- Centralize recursion prevention through raw-tool access in [`ToolGuardInvoker`](src/cuga/backend/cuga_graph/policy/tool_guard/tool_invoker.py).
- Centralize fail-closed semantics in `ToolGuardRuntime.guard_tool_call()`.
- Avoid double-wrapping providers.
- Preserve SDK compatibility for knowledge injection and dynamic tool addition.
- Prove blocked calls do not execute the original tool.
- Ensure direct SDK ToolGuard generation and runtime lookup use the same app name.