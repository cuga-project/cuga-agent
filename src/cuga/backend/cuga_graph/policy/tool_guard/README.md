# ToolGuard

ToolGuard is Cuga's runtime policy-enforcement layer for tool calls.

It lets a `ToolGuide` policy do more than enrich a tool description: if the policy also defines a tool-specific guard under `tool_guards`, Cuga can execute that guard before the actual tool call and decide whether the call should be allowed or blocked.

ToolGuard is implemented as a provider-level decorator around Cuga tool providers:

```text
CugaAgent / DynamicAgentGraph
  └── ToolGuardingToolProvider
        └── DirectLangChainToolsProvider / CombinedToolProvider / custom provider
```

This means ToolGuard can protect tools from multiple sources through one enforcement seam:

- direct SDK LangChain tools passed to `CugaAgent(tools=[...])`
- registry/API tools
- tracker/runtime tools
- future custom providers, when wrapped

---

## Key concepts

### `ToolGuide`

A `ToolGuide` is a policy that enriches tool behavior or descriptions.

A regular `ToolGuide` may only add instructions to a tool description.

A ToolGuard-enabled `ToolGuide` also has `tool_guards`:

```python
tool_guards={
    "book_flight": {
        "violating_examples": [...],
        "compliance_examples": [...],
        "policy_code": "...",
    }
}
```

A `ToolGuide` also carries a `guards_enabled` flag (default `True`). Setting it to `False` disables guard enforcement for the entire policy while keeping the tool description enrichment active. This lets you temporarily bypass guards without deleting them or disabling the whole policy.

```python
guards_enabled=False  # guards still exist, but are not enforced at runtime
```

### `ToolGuardingToolProvider`

`ToolGuardingToolProvider` wraps a normal tool provider.

It delegates normal provider behavior:

```python
await provider.initialize()
await provider.get_apps()
await provider.get_tools(app_name)
```

but returns guarded tool wrappers from public tool access methods.

The wrapped provider remains responsible for producing raw tools. ToolGuard only adds runtime policy enforcement.

### `ToolGuardRuntime`

`ToolGuardRuntime` loads active `ToolGuide` policies with guard code and validates tool calls.

It follows these rules:

| State | Behavior |
|---|---|
| No policy exists | tool runs normally |
| ToolGuide exists but has no guard for this tool | tool runs normally |
| Guard exists for a different tool | current tool runs normally |
| ToolGuide has `guards_enabled=False` | guards skipped, tool runs normally |
| Applicable guard exists and passes | original tool runs |
| Applicable guard exists and blocks | original tool is not called |
| Applicable guard exists but runtime/domain cannot load | tool call is blocked |

The important rule is:

> ToolGuard only decides calls when an applicable guard exists for that app/tool.

No applicable guard means ToolGuard is transparent.

---

## Runtime flow

```text
agent.invoke(...)
  -> CugaLite asks tool provider for tools
  -> ToolGuardingToolProvider returns guarded tool wrappers
  -> model calls a tool
  -> guarded wrapper normalizes arguments
  -> ToolGuardRuntime.guard_tool_call(...)
  -> if allowed: original tool is called
  -> if blocked: original tool is not called
```

Blocked calls return a structured payload:

```python
{
    "error": "Tool call blocked by policy: ...",
    "blocked_by_policy": True,
    "policy_violation": True,
    "tool": "book_flight",
    "app": "runtime_tools",
}
```

---

## Direct SDK usage

Example direct-tool flow:

```python
from cuga import CugaAgent
from langchain_core.tools import tool


@tool
def book_flight(user_id: str, flight_id: str, passengers: int) -> str:
    """Book a flight for a user."""
    return f"Flight {flight_id} booked for user {user_id} with {passengers} passengers"


@tool
def get_membership(user_id: str) -> str:
    """Get user membership level."""
    return "regular"


agent = CugaAgent(tools=[book_flight, get_membership])
```

At construction time, Cuga wraps the direct provider:

```text
CugaAgent
  └── ToolGuardingToolProvider
        └── DirectLangChainToolsProvider(app_name="runtime_tools")
```

The wrapper is transparent until a guard policy exists.

