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
├── tools/                       # manual clients for a running stack (not pytest tests)
│   ├── multiprocess_client.py   # fixed-size closed-loop load, one JSON line
│   └── capacity_ramp.py         # stepped concurrent users, finds the largest passing level
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

### Capacity ramp (manual tool)

Finds how many concurrent users one server handles. It steps through user levels, holds each level, and reports turns/s, p50/p95/p99 and error rate. A level passes when the p95 is under `--slo-p95-s` (default 30 s) and the error rate is under `--max-error-rate` (default 1%). The last line of output is `largest_passing_users`.

Use a mock LLM delay so that a conversation lasts as long as it does with a real LLM. With no delay the mock answers instantly and the test only measures peak throughput, not the number of users that can stay active.

1. Start the stack. Use PostgreSQL (`storage.mode=prod`) so SQLite is not the limit:

   ```bash
   export CUGA_MOCK_LLM=true CUGA_MOCK_LLM_LATENCY_MS=3000 CUGA_MODE=api
   export DYNACONF_STORAGE__MODE=prod
   export DYNACONF_STORAGE__POSTGRES_URL=postgresql://<user>:<password>@127.0.0.1:5432/cuga_load  # pragma: allowlist secret
   export DYNACONF_AUTH__ENABLED=false DYNACONF_AUTH__REQUIRE_HTTPS=false
   uv run cuga start demo --digital-sales
   ```

   The database must exist and have the `vector` extension available. Set `LITELLM_MODE=PRODUCTION` if a `.env` file in a parent directory sets other values you do not want.

2. In another terminal, run the ramp (about 2.5 minutes per level at the defaults):

   ```bash
   uv run python src/system_tests/load/tools/capacity_ramp.py \
       --levels 25,50,100,200,400 --think-s 10 --slo-p95-s 30 --out ramp.jsonl --label my-run
   ```

   | Option | Default | Meaning |
   |---|---|---|
   | `--levels` | `50,100,200,300` | Concurrent users per step |
   | `--think-s` | `10` | Mean pause after each turn (0.5x to 1.5x) |
   | `--hold-s` | `120` | Steady seconds measured per level |
   | `--ramp-up-s` | `15` | Users start spread over this time |
   | `--turns` | `2` | Turns per conversation (1 or 2) |
   | `--procs` | `4` | Client processes; raise them if the client becomes the limit |
   | `--stop-after-fails` | `2` | Stop after this many failing levels in a row |
   | `--url` | `http://127.0.0.1:7860` | Server to test |

3. Read the result. Each level prints one JSON row. `avg_in_flight` is the average number of requests being served at the same time (Little's law). Compare runs only with the same `CUGA_MOCK_LLM_LATENCY_MS`, `--think-s`, hardware and database.

Limits of this tool: the client runs on the same machine unless you point `--url` at another host, the mock delay does not include the CPU cost of streaming a real response, and a 2-minute hold does not show slow memory growth (run a longer `--hold-s` at about 70% of the capacity for that).

### Helper unit tests

```bash
uv run pytest src/system_tests/load/tests/ -q
```

## What each test validates

Both tests run the same user flow per thread:

1. **Primary:** `list all my accounts, how many are there?`
2. **Followup:** `how many accounts did we retrieve?`

Each user creates its own conversation (`POST /api/conversations`) and sends the server-issued ID as `X-Thread-ID`. Checks include:

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
