# IBM SuggestHub CUGA Prototype

SuggestHub demonstrates the production loop with CUGA as the intake surface:

```text
Employee talks to Bob in CUGA chat
        ↓
Bob calls SuggestHub MCP tools
        ↓
SQLite backend updates
        ↓
Public Hub / Manager Dashboard refresh from the same backend
```

The companion app is intentionally not a chatbot. It shows the non-chat product
surfaces: public hub feed, voting, manager dashboard, and resolved story/blog.

The prototype does not implement IBM w3id/SSO. Voting uses a browser
`localStorage` visitor ID. The manager dashboard is gated by an `@ibm.com`
email field until real IBM ID login is wired in.

## Run the Full CUGA Demo

From the repo root:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run --project . cuga start suggesthub
```

Open:

- CUGA chat: http://127.0.0.1:7860
- SuggestHub public hub: http://127.0.0.1:8095
- SuggestHub manager dashboard: http://127.0.0.1:8095/manager

Stop with `Ctrl+C` in the terminal running `cuga start suggesthub`.

If Bob fails with a provider `500` or a model timeout, restart with a smaller
completion budget or another configured model:

```powershell
$env:SUGGESTHUB_MAX_TOKENS="1000"
$env:MODEL_NAME="your-configured-chat-model"
uv run --project . cuga start suggesthub
```

## What Starts

- CUGA registry on the configured registry port
- CUGA built-in chat UI with Bob SuggestHub instructions
- SuggestHub MCP tools from `mcp_server.py`
- SuggestHub companion FastAPI app on port `8095`
- Shared SQLite database seeded with demo suggestions

## Demo Script

1. Open CUGA chat and tell Bob:
   `The standing desks on floor 3 are always broken and people waste time finding a working one.`
2. Bob checks SuggestHub for duplicates and offers the existing suggestion/upvote path.
3. Ask Bob to create a distinct suggestion if needed, then confirm publishing.
4. Open the companion app and watch the Public Hub update from the backend.
5. Open http://127.0.0.1:8095/manager, enter an `@ibm.com` email, then enter a suggestion ID, draft a response, and post a status update.
6. Review the Success Story section to show the closed loop.

## Companion-Only Mode

Use this only when you want to inspect the hub/dashboard without CUGA chat:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run --project . python docs/examples/suggesthub/run.py --reload
```

Then open:

- Public hub: http://127.0.0.1:8095
- Manager dashboard: http://127.0.0.1:8095/manager

## Verify the Companion API

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8095/api/suggestions
```

You should see HTTP `200` and seeded suggestions in the response.

Reset demo data from the companion UI or with:

```powershell
Invoke-WebRequest -UseBasicParsing -Method POST http://127.0.0.1:8095/api/demo/reset
```

Do not commit machine-specific paths or personal usernames into this README.

---

## Performance Tracing Tests

[`test_perf.py`](test_perf.py) instruments the 8 hotspot methods inside the
`cuga chat` pipeline with `time.perf_counter` wrappers and prints a per-segment
timing table to `stderr` after every test.

### Quick start (no API key required)

```powershell
# From the repo root — uses a mock LLM, no credentials needed
uv run --project . pytest docs/examples/suggesthub/test_perf.py -s -v
```

### Live mode (real LLM + real MCP server)

Start the full demo first (`cuga start suggesthub`), then in a second terminal:

```powershell
uv run --project . pytest docs/examples/suggesthub/test_perf.py -s -v --live
```

### What the output looks like

```
─────────────────────────────────────────────────────────
 TIMING REPORT  [test_simple_message]
─────────────────────────────────────────────────────────
 1_check_sse_availability [setup]          0.089 s
 2_apply_context_summarization             0.031 s
 3_build_runtime_context                   0.287 s
 4_build_bound_agent                       0.001 s
 5_chain_ainvoke [LLM call]                1.824 s  ×1
 6_execute_tool:find_similar_suggestions   0.441 s
 5_chain_ainvoke [LLM call]                1.612 s  ×2
 7_run_stream [TOTAL wall-clock]           4.285 s
 8_get_output [graph.get_state]            0.003 s
─────────────────────────────────────────────────────────
 TOTAL                                     8.573 s
─────────────────────────────────────────────────────────
```

### Tests included

| Test | What it exercises |
|---|---|
| `test_simple_message` | Basic chat round-trip, no tool calls |
| `test_tool_trigger_message` | Full MCP tool path: `find_similar_suggestions` → LLM round-trip 2 |
| `test_timing_thresholds` | Asserts per-segment budgets (mock mode only — catches regressions in CI) |

### Segment reference

| # | Segment | Source location | What makes it slow |
|---|---|---|---|
| 1 | `check_sse_availability` | [`chat_agent.py:52`](../../src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) | HTTP probe on startup (timeout now 1 s, was 5 s) |
| 2 | `apply_context_summarization` | [`chat_agent.py:435`](../../src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) | May call LLM to compress long history |
| 3 | `_build_runtime_context` | [`chat_agent.py:219`](../../src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) | Knowledge engine + tool-list assembly every turn |
| 4 | `_build_bound_agent` | [`chat_agent.py:298`](../../src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) | `LLMManager.get_model` + `model.bind_tools` |
| 5 | `chain.ainvoke` | [`chat_agent.py:451`](../../src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) | Main OpenAI/LiteLLM network round-trip |
| 6 | `execute_tool:<name>` | [`chat_agent.py:320`](../../src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) | MCP stdio round-trip to `mcp_server.py` subprocess |
| 7 | `run_stream` | [`agent_loop.py:680`](../../src/cuga/backend/cuga_graph/utils/agent_loop.py) | Total wall-clock for the entire agent turn |
| 8 | `get_output` | [`agent_loop.py:536`](../../src/cuga/backend/cuga_graph/utils/agent_loop.py) | LangGraph checkpoint read after stream ends |

### Optimisations already applied

| Fix | File | Change |
|---|---|---|
| SSE probe timeout | [`chat_agent.py:52`](../../src/cuga/backend/cuga_graph/nodes/chat/chat_agent/chat_agent.py) | Default `timeout` 5 s → 1 s |
| Tool-use round trips | [`chat.py:88`](../../src/cuga/backend/cuga_graph/nodes/chat/chat.py) | `max_round_trips` 4 → 2 |

### Deferred optimisations (see `# PERF:` comments in `test_perf.py`)

| Priority | Fix | Expected gain |
|---|---|---|
| 🔴 | Cache `_build_runtime_context` per `thread_id` | ~0.3 s/turn |
| 🟡 | Set `USE_LEGACY_EXECUTION=true` for SuggestHub | ~0.2 s/turn |
| 🟡 | Pre-warm `LLMManager.get_model` at startup | One-time ~0.1 s |
| 🟢 | Guard `apply_context_summarization` for sessions < 10 messages | ~0.04 s/turn |