Direct SDK tools are enforced at runtime under the app name `"runtime_tools"`.
If a `ToolGuide` uses `target_apps`, it must include `"runtime_tools"` for
directly provided LangChain/runtime tools; otherwise the runtime app filter will
treat the guard as not applicable.

---

## Creating a ToolGuide

```python
policy_id = await agent.policies.add_tool_guide(
    name="Flight Booking Membership Policy",
    content="""
Regular members cannot book flights for more than 3 passengers.
Gold and silver members have no passenger restrictions.
""",
    target_tools=["book_flight"],
    description="Membership-based restrictions for flight bookings",
)
```

At this point, this is still only a `ToolGuide`. If there is no `tool_guards` entry with policy code, ToolGuard does not block tool calls.

---

## Generating guard examples

```python
violating_examples, compliance_examples = await agent.policies.generate_tool_guard_examples(
    policy_id=policy_id,
    target_tool="book_flight",
)
```

Then store the examples:

```python
await agent.policies.update_tool_guard(
    policy_id=policy_id,
    tool_guards={
        "book_flight": {
            "violating_examples": violating_examples,
            "compliance_examples": compliance_examples,
        }
    },
)
```

`update_tool_guard()` uses merge semantics:

- omitted tool guards are preserved
- omitted fields on existing guards are preserved
- updating examples does not delete existing policy code
- updating policy code does not delete examples

---

## Generating guard code

For direct SDK tools, the virtual app name is usually:

```python
"runtime_tools"
```

So guard code can be generated explicitly with:

```python
guard_code = await agent.policies.generate_tool_guard_code(
    policy_id=policy_id,
    target_tool="book_flight",
    app_name="runtime_tools",
)
```

If `app_name` is omitted, ToolGuard tries to auto-detect the app exposing the target tool. If multiple apps expose the same tool name, pass `app_name` explicitly.

For runtime enforcement, `target_apps` is also checked. Direct SDK tools use
`"runtime_tools"` at runtime, so policies scoped with `target_apps` must include
`"runtime_tools"` to enforce on those tools.

Then store the generated code:

```python
await agent.policies.update_tool_guard(
    policy_id=policy_id,
    tool_guards={
        "book_flight": {
            "violating_examples": violating_examples,
            "compliance_examples": compliance_examples,
            "policy_code": guard_code,
        }
    },
)
```

After this update, the provider runtime/cache is invalidated. The same existing agent can enforce the new guard on the next invocation.

---

## Batch-generating guards from a JSON policy file

The SDK can import a policy JSON file and generate ToolGuards for every eligible
Tool Guide policy in that file:

```python
result = await agent.policies.generate_tool_guards_from_json(
    "policies-export.json",
)
```

By default, imported policies are merged into existing policy storage. Existing
stored policies are not generated unless their IDs are also present in the JSON
file.

To replace existing policy storage before importing and generating:

```python
result = await agent.policies.generate_tool_guards_from_json(
    "policies-export.json",
    clear_existing=True,
)
```

The result includes the import summary, source policy IDs, per-policy generation
results, skipped policies, and batch-level errors:

```python
{
    "status": "ok",
    "import": {"count": 2, "enabled": True, "errors": []},
    "source_policy_ids": ["tool_guide_one", "tool_guide_two"],
    "generated": {...},
    "skipped": [],
    "errors": [],
}
```

Policies are skipped when they are not Tool Guides, are disabled, are missing
after import, or target wildcard tools instead of concrete tool names.

---

## Invoking the same agent

```python
result = await agent.invoke(
    "Book flight AB12 for uid_56845 with 4 passengers"
)
```

Expected behavior:

- the model may call `book_flight`
- the guarded wrapper runs first
- `ToolGuardRuntime.guard_tool_call(...)` validates the call
- if the guard blocks, `book_flight` is not executed
- the agent receives a structured policy violation result

No agent reconstruction is required.

---

## Provider wrapping helpers

`ToolGuardingToolProvider` exposes helper functions so SDK/graph code does not duplicate wrapping logic.

```python
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import (
    ensure_toolguard_provider,
    configure_toolguard_provider,
    invalidate_toolguard_provider,
    unwrap_tool_provider,
)
```

