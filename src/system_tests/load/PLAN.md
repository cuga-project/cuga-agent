# Load Test Plan

Strategy and scope for concurrent load tests in this directory. For how to run tests and interpret reports, see [README.md](./README.md).

## Purpose

These tests answer one primary question:

> **When many users run the same agent flow at the same time, does each thread stay isolated — correct answers, scoped variables, and consistent chat history — without cross-thread leakage?**

Secondary signals: concurrency timing (speedup, tail latency, finish spread) to spot contention regressions on a single-machine full stack.

## What this proves

| Claim | Evidence |
|-------|----------|
| Per-thread isolation | Unique `X-Thread-ID` per simulated user; answers and state scoped to that thread |
| Correct end-to-end flow | Primary + followup query through demo server, registry, MCP, CugaLite graph, sandbox execution |
| Uniform state under load | Post-run check that every thread has the same variable and chat message counts (mocked test) |
| Parallelism exists | Concurrency report: speedup, per-user timings, finish timeline |
| Mock path is deterministic | `CUGA_MOCK_LLM` removes external LLM variance for repeatable CI/local runs |

### Mocked test (`load_test_with_mocked_llm.py`)

Best for **CI and local regression**. Validates:

- Answer contains expected keyword (`50`)
- After primary: 3 variables, 4 chat messages (all threads)
- After followup: 3 variables, 6 chat messages (all threads)
- No thread has fewer counts than expected (overload/leak signal)

### Real LLM test (`load_test.py`)

Best for **integration smoke** with real credentials. Validates answer isolation and keyword checks; does not enforce strict state counts today. Keep user count low (3–5).

## What this does not prove

Do **not** infer production capacity or SLA from these tests alone.

| Not covered | Why |
|-------------|-----|
| Production deployment | Tests spawn demo + registry + MCP on one host; single process, in-memory `MemorySaver` |
| Real LLM latency / rate limits | Mocked test bypasses LLM APIs entirely |
| Postgres / multi-worker scaling | Load tests run local SQLite; prod uses pooled `ProdRelationalStore` (same code path, not exercised here) |
| Sustained load | Each run is a burst: N users × 2 turns, then exit |
| Realistic traffic patterns | No ramp-up, think time, retries, or mixed queries |

Safe framing after a passing 50-user mocked run:

- *“Verified thread isolation for 50 concurrent two-turn flows on local full-stack mock setup.”*

Avoid:

- *“We support 50 concurrent production users.”*

## Architecture under test

```
pytest
  └── N concurrent httpx streams → demo server (single process)
        ├── LangGraph agent (MemorySaver, per thread_id)
        ├── CugaLite + sandbox code execution
        ├── Registry + digital sales MCP
        └── Conversation history → SQLite or Postgres (pooled store; unless `X-Disable-History`)
```

Each user flow:

1. **Primary:** `list all my accounts, how many are there?`
2. **Followup:** `how many accounts did we retrieve?`

Client timeout per stream: **60s** (`run_task(..., timeout=60.0)`).

## Known bottlenecks (observed)

These explain finish spread and failures at high N; they are capacity characteristics, not isolation bugs.

### Mitigations applied (2026-06-11)

1. ~~**Save before Answer**~~ — `Answer` is yielded first; history saves run in a background task (`_schedule_history_save`).
2. ~~**SQLite write pattern**~~ — Relational stores are pooled per `db_name` in `StorageFacade`; WAL mode on local SQLite. Callers no longer open/close per query.
3. ~~**Sync `graph.get_state()`**~~ — Hot-path reads use `_aget_graph_state_values()` (`asyncio.to_thread`).

### Storage: local vs prod

Both backends share the same caller code (`conversation_history`, `config_store`, `secrets_store`) and `StorageFacade` pooling:

| Backend | Implementation | Pooling behavior |
|---------|----------------|------------------|
| **Local** (`storage.mode=local`) | `LocalRelationalStore` — sync SQLite via `asyncio.to_thread` | One cached connection per `db_name`; WAL enabled |
| **Prod** (`storage.mode=prod`) | `ProdRelationalStore` — native async `asyncpg` | One cached pool per `db_name` (`max_size=4`); no per-request pool teardown |

Load tests use **local SQLite only**. Prod benefits from the same pooling fix (previously each save closed the asyncpg pool). Prod-specific tuning (shared pool across `db_name`, larger `max_size`, multi-worker) is out of scope for this suite.

**Tradeoff:** History may persist slightly **after** the client receives `Answer` (background save). Correctness of stream completion is unchanged; DB durability lags by milliseconds–seconds under load.

### Remaining limits under high concurrency

4. **Single-process demo** — One agent graph and event loop; CPU, MCP, and sandbox still queue.
5. **Background save ordering** — See tradeoff above; not a client-visible latency issue after mitigations.

### Observed local ceiling (Mac, mocked, history enabled)

