# SuggestHub Performance Tracing Plan

## Top-Level Overview

**Goal:** Add lightweight `time.perf_counter` instrumentation at every slow seam inside the
`cuga chat` request→response pipeline as it runs for the SuggestHub example.  A pytest test
file (`docs/examples/suggesthub/test_perf.py`) will monkey-patch the eight hotspot methods,
run a set of representative test messages, assert timing thresholds, and print a per-segment
breakdown so CI flags regressions automatically.

**Scope:**
- `docs/examples/suggesthub/test_perf.py` — pytest file with timing patches and assertions.
- Two safe low-risk production fixes applied directly to source: reduce `check_sse_availability`
  timeout from 5 s → 1 s, and cap `max_round_trips` from 4 → 2 in `_execute_direct_tool_calls`.
- All other optimisation recommendations documented as `# PERF:` comments inside the test file.

**Non-goals:** OpenTelemetry, Langfuse, Prometheus, persistent logging.

---

## Identified Hotspots (grounded in code)

| # | Location | Symbol | Why it is slow |
|---|---|---|---|
| 1 | `chat_agent.py:435` | `apply_context_summarization` | Calls `AgentState.manage_message_context` which may itself call the LLM to summarise history |
| 2 | `chat_agent.py:450` | `_build_runtime_context` | Calls knowledge engine, assembles system prompt, deduplicates tools — network + I/O |
| 3 | `chat_agent.py:302` | `_build_bound_agent → model.bind_tools` | Resolves LLM from `LLMManager.get_model` on every invoke |
| 4 | `chat_agent.py:451` | `chain.ainvoke` (first LLM call) | Main OpenAI/LiteLLM round-trip |
| 5 | `chat.py:107` | `agent.execute_tool` (per tool call) | Each MCP tool = stdio subprocess round-trip to `mcp_server.py` |
| 6 | `chat.py:116` | `agent.invoke` inside tool loop | Up to 4 additional LLM round-trips |
| 7 | `chat_agent.py:104` | `check_sse_availability` (setup) | HTTP probe with 5-second timeout on every fresh `setup()` |
| 8 | `agent_loop.py:540` | `graph.get_state` inside `get_output` | LangGraph checkpoint read after stream ends |

---

## Sub-Tasks

---

### Sub-Task 1 — Write the pytest perf tracing file

**Intent**
Create `docs/examples/suggesthub/test_perf.py`.  At module import time it monkey-patches
the eight hotspot async methods with `time.perf_counter` wrappers that store durations in
a module-level `collections.defaultdict(list)` keyed by segment name.  After each test the
`perf_report` fixture prints a structured table to `sys.stderr` and resets the dict.

**Expected Outcomes**
- `pytest docs/examples/suggesthub/test_perf.py -s` runs and after each test case prints:

```
─────────────────────────────────────────────────────
 TIMING REPORT  [test_simple_message]
─────────────────────────────────────────────────────
 check_sse_availability               0.12 s
 apply_context_summarization          0.04 s
 _build_runtime_context               0.31 s
 _build_bound_agent                   0.00 s
 chain.ainvoke  [LLM call 1]          1.82 s
 execute_tool: find_similar_sugg…     0.44 s
 chain.ainvoke  [LLM call 2]          1.61 s
 ─────────────────────────────────────────────────────
 TOTAL                                4.34 s
─────────────────────────────────────────────────────
```

- A `test_timing_thresholds` test asserts total wall-clock < 15 s per turn and individual
  segments < their budgets (e.g. `_build_runtime_context` < 2 s).
- No test fixture requires a running server — a mock LLM response is injected so the test
  can run in CI without real OpenAI credentials.

**Todo List**
1. Create `docs/examples/suggesthub/test_perf.py`.
2. Write a `patch_timings` autouse session fixture that wraps all 8 hotspot methods using
   `functools.wraps` + `time.perf_counter` — pure stdlib, no new dependencies.
3. Write a `perf_report` function fixture that yields (so patching runs before the test body),
   then after yield prints the table and resets the dict.
4. Write `test_simple_message` — sends `"The coffee machine on Floor 3 is broken"` through
   the agent and asserts a non-empty response is returned.
5. Write `test_tool_trigger_message` — sends `"Log a safety issue about the broken railing on
   Level 2"` to exercise the `execute_tool` → `find_similar_suggestions` → LLM-round-2 path.