### `ensure_toolguard_provider(...)`

Wraps a provider, or reconfigures it if it is already wrapped:

```python
tool_provider = ensure_toolguard_provider(
    base_provider,
    policy_storage=policy_storage,
    cuga_folder=".cuga",
    enabled=True,
)
```

### `configure_toolguard_provider(...)`

Updates a wrapped provider if the provider is ToolGuard-wrapped:

```python
configure_toolguard_provider(
    tool_provider,
    policy_storage=policy_storage,
)
```

### `invalidate_toolguard_provider(...)`

Clears runtime/cache state after policy or tool changes:

```python
invalidate_toolguard_provider(tool_provider)
```

### `unwrap_tool_provider(...)`

Returns the underlying raw provider when needed:

```python
base_provider = unwrap_tool_provider(tool_provider)
```

This is mainly useful for compatibility paths such as checking whether the underlying provider is a `DirectLangChainToolsProvider`.

---

## Policy code execution and trust model

`policy_code` is admin-authored Python executed by the ToolGuard runtime in the
tool-execution/backend context. In current CUGA Lite flows, local agent execution
calls the guarded provider in the backend service process, and E2B execution
calls back to the backend `/functions/call` path; in both cases the guard code
runs with backend service-process privileges, not inside the agent code sandbox.

Only trusted administrators with manage access should be allowed to create or
modify `policy_code`. Review guard code for correctness, security, and
performance before enabling it. Moving guard execution into a sandboxed
tool-execution worker is a defense-in-depth architecture option, but it is not
the current runtime behavior.

---

## Raw tools and recursion prevention

ToolGuard guard code can call tools through a delegate.

To prevent recursive guard wrapping, `ToolGuardingToolProvider` exposes raw tool APIs:

```python
await provider.get_raw_tools(app_name)
await provider.get_all_raw_tools()
```

`ToolGuardInvoker` prefers these APIs when available:

```python
if hasattr(tool_provider, "get_all_raw_tools"):
    tools = await tool_provider.get_all_raw_tools()
else:
    tools = await tool_provider.get_all_tools()
```

This lets guard code invoke helper tools without recursively re-entering guarded wrappers.

---

## Files in this folder

### `manager.py`

Build-time ToolGuard integration.

Responsibilities:

- initialize available tools for generation
- generate violating/compliance examples
- generate guard code
- save ToolGuard runtime domain files under `{cuga_folder}/toolguard/domain`
- auto-detect app name when unambiguous

### `tool_guard_runtime.py`

Runtime enforcement.

Responsibilities:

- load active `ToolGuide` policies with guard code
- load ToolGuard runtime domains
- build app-specific runtime mappings
- validate tool calls
- fail closed only when an applicable guard exists but runtime/domain loading fails

### `tool_invoker.py`

ToolGuard delegate bridge.

Responsibilities:

- expose Cuga tools to ToolGuard guard code
- prefer raw tools from `get_all_raw_tools()`
- avoid recursive guarded-wrapper calls

### `tool_guard_policy_updates.py`

Policy mutation helpers.

Responsibilities:

- merge incoming tool guard updates into existing `ToolGuide.tool_guards`
- preserve omitted tools
- preserve omitted fields on existing guards

---

## Validation

Focused unit tests live in:

```text
tests/unit/test_toolguard_provider.py
```

Useful commands:

```bash
uv run ruff check \
  src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/toolguard.py \
  src/cuga/backend/cuga_graph/policy/tool_guard/

uv run pytest tests/unit/test_toolguard_provider.py
```

---

## Design rule

Keep ToolGuard responsibilities separated:

```text
sdk.py
  public SDK API and lifecycle orchestration

ToolGuardingToolProvider
  provider wrapping, runtime enforcement, invalidation, raw-tool access

ToolGuardManager
  build-time examples/code/domain generation

ToolGuardRuntime
  runtime guard loading and guard execution

tool_guard_policy_updates.py
  policy mutation helpers
```

Raw providers such as `CombinedToolProvider` and registry tool factories should stay focused on producing raw tools. They should not contain ToolGuard enforcement logic.