# Tool-call budgets

Bounds how many tool/API calls a task may make. Three nested budgets, all under
`[advanced_features]` in [`settings.toml`](../../src/cuga/settings.toml), each
disabled by setting it to `0`.

## The problem

CUGA had two limits before this, and neither bounded the number of tool calls:

| Existing setting | Bounds |
|---|---|
| `cuga_lite_max_steps` (70) | how many **code blocks** a turn may run |
| `tool_call_timeout` (30) | how long **one call** may take |

Neither caps how many calls a block makes. A single generated block can loop
`call_api` unboundedly — in one observed run, **7,685 calls** before the task was
stopped by hand. Functionally a hang that burns budget and time.

## The three budgets

| Setting | Default | Reset | Role |
|---|---|---|---|
| `max_tool_calls_per_block` | 100 | every code block | **fail-fast guard** |
| `max_tool_calls_per_run` | 256 | every user turn | **turn ceiling** |
| `max_tool_calls_per_thread` | 2000 | never | **conversation ceiling** |

**Only the budgets that do not reset are bounds.** The block budget is
recoverable on purpose — the model reflects and writes another block with a
*fresh* block budget — so on its own it permits
`cuga_lite_max_steps × max_tool_calls_per_block` = **7,000** calls, roughly the
runaway it exists to stop. It shortens each stall; it does not bound spend.
`test_per_block_cap_alone_does_not_bound_the_run` pins that so the block cap is
never mistaken for a ceiling.

Env overrides follow the usual dynaconf form, e.g.
`DYNACONF_ADVANCED_FEATURES__MAX_TOOL_CALLS_PER_RUN=512`.

## Why these defaults

**They are engineering judgment, not measurement.** No trace analysis of
production task cost informed them. They were chosen so the caps never fire for
legitimate work while still stopping a runaway, and they should be revisited
against real data — see *Tuning* below.

- **`max_tool_calls_per_block = 100`** — a block needing more than 100 calls is
  almost always a loop that should page, batch, or filter instead. Low enough to
  break a stall in seconds; recoverable, so a legitimate heavy block just retries
  more narrowly. This is a latency knob: lower it if you care about noticing
  runaways fast, raise it if you have genuinely call-heavy single blocks.
- **`max_tool_calls_per_run = 256`** — roughly an order of magnitude above a
  typical multi-step task and ~30× below the observed runaway, so it should be
  invisible in normal use. This is the main cost knob.
- **`max_tool_calls_per_thread = 2000`** — about eight full-budget turns. A
  conversation legitimately spending 2,000 tool calls is rare; this is the
  backstop for a long thread that the per-turn cap cannot bound, and the least
  evidence-based of the three.

### Tuning

Measure before changing. Take the p99 tool-call count of legitimate tasks in your
deployment and set `max_tool_calls_per_run` to roughly 3× it — high enough never
to fire on real work, low enough to stop a loop early. Set the thread ceiling
from typical conversation length × the run budget.

