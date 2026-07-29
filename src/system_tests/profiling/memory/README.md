# CUGA Memory Profiling Harness

Measures how much RAM the CUGA SDK and server consume at well-defined
checkpoints — both in isolation and as a full `cuga start demo` process tree.
Results are written as timestamped JSON files under `results/`.

> **This is a separate tool from the `src/system_tests/profiling/` harness.**
> That harness measures task/LLM latency via Langfuse experiments.
> This tool measures *resident memory* (RSS, USS, PSS) at specific lifecycle
> checkpoints.  The two have no shared code or configuration.

---

## Quick Start

```bash
# Full benchmark: 5 runs each, prints median/min/max, writes results/memory-<ts>.json
bash src/system_tests/profiling/memory/run_memory_bench.sh

# Quick check: 1 run
bash src/system_tests/profiling/memory/run_memory_bench.sh 1

# 5 runs explicitly (default)
bash src/system_tests/profiling/memory/run_memory_bench.sh --runs 5

# Keep run #1 instead of discarding it
bash src/system_tests/profiling/memory/run_memory_bench.sh --runs 5 --keep-first
```

Scripts must be run from the **repo root** or any directory — `run_memory_bench.sh`
`cd`s to its own directory automatically.

---

## Directory Structure

```
memory/
├── README.md                      # this file
├── run_memory_bench.sh            # primary entry point
├── memlib.py                      # shared sampling library (not a script)
├── measure_sdk_memory.py          # SDK checkpoints (Lens A baseline)
├── measure_server_memory.py       # server single-process checkpoints
├── measure_tree_memory.py         # full cuga start demo process tree
├── import_memory_breakdown.py     # per-import RSS delta finder (Lens A detail)
├── tracemalloc_report.py          # Python-heap attribution (Lens B)
├── memray_profile.sh              # native memory flamegraph driver (Lens C)
├── aggregate_memory_results.py    # median/min/max aggregator (called by bench script)
└── results/                       # generated JSON output (git-ignored except .gitkeep)
```

---

## Scripts

### `run_memory_bench.sh` — main entry point

Runs `measure_sdk_memory.py`, `measure_server_memory.py`, and
`measure_tree_memory.py` N times each, writes each run's JSON to `results/`,
then calls `aggregate_memory_results.py` to compute statistics and write a
summary file.

```bash
bash run_memory_bench.sh              # default: 5 runs
bash run_memory_bench.sh 3            # positional N: 3 runs
bash run_memory_bench.sh --runs 5     # named flag: 5 runs
bash run_memory_bench.sh --runs=5     # also accepted
bash run_memory_bench.sh --keep-first # include run #1 in the median
```

Output: a human-readable summary table on stdout plus a timestamped file at
`results/memory-<YYYY-MM-DDTHH-MM-SSZ>.json`.

By default **run #1 is discarded** from the median calculation to avoid
cold-cache noise (page-cache misses, JIT warm-up).  Pass `--keep-first` to
override.

---

### `measure_sdk_memory.py` — SDK checkpoint RSS

Measures RSS and USS at six lifecycle checkpoints by spawning fresh subprocesses
so no import state leaks between measurements.

```bash
uv run python src/system_tests/profiling/memory/measure_sdk_memory.py              # isolated (default)
uv run python src/system_tests/profiling/memory/measure_sdk_memory.py --isolated   # explicit
uv run python src/system_tests/profiling/memory/measure_sdk_memory.py --walk       # single sequential process
```

| Flag | Description |
|------|-------------|
| `--isolated` | Each checkpoint runs in its own fresh subprocess (default). Each number is independent. |
| `--walk` | One subprocess walks through all checkpoints in order. Each reading includes all prior state. |

Checkpoints measured:

| Checkpoint | What has happened |
|-----------|-------------------|
| `baseline` | Python interpreter only; no cuga imports |
| `import_cuga` | `import cuga` complete |
| `import_sdk` | `from cuga import CugaAgent` complete |
| `constructed` | `CugaAgent(tools=[echo])` instantiated |
| `converged` | After one `agent.invoke()` with a fake model |
| `growth` | After 10 `agent.invoke()` calls |

