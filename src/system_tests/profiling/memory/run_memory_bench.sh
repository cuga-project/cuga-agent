#!/usr/bin/env bash
# run_memory_bench.sh — CUGA memory benchmark harness.
#
# Invokes the T3/T5/T6 measurement scripts N times each (with --isolated),
# collects their JSON output into results/ with timestamps, then calls
# aggregate_memory_results.py to compute median/min/max.
#
# Missing scripts are skipped with a warning (they may not exist during
# parallel task development).
#
# Usage:
#   bash run_memory_bench.sh           # default 5 runs
#   bash run_memory_bench.sh 3         # 3 runs
#   bash run_memory_bench.sh --runs 5  # 5 runs
#   bash run_memory_bench.sh --keep-first --runs 5
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
RUNS=5
KEEP_FIRST=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runs)
            if [[ $# -lt 2 ]]; then
                echo "Error: --runs requires a value" >&2
                echo "Usage: $0 [--runs N | N] [--keep-first]" >&2
                exit 1
            fi
            RUNS="$2"
            shift 2
            ;;
        --runs=*)
            RUNS="${1#--runs=}"
            shift
            ;;
        --keep-first)
            KEEP_FIRST="--keep-first"
            shift
            ;;
        [0-9]*)
            RUNS="$1"
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--runs N | N] [--keep-first]" >&2
            exit 1
            ;;
    esac
done

if ! [[ "$RUNS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: --runs must be a positive integer, got: '$RUNS'" >&2
    echo "Usage: $0 [--runs N | N] [--keep-first]" >&2
    exit 1
fi

echo "========================================" >&2
echo "  CUGA Memory Benchmark  (runs=${RUNS})" >&2
echo "========================================" >&2

# ---------------------------------------------------------------------------
# T3/T5/T6 measurement scripts (may not all exist yet)
# ---------------------------------------------------------------------------
MEASURE_SCRIPTS=(
    "measure_sdk_memory.py"
    "measure_server_memory.py"
    "measure_tree_memory.py"
)

mkdir -p results

COLLECTED_FILES=()

for ((i = 1; i <= RUNS; i++)); do
    echo "" >&2
    echo "=== Run ${i}/${RUNS} ===" >&2

    for script in "${MEASURE_SCRIPTS[@]}"; do
        if [[ ! -f "${script}" ]]; then
            echo "Warning: ${script} not found — skipping" >&2
            continue
        fi

        echo "--- Run ${i}/${RUNS}: ${script} ---" >&2
        ts="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
        out_file="results/run-${script%.py}-${i}-${ts}.json"
        log_file="results/run-${script%.py}-${i}-${ts}.log"

        if ! uv run python "${script}" --isolated > "${out_file}" 2>"${log_file}"; then
            echo "Warning: ${script} run ${i} failed — see ${log_file}" >&2
            rm -f "${out_file}"
            continue
        fi

        if [[ ! -s "${out_file}" ]]; then
            echo "Warning: ${script} run ${i} produced no output — skipping" >&2
            rm -f "${out_file}"
            continue
        fi

        echo "  Saved: ${out_file}" >&2
        COLLECTED_FILES+=("${out_file}")
    done
done

# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------
if [[ ${#COLLECTED_FILES[@]} -eq 0 ]]; then
    echo "Error: no successful runs collected — nothing to aggregate." >&2
    exit 1
fi

echo "" >&2
echo "--- Aggregating ${#COLLECTED_FILES[@]} result file(s) ---" >&2

# shellcheck disable=SC2086
uv run python aggregate_memory_results.py ${KEEP_FIRST} "${COLLECTED_FILES[@]}"
