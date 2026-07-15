# CUGA Startup Profiling Tool

Measures how long CUGA takes to reach a **usable state** from a cold start —
both the SDK (in-process) and the HTTP server. Results are written as
timestamped JSON files under `results/`.

> **This is a separate tool from the `src/system_tests/profiling/` harness.**
> That harness measures task/LLM latency via Langfuse experiments.
> This tool measures *cold-start* time (import + construction + readiness).
> The two have no shared code or configuration.

Design reference: `docs/superpowers/specs/2026-07-14-cuga-startup-optimization-design.md`
(issue #486)

---

## Quick Start

```bash
# Full benchmark: 3 runs each, prints median/min/max, writes results/startup-<ts>.json
bash src/system_tests/profiling/startup/run_startup_bench.sh

# Quick check: 1 run
bash src/system_tests/profiling/startup/run_startup_bench.sh 1

# 5 runs for tighter statistics
bash src/system_tests/profiling/startup/run_startup_bench.sh --runs 5
```

The script must be run from the **repo root** or any directory — it `cd`s to
its own location automatically.

---

## Directory Structure

```
startup/
├── README.md                  # this file
├── run_startup_bench.sh       # primary entry point
├── measure_sdk_startup.py     # SDK cold-start measurement
├── measure_server_startup.py  # server cold-start measurement
├── import_breakdown.py        # ranked import hotspot finder
├── aggregate_results.py       # statistics + results writer (called by the bench script)
└── results/                   # generated JSON output (git-ignored except .gitkeep)
```

---

## Scripts

### `run_startup_bench.sh` — main entry point

Runs both measurement scripts N times each, then calls `aggregate_results.py`
to compute statistics and write a results file.

```bash
bash run_startup_bench.sh              # default: 3 runs
bash run_startup_bench.sh 1            # positional N: 1 run
bash run_startup_bench.sh --runs 5     # named flag: 5 runs
bash run_startup_bench.sh --runs=5     # also accepted
```

Output: a human-readable summary table on stdout plus a timestamped file at
`results/startup-<YYYY-MM-DDTHH-MM-SSZ>.json`.

---

### `measure_sdk_startup.py` — SDK cold-start

Measures how long it takes for the CUGA Python SDK to reach a ready state.
Each invocation spawns a **fresh subprocess** so `sys.modules` is completely
empty — no warm module cache can distort the result.

```bash
uv run python src/system_tests/profiling/startup/measure_sdk_startup.py
uv run python src/system_tests/profiling/startup/measure_sdk_startup.py --with-invoke
```

| Flag | Description |
|------|-------------|
| _(none)_ | Measure `import_s`, `construct_s`, and `ready_s` only |
| `--with-invoke` | Also measure `llm_first_call_s` (uses a fake LLM model — no real provider contacted) |

Prints a single JSON line to stdout, e.g.:

```json
{"import_s": 1.123, "construct_s": 0.045, "ready_s": 1.168}
{"import_s": 1.123, "construct_s": 0.045, "ready_s": 1.168, "llm_first_call_s": 0.031}
```

Any log noise from cuga itself is forwarded to stderr and does not affect the
JSON output.

---

### `measure_server_startup.py` — server cold-start

Launches `cuga.backend.server.main:app` via uvicorn on a **free ephemeral
port** (no collision with a running dev server) and polls
`/health/readiness` every 0.25 s until the endpoint returns `"ready": true`.

```bash
uv run python src/system_tests/profiling/startup/measure_server_startup.py
```

No flags. Prints a single JSON line to stdout:

```json
{"server_ready_s": 3.241}   # success
{"server_ready_s": null}    # timeout (90 s) or launch failure
```

**Fallback behaviour:** If the HTTP endpoint is consistently unreachable (e.g.
missing API keys / environment variables), the script watches the server's
combined stdout+stderr for the log line `"Application finished starting up..."`.
When that line appears the clock stops. This still produces a valid cold-start
number measuring import + lifespan initialisation, but the stderr output will
note that the fallback fired. See [Environment variables](#environment-variables)
below.

---

### `import_breakdown.py` — ranked import hotspots

Runs a Python statement in a subprocess under `-X importtime` and prints the
top N modules sorted by **self time** (time spent in that module's own code,
excluding its transitive imports). Use this to identify which packages are the
biggest contributors to import latency.

```bash
# Default: profile 'import cuga.sdk', show top 30
uv run python src/system_tests/profiling/startup/import_breakdown.py

# Profile the server entrypoint instead
uv run python src/system_tests/profiling/startup/import_breakdown.py \
    --stmt "import cuga.backend.server.main"

# Show more entries
uv run python src/system_tests/profiling/startup/import_breakdown.py --top 50

# Combine both flags
uv run python src/system_tests/profiling/startup/import_breakdown.py \
    --stmt "import cuga.backend.server.main" --top 50
```

| Flag | Default | Description |
|------|---------|-------------|
| `--stmt` | `"import cuga.sdk"` | Python statement to execute under `-X importtime` |
| `--top` | `30` | Number of modules to display (sorted by `self_us` descending) |

Example output:

```
--------------------------------------------------------
 #  self_us   cum_us  module
--------------------------------------------------------
 1   123456   234567  pydantic.main
 2    98765   101234  langchain_core.messages
...
```

---

### `aggregate_results.py` — statistics writer

Called automatically by `run_startup_bench.sh`. Can also be invoked directly.

```bash
uv run python src/system_tests/profiling/startup/aggregate_results.py \
    --sdk-runs    '{"import_s":1.1,"construct_s":0.05,"ready_s":1.15}' \
                  '{"import_s":1.0,"construct_s":0.04,"ready_s":1.04}' \
    --server-runs '{"server_ready_s":3.2}' '{"server_ready_s":3.0}'
```

| Flag | Description |
|------|-------------|
| `--sdk-runs JSON …` | One JSON string per SDK run (from `measure_sdk_startup.py`) |
| `--server-runs JSON …` | One JSON string per server run (from `measure_server_startup.py`) |
| `--results-dir DIR` | Output directory (default: `results`) |

---

## Metrics Reference

| Metric | Source | Description |
|--------|--------|-------------|
| `import_s` | SDK | Wall-clock time to execute `import cuga.sdk` + `from cuga.sdk import CugaAgent` |
| `construct_s` | SDK | Wall-clock time to construct `CugaAgent(tools=[echo])` |
| `ready_s` | SDK | `import_s + construct_s` — total SDK cold-start time |
| `llm_first_call_s` | SDK (`--with-invoke`) | Time for the first `agent.invoke()` using a fake model; recorded separately, **not** included in `ready_s` |
| `server_ready_s` | Server | Wall-clock time from `subprocess.Popen()` until `/health/readiness` returns `"ready": true` (or until the fallback log line is detected) |

### Ready-state definitions

- **SDK ready** — the process can execute the first `invoke()`. Defined as:
  import time + `CugaAgent` construction time. LLM network latency is
  explicitly excluded and captured in the separate `llm_first_call_s` metric.

- **Server ready** — the server process is accepting requests. Defined as:
  process start → `/health/readiness` returns `{"ready": true}`.

### Design decisions worth knowing

- **Subprocess isolation** — every SDK measurement spawns a fresh subprocess,
  so `sys.modules` is empty. There is no warm module cache.
- **LLM time excluded from `ready_s`** — `ready_s` never includes LLM
  network time, even when `--with-invoke` is used.
- **Median of N runs** — smooths OS scheduler and disk-cache jitter. Use
  `run_startup_bench.sh` (default 3 runs) for representative numbers; use
  `--runs 5` or higher for published benchmarks.
- **Port isolation** — `measure_server_startup.py` binds to a free ephemeral
  port, so it never conflicts with a running dev server.

---

## Environment Variables

`measure_sdk_startup.py` inherits the current shell environment. No special
variables are required for basic SDK measurement.

`measure_server_startup.py` starts a real uvicorn process, so any environment
variables the CUGA server needs (API keys, model config, etc.) must be set in
the calling shell before running the benchmark:

```bash
export SOME_API_KEY=...
bash src/system_tests/profiling/startup/run_startup_bench.sh
```

Without required variables the server may fail to reach full `"ready": true`.
The fallback log-line detector will still capture a cold-start time
(import + lifespan init), but `server_ready_s` will reflect a partial startup.
The measurement is still useful for tracking import and lifespan overhead
independently of downstream service availability.

---

## Results Files

Results are written to `results/startup-<YYYY-MM-DDTHH-MM-SSZ>.json` and are
**git-ignored** (a `.gitkeep` keeps the directory tracked). Each file contains:

```jsonc
{
  "timestamp": "2026-07-14T12-00-00Z",
  "sdk_runs": 3,
  "server_runs": 3,
  "sdk": {
    "import_s":    {"median": 1.05, "min": 1.00, "max": 1.12},
    "construct_s": {"median": 0.04, "min": 0.04, "max": 0.05},
    "ready_s":     {"median": 1.09, "min": 1.04, "max": 1.17}
  },
  "server": {
    "server_ready_s": {"median": 3.20, "min": 3.10, "max": 3.35}
  },
  "raw": {
    "sdk_runs":    [...],
    "server_runs": [...]
  }
}
```