Prints one JSON object as the last line of stdout:

```json
{
  "surface": "sdk",
  "checkpoints": [
    {"label": "baseline",     "rss_mb": 42.1, "uss_mb": 38.5, "peak_rss_mb": 42.1, "modules": 68, ...},
    {"label": "import_cuga",  "rss_mb": 185.3, "uss_mb": 172.0, "peak_rss_mb": 185.3, "modules": 412, ...},
    ...
  ]
}
```

---

### `measure_server_memory.py` — server single-process checkpoints

Launches the CUGA uvicorn server in a subprocess, waits for readiness, and
samples the server process RSS/USS at each checkpoint.

```bash
uv run python src/system_tests/profiling/memory/measure_server_memory.py
```

No flags.  Prints one JSON object as the last line of stdout.

Checkpoints measured:

| Checkpoint | What has happened |
|-----------|-------------------|
| `uvicorn_baseline` | Trivial `FastAPI()` app (no CUGA code); isolates uvicorn's own cost |
| `cold` | `/health/readiness` returns `true`; no requests served yet |
| `converged` | After one `GET /api/commands` (forces lazy import of slash-commands subsystem) |
| `growth` | After 21 `GET /api/commands` requests (1 converged + 20 growth calls) |

No LLM credentials are required — `GET /api/commands` returns 200 regardless
of whether a `.cuga` skills folder exists.

---

### `measure_tree_memory.py` — full process-tree footprint

Launches `cuga start demo` and walks the **entire process tree** with
`memlib.tree_sample` once the demo server reports ready.  Tears down the whole
process group on exit.

```bash
uv run python src/system_tests/profiling/memory/measure_tree_memory.py
uv run python src/system_tests/profiling/memory/measure_tree_memory.py --flags "--crm --email"
```

| Flag | Description |
|------|-------------|
| `--flags` | Extra flags forwarded to `cuga start demo` (quoted string) |

Prints one JSON object as the last line of stdout with per-process records and
a tree total using USS (macOS) or PSS (Linux).

---

### `import_memory_breakdown.py` — per-import RSS delta (Lens A detail)

Ranks every imported module by the **RSS delta** it caused — the memory
analogue of `-X importtime`.  A custom meta-path hook wraps each module's
`exec_module` call with before/after RSS snapshots.

```bash
# Default: profile 'import cuga.sdk', show top 30
uv run python src/system_tests/profiling/memory/import_memory_breakdown.py

# Profile the server entrypoint instead
uv run python src/system_tests/profiling/memory/import_memory_breakdown.py \
    --stmt "import cuga.backend.server.main"

# Show more entries
uv run python src/system_tests/profiling/memory/import_memory_breakdown.py --top 50

# Add cross-check column: re-run each top module in total isolation
uv run python src/system_tests/profiling/memory/import_memory_breakdown.py --cross-check
```

| Flag | Default | Description |
|------|---------|-------------|
| `--stmt` | `"import cuga.sdk"` | Python statement to profile |
| `--top` | `30` | Number of modules to display (sorted by `self_mb` descending) |
| `--cross-check` | off | Re-run each top module in isolation; adds `cross_mb` column |

Example output (stderr table):

```
----------------------------------------------
 #  self_mb  cum_mb  module
----------------------------------------------
 1     48.3   102.1  torch
 2     22.7    31.4  langchain_core
...
```

The JSON result (last line of stdout) contains the full ranked list.

---

### `tracemalloc_report.py` — Python-heap attribution (Lens B)

Uses Python's `tracemalloc` to attribute **Python-heap** allocations to source
files and modules, across the same SDK lifecycle checkpoints.

```bash
uv run python src/system_tests/profiling/memory/tracemalloc_report.py
uv run python src/system_tests/profiling/memory/tracemalloc_report.py \
    --start-checkpoint import_sdk --top 10
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start-checkpoint` | `baseline` | First checkpoint from which diffs are computed |
| `--top` | `25` | Top N entries per checkpoint by allocation size |