6. Write `test_timing_thresholds` — parametrized assertion over the collected timings dict.
7. Inject a mock `LLMManager.get_model` return value at module level to allow dry-run without
   real API credentials; gate with an `--live` pytest CLI flag to skip the mock when running
   against a real model.

**Relevant Context**
- [`docs/examples/suggesthub/agents/bob_agent.py`](docs/examples/suggesthub/agents/bob_agent.py) — `get_bob_agent()` returns `CugaAgent`
- [`src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py`](src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) — all hotspot methods
- [`src/cuga/backend/cuga_graph/utils/agent_loop.py`](src/cuga/backend/cuga_graph/utils/agent_loop.py:680) — `run_stream` lines 680-700

**Status:** [x] done

---

### Sub-Task 2 — Add per-tool timing inside `execute_tool`

**Intent**  
The MCP tool loop can fire up to 4 times per turn; each tool call goes over stdio to the
SuggestHub subprocess.  We need to label each call with the tool name so the report shows
`execute_tool: find_similar_suggestions  0.44 s` individually, not a single aggregate.

**Expected Outcomes**
- Every MCP tool invocation (e.g. `find_similar_suggestions`, `save_suggestion_draft`) appears
  as its own labelled row in the timing table.
- The inner retry path (`ClosedResourceError` reconnect in `execute_tool` lines 345-352) is
  captured and shown with label `execute_tool:<name>:retry`.

**Todo List**
1. Inside the `execute_tool` wrapper in `perf_trace.py`, extract `tool_call.get("name")` and
   use it as the dict key: `f"execute_tool:{tool_name}"`.
2. Capture both the primary attempt and the retry separately.

**Relevant Context**
- [`src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py:320`](src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) — `execute_tool`
- [`docs/examples/suggesthub/mcp_server.py`](docs/examples/suggesthub/mcp_server.py) — list of 8 tools that will appear in output

**Status:** [x] done

---

### Sub-Task 3 — Apply safe source fixes + document the rest

**Intent**
Apply the two low-risk fixes directly to production source, then add `# PERF:` annotation
comments in `test_perf.py` for every other recommended optimisation so you have a clear
actionable reference alongside the trace data.

**Safe fixes to apply directly to source:**

| File | Change | Why safe |
|---|---|---|
| [`chat_agent.py:52`](src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) | `check_sse_availability(url, timeout=5)` default → `timeout=1` | The SSE server either responds in < 100 ms or is down; 5 s only delays startup |
| [`chat.py:92`](src/cuga/backend/cuga_graph/nodes/chat/chat.py) | `max_round_trips: int = 4` → `max_round_trips: int = 2` | SuggestHub MCP tools never require more than 1 round-trip; extra rounds only trigger if the LLM incorrectly re-calls an auto-execute tool |

**Documented-only optimisations (added as `# PERF:` comments in `test_perf.py`):**

| Priority | Fix | Why deferred |
|---|---|---|
| 🔴 | Cache `_build_runtime_context` per `thread_id` | Needs invalidation logic; risk of stale tool list |
| 🟡 | Set `USE_LEGACY_EXECUTION=true` for SuggestHub | Bypasses SSE — needs integration testing |
| 🟡 | Pre-warm `LLMManager.get_model` at startup | One-time hit, not urgent |
| 🟢 | Guard `apply_context_summarization` for < 10 messages | Minor gain; guard condition TBD |

**Expected Outcomes**
- `check_sse_availability` default timeout is 1 s in source.
- `max_round_trips` default is 2 in `_execute_direct_tool_calls`.
- `test_perf.py` header docstring contains the full recommendation table with `# PERF:` markers.

**Todo List**
1. Edit [`src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py:52`](src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) — change `timeout: int = 5` → `timeout: int = 1`.
2. Edit [`src/cuga/backend/cuga_graph/nodes/chat/chat.py:92`](src/cuga/backend/cuga_graph/nodes/chat/chat.py) — change `max_round_trips: int = 4` → `max_round_trips: int = 2`.
3. Add docstring + `# PERF:` comments in `test_perf.py` for the deferred recommendations.

**Relevant Context**
- [`src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py:52`](src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) — `check_sse_availability` signature
- [`src/cuga/backend/cuga_graph/nodes/chat/chat.py:92`](src/cuga/backend/cuga_graph/nodes/chat/chat.py) — `_execute_direct_tool_calls` signature with `max_round_trips`

**Status:** [x] done
