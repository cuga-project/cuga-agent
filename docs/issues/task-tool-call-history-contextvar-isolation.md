# TaskToolCallHistory: contextvar state does not survive across LangGraph node dispatches

Status: **prototype broken, root cause understood, real fix not yet built.** Tracking doc — will be updated as work continues; may end up filed as an issue with this as the body plus a link to a prototype-fix branch.

## 1. Where this came from

While investigating M3 capability_4's "runaway" / step-limit-hit failures (umbrella issue [cuga-agent#385](https://github.com/cuga-project/cuga-agent/issues/385)), we found that [PR #493](https://github.com/cuga-project/cuga-agent/pull/493) ("Sandbox timeout threw away everything a code block had done...") — already merged into `integration/m3-eval` — only fixes **one** runaway shape: a code block that runs long enough to hit the 90s sandbox timeout, gets killed, and the agent blindly re-runs the identical doomed code. Checking 8 of cap4-300's 36 step-limit-hit tasks showed #493's fix marker never fires for most of them: the repetition is spread across many separate, individually-fast blocks/steps (e.g. one task made 807 calls to one tool, only 200 distinct argument sets, across the whole task) — not concentrated in one block that runs long enough to time out.

The PR's own reviewer has asked for `max_tool_calls_per_block` / `BlockToolCallBudget` (the per-block abort-on-budget-exceeded mechanism in the same PR) to be **removed** — it's considered not robust enough for the gain it provides. (That removal itself is a separate, still-open to-do — not done as part of this work.)

### Constraints the user set for any replacement mechanism

These came from direct instructions during design and shape everything below:

- **General, not cap4/VAKRA-specific.** *"i don't want anything we do to be dependent on whether the cap4 tools specifically have those properties. i mean in general."* We had found `riak_driver_riakclient_increment` (a genuinely non-idempotent tool) in a different M3 capability's catalog while investigating whether caching tool results by (tool, args) would ever be unsafe — confirming side-effecting tools do exist in general, not just hypothetically.
- **Never cache tool results.** *"no i don't want to cache the values at all. could we just cache the tool+param values, and add some special instructions on repeat?"* — i.e. track *that* a call was repeated, never substitute a cached return value for a real call. Every call must always execute for real, regardless of side effects, because there is no general way to know a tool is safe to skip.
- **Purely observational.** Must never block, abort, delay, or alter execution — only annotate. This was explicitly contrasted against `BlockToolCallBudget`, which *does* abort blocks and is the thing being asked to be removed for being too aggressive relative to its benefit. The design goal was something structurally safer (never blocks/aborts) — though see §6 for the residual risk (an ignorable prompt) that a safer design doesn't remove.
- **No regex matching** (a standing preference from earlier in this investigation, re-applied here): matching is by exact tool name + exact JSON-serialized argument equality, not fuzzy/regex matching.
- Four distinct repetition *patterns* were identified and each needed its own probe before any fix could be judged (`cap4_runaway_probe1..4` / `_n3` / `_n4` in `benchmarks/m3/eval_config.toml`, cuga-eval repo):
  1. **Exact-duplicate per-item loop** (`e71440999ce8-99b792c44651`, law_episode) — 807 total calls to `get_roles_by_person_name`, only ~200 distinct argument sets; the same ~200-name cast list looped ~4x with real exact-duplicate calls.
  2. **Retriever-rephrasing loop** (`d14bbb0be92d-ae240cc7a80e`, professional_basketball) — 16 calls, all to one query/retriever tool, varying free-text query each time. No exact duplicates — a dedup/cache approach cannot catch this shape at all; it needs the "N distinct variations" signal, not the "exact repeat" signal.
  3. **Parameter-guessing loop** (`55b7e50368aa-01b9e0ab72a2`, mondial_geo) — 5 calls, one analytical tool, different parameter guesses each time. Flagged explicitly as the one probe where "fixing repetition" might be unsafe: this shape is often *legitimate* systematic search, the same shape that succeeds elsewhere, and the same failure mode PR #493's own A/B data showed for its now-being-removed budget mechanism (over-eager intervention penalizing would-be-converging search).
  4. **Mixed-exploration** (`1960f609e439-3a1b0a9c3535`, codebase_comments) — 17 calls: 13× `find_tools` + 4× one other tool. Named as the mildest of the four and the one least likely to be helped by a repeat-call note at all, since there's no repeated *tool*, just repeated *search*.