> **Warning:** tracemalloc captures **Python heap only**.  C extensions
> (torch, onnxruntime, sqlite-vec, fastembed) are NOT included.  Do not read
> `tracemalloc_total_mb` as process memory.  The `native_gap_mb` field in each
> checkpoint record shows the unmeasured native allocation:
> `native_gap_mb = rss_mb_at_checkpoint − tracemalloc_total_mb`.

> **Note:** tracemalloc inflates RSS by ~10–20% due to its allocation tracking
> overhead.  Run this separately from RSS-only measurements; do not mix the
> numbers.

Prints one JSON object as the last line of stdout:

```json
{
  "surface": "tracemalloc",
  "warning": "...",
  "start_checkpoint": "baseline",
  "top_n": 25,
  "checkpoints": [
    {
      "checkpoint": "import_cuga",
      "tracemalloc_total_mb": 34.2,
      "rss_mb_at_checkpoint": 185.3,
      "native_gap_mb": 151.1,
      "top_by_lineno": [...],
      "top_by_module": [{"module": "langchain_core", "size_mb": 8.4}, ...]
    },
    ...
  ]
}
```

---

### `memray_profile.sh` — native memory flamegraph (Lens C)

Wraps `memray run [--native]` to produce allocation flamegraphs for both the
SDK worker and the uvicorn server target.

```bash
bash src/system_tests/profiling/memory/memray_profile.sh
bash src/system_tests/profiling/memory/memray_profile.sh --sdk-only
bash src/system_tests/profiling/memory/memray_profile.sh --server-only
```

| Flag | Description |
|------|-------------|
| `--sdk-only` | Profile the SDK worker only |
| `--server-only` | Profile the uvicorn server only |

Output files written to `results/` with timestamped names:

```
results/memray-sdk-<ts>.bin        raw allocation trace
results/memray-sdk-<ts>.html       flamegraph (open in browser)
results/memray-server-<ts>.bin
results/memray-server-<ts>.html
```

If `memray` is not installed the script prints an install hint and exits 0 —
the test suite stays green without the optional dependency.

Install memray:

```bash
uv add --dev memray          # add to project
# or
pip install memray           # global / venv
```

---

### `aggregate_memory_results.py` — statistics writer

Called automatically by `run_memory_bench.sh`.  Can also be invoked directly.

```bash
uv run python src/system_tests/profiling/memory/aggregate_memory_results.py \
    results/run_sdk_*.json results/run_server_*.json

uv run python src/system_tests/profiling/memory/aggregate_memory_results.py \
    --keep-first results/run_*.json
```

| Flag | Description |
|------|-------------|
| `JSON_FILE …` | Per-run JSON files produced by any measurement script |
| `--keep-first` | Include run #1 in statistics (default: discard) |
| `--results-dir DIR` | Output directory (default: `results`) |

---

## Metric Definitions

| Metric | Source | Description |
|--------|--------|-------------|
| `rss_mb` | psutil | **Resident Set Size** — total physical RAM pages mapped to the process (includes shared library pages counted once per process). Do not sum RSS across processes. |
| `uss_mb` | psutil | **Unique Set Size** — physical pages that belong *exclusively* to this process. Safe to sum across a process tree for an approximate total. Not available on all platforms. |
| `pss_mb` | psutil | **Proportional Set Size** — each shared page counted as `1/N` where N is the number of processes sharing it. The most accurate single-tree total. Linux only. |
| `peak_rss_mb` | `resource.getrusage` | High-water-mark RSS since process start, as reported by the kernel. Already normalised to MB by memlib (see Caveats). |
| `modules` | `len(sys.modules)` | Number of Python modules loaded at the time of sampling. Useful for correlating memory growth with import activity. |
| `tracemalloc_total_mb` | tracemalloc | Total Python-heap bytes tracked by `tracemalloc`. **Python heap only** — does not include C extensions. |
| `native_gap_mb` | computed | `rss_mb − tracemalloc_total_mb`. The portion of RSS that tracemalloc cannot see (C extensions, native allocators). |

