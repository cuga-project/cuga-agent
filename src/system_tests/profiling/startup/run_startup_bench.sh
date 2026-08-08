#!/usr/bin/env bash
# run_startup_bench.sh — CUGA startup benchmark harness.
#
# Runs measure_sdk_startup.py and measure_server_startup.py N times each,
# then calls aggregate_results.py to compute median/min/max and write a
# timestamped JSON file under results/.
#
# Usage:
#   bash run_startup_bench.sh          # default 3 runs
#   bash run_startup_bench.sh 1        # 1 run (quick test)
#   bash run_startup_bench.sh --runs 5 # 5 runs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
RUNS=3

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runs)
            RUNS="$2"
            shift 2
            ;;
        --runs=*)
            RUNS="${1#--runs=}"
            shift
            ;;
        [0-9]*)
            RUNS="$1"
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--runs N | N]" >&2
            exit 1
            ;;
    esac
done

echo "========================================" >&2
echo "  CUGA Startup Benchmark  (runs=${RUNS})" >&2
echo "========================================" >&2

# ---------------------------------------------------------------------------
# Collect runs
# ---------------------------------------------------------------------------
SDK_RUNS=()
SERVER_RUNS=()

for ((i = 1; i <= RUNS; i++)); do
    echo "" >&2
    echo "--- Run ${i}/${RUNS}: SDK startup ---" >&2
    # Capture only stdout; stderr flows through to the terminal so failures are visible.
    if ! sdk_json=$(uv run python measure_sdk_startup.py | tail -n 1); then
        echo "Warning: SDK run ${i} failed — skipping" >&2
        continue
    fi
    if [[ -z "${sdk_json}" ]]; then
        echo "Warning: SDK run ${i} produced no output — skipping" >&2
        continue
    fi
    echo "  SDK result: ${sdk_json}" >&2
    SDK_RUNS+=("${sdk_json}")

    echo "" >&2
    echo "--- Run ${i}/${RUNS}: Server startup ---" >&2
    # Capture only stdout; stderr flows through to the terminal so failures are visible.
    if ! server_json=$(uv run python measure_server_startup.py | tail -n 1); then
        echo "Warning: Server run ${i} failed — skipping" >&2
        continue
    fi
    if [[ -z "${server_json}" ]]; then
        echo "Warning: Server run ${i} produced no output — skipping" >&2
        continue
    fi
    echo "  Server result: ${server_json}" >&2
    SERVER_RUNS+=("${server_json}")
done

# ---------------------------------------------------------------------------
# Build argument lists for aggregate_results.py
# ---------------------------------------------------------------------------
AGG_ARGS=(--sdk-runs)
for j in "${SDK_RUNS[@]}"; do
    AGG_ARGS+=("${j}")
done

AGG_ARGS+=(--server-runs)
for j in "${SERVER_RUNS[@]}"; do
    AGG_ARGS+=("${j}")
done

echo "" >&2
echo "--- Aggregating results ---" >&2
uv run python aggregate_results.py "${AGG_ARGS[@]}"
