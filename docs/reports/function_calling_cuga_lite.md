# Function Calling in CUGA-Lite (API mode): Status, Gaps, and a Design for SDK-First Tool Invocation Policy

**Date:** 2026-07-11 · **Author:** Segev Shlomov · **Method:** end-to-end source trace (no README/demo claims; every statement carries a `file:line` citation)

---

## 1. Executive summary

- **CUGA-lite has native function-calling *binding*, but no native function-calling *execution*.** A full `bind_tools` subsystem exists (6 modes, an LLM shortlister cap, per-model runtime profiles), yet when a bound model returns `AIMessage.tool_calls`, the calls are **transpiled back into Python** (`result = await tool(args)` → sandbox) rather than dispatched. Only `tool_calls[0]` survives; parallel calls are silently dropped; a response with both text and tool_calls has its tool_calls ignored.
- **The system prompt actively forbids the very feature the binding machinery enables** — even when tools are bound, the model is instructed to "NEVER output … native tool-call syntax" (`prompts/mcp_prompt.jinja2:138`). The two halves of the feature fight each other.
- **The SDK exposes none of it.** `CugaAgent` has no constructor or `invoke()` parameter for tool-calling mode. The knobs already flow through LangGraph's `configurable` channel with a fully implemented precedence (`configurable > model-profile > settings`, `model_runtime_profile.py:74-115`) — the SDK simply never populates it. Today the only path is the untyped `config={"configurable": {...}}` backdoor.
- **Status assessment: an experimental benchmark harness, stalled since 2026-06-03**, hardened around edge cases (cap/shortlister) but never given execution semantics, prompt support, an SDK surface, or tests for its core transpiler.
- **Recommendation:** ship a first-class, layered **Tool Invocation Policy** — `ToolCalling` config at settings/profile/agent/invoke/per-tool levels — with *hybrid* execution semantics: **first-class native tool-call handling via sandbox-backed execution** (tool_calls are honored and executed through the same guarded, tracked, variable-aware machinery as code-act — *not* an immediate ToolNode-style direct dispatcher; that remains a gated Phase 3). ~90% of the runtime plumbing already exists; the missing pieces are the execution contract, the prompt, the typed SDK surface, and tests. Phased plan in §6, ~4 PRs, each independently valuable.

---

## 2. Scope and method

Everything below was traced in source on `main` (`df173678`), working branch `function_calling`. Key files read end-to-end:

| Area | Files |
|---|---|
| Lite loop | `nodes/cuga_agent_core/graph/shared_graph.py`, `shared_nodes.py` |
| Adapter & recovery | `nodes/cuga_lite/adapter/graph_adapter.py`, `adapter/response_utils.py`, `adapter/prepare_node.py`, `adapter/sandbox_node.py` |
| Binding machinery | `nodes/cuga_lite/helpers/bind_tools.py`, `bind_tools/cap.py`, `model_runtime_profile.py` |
| Providers | `nodes/cuga_lite/providers/{base,langchain,combined,registry,toolguard}.py` |
| SDK | `src/cuga/sdk.py`, `nodes/cuga_lite/cuga_lite_graph.py` |
| Config | `src/cuga/settings.toml`, `src/cuga/config.py` |
| Prompts | `nodes/cuga_lite/prompts/mcp_prompt.jinja2` |
| Tests | `tests/unit/test_cuga_lite_bind_tools.py`, `tests/unit/test_tool_use_failed_recovery.py`, `nodes/cuga_lite/tests/*` |

---

## 3. Current state — the deep dive

### 3.1 Two tool-invocation paths exist; one is real

**Path 1 — code-act (the production default).** The lite graph is exactly three nodes: `prepare → call_model ⇄ sandbox` (`shared_graph.py:50-61`). `prepare` extracts each tool's callable (preferring `.coroutine` → `.func` → `._run`) into `adapter._tools_context[tool.name]` (`prepare_node.py:337-351`); the sandbox merges those callables into the exec namespace (`sandbox_node.py:76`) so generated Python calls tools by name. Schemas are dropped in this map — it is a name→callable dict.

**Path 2 — native binding (the dormant shim).** `call_model` asks the adapter for a bound model: `bound = await adapter.resolve_bind_tools(...) or active_model` (`shared_nodes.py:185`). `resolve_model_with_bind_tools` (`helpers/bind_tools.py:142-373`) selects `StructuredTool`s per mode and calls `active_model.bind_tools(...)` — all nine real `.bind_tools(` call sites in the lite/core tree live in this one file. Default mode is `"none"` (`settings.toml:56`), so in practice nothing binds unless configured or a model profile forces it.

There is **no** `ToolNode`, `ToolExecutor`, `tool_choice`, `parallel_tool_calls`, or `with_structured_output` anywhere in `cuga_lite`/`cuga_agent_core` (grep-clean). The Supervisor graph never binds tools (`CoreGraphAdapter.resolve_bind_tools` returns `None`, `graph_nodes.py:103-117`).

### 3.2 What actually happens to a native tool call (the critical finding)

When a bound model returns `AIMessage.tool_calls`:

1. `normalize_response` (`graph_adapter.py:149-159`) checks `content` — **only if the text content is empty** does it attempt recovery: `extract_code_from_response_tool_calls(response)`.
2. That transpiler (`adapter/response_utils.py:37-63`) reads **`tool_calls[0]` only**, JSON-decodes string args, and returns:
   ```python
   ```python
   result = await {name}({args_str})
   print(result)
   ```
   ```
3. The fenced string re-enters the normal code path (`shared_nodes.py:197` → `Command(goto="sandbox")` at `:230-239`) and executes in the sandbox against `_tools_context` like any generated code.

A second recovery path exists for providers that *reject* tool attempts (`tool_use_failed`, e.g. Groq 400): `ainvoke_model` catches the exception, extracts the attempted call, and fabricates a `_FakeResponse` whose content is the same fenced Python (`graph_adapter.py:107-124`, `errors.py:101-120`).

**Consequences of the current contract:**

| # | Defect | Evidence | Impact |
|---|---|---|---|
| D1 | Parallel tool calls silently dropped — only `tool_calls[0]` is transpiled | `response_utils.py:45` | Models that emit multi-call turns (increasingly the norm) lose work invisibly |
| D2 | `text + tool_calls` → tool_calls ignored entirely | `graph_adapter.py:151` (`if not content:`) | A "here's my reasoning" preamble erases the tool call; model gets no ToolMessage/observation and loops |
| D3 | Transpiler is **untested** — no test imports `response_utils`; the empty-content branch of `normalize_response` is never exercised | test grep; `test_agent_graph_adapter.py:164-179` covers only non-empty content | The only bridge between native FC and execution has zero coverage |
| D4 | Prompt contradiction: binding enables FC while the prompt forbids it unconditionally | `mcp_prompt.jinja2:138` ("NO FUNCTION CALLING JSON … NEVER") — no `bind_tools` conditional anywhere in the template | Bound modes rely on the model *disobeying* instructions; pad-to-cap docstring even measures this ("0 tool calls vs 5-7 without padding", `cap.py:218-243`) |
| D5 | No `tool_choice` support at all | grep-clean | Cannot force/forbid a call; no structured-answer termination |
| D6 | Args transpiled via `json.dumps`/`repr` into source (`response_utils.py:31-34`) | code | Adequate but lossy for non-JSON-safe values; validation against `args_schema` never happens |
| D7 | Cap knobs are settings-only — `max_count` and `pad_to_cap` have no `configurable` override | `cap.py:43,65` | Per-agent/per-run tuning impossible without global mutation |
| D8 | Dead parameters: `create_cuga_lite_graph(agent_state=…, model_settings=…)` accepted, never read | `cuga_lite_graph.py:164,168` | Misleading extension points |
| D9 | **Models without `bind_tools` support crash the run** when binding is enabled: `NotImplementedError` is a subclass of `RuntimeError`, so the deliberate `except RuntimeError: raise` for cap errors (`helpers/bind_tools.py:367-370`) re-raises it instead of falling back to the unbound model — `call_model` dies | reproduced live (§3.5); `langchain_core` `BaseChatModel.bind_tools` raises `NotImplementedError` by default | Custom/community models + any binding mode = hard failure, not degradation |

### 3.3 Configuration today — inventory and precedence

The resolution ladder **already exists and is correct**: `configurable > model runtime profile > settings` (`model_runtime_profile.py:74-115`, verified). What flows through it:

| Knob | settings.toml | config.py Validator | `configurable` override | SDK typed surface |
|---|---|---|---|---|
| `cuga_lite_bind_tools_mode` (`none/find_tools/all/apps/tools/apps_and_tools`) | `:56` | ✗ (code fallback) | ✓ (`model_runtime_profile.py:88`) | ✗ |
| `cuga_lite_bind_tools_apps` | `:57` | ✗ | ✓ (`:95`) | ✗ |
| `cuga_lite_bind_tools_tool_names` | `:58` | ✓ (`config.py:196`) | ✓ (`:102`) | ✗ |
| `cuga_lite_bind_tools_include_find_tools` | `:59` | ✗ | ✓ (`:109`) | ✗ |
| `cuga_lite_bind_tools_max_count` | `:62` | ✓ (`:197`) | **✗** (`cap.py:43`) | ✗ |
| `cuga_lite_bind_tools_pad_to_cap` | `:64` | ✓ (`:198`) | **✗** (`cap.py:65`) | ✗ |

Model profiles: only `gpt-oss-20b` has one (`model_runtime_profile.py:9-14`) — it silently auto-enables `mode="apps"` for `["knowledge","filesystem"]` + `include_find_tools`. So **a CugaAgent on gpt-oss-20b already runs with native binding on, and nothing in the SDK reveals or controls that.**

Tool selection internals worth preserving: dedup-by-name first-wins across provider + overlay (`helpers/bind_tools.py:93-139`); the overlay carries in-graph tools not registered with providers (find_tools, skills `load_skill`, `run_command`, `write_file`, todos) via `lc_bind_tools_meta` (`prepare_node.py:238-239, 583-586`), deliberately isolated from the executor's `_tools_context` (`cuga_lite_graph.py:184-187`); the over-cap shortlister costs one LLM round-trip and hard-raises on hallucinated names or a missing query (`cap.py:125-215, 303-312`) — provider-safe by design for benchmarks.