### Checkpoint definitions

- **`baseline`** — Python interpreter only; no cuga code imported.
- **`cold`** (server) — server is ready to accept requests; no requests served.
  This is the highest-value checkpoint for fleet sizing: it reflects the memory
  cost at steady-state for an idle instance.
- **`converged`** — after the first real request.  Lazy imports deferred by
  FastAPI routers and `@lru_cache` resolvers have been triggered.  This is the
  accurate post-warmup footprint, not `cold`.
- **`growth`** — after repeated requests.  Measures allocations that accumulate
  with load (caches, connection pools, compiled regex).

---

## How to Read the Output

### JSON schema (per-run files)

```jsonc
{
  "surface": "sdk",            // "sdk" | "server" | "tree" | "tracemalloc"
  "checkpoints": [
    {
      "label": "baseline",     // checkpoint name
      "rss_mb": 42.1,          // resident set size in MB
      "uss_mb": 38.5,          // unique set size in MB (null if unavailable)
      "peak_rss_mb": 42.1,     // high-water-mark RSS in MB
      "modules": 68,           // len(sys.modules) at sample time
      "platform": {"system": "Darwin", "machine": "arm64", "python": "3.12.0"},
      "config": {"llm_platform": "openai", "policy_enabled": false, ...},
      "ts": "2026-07-14T12:00:00Z"
    },
    ...
  ]
}
```

### JSON schema (aggregate results file)

```jsonc
{
  "timestamp": "2026-07-14T12-00-00Z",
  "n_runs": 5,
  "platform": {...},
  "config": {...},
  "stats": {
    "sdk/baseline": {
      "rss_mb":      {"median": 42.1, "min": 41.8, "max": 42.4},
      "uss_mb":      {"median": 38.5, "min": 38.1, "max": 39.0},
      "peak_rss_mb": {"median": 42.1, "min": 41.8, "max": 42.4},
      "modules":     {"median": 68,   "min": 68,   "max": 68}
    },
    ...
  },
  "raw_files": [...]
}
```

### Summary table columns

```
  Surface/Checkpoint             Metric        Median        Min        Max
  ──────────────────────────────────────────────────────────────────────────
  sdk/baseline                   rss_mb        42.1 MB    41.8 MB    42.4 MB
  sdk/baseline                   uss_mb        38.5 MB    38.1 MB    39.0 MB
  sdk/import_cuga                rss_mb       185.3 MB   183.1 MB   187.2 MB
  ...
```