| Users | Before mitigations | After mitigations (2026-06-11) |
|-------|-------------------|----------------------------------|
| 5 (default) | Stable; fast CI signal | Same |
| 50 | Pass; ~15s finish spread | Pass; finish spread ~10s, most users within ~0.2s of each other (~22.3–22.5s wall) |
| 100 | Many `httpx.ReadTimeout` failures | Re-test after changes; expect improvement but single-process limits remain |

Use the concurrency report finish timeline as the baseline when comparing future runs at the same N.

## Test pyramid (recommended)

### Tier 1 — Every PR / local dev

- **Test:** `load_test_with_mocked_llm.py`
- **Users:** 5 (default)
- **Gate:** All pass; isolation + uniform counts
- **Runtime:** Minutes

```bash
uv run pytest src/system_tests/load/load_test_with_mocked_llm.py -q -s
```

### Tier 2 — Pre-merge / weekly

- **Test:** mocked
- **Users:** 10–20
- **Gate:** Pass; review concurrency report warnings
- **Action:** Investigate if speedup &lt; ~0.5× N or finish spread grows sharply vs Tier 1 baseline

```bash
uv run pytest src/system_tests/load/load_test_with_mocked_llm.py -q -s --load-test-users 20
```

### Tier 3 — Optional local stress

- **Test:** mocked
- **Users:** 50
- **Gate:** Pass preferred; warnings informational
- **Use:** Characterize local ceiling before release; document spread in notes/PR

```bash
uv run pytest src/system_tests/load/load_test_with_mocked_llm.py -q -s --load-test-users 50
```

Do **not** gate CI on 50 or 100 users unless infra and timeouts are tuned for it.

### Tier 4 — Real LLM smoke

- **Test:** `load_test.py`
- **Users:** 3–5
- **Gate:** Answers isolated; keyword present
- **Schedule:** Manual or nightly with secrets

```bash
uv run pytest src/system_tests/load/load_test.py -q -s --load-test-users 5
```

## Potential follow-up work

Planned extensions beyond the current suite (not implemented unless tracked separately):

### Near term (load test harness)

- [ ] Send `X-Disable-History: true` in mocked runs to stress graph/MCP without SQLite on the critical path
- [ ] Optional adaptive or tiered client timeout (e.g. `max(60, num_users * 2)`)
- [ ] Document recommended max `--load-test-users` for CI vs local stress in README
- [ ] Apply strict post-run state count checks to `load_test.py` (optional)
- [ ] Optional ramp mode: 10 → 25 → 50 users in one session, record breakpoint

### Medium term (server / infra)

- [x] Yield `Answer` before async background save (or make save non-blocking for stream completion)
- [x] Reuse relational store connection pool instead of open/close per query
- [x] Wrap hot-path `get_state()` in `asyncio.to_thread` where safe
- [ ] Soak test: 10 users for 30–60 minutes (leak / connection buildup)

### Long term (production-like stress)

- [ ] Deployed environment load test (k6/Locust) against `/stream` with Postgres and multi-worker setup
- [ ] Separate benchmarks for sandbox executor, MCP, and history persistence
- [ ] SLO targets: p95 primary duration, max finish spread, error rate at defined N

## Interpreting success vs regression

**Pass (correctness):** All users complete both turns; answers valid; mocked uniform state counts.

**Healthy concurrency (informational):**

- Speedup roughly scales with N (not necessarily linear)
- Finish spread modest relative to median primary duration
- No concurrency warnings in report, or stable vs baseline

**Regression signals:**

- Isolation failure (wrong answer, missing variables, mismatched counts across threads)
- Sudden increase in finish spread or primary tail at same N
- Failures flip from 0% to many at Tier 1 (5 users) — likely a real bug, not capacity

**Capacity saturation (expected at high N on Mac):**

- `httpx.ReadTimeout` before `Answer` event
- Partial or empty event lists
- 503 on state endpoint during overload

Treat saturation at 100 users on a laptop as a **finding**, not a test failure, unless Tier 1–2 regress.

## Related code

| Area | Location |
|------|----------|
| Mock LLM | `src/cuga/backend/llm/load_test_mock.py` |
| Stream + background history save | `src/cuga/backend/server/main.py` (`event_stream`, `_schedule_history_save`, `_aget_graph_state_values`) |
| History persistence | `src/cuga/backend/server/conversation_history.py` |
| Storage pooling (local + prod) | `src/cuga/backend/storage/facade.py`, `relational/local.py`, `relational/prod.py` |
| Test harness | `src/system_tests/e2e/base_test.py` (`run_task`) |
| Isolation helpers | `src/system_tests/load/isolation.py` |
| Metrics report | `src/system_tests/load/metrics.py` |

## Changelog

| Date | Notes |
|------|-------|
| 2026-06-11 | Initial plan: scope, pyramid, bottlenecks, follow-up items |
| 2026-06-11 | Implemented: Answer-before-save, pooled stores (local + prod), async get_state |
| 2026-06-11 | 50-user re-run: finish spread ~10s → ~0.2s cluster at ~22s wall (documented above) |