### 3.4 SDK surface today: nothing typed, one backdoor

- `CugaAgent.__init__` params (`sdk.py:1603-1618`): `tools`, `tool_provider`, `model`, `callbacks`, policy/knowledge/skills knobs. **Nothing about invocation mode.**
- `invoke()`/`stream()` set only: `track_tool_calls`, `skills_enabled/folder`, `thread_id`, `policy_system`, `knowledge_engine`, `callbacks` (`sdk.py:2257-2299`).
- The caller-supplied `config={"configurable": {...}}` survives un-stripped (`sdk.py:1785-1787`), so this works **today** — undocumented and untyped:
  ```python
  await agent.invoke(task, config={"configurable": {
      "cuga_lite_bind_tools_mode": "tools",
      "cuga_lite_bind_tools_tool_names": ["send_email"],
  }})
  ```
- Schema survival is a **non-problem**: `DirectLangChainToolsProvider.get_tools` returns the caller's `StructuredTool`s unchanged (`providers/langchain.py:121`) and those exact objects are what `bind_tools` receives — `args_schema`, descriptions, coroutines intact. Binding SDK tools natively already works mechanically.
- No server `/api/config/*` endpoint, no UI toggle, no `CugaLiteState`/`AgentState` field for invocation mode. The one precedent for promoting a knob to a per-agent SDK arg is `CugaSupervisor(cuga_lite_max_steps=…)` — done via state (`cuga_supervisor_state.py:59`, `sdk.py:2821→3137`).

### 3.5 Reproducible failure demonstration (run 2026-07-11, unmodified code)

Task: *"notify customers acme and globex"*, one `@tool notify(customer)` appending to a ground-truth list. A scripted FC-style model (deterministic `BaseChatModel`) plays the two natural encodings an FC-trained model emits; binding enabled via the `configurable` backdoor (`cuga_lite_bind_tools_mode="all"`). Local sandbox. Actual output:

```
=== SCENARIO A: parallel tool_calls (empty content) ===
agent's final answer : 'Done — both acme and globex have been notified.'
tools ACTUALLY run   : ['acme']
VERDICT: FAIL — globex silently dropped, answer claims FALSE SUCCESS        (D1)

=== SCENARIO B: text preamble + tool_calls ===
agent's final answer : "I'll notify both customers now."
tools ACTUALLY run   : []
VERDICT: FAIL — ZERO tools ran; the announcement became the final answer   (D2)

=== SCENARIO C: baseline code-act (no binding) ===
tools ACTUALLY run   : ['acme', 'globex']
VERDICT: PASS — code-act handles it
```

Scenario A is the worst failure class in agent systems: **silent partial execution reported as full success** — no error, no trace, a convincing wrong answer. Scenario B is arguably worse UX: the agent *announces* the action, does nothing, and terminates (the "Execution output" feedback loop never fires because no code was produced, and with `cuga_lite_nl_auto_continue=false` the NL response ends the run). Scenario C proves the failures are specific to the FC encoding, not the task. A fourth failure (D9) reproduced while building this: a model whose `bind_tools` raises `NotImplementedError` (the `langchain_core` default) crashes `call_model` outright because the cap's `except RuntimeError: raise` swallows the fallback — `NotImplementedError ⊂ RuntimeError`.

Why this matters today, not hypothetically: the **gpt-oss-20b runtime profile auto-enables binding** (`model_runtime_profile.py:9-14`) — every deployment on that model runs these code paths with zero configuration, and the pad-to-cap measurements (`cap.py:218-243`: "0 tool calls vs 5-7 without padding") prove real models do emit native tool_calls under binding despite the prompt ban.

The scenarios are landed as **strict-xfail tests** — executable documentation that flips to hard failures the moment the defects are fixed: `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/test_native_tool_calls_execution.py` (D1, D2, D9 xfail + a passing code-act control). Verified: `1 passed, 3 xfailed`. Tracked upstream as **issue #471**.

### 3.6 Test coverage map

- **Locked in:** selection/cap logic — 23 tests in `tests/unit/test_cuga_lite_bind_tools.py` assert *what gets bound* (modes, dedup, overlay, cap boundary, shortlist, pad, find_tools slot). `tool_use_failed` recovery helpers fully covered (`tests/unit/test_tool_use_failed_recovery.py`).
- **Uncovered:** the `tool_calls → code` transpiler; the empty-content recovery branch; multi-call behavior; text+tool_calls behavior; any end-to-end "bound model emits tool_calls → sandbox executes" flow. **The half of the feature that runs at inference time is the untested half.**

### 3.7 History and verdict

~20 commits touch `*bind_tools*`: born 2026-05-02, core cap work in #202/#203 (2026-05-26), last touched 2026-06-03 (a tracing fix). The code comments frame it as a measurement harness ("silent truncation would corrupt benchmark results", `cap.py:9-13`). **Verdict: a well-hardened experiment that was never finished into a product feature.** The binding half is solid; the execution half is a recovery hack; the prompt half is hostile; the SDK half is absent.