## 2. What we built

`TaskToolCallHistory` (`src/cuga/backend/cuga_graph/nodes/cuga_lite/tracking/tracker.py`), explicitly labeled a **prototype** in its own docstring, built to be tested against the 4 probes above before any decision to invest further.

- A contextvar-held mutable dict (`_task_tool_calls_context`), matching the existing pattern used by the sibling mechanism `BlockToolCallBudget` in the same file (same "mutable dict survives context copies" comment/rationale, copied and adapted).
- `reset()` — starts a fresh per-task holder: `{"exact": {}, "by_tool_args": {}, "notes": []}`.
- `record_call(tool_name, args)` — called at every tool-invocation site (`registry.py::call_api`, `combined.py`'s tracker-tool wrapper). Tracks:
  - `exact[tool_name::json(args)]` count → on the call where this hits exactly 2 (i.e. the *first* repeat), queues a note: *"you just called X with the exact same arguments as a previous call in this task... calling it again won't produce new information."* Fires once per (tool, args) pair, not on every subsequent repeat, to avoid spamming a long loop.
  - `by_tool_args[tool_name]` (a `set` of distinct arg-key strings, **not** a raw call counter) → on the call where the *distinct-variation* count first reaches 3, queues a note: *"you've now called X with N different sets of arguments in this task... consider whether a different tool has the info you need."* Deliberately gentler wording than the exact-repeat note, because this shape (probe 3, mondial_geo) is often legitimate search.
  - Using distinct-variation count (not total call count including exact repeats) for the second note was itself a bug found and fixed during a self-written smoke test *before* any real eval run: the first draft used raw call counts, which double-fired both notes on a 3rd identical call. Fixed by switching to a `Set[str]` of distinct arg-keys with an `is_new_variation` gate.
- `pop_pending_notes()` — drains and returns queued notes. Called exactly once, from `local_executor.py`'s successful (non-exception, non-timeout) execution path, appended to that block's own stdout result — informational only, same category of intervention as the existing timeout-evidence guidance from PR #493, just triggered by call-repetition instead of a timeout.
- Original reset trigger (now known broken, see §3): a hook in `shared_nodes.py`'s shared `call_model` node (used by both CugaLite and CugaSupervisor graphs), firing on `len(effective_messages) == 1` — the same "first message of a new thread" signal already used there for personal-instruction (`pi`) injection.

Wiring: `registry.py` line ~53 and `combined.py` line ~135 each call `TaskToolCallHistory.record_call(...)` immediately when a tool is invoked (before the actual HTTP/tracker call, so it always records regardless of what happens after — including exceptions from the *user's* generated code that come later in the same block).

## 3. Bug #1 (found, fixed): reset() and the tool-call sites run in different LangGraph node dispatches

### Observation that surfaced it

Ran the full prototype against `cap4_runaway_probe_n4` (all 4 tasks). Grepped the entire bundle — console log, all trajectory JSONs (full, untruncated field values, not a preview slice) — for the note text (`"exact same arguments"`, `"different sets of arguments"`). **Zero occurrences**, across all 4 tasks, despite `professional_basketball`'s own trajectory showing a textbook case: `professional_basketball_get_max_weight_by_birth_country(birth_country="United States")` called with byte-identical arguments in two separate code blocks (steps 21, 31, 36 of that run's trajectory — three occurrences of the exact same call).

### Root cause

`TaskToolCallHistory.reset()` ran inside `call_model` (`shared_nodes.py`). `record_call()`/`pop_pending_notes()` run inside `execute_node` → `local_executor.py` (a **different** LangGraph node). LangGraph's Pregel runner dispatches each node via `AsyncBackgroundExecutor.submit()` (`langgraph/pregel/_executor.py`), which calls `copy_context()` on every dispatch. A `ContextVar.set()` performed inside one node's dispatch is invisible to a sibling node's dispatch, because the sibling runs under its own copy of the context taken *before* the first node's mutation happened.

Confirmed with a minimal repro (not the real graph, a standalone asyncio script): setting a contextvar inside one `asyncio.create_task()`-wrapped coroutine and reading it from a second, separately-created task returns the default (`None`), while reading it from a direct `await` in the *same* task (no task boundary) correctly sees the mutation. `BlockToolCallBudget` never hits this because its `reset()` and `check_and_increment()` both happen inside the *same* function call (`local_executor.execute()`), in the same task — the "shared mutable dict across a context copy" trick that its own code comment describes only works because the copy in question (`asyncio.wait_for`'s internal task) happens *after* the reset, within one dispatch.

### Fix applied

Moved the reset out of `call_model` entirely and into `LocalExecutor.execute()` itself (`src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/local/local_executor.py`), gated by a new `is_new_task: bool = False` parameter:

```python
async def execute(self, wrapped_code, context_locals, timeout=30, is_new_task=False):
    ...
    if is_new_task:
        TaskToolCallHistory.reset()
```

Caller (`code_executor.py::_eval_with_tools_async_impl`, the 'local' execution branch) computes the signal from state that's already correct and already-incremented by the time `execute_node` runs:

```python
result = await executor.execute(
    ...,
    is_new_task=(getattr(state, 'step_count', None) == 1),
)
```

`state.step_count` starts at `0` (set at task prep, `prepare_node.py`) and is incremented to `1` by `call_model` *before* the `Command` handing off to `execute_node` — so by the time `execute_node`/`_eval_with_tools_async_impl` runs, `state.step_count == 1` reliably means "first code block of this task," using a value that's explicitly threaded through LangGraph's state channels (not a contextvar), so it doesn't have the same cross-dispatch visibility problem.

Removed the now-dead reset call from `shared_nodes.py`.

### Verification of fix #1 (misleading — see §4)

1. Standalone smoke test calling `LocalExecutor.execute()` directly, twice in sequence, simulating block 1 (`is_new_task=True`, two exact-duplicate calls) then block 2 (`is_new_task=False`, a 3rd distinct variation) then block 3 (`is_new_task=True` again, a fresh task) — all three assertions passed (exact-repeat note fires, distinct-variation note fires, new task starts clean).
2. A second, more faithful smoke test built the tool wrapper through the **real** `registry.py::create_tool_from_api_dict` factory (not a hand-written stand-in), called it twice through `LocalExecutor.execute()` — note fired correctly.
3. Full cuga-agent test suite: `pytest -k "executor or tracker or registry or combined"` → 384 passed, same 5 pre-existing/unrelated failures as before this work (filesystem-seeding tests, e2b package not installed — confirmed unrelated by `git diff --stat` and by reading the failures directly).

All three of these passed and all three are **not representative of the real graph**: none of them cross an actual LangGraph node-dispatch boundary between the two "blocks" being compared. They call `execute()` twice from plain sequential Python `await`s in one test script — which is exactly the "same task, no copy_context() in between" case that was never broken.

## 4. Bug #2 (found, NOT fixed): every node dispatch is isolated, not just call_model vs execute_node

### Observation that surfaced it

Re-ran the real M3 eval (`cap4_runaway_probe_n4`) with fix #1 in place. `professional_basketball` happened to flip to a genuine PASS (dialogue=1.00, up from FAIL/groundedness=0.0 before) — but **still zero note occurrences** anywhere in the bundle (console log + full untruncated trajectory JSON), despite the trajectory containing the *same* textbook repeat as before: `professional_basketball_get_max_weight_by_birth_country(birth_country="United States")` called identically in two separate code blocks (steps 23 and 28 of this run's trajectory).

(Note: we are not claiming the PASS is caused by fix #1 or by anything in this mechanism — no notes fired, so nothing in `TaskToolCallHistory` could have influenced that run's outcome. Most likely explanation is ordinary stochasticity in an LLM-driven agent re-run; not investigated further since it's off the critical path here.)

### Root cause

Fix #1 only solved *co-location within one node dispatch* (`call_model` and `execute_node` no longer needed to share state — the reset moved to live inside `execute_node`'s own dispatch). It did **not** address the deeper fact that **every** LangGraph node dispatch — including two separate dispatches of the *same* node (e.g. `execute_node` running for code block N, then `execute_node` running again for code block N+1) — gets its own `copy_context()` via `AsyncBackgroundExecutor.submit()`. Code block N and code block N+1 are, from LangGraph's perspective, two unrelated `submit()` calls; nothing this design does inside one of them is visible in the other.

Confirmed with a second, more faithful repro: a helper that mimics langgraph's actual dispatch mechanism (`run_coroutine_threadsafe(coro, loop, context=copy_context())`, matching `AsyncBackgroundExecutor.submit()`'s real code) — calling it twice for the "same node," in sequence, exactly reproduces the bug: the second dispatch's `_task_tool_calls_context.get()` returns `None`, even though the first dispatch had reset it. This is fatal for `TaskToolCallHistory`'s entire premise (repetition detected *across* separate code blocks) in a way it is not fatal for `BlockToolCallBudget` (which only ever needs state to survive *within* one block/one dispatch, never across dispatches).

### Why the earlier "verification" didn't catch this

All three checks in §3 called `execute()` twice from the same Python call stack with plain `await` — no `copy_context()` boundary was ever exercised between the two calls, so they could not have detected a bug that only manifests across *separate* Pregel dispatches. This was a real gap in the validation, not a flaw in the fix itself (the fix was, and remains, correct for what it targeted) — it's a case of the fix being real but insufficient, and the test methodology not being adversarial enough to reveal that.

### What this means

**No placement of `reset()` / `record_call()` / `pop_pending_notes()` relative to each other, as long as they rely purely on `contextvars`, can make state survive from one code block to the next**, because every code block is its own top-level node dispatch with its own copied context. This is a structural property of how LangGraph's Pregel runner executes nodes, not a bug in our usage of contextvars — `BlockToolCallBudget` works *only* because its entire lifecycle (reset + all increments) happens to fit inside a single dispatch.

## 5. Options for an actual fix (none implemented yet)

Any real fix must stop relying on contextvars to carry the holder across node dispatches, and instead ride on something LangGraph explicitly threads through — state channels (`Command(update={...})`) or an object captured by closure at a point that is *itself* per-task and doesn't get rebuilt every dispatch.

**(a) Per-task object captured by the tool-wrapper closures at tool-creation time. RULED OUT.** `registry.py::create_tool_from_api_dict` and `combined.py`'s tracker-tool factory build `tool_func` closures once (currently capturing `app_name`, `tool_name`, `operation_id`, `agent_id`). This only works if tools are rebuilt fresh once per task/thread — checked, and they are **not**: `ToolRegistryProvider.get_tools(app_name)` caches its built tools in `self.tools_cache` per app_name (`registry.py` ~line 353), and on the cuga-eval side, `eval_m3.py` builds `tool_provider = CombinedToolProvider()` explicitly **once and reuses it across every domain and every sample in the run** — its own comment says so: *"OPTIMIZATION: Create tool provider ONCE for this task and reuse across all domains"* (`benchmarks/m3/eval_m3.py` ~line 1608). `FilteredToolProvider` (the per-domain view each sample's `CugaAgent` actually receives) wraps that same shared provider — it filters, it doesn't rebuild. So the `tool_func` closures a sample's agent calls are, in general, the *same* closure objects used by every other sample in that run; there is no per-task moment at which to capture a fresh per-task object into them.

**(b) Plain global dict keyed by a stable per-task id. → refined into (b′), see below.**

**(c) State-channel field.** Store the holder as a field on `AgentState`/`CugaLiteState` (like `step_count`, `cuga_lite_metadata`) so LangGraph persists and threads it explicitly across dispatches the normal way. Solves persistence cleanly, but `registry.py`/`combined.py`'s tool-calling closures have no access to `state` at all (unlike `context_locals`, which is explicitly threaded) — and since those closures are shared across the whole run per the finding above, there's no way to re-inject a fresh state value into an already-built closure each dispatch either. Converges back to needing (b′)'s indirection anyway.

### (b′) Chosen direction: global dict keyed by `thread_id`, contextvar carries only the *key*, set fresh every dispatch

The insight that unlocks this: the earlier attempts all tried to make a contextvar carry the **accumulating value** (the growing history dict) across dispatches, which Bug #2 proves is impossible. But a contextvar *can* reliably carry a small, **non-accumulating, freshly-set-every-time** value across the one boundary that matters (`local_executor.execute()`'s top, down into the `asyncio.wait_for`-spawned sub-task where the tool call actually happens) — that's exactly the boundary `BlockToolCallBudget` already uses successfully. So: keep the *identifier* on a contextvar (reset correctly every single dispatch — never relying on it surviving from a previous dispatch), and keep the *accumulating state* in an ordinary module-level dict, keyed by that identifier, which trivially survives everything because it isn't contextvar/task-scoped state at all.

- `thread_id` is already unique per M3 sample and stable across every turn/block of that one sample: `benchmarks/helpers/sdk_eval_helpers.py` builds it as `f"eval_{task_name}_{task_index}_{uuid.uuid4().hex[:8]}"` (single-turn path) / `f"multiturn_{task_name}_{task_index}_{uuid.uuid4().hex[:8]}"` (multiturn path) — the trailing `uuid.uuid4()` means even repeated runs of the identical `sample_id` (e.g. `compare.sh --runs 5`) get distinct keys, so there's no cross-run collision risk either.
- `code_executor.py::_eval_with_tools_async_impl` already receives `thread_id` as a parameter (currently only used for the e2b branch) — thread it into the local branch's `executor.execute(..., task_key=thread_id)` call too.
- `local_executor.py::execute()` calls `TaskToolCallHistory.bind(task_key)` unconditionally at the top of *every* dispatch (replacing the `is_new_task`-gated `reset()` from fix #1). `bind()` sets the contextvar to `task_key` (cheap, always-correct, never needs to survive to the next dispatch — it's rebuilt fresh every time from an explicit parameter) and lazily creates a fresh entry in the module-level `_task_histories` dict the first time a given key is seen. Existing keys are left untouched, so state genuinely accumulates across every block of one task, indexed by an ordinary dict lookup rather than by hoping a contextvar mutation crossed a boundary it structurally cannot cross.
- `record_call`/`pop_pending_notes` read the current key from the contextvar, then look up (not `.get()` off the contextvar itself) the shared dict entry.
- Memory: `_task_histories` is capped (simple `OrderedDict` + evict-oldest) so a long batch run doesn't grow it unboundedly; this is a prototype-level safeguard, not exposed to cuga-eval.
- Concurrency: safe under real concurrent tasks in one process (e.g. a future working `--batch-size` worker pool) as long as `thread_id` stays unique per concurrently-running task, which the generation scheme above already guarantees.

**Status: implemented and confirmed working in a real M3 eval run** (see §7).

## 6. Other open considerations (carried over from design, not re-litigated here)

- Even a working version of this mechanism only produces a **prompt annotation** the model can choose to ignore. PR #493 already demonstrated this exact residual risk with its own "loop-inversion" prompt heuristic, described in that PR's own text as "verifiably in context and ignored." A structurally-safer design (never blocks/aborts, unlike the being-removed `BlockToolCallBudget`) does not make the note un-ignorable — it trades a hard-failure risk (wrongly aborting a converging search) for a soft-failure risk (the model shrugs off the note). Worth deciding, once the mechanism actually fires in real runs, whether ignored notes are still net-positive or just added token cost.
- Per-pattern generality assessment from earlier discussion, still believed accurate: retriever-rephrasing (probe 2) is a general pattern (not VAKRA-specific); per-item-loop absence-of-mechanism (probe 1) is general; parameter-guessing (probe 3) has genuine VAKRA-flavor risk (poor parameter discoverability in this benchmark specifically inflates how often this shape appears) — meaning a fix here should expect probe 3 to be the least reliable win even once the mechanism works at all.
- `BlockToolCallBudget` removal (requested by PR #493's reviewer) is a separate, still-open task, not done as part of this work — noted here only because it's the reason this replacement mechanism exists at all.

## 7. Current status / next step

Fix #2 (design (b′) above) is implemented in the working tree (uncommitted, `integration/m3-eval` branch):

- `tracker.py`: `TaskToolCallHistory.reset()` replaced by `bind(task_key)`; accumulating state moved from `_task_tool_calls_context` (a ContextVar holding the growing dict — the broken design) to a module-level `_task_histories: OrderedDict[str, dict]` (capped at 64 entries, evicts oldest) keyed by `task_key`; the ContextVar (`_current_task_key_context`) now carries only the key, set fresh on every `bind()` call.
- `local_executor.py`: `execute()`'s `is_new_task: bool` param replaced by `task_key: Optional[str]`; calls `TaskToolCallHistory.bind(task_key)` unconditionally at the top of every dispatch (not gated on step_count anymore — every dispatch must re-bind, since the whole point is that no dispatch can assume a previous one's contextvar mutation survived).
- `code_executor.py`: the local-execution branch of `_eval_with_tools_async_impl` now passes `task_key=thread_id` (a parameter it already received, previously only wired to the e2b branch).

**Validated so far:**
1. Standalone smoke test (`smoke_bprime_fix.py`) that — unlike every earlier "verification" in this doc — actually exercises a `copy_context()` boundary between blocks, by using a `submit_like_langgraph()` helper that mirrors `AsyncBackgroundExecutor.submit()`'s real `context=copy_context()` call. Two blocks dispatched through *separate* copied contexts (simulating two separate `execute_node` Pregel dispatches) correctly share history under the same `task_key`; a third block with a *different* `task_key` correctly sees no leaked history. This is the first test in this investigation that was actually capable of catching Bug #2 — the earlier ones in §3 could not have, regardless of whether the code they tested was right or wrong.
2. Full test suite: `pytest -k "executor or tracker or registry or combined"` → 384 passed, same 5 pre-existing/unrelated failures as every prior run.
3. **Real M3 eval re-run against `cap4_runaway_probe_n4` (all 4 probes) — notes fire in every single one.** Grepped each task's full untruncated trajectory JSON (not the earlier bundle-only location, which only persists the last-processed task's trajectory — used `benchmarks/m3/logging/trajectory_data/m3_multiturn_evaluation_*/<sample_id>.json` instead, which cuga-eval writes per task regardless):

   | probe | pattern | exact-repeat notes | distinct-variation notes | outcome this run |
   |---|---|---|---|---|
   | `professional_basketball` (`d14bbb0be92d-ae240cc7a80e`) | retriever-rephrasing | 532 | 4 | FAIL (exactmatch=1.0, groundedness=0.0 — unrelated failure mode, notes fired correctly regardless) |
   | `law_episode` (`e71440999ce8-99b792c44651`) | exact-duplicate per-item loop | 330 | 6 | **PASS** (dialogue=1.00, exactmatch=1.0, groundedness=1.0) |
   | `codebase_comments` (`1960f609e439-3a1b0a9c3535`) | mixed-exploration | 230 | 2 | FAIL (unrelated: wrong answer content, not a runaway) |
   | `mondial_geo` (`55b7e50368aa-01b9e0ab72a2`) | parameter-guessing | 2 | 2 | FAIL (hit step limit) |

   Spot-checked one note's exact rendered text in `professional_basketball`'s trajectory (step 19) to confirm it's not a false-positive count from the word appearing in unrelated text — it's the real, correctly-worded note: *"Note: you just called professional_basketball_get_player_names_height with the exact same arguments as a previous call in this task (arguments: {"min_height": 80})..."*.

   **On the `law_episode` PASS:** worth being precise about what this does and doesn't prove. The task passing is consistent with the fix helping, but a single run of a stochastic LLM-driven agent is not proof of causation on its own — this run's tool-call count (675) is still very high and nowhere near "the loop stopped after the note," so the mechanism visibly fired throughout without stopping the repetition; the model may have converged on the right answer anyway despite (not because of) continuing to loop. Do not read this single PASS as validating that notes reduce runaway behavior — it only validates that **the plumbing now works end-to-end**. The question of whether these notes actually change behavior (get the model to stop looping sooner, or answer more accurately) is still open and needs the `compare.sh --runs 5` treatment before drawing any conclusion, exactly as this session has done for every other fix.

4. Full test suite: `pytest -k "executor or tracker or registry or combined"` → 384 passed, same 5 pre-existing/unrelated failures as every prior run in this doc.

**Next step:** now that the plumbing is confirmed working, move to the originally-planned validation pattern used for every other fix this session — single run already done above; next is `compare.sh --runs 5` on `cap4_runaway_probe_n4` (or `_n3`) to get an actual signal on whether the notes change outcomes, separating "the mechanism fires" (now proven) from "the mechanism helps" (not yet known).

## Appendix: file/line references (as of this writing, uncommitted on `integration/m3-eval`)

- `src/cuga/backend/cuga_graph/nodes/cuga_lite/tracking/tracker.py` — `TaskToolCallHistory` class, `bind()`/`record_call()`/`pop_pending_notes()`, `_current_task_key_context` (carries only the key), `_task_histories` (module-level, capped `OrderedDict`, carries the accumulating state)
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/registry.py` — `call_api()` (~line 53), `create_tool_from_api_dict()`'s `tool_func` closure (~line 227); `ToolRegistryProvider.get_tools()`'s `tools_cache` (~line 353) is why option (a) was ruled out
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/providers/combined.py` — `create_tool_from_tracker()`'s `tool_func` closure (~line 135)
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/local/local_executor.py` — `execute()`, `task_key` param, `TaskToolCallHistory.bind(task_key)` called unconditionally every dispatch, note-popping at the end of the successful path
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/code_executor.py` — `_eval_with_tools_async_impl()`, passes `task_key=thread_id` on the local branch
- `src/cuga/backend/cuga_graph/nodes/cuga_agent_core/graph/shared_nodes.py` — dead reset call removed from `call_model`
- `.venv/lib/python3.12/site-packages/langgraph/pregel/_executor.py` — `AsyncBackgroundExecutor.submit()`, the `copy_context()` call that causes both bugs
- cuga-eval `benchmarks/helpers/sdk_eval_helpers.py` — `thread_id = f"eval_{task_name}_{task_index}_{uuid.uuid4().hex[:8]}"` (single-turn) / `f"multiturn_..."` (multiturn) — the uniqueness guarantee (b′) depends on
- cuga-eval `benchmarks/m3/eval_m3.py` (~line 1608) — `tool_provider = CombinedToolProvider()`, "Create tool provider ONCE for this task and reuse across all domains" — the comment that ruled out option (a)

cuga-eval repo (sibling): `benchmarks/m3/eval_config.toml` — `cap4_runaway_probe1..4`, `_n3`, `_n4` (the 4 probe tasks used for all validation above).