For **benchmark and CI runs**, consider a much lower run budget: there, a
degraded "answer from what you have" scores as a bad answer and looks like a
model regression rather than a budget breach. A hard failure is more useful —
see [#665](https://github.com/cuga-project/cuga-agent/issues/665) for the
proposed `exit_behavior` knob.

## Behaviour when a budget is spent

Enforcement checks **widest-first** (thread → run → block), so the error carries
the most consequential advice.

- **Block breach** — recoverable. The error surfaces as execution output, names
  how much run budget is left, and the model retries with a narrower loop.
- **Run or thread breach** — terminal for the turn. `call_model` withholds the
  tools for **one** synthesis pass, instructs the model to answer from the data
  already retrieved, and ends the turn. Code emitted anyway is not executed, and
  auto-continue cannot reopen the turn.

Withholding the tools is the load-bearing part. Previously the model kept every
tool bound, so each retry burned a full LLM call, hit the same wall, and came
back — until `cuga_lite_max_steps` tripped and the task ended with *"Maximum step
limit reached"* instead of the answer it could have written. An instruction not
to call tools is a request; an empty tool list is a constraint.

## Where enforcement lives

All three are checked in a single `enforce_call_budget`
([`cuga_lite/tracking/tracker.py`](../../src/cuga/backend/cuga_graph/nodes/cuga_lite/tracking/tracker.py)),
reached by two paths:

| Invocation style | Enforced at |
|---|---|
| `await call_api('app', 'tool', {...})` — registry-routed | `call_api` (local closure + registry provider) |
| `await tool_name(**{...})` — by name (MCP/SDK, direct LangChain, plain python, skills, runtime fs/shell, `find_tools`, todos, `delegate_to_*`) | `counted_tool_call()` in `CodeExecutor.eval_with_tools_async` |

Charging at the namespace handed to generated code — rather than at each of the
twelve registration sites — is what makes the guarantee hold for tool kinds
nobody thought to wrap. Both the CugaLite and supervisor graphs pass through it,
so a newly registered tool is charged with no extra work. A registry-backed tool
crosses both boundaries; a re-entrancy guard charges it once, not twice.

Outside a seeded sandbox context every counter is a no-op, so non-CugaLite
callers are unaffected.

### State

`tool_calls_used_run` lives on the subgraph states and is reset by `prepare`
(which runs on `START -> prepare` only — resuming after a tool-approval interrupt
therefore continues the budget rather than refilling it).

`tool_calls_used_thread` lives on **`AgentState`** with a `keep_highest` reducer.
Both parts matter:

- CugaLite and CugaSupervisor run as **compiled subgraph nodes**, re-entered
  fresh each turn, sharing only state keys the parent also declares. A counter
  living solely on the subgraph state restarts at 0 every turn and the
  conversation ceiling never binds.
- `server/main.py` rebuilds `AgentState(**latest_state_values)` each turn, so the
  incoming value is sometimes the field default `0`. A monotonic counter cannot
  be reset by a caller rebuilding its input.

**Consequence:** the conversation ceiling cannot be reset within a `thread_id`.
Once a thread reaches `max_tool_calls_per_thread`, each later turn costs one
model call and returns "budget spent". A new conversation needs a new
`thread_id`.

## Not covered

- **Remote execution (E2B / OpenSandbox)** — the remote sandbox runs injected
  `call_api` source that never touches the in-process counters, so the cap is
  **silently** unenforced there and `tool_calls_used_*` reports `0` rather than
  "not counted". [#668](https://github.com/cuga-project/cuga-agent/issues/668)
- **`LocationResolverAgent`** — a LangGraph ReAct agent whose tools execute via
  `ToolNode`, outside the sandbox namespace.
  [#667](https://github.com/cuga-project/cuga-agent/issues/667)
- **Per-tool limits** — every tool costs one unit; capping one expensive tool
  requires lowering the global cap.
  [#666](https://github.com/cuga-project/cuga-agent/issues/666)

## Relation to LangChain's `ToolCallLimitMiddleware`

[`ToolCallLimitMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/tool_call_limit/ToolCallLimitMiddleware)
has `run_limit` and `thread_limit` — the same two units, which is where this
design takes its vocabulary ("run" = one graph invocation = one user turn).

It cannot be used here. It counts tool calls the model **requests**, in
`after_model` off `AIMessage.tool_calls`, before a `ToolNode` executes them.
CugaLite has no `ToolNode` — even with `bind_tools` on, `AIMessage.tool_calls` is
transcoded into a Python block that runs in the sandbox. The 7,685-call runaway
registers there as **one** tool call, so `run_limit` would never fire. Counting
has to happen at the sandbox namespace instead. Its block scope has no analogue
in a tool-calling agent for the same reason.

One deliberate difference: that middleware's run counter tracks *attempted* calls
including blocked ones while its thread counter tracks only allowed ones. All
three counters here check capacity before incrementing, so each one means "calls
that actually happened".