---

## 4. Answering the questions directly

**Does function calling exist in cuga-lite?** Partially. *Binding* exists and is production-hardened; *execution* of native tool calls does not exist as a first-class path — tool calls are down-converted to code, single-call-only, and only as an error-recovery behavior.

**Can we define it via SDK?** Not as a supported feature. It is reachable only through the untyped `config.configurable` passthrough (works, undocumented, unvalidated, and silently interacts with model profiles). There is no typed parameter, no validation, no docs, no per-tool granularity, and no `tool_choice`.

**Should we add it?** Yes — the runtime plumbing is ~90% built, the precedence resolver already exists, and the strategic value (below) is high. What's missing is a coherent *contract*.

---

## 5. Design proposal: Tool Invocation Policy (`ToolCalling`)

### 5.1 Design principles

1. **CUGA stays a code-act agent at heart.** Native FC is an *invocation encoding*, not a new agent architecture. Whatever the model emits — Python or tool_calls — execution flows through the **same** guarded pipeline: ToolGuard-wrapped providers, `ToolCallTracker`, variables manager, policies, HITL. One execution spine, two front-end encodings. This is the creative core of the design: we don't build a second agent; we make the existing sandbox the universal tool bus.
2. **Configurable at every altitude, one resolver.** Five layers, single precedence chain (extending the proven `resolve_bind_tools_fields`): `settings.toml` (fleet default) → **model runtime profile** (per-model) → **`CugaAgent(tool_calling=…)`** (per-agent) → **`invoke(tool_calling=…)`** (per-run) → **per-tool annotation** (finest grain). **Per-tool semantics, precisely:** the annotation is a *constraint within* the resolved mode — it marks which tools are offered natively when the mode allows native calls — it is **not** an override of an explicit run policy. `invoke(tool_calling=ToolCalling(mode="code"))` beats every `metadata["cuga.invocation"]="native"` annotation; conversely an annotation can *narrow* (`"code"`-only tool inside hybrid mode) but never *widen* what the run policy permits.
3. **Zero regression by default.** `mode="code"` (today's `"none"`) remains the default; every new behavior is opt-in; every failure degrades to code-act (the `tool_use_failed`/fallback ladder already models this).
4. **Fix the broken semantics regardless of adoption** (D1–D4 are latent correctness bugs for gpt-oss-20b users *today*).

### 5.2 The SDK API

```python
from cuga import CugaAgent, ToolCalling

agent = CugaAgent(
    tools=[search_deals, send_email, create_invoice],
    tool_calling=ToolCalling(
        mode="hybrid",                      # "code" (default) | "native" | "hybrid" | "auto"
        native_tools=["send_email"],        # or apps=["crm"]; both map onto existing selection modes
        include_find_tools=True,
        max_bound_tools=64,                 # promotes cap to per-agent (fixes D7)
        parallel=False,                     # multi-call turns run sequentially by default; opt in for
                                            # read-only fan-out — parallel changes ordering/rate-limit
                                            # behavior for side-effectful tools
        tool_choice="auto",                 # "auto" | "none" | "required" | {"name": "send_email"}  (fixes D5)
        on_unsupported="fallback_code",     # or "error" — provider degradation policy
    ),
)

# per-run override (audit-sensitive run: force one specific structured call)
result = await agent.invoke(task, tool_calling=ToolCalling(mode="native", tool_choice={"name": "create_invoice"}))

# per-tool annotation — the finest grain
@tool(metadata={"cuga.invocation": "native"})   # this tool is always offered natively
def send_email(to: str, subject: str, body: str) -> str: ...
```

`ToolCalling` is a pydantic model in a leaf module (pattern: `RunReceipt`). The SDK serializes it into the **existing** `configurable` keys (exactly how `track_tool_calls` is forwarded, `sdk.py:2257`) — `mode/apps/tool_names/include_find_tools` map 1:1 onto today's keys; `max_bound_tools`, `parallel`, `tool_choice`, `on_unsupported` become four new `configurable`-first keys with settings fallbacks. **No graph rebuild, no state schema change, no new nodes for Phase 1–2.**

`mode="auto"` = profile-driven (today's implicit behavior, made explicit and visible: `agent.tool_calling_resolved` property returns the effective policy so gpt-oss-20b users can finally *see* what the profile did).

### 5.3 Execution semantics (the real feature)

**`hybrid` (recommended headline mode).** Tools are bound; the prompt (§5.4) tells the model *both* encodings are valid — write Python for orchestration (loops, joins, transforms, multi-step dataflow) and emit tool_calls for direct calls. Execution contract replacing today's recovery hack:

1. `normalize_response` handles **all** tool_calls, not `[0]` (fixes D1), and handles **text + tool_calls** by preserving the text as reasoning and still executing the calls (fixes D2).
2. Multi-call turns transpile to a **deterministic batch block** executed in the sandbox — sequential by default; `asyncio.gather` when `parallel=True`:
   ```python
   _batch = await asyncio.gather(send_email(...), update_crm(...), return_exceptions=True)
   for _i, _r in enumerate(_batch):
       print(f"[call {_i}] {'ERROR: ' + repr(_r) if isinstance(_r, Exception) else _r}")
   ```
   **Batch failure semantics (explicit, because the central failure case here is *silent* partial execution):** `return_exceptions=True` — one failed call never cancels its siblings; every call produces a line in the observation, success or error, so partial execution is impossible to miss and the model sees exactly which calls failed and can retry only those. Sequential mode stops at the first failure by default (side-effect safety: don't fire call N+1 after N failed) and reports the completed/failed/not-attempted split. This is why "sandbox as universal tool bus" wins: batching over the already-wrapped callables gives us multi-call native FC with ToolGuard, timeout (`tool_call_timeout`), tracking, and variables (`result_N` lands in `variables_storage` like any code-act result — the model can reference it next turn, which pure ToolMessage designs lose).
   **Identifier safety:** the current transpiler emits `await {name}(...)` as raw source — unsafe for tool names that aren't valid Python identifiers (hyphens, dots, MCP-namespaced names). Generated batches must call through a lookup, e.g. `await _tools["mcp-server.send-email"](**args)` against the sandbox's tool map, never by splicing names into source.
3. **Args validation before execution** (fixes D6): validate `tool_call.args` against the bound `StructuredTool.args_schema`; on failure, feed a structured validation error back as the observation (same shape as sandbox errors) so the model self-corrects — no crash, no silent coercion. **Required data-flow change:** `_tools_context` is name→callable only (`prepare_node.py:337-351`) — P0/P2 must preserve a `name → StructuredTool` (or `name → args_schema`) map alongside it; `lc_bind_tools_meta` already carries the full `StructuredTool`s for binding, so this is a lookup-path addition, not new plumbing.
4. `tool_choice` is passed through `bind_tools(..., tool_choice=…)` where the provider supports it; `on_unsupported` decides between transparent fallback to code-mode (default) and hard error (for compliance-critical flows). The existing `tool_use_failed` → code recovery becomes one rung of this documented ladder instead of an easter egg.

**`native`.** Same pipeline, but the prompt inverts: tool_calls are the *primary* encoding; the Python block remains available as an escape hatch for computation. This mode exists for (a) models whose RL training makes them markedly better at native FC than code emission, (b) strict environments that want every tool invocation as a structured, pre-validated record, and (c) benchmark A/B research — the original motivation of this machinery, now with sound execution.

**`code`.** Today's behavior, unchanged, default.

**Phase-3 option (explicitly deferred): a true direct-dispatch executor** — a `tool_executor` branch in `call_model` that invokes `StructuredTool.ainvoke` directly and appends `ToolMessage`s, skipping the sandbox for trivial single-call turns (saves sandbox startup latency, enables per-call streaming). Deferred because it forks the execution spine: variables, ToolGuard wrapping, tracking, output-size limits, and HITL all hook the sandbox path today. Revisit only with latency data from Phase 2's receipt integration.

**Is the sandbox *needed* for FC execution?** Split the question:

- *For safety of the call itself — no.* A materialized tool_call is data, not code: name + schema-validated args against an already-trusted callable. Running `SecurityValidator` over the transpiled one-liner is theater, and on the `opensandbox`/`docker`/`e2b` backends we pay a full container round-trip to execute what is semantically a single in-process function call.
- *For CUGA's cross-cutting guarantees — today, yes.* The sandbox path is currently the only home of: per-call timeout (`tool_call_timeout` enforced in `executors/common`), `ToolCallTracker` start/stop (`sandbox_node.py:79,199`), result binding into `variables_storage` (what lets the model reference `result_N` in later turns — pure-`ToolMessage` designs lose this), output-length truncation (`execution_output_max_length`), and the error-observation feedback loop. ToolGuard is the exception — it wraps at the *provider* level (`ensure_toolguard_provider`), so guarding survives either route.
- *Pragmatics:* keep the sandbox as the universal tool bus near-term. For trivial calls the overhead is controllable — the codebase already forces the **local** executor for short internal calls (`find_tools`/`load_skill`, `code_executor.py:183-186`); routing transpiled single-tool-call blocks the same way makes the "sandbox tax" a function call plus validation, not a container round-trip, with zero architectural change. Direct dispatch remains the Phase-3 opt-in, gated on receipt telemetry, and must explicitly re-home timeout/tracking/variables before it ships.

### 5.4 Prompt strategy (fixes D4) — a dedicated native prompt, not a conditional

A single flipped rule is **not enough**: the whole `mcp_prompt.jinja2` is a code-act prompt (it repeatedly mandates "output must be a Python code block", frames tools as "async functions in your execution environment", and even lists native tool-call JSON as an ❌ INCORRECT example). A code-preferring model obeys the overwhelming signal and keeps writing code. Confirmed empirically: with the code-act prompt + a flipped rule 7, watsonx gpt-oss-120b emitted **0** native tool calls across task phrasings.

The shipped design is a **dedicated `mcp_prompt_native.jinja2`** — structurally parallel to the code-act prompt (same role, connected apps, tools list, knowledge/skills/todos sections, and the durable data-source + pagination + name-accuracy rules) but with **all** sandbox/code/Python/print/await language removed and every tool reference reframed as native function calling. `prepare_node` selects it when `tool_invocation_mode ∈ {native, hybrid}`; otherwise the code-act prompt renders byte-identical to `main`. Result (verified live): the same watsonx model now returns native tool calls (`native_fired = 2–3` per run) which CUGA executes correctly.

### 5.5 Ecosystem integration (what this serves)

| Consumer | Value |
|---|---|
| **HITL / Tool Approval policies** | The sleeper feature: a native tool_call carries **exact, schema-validated arguments before anything runs**. Approval UIs can render "send_email(to='cfo@…', subject='…')" pre-execution — today approval wraps opaque generated code. `tool_choice={"name": …}` + approval = auditable, deterministic, gated actions. |
| **Model heterogeneity** | Per-model profiles already exist; this makes them controllable. Route gpt-oss-class models to `hybrid`, keep watsonx granite on `code`, all from one config surface — per agent, not per deployment. |
| **watsonx Orchestrate / MCP interop** | External orchestrators speak FC natively; a lite agent that genuinely executes tool_calls composes cleanly instead of via the transpile hack. |
| **Run Receipt (PR #467)** | Add `invocation breakdown: 3 native / 2 code` to the receipt — free A/B telemetry for the benchmark question this machinery was born to answer. |
| **Evolve / distillation** | Structured calls are higher-quality trajectory data than parsed code strings. |
| **Latency (Phase 3)** | Direct dispatch skips sandbox startup for trivial calls. |

### 5.6 Provider reality & degradation ladder

Treat provider support as **capability-matrix work, not a static claim**: `bind_tools` exists across langchain-openai (incl. `tool_choice`, parallel), ChatWatsonx (tool_choice limited), ChatGroq (known `tool_use_failed` failure mode — already handled), and litellm (varies by upstream) — but `tool_choice` values, `parallel_tool_calls` flags, and bind signatures differ per provider and per version. P2 therefore includes **capability detection**: probe/feature-flag per model class, wrap `bind_tools(...)`/`tool_choice` passthrough in `TypeError`/`NotImplementedError` fallbacks (D9's lesson generalized), and degrade per the ladder rather than crash. Ladder, applied per-call and logged once per run: **native attempt → provider rejects (`tool_use_failed`) → existing code recovery → still failing → unbound code-act**. `on_unsupported="error"` short-circuits the ladder for compliance flows.

### 5.7 File-touch plan and estimates

| Phase | Change | Files | Est. |
|---|---|---|---|
| **P0 — correctness (independent PR)** | Transpile all tool_calls (+gather), handle text+tool_calls, tests for `response_utils`/`normalize_response` | `adapter/response_utils.py`, `graph_adapter.py`, new tests | ~150 src + 200 test |
| **P1 — typed SDK surface** | `ToolCalling` model; forward to `configurable`; `configurable` overrides for cap knobs (D7); remove dead params (D8); expose `tool_calling_resolved` | new `cuga_graph/utils/tool_calling.py`, `sdk.py` (~40), `cap.py` (2 reads), `cuga_lite_graph.py` | ~250 src + 250 test |
| **P2 — modes & prompt** | `tool_invocation_mode` prompt conditional; `tool_choice` passthrough; args-schema validation; fallback ladder; receipt `invocation` breakdown; per-tool `metadata["cuga.invocation"]` | `mcp_prompt.jinja2`, `helpers/bind_tools.py`, `shared_nodes.py`, `run_receipt.py` | ~350 src + 300 test |
| **P3 — deferred** | Direct-dispatch executor, streaming tool events, server `/api/config/tool-calling` + UI toggle | — | design-gate on P2 telemetry |

### 5.8 Testing plan

- Unit: transpiler (single/multi/parallel/string-args/malformed), text+tool_calls, args-schema rejection feedback, `ToolCalling`→configurable serialization, precedence ladder (5 layers), cap override, tool_choice passthrough per provider class (mocked).
- Integration: fake `BaseChatModel` emitting tool_calls → assert sandbox execution, tracker records, variables landing, ToolGuard interception (pattern exists: `test_run_receipt_integration.py`).
- E2E (real watsonx, existing harness): hybrid mode on the accounts demo; assert receipt shows native+code split; flag-off run byte-identical to main.
- Regression: full `test_cuga_lite_bind_tools.py` suite must pass untouched (selection logic is not modified).

### 5.9 Risks

| Risk | Mitigation |
|---|---|
| Prompt change alters code-mode behavior | `code` mode renders the identical prompt (golden-file test) |
| Provider FC quality variance (watsonx) | default stays `code`; ladder degrades transparently; `on_unsupported` for strict flows |
| gpt-oss-20b profile users see behavior change from P0 fixes | Correctness fix, but **release-note prominently**: previously *ignored* tool_calls (D1's dropped siblings, D2's preamble turns) will now actually execute — side effects that silently didn't happen before will start happening |
| Hybrid prompt confuses weaker models | decision heuristic in prompt + per-model profiles can pin `code` |
| Scope creep toward ToolNode rewrite | Phase 3 explicitly gated on telemetry; the sandbox-as-tool-bus contract is the accepted architecture |

### 5.10 Alternatives considered and rejected

1. **Full LangGraph `ToolNode` rewrite** — forks the execution spine; loses variables/ToolGuard/tracking/HITL integration that all hook the sandbox; large blast radius for unproven latency gain. Kept as gated P3.
2. **Settings-only expansion (no SDK types)** — perpetuates the global-mutation anti-pattern; multi-agent processes (Supervisor) can't diverge per agent.
3. **Per-provider adapters emitting provider-specific FC** — LangChain already normalizes this at `bind_tools`; duplicating it below is waste.

---

## 5.11 Open design questions to settle before P0/P2

1. **Invalid tool-name identifiers** — the transpiler must stop splicing tool names into source (`await {name}(...)`). Decide the lookup mechanism: a `_tools[...]` map call (proposed) vs. a sanitized-alias table injected into the sandbox namespace. Affects P0.
2. **Batch failure semantics** — confirm the contract proposed in §5.3: `gather(return_exceptions=True)` with per-call outcome lines in the observation; sequential mode stops at first failure and reports completed/failed/not-attempted. Is stop-at-first-failure right for all side-effectful sequences, or should it be a `ToolCalling` knob? Affects P0.
3. **Parallel default** — proposal: `parallel=False`; parallel preserves model-emitted batch semantics but is opt-in because it changes ordering, can trip rate limits, and creates partial-success ambiguity for side-effectful tools. Confirm before P1 freezes the API.
4. **Schema-map preservation** — P0/P2 must carry a `name → StructuredTool`/`args_schema` map alongside the name→callable `_tools_context` (reuse `lc_bind_tools_meta`). Decide the single source of truth so validation, binding, and execution never disagree about a tool's schema.

---

## 6. Recommended next steps

1. **File the P0 bug PR now** (transpiler correctness + tests) — it fixes latent defects that bite every gpt-oss-20b deployment today and stands alone.
2. Land **P1** (typed `ToolCalling` + forwarding) — small, zero-risk, instantly makes the existing machinery a *feature* instead of folklore.
3. Land **P2** (hybrid mode + prompt + receipt breakdown) and publish an internal A/B (code vs hybrid vs native on the digital_sales eval) — this finally answers the benchmark question the machinery was built for, with production-grade semantics.
4. Decide P3 from the receipt telemetry.

---

## 7. Implementation plan for this PR (P0 + P1, fully gated)

**Guiding constraint:** the default is byte-for-byte unchanged. Native FC is inert unless a caller opts in via the SDK. The single explicit signal is a new `cuga_lite_tool_invocation_mode` configurable key (`code` default | `native` | `hybrid`); when absent/`code`, every new branch is bypassed and the prompt renders identically. Everything is `try/except`-guarded and degrades to code-act, never crashes.

**Gating model (as shipped).** The corrected multi-call transpiler and preamble handling fire **only when FC is opted in** (`tool_invocation_mode ∈ {native, hybrid}`). The mode is derived **per call** from `configurable` (`configurable > global setting > "code"`) via `native_tool_calls_enabled()` in `nodes/cuga_lite/tool_calling.py` — **not** stored on the shared per-graph adapter, so concurrent invokes with different modes never race (the earlier `adapter._allow_native_tool_calls` field was removed in commit 98e88443). `normalize_response(response, configurable)` and `prepare_node`'s prompt selection both call the resolver. The lone ungated exception is the D9 fallback, because turning a crash into graceful degradation can never change a successful path.

> **NOTE — this subsection is the *original plan*; the shipped implementation diverged (see §7.1 for the authoritative state).** Key differences: the rule-7 prompt conditional was replaced by a **dedicated `mcp_prompt_native.jinja2` template**; native mode is derived **per call** from `configurable` (`native_tool_calls_enabled()`), **not** stored as `adapter._allow_native_tool_calls`; **`tool_choice` shipped** (it is no longer deferred); and the work landed across **13 commits**, not 6.

**Commits (original plan — 6):**

1. **`fix(cuga_lite): fall back to unbound model when bind_tools is unsupported` (D9).** Add a `_safe_bind(model, tools)` helper in `helpers/bind_tools.py` wrapping every `bind_tools` call; `NotImplementedError` → return the unbound model (before the cap's `except RuntimeError: raise`). Flip the D9 xfail to passing. Ungated, universally safe.
2. **`fix(cuga_lite): execute all native tool_calls, not just the first` (D1).** Rewrite `extract_code_from_response_tool_calls(response, *, multi=False)`: `multi=False` reproduces today's single-call output byte-for-byte; `multi=True` emits one `result_i = await name(...)`/`print` per call (sequential — partial execution stays visible via ordered prints). Identifier safety: emit bare-identifier calls only; a non-`isidentifier()` name is skipped with a visible `[skipped non-identifier tool: …]` marker (never spliced — `getattr`/`globals`/`eval` are blocked by `SecurityValidator` and prompt rule 9). Introduce the `cuga_lite_tool_invocation_mode` key + `adapter._allow_native_tool_calls`; `normalize_response` passes `multi=self._allow_native_tool_calls`. Flip D1 xfail (test opts into FC via configurable).
3. **`fix(cuga_lite): execute tool_calls emitted alongside a text preamble` (D2).** In `normalize_response`, when FC is opted in and the content holds no code fence, transpile the tool_calls and demote the preamble text to `reasoning` (so it is preserved, not lost). Legacy path (FC off) is the exact current code. Flip D2 xfail.
4. **`feat(cuga_lite): permit native tool calls in the system prompt under FC modes`.** Add `allow_native_tool_calls` to `create_mcp_prompt` → one Jinja conditional around rule 7 in `mcp_prompt.jinja2` (default renders today's text verbatim — golden test). `prepare_node` derives it from `tool_invocation_mode`.
5. **`feat(sdk): typed ToolCalling config to enable native function calling`.** New `ToolCalling` pydantic model (leaf module) + `tool_calling_to_configurable()`; `CugaAgent(tool_calling=…)` and `invoke(tool_calling=…)` merge it into `run_config["configurable"]` (default `None` → `{}` → nothing set). Maps `mode/native_tools/apps/include_find_tools/max_bound_tools` onto the existing bind_tools keys + the new invocation-mode key; promotes the cap to a per-run override (D7). All serialization guarded. Scripted-model integration test through `CugaAgent(tool_calling=ToolCalling(mode="native"))`.
6. **`docs + e2e`.** README "Native Function Calling" section (enable via SDK, default-off note, provider caveats); real-API before/after example under `docs/examples/` (skips without keys); report marked P0/P1-done.

**Deferred (documented, not in this PR):** parallel fan-out (`asyncio.gather` — needs a sandbox-injected helper since `import asyncio` is restricted), `on_unsupported="error"`, args-schema pre-validation, and the receipt `invocation` breakdown. Each is additive and non-breaking. (**`tool_choice` passthrough was NOT deferred — it shipped**, with capability-detection fallback.)

**Test/verify:** the four scripted xfail→pass tests; transpiler unit tests (single/multi/hyphen-name/string-args/malformed/empty); `normalize_response` tests (preamble, code-block-wins, FC-off legacy); D9 fallback test; `ToolCalling`→configurable serialization + default-`None` no-op; golden prompt test (FC-off == current); scripted integration through the SDK; real-API before/after (gated on `.env` keys). Full `run_tests.sh --skip-stability` gate + the FC-off "default unchanged" assertions.

### 7.1 Status: shipped on branch `function_calling`

Commits: D9 safe-bind fallback · D1 multi-call transpiler + FC gate · D2 preamble handling · prompt rule-7 conditional + config key · `ToolCalling` SDK surface + `max_count` override · docs/example. The four scripted defect tests (real graph + SDK + sandbox, deterministic model) now pass; on `main` they are `xfail`. All new behavior is gated behind `cuga_lite_tool_invocation_mode` / `ToolCalling`; the golden prompt test proves the default prompt is byte-identical.

**Real-API result (watsonx `gpt-oss-120b`, the `.env` model):** with the **dedicated native prompt** (§5.4), the live model now returns native tool calls (`native_fired = 2–3` per run) which CUGA executes correctly — end-to-end native function calling with the real provider. (With the code-act prompt it emitted 0, writing Python instead — which is what proved the prompt, not the execution wiring, was the gating factor.) The deterministic scripted tests remain the rigorous proof of the exact D1/D2/D9 before→after; the live run proves real models engage the native path once the prompt fits.

Commit added since: **dedicated `mcp_prompt_native.jinja2`** + template selection (code-act prompt reverted to byte-identical `main`), and **`tool_choice`** passthrough with capability-detection fallback (D5).

---

## Appendix A — full `configurable` key inventory for the lite path

Keys read via `configurable.get(...)` in `nodes/cuga_lite/**` + `cuga_agent_core/**` (✓ = SDK populates today): `track_tool_calls` ✓, `thread_id` ✓, `callbacks` ✓, `policy_system` ✓, `knowledge_engine` ✓, `skills_enabled`/`skills_folder` ✓(conditional), `llm` ✗, `cuga_lite_max_steps` ✗, `reflection_enabled` ✗, `apps_list` ✗, `enable_todos` ✗, `shortlisting_tool_threshold` ✗, `mcp_few_shot_examples` ✗, `cuga_lite_enable_few_shots` ✗, `special_instructions` ✗, `upload_context` ✗, `agent_id`/`knowledge_config_hash` ✗(server), `enable_filesystem_tools` ✗, `cuga_lite_bind_tools_mode/apps/tool_names/include_find_tools` ✗. The breadth of ✗ entries is the broader configurability story this proposal's `ToolCalling` pattern (typed config → configurable) should eventually template for.

## Appendix B — the transpiler, verbatim (current contract)

`adapter/response_utils.py:37-63` — the entirety of native FC execution today:

```python
tool_call = tool_calls[0]                       # ← parallel calls dropped here
...
return f"```python\nresult = await {name}({args_str})\nprint(result)\n```"
```

invoked only from `graph_adapter.py:151-155`, only when `response.content` is empty.
