## Problem

When `advanced_features.langfuse_tracing=true`, a single `CugaAgent.invoke()` run can produce **multiple root traces** in Langfuse instead of one tree. Generations from nested LLM calls appear as siblings or separate roots, not children of the main run.

This affects **standalone cuga-agent** (server, SDK, policy output formatting) and is amplified by eval harnesses that also open an outer span (see [cuga-eval#28](https://github.com/cuga-project/cuga-eval/issues/28)).

### Root cause

LangGraph passes Langfuse `CallbackHandler` instances via `config["callbacks"]` / `config["configurable"]["callbacks"]` for `call_model`, but several **nested** code paths call `ainvoke` without that config:

| Nested path | Module |
|-------------|--------|
| Reflection after sandbox | `sandbox_node.py` |
| find_tools shortlister | `prompt_utils.py` |
| NL auto-continue classifier | `nl_auto_continue_classifier.py` (used `config={"callbacks": []}` on main) |
| OutputFormatter policy | `enactment.py` |
| Context summarization | `context_management_utils.py` |

Each orphan `ainvoke` can create a new Langfuse root trace when the handler has no `trace_context.trace_id`.

Additionally, when callers pass a **trace-scoped** handler per `invoke` *and* attach a generic handler on `CugaAgent(..., callbacks=[...])`, both handlers may compete unless deduplicated.

## Proposed fix (branch pushed)

Branch: `fix/langfuse-nested-callback-propagation`  
Commit: `5591085a` — `fix(tracing): propagate Langfuse callbacks to nested LLM calls`

- New `langfuse_tracing.py`: contextvar + `sync_langfuse_callbacks_from_config()` + `get_langfuse_invoke_config()`
- Prefer explicit LangGraph `RunnableConfig` for reflection, bind-time shortlister, and runtime `find_tools` (see [#288](https://github.com/cuga-project/cuga-agent/issues/288)); contextvar remains fallback for paths without `config`
- Wire nested call sites listed above
- `sdk._apply_callbacks`: drop agent-level Langfuse handler when `langfuse_trace_id` or per-invoke trace handler is present
- Unit tests: `tests/unit/test_langfuse_tracing.py`

## Test evidence (no Langfuse API keys required)

### Before (`main`)

- No `langfuse_tracing` module; nested paths do not propagate callbacks.
- Example on `main` — NL auto-continue **explicitly clears** callbacks:

```python
resp = await llm.ainvoke(..., config={"callbacks": []})
```

- Regression file `tests/unit/test_langfuse_tracing.py` does not exist on `main`.

### After (`fix/langfuse-nested-callback-propagation`)

```bash
cd cuga-agent
.venv/bin/python -m pytest tests/unit/test_langfuse_tracing.py -v
```

```
============================== 10 passed in ~6s ===============================
```

Key assertions:

- `test_nl_auto_continue_passes_invoke_config` — nested `ainvoke` receives parent callbacks
- `test_output_formatter_ainvoke_receives_callbacks` — policy formatter LLM uses same callbacks
- `test_context_summarization_does_not_wrap_model_with_config` — summarization relies on the contextvar set in `call_model` instead of `with_config(callbacks=...)` (avoids breaking `_setup_model_profile`)
- `test_apply_callbacks_drops_agent_langfuse_when_trace_id_set` — SDK dedupes duplicate Langfuse handlers

## Manual verification (Langfuse UI — optional but convincing)

**Setup:** `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, and `DYNACONF_ADVANCED_FEATURES__LANGFUSE_TRACING=true`.

**Repro (cuga-agent only, no cuga-eval):**

1. Enable reflection + low shortlist threshold in settings (or use M3-like dynaconf overrides).
2. Run one short `CugaAgent.invoke()` task that triggers reflection or find_tools.
3. In Langfuse → Traces, filter by time window.

**Before:** several root traces for one logical task (e.g. one per `call_model` step + reflection + shortlister).

**After:** one trace with nested GENERATION observations under a single root.

**Please attach to this issue (screenshots):**

1. **Before** — Langfuse trace list showing multiple roots for one invoke (timestamp + trace names).
2. **After** — single trace expanded in tree view showing generations nested under one root.
3. (Optional) One trace JSON export snippet: `observations[].parentObservationId` populated for nested generations.

## Acceptance criteria

- [ ] One Langfuse trace per `agent.invoke()` when caller supplies trace-scoped callbacks (or server uses single handler consistently)
- [ ] Nested paths (reflection, find_tools, NL auto-continue, output formatter, context summarization) use propagated callbacks
- [ ] `pytest tests/unit/test_langfuse_tracing.py` passes in CI
- [ ] No regression for pip-installed SDK consumers that only set `callbacks` on `CugaAgent` construction

## Related

- cuga-eval [#28](https://github.com/cuga-project/cuga-eval/issues/28) — M3 eval span tree / orphaned chat nodes (harness + agent)
- cuga-eval [#27](https://github.com/cuga-project/cuga-eval/issues/27) — React / too many traces (may share root cause)