**`Median`** is the median across N−1 runs (run #1 discarded by default).
Use `--keep-first` to include all runs.

---

## Caveats

**Read this section before drawing conclusions from any number in this harness.**

### Cold vs converged: lazy imports defer cost, they do not eliminate it

The `cold` checkpoint reflects the server's RSS immediately after readiness.
Many modules are not yet imported because FastAPI routers use lazy loading and
`@lru_cache` resolvers have not been called.  The `converged` checkpoint, taken
after the first real request, is the accurate post-warmup footprint.

Do not cite `cold` numbers as the server's memory cost.  Use `converged` for
steady-state comparisons and `cold` only to measure import/lifespan overhead.

### USS instead of PSS on macOS

`memlib.tree_sample` uses **USS** as the tree total on macOS because PSS is
not available there (it requires `/proc/<pid>/smaps`, a Linux-only interface).
USS counts only pages that belong exclusively to each process, so **shared
library pages are not counted at all**.  This means the tree total on macOS
is a **lower bound** on actual physical memory consumption — the true cost is
higher once shared pages are amortised across the tree.

On Linux, `tree_sample` uses **PSS**, which correctly amortises shared pages
and produces an accurate tree total.

### tracemalloc excludes native C extensions

`tracemalloc` intercepts Python's memory allocator (`PyMem_*` calls).
Allocations made directly by C extensions — torch, onnxruntime, sqlite-vec,
fastembed — bypass this entirely.  `tracemalloc_total_mb` will always be far
below `rss_mb` for any workload that uses these libraries.

The `native_gap_mb` field in each checkpoint record makes this visible:
`native_gap_mb = rss_mb − tracemalloc_total_mb`.  A large gap means native
extensions dominate; use Lens C (memray) to investigate those allocations.

tracemalloc is Lens B — useful for attributing **Python-heap allocations** to
specific source files and modules.  Do not use it to answer "how much RAM does
the server use?"

### Never sum RSS across processes

RSS includes shared library pages (libc, libpython, torch `.so` files) counted
**independently in every process**.  Summing RSS across a process tree produces
a grossly inflated number — often 2–5× the actual physical cost.

Always use **USS** (macOS) or **PSS** (Linux) for tree totals.
`measure_tree_memory.py` does this automatically via `memlib.tree_sample`.

### `ru_maxrss` units: macOS=bytes, Linux=kilobytes

`resource.getrusage(RUSAGE_SELF).ru_maxrss` has platform-dependent units:
- **macOS**: bytes
- **Linux**: kilobytes

Getting this wrong produces a silent 1024× error.  `memlib._peak_rss_mb()`
normalises the value to megabytes before returning it, so all `peak_rss_mb`
fields in this harness are always in MB regardless of platform.

### N=5 median; discard run #1

The bench harness defaults to 5 runs and discards run #1 before computing
statistics.  Run #1 is noisier because the OS page cache is cold, Python's
import machinery has not warmed up, and any JIT-compilation costs are
front-loaded.  Runs #2–5 reflect the footprint a restarted production instance
would actually observe after its first start.

Pass `--keep-first` if you specifically want to measure cold-cache startup cost.

### memray `--native` on macOS/arm64

memray with `--native` instrumentation can produce HTML flamegraphs on
macOS/arm64, but C-extension frames from dynamically-linked libraries may be
missing or truncated — particularly for torch and onnxruntime.  The flamegraph
is still useful for Python-level allocation attribution.  For complete native
frames, run on Linux x86-64 where DWARF unwinding is fully supported.

---

## Quick Reference

| Question | Script |
|----------|--------|
| How much RAM does `import cuga.sdk` use? | `measure_sdk_memory.py` → `import_sdk` checkpoint |
| How much RAM does a constructed `CugaAgent` use? | `measure_sdk_memory.py` → `constructed` checkpoint |
| How much RAM does the server use at idle? | `measure_server_memory.py` → `cold` checkpoint |
| How much RAM does the server use after warmup? | `measure_server_memory.py` → `converged` checkpoint |
| How much RAM does the full `cuga start demo` tree use? | `measure_tree_memory.py` |
| Which Python packages allocate the most RAM at import? | `import_memory_breakdown.py` |
| Which Python source files allocate the most heap memory? | `tracemalloc_report.py` |
| What are the native allocation hotspots? | `memray_profile.sh` |
| Statistical summary across N runs | `run_memory_bench.sh` |

---

## Environment Variables

All measurement scripts inherit the calling shell's environment.

`measure_server_memory.py` and `measure_tree_memory.py` launch real server
processes.  Any environment variables the CUGA server requires (API keys, model
config, etc.) must be set before running:

```bash
export SOME_API_KEY=...
bash src/system_tests/profiling/memory/run_memory_bench.sh
```

Without required variables the server may not reach `"ready": true`.  The
`cold` checkpoint will still record a valid RSS number (reflecting import +
lifespan overhead), but the `converged` and `growth` checkpoints depend on the
server processing requests successfully.

---

## Results Files

Results are written to `results/memory-<YYYY-MM-DDTHH-MM-SSZ>.json` and are
**git-ignored** (a `.gitkeep` keeps the directory tracked).

Per-run raw files are also written to `results/run_<surface>_<N>_<ts>.json`
during the bench run and kept for debugging.  They are also git-ignored.
