# Concurrent Load Tests

System tests that simulate multiple concurrent users against the demo server and validate per-thread state isolation, timing, and (for the mocked variant) deterministic LLM responses.

## Directory layout

```
src/system_tests/load/
├── README.md
├── PLAN.md                      # scope, what tests prove, stress-test pyramid
├── conftest.py                  # --load-test-users CLI flag
├── metrics.py                   # timing report helpers
├── isolation.py                 # per-thread state count validation
├── load_test.py                 # real LLM concurrent load test
├── load_test_with_mocked_llm.py # mock LLM (CUGA_MOCK_LLM) load test
└── tests/                       # unit tests for helpers above
    ├── test_conftest.py
    ├── test_metrics.py
    └── test_isolation.py
```

Mock LLM runtime code lives in `src/cuga/backend/llm/load_test_mock.py`. Its unit tests stay in `tests/unit/test_load_test_mock_llm.py`.

## Running

### Mocked LLM load test (recommended for CI / local concurrency checks)

No external LLM API calls. Starts demo, registry, and digital sales MCP servers automatically.

```bash
uv run pytest src/system_tests/load/load_test_with_mocked_llm.py -q -s
```

Scale concurrent users:

```bash
uv run pytest src/system_tests/load/load_test_with_mocked_llm.py -q -s --load-test-users 10
```

Or via env:

```bash
CUGA_LOAD_TEST_USERS=10 uv run pytest src/system_tests/load/load_test_with_mocked_llm.py -q -s
```

### Real LLM load test

Requires configured LLM credentials (Groq/OpenAI/etc. per project settings):

```bash
uv run pytest src/system_tests/load/load_test.py -q -s --load-test-users 5
```

### Helper unit tests

```bash
uv run pytest src/system_tests/load/tests/ -q
```

## What each test validates

Both tests run the same user flow per thread:

1. **Primary:** `list all my accounts, how many are there?`
2. **Followup:** `how many accounts did we retrieve?`

Each user gets a unique `X-Thread-ID`. Checks include:

- Answers contain expected keyword `50`
- Per-thread variables and chat history (mocked test)
- Post-run uniform state counts across all threads (no overload/leak)
- Concurrency timing report on success (speedup, tail latency, finish spread)

## Expected state counts (mocked test)

| Checkpoint | Variables | Chat messages |
|------------|-----------|---------------|
| After primary | 3 | 4 |
| After followup (post-run) | 3 | 6 |

If CugaLite flow changes, update `expected_*_count` constants on `LoadTestWithMockedLLM`.

## Logs

Test logs are written under:

```
src/system_tests/e2e/logs/<TestClass>_<test_method>/
├── demo_server.log
├── registry_server.log
└── digital_sales_mcp.log
```

## Interpreting the concurrency report

Printed after a successful run:

- **Concurrency speedup** — `sum(user totals) / wall clock`; ~N× with N users indicates good parallelism
- **Finish spread** — large spread suggests tail latency or contention
- **Warnings** — informational only (low speedup, uneven primary durations, etc.)
