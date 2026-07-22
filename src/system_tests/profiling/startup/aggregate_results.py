"""
aggregate_results.py — aggregate startup benchmark runs into statistics.

Reads SDK JSON lines and server JSON lines passed as CLI arguments, computes
median / min / max per metric, writes a timestamped results file, and prints
a human-readable summary table.

Usage (called by run_startup_bench.sh):
    uv run python aggregate_results.py \
        --sdk-runs '{"import_s":1.1,...}' '{"import_s":1.0,...}' \
        --server-runs '{"server_ready_s":3.2}' '{"server_ready_s":3.0}'
"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


def _median(values: list[float]) -> float:
    return statistics.median(values)


def aggregate(runs: list[dict]) -> dict:
    """Return {metric: {median, min, max}} for a list of run dicts."""
    if not runs:
        return {}

    # Collect all numeric keys across all runs (skip None values)
    all_keys: list[str] = []
    seen: set[str] = set()
    for run in runs:
        for k in run:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    result: dict[str, dict] = {}
    for key in all_keys:
        values = [run[key] for run in runs if run.get(key) is not None]
        if not values:
            result[key] = {"median": None, "min": None, "max": None}
        else:
            result[key] = {
                "median": _median(values),
                "min": min(values),
                "max": max(values),
            }
    return result


def print_table(sdk_stats: dict, server_stats: dict) -> None:
    """Print a readable summary table to stdout."""
    # Ordered metrics to display
    sdk_metrics = ["import_s", "construct_s", "ready_s", "llm_first_call_s"]
    server_metrics = ["server_ready_s"]

    col_w = 12
    header = f"{'Metric':<22} {'Median':>{col_w}} {'Min':>{col_w}} {'Max':>{col_w}}"
    sep = "-" * len(header)

    print()
    print("=" * len(header))
    print("  CUGA Startup Benchmark Summary")
    print("=" * len(header))
    print(header)
    print(sep)

    def _fmt(v) -> str:
        if v is None:
            return "n/a"
        return f"{v:.4f}s"

    printed_any_sdk = False
    for metric in sdk_metrics:
        if metric in sdk_stats:
            s = sdk_stats[metric]
            print(
                f"  {metric:<20} {_fmt(s['median']):>{col_w}} {_fmt(s['min']):>{col_w}} {_fmt(s['max']):>{col_w}}"
            )
            printed_any_sdk = True

    if printed_any_sdk:
        print(sep)

    for metric in server_metrics:
        if metric in server_stats:
            s = server_stats[metric]
            print(
                f"  {metric:<20} {_fmt(s['median']):>{col_w}} {_fmt(s['min']):>{col_w}} {_fmt(s['max']):>{col_w}}"
            )

    print("=" * len(header))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate startup benchmark runs.")
    parser.add_argument(
        "--sdk-runs",
        nargs="+",
        metavar="JSON",
        default=[],
        help="JSON strings from each measure_sdk_startup.py run.",
    )
    parser.add_argument(
        "--server-runs",
        nargs="+",
        metavar="JSON",
        default=[],
        help="JSON strings from each measure_server_startup.py run.",
    )
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory to write the timestamped JSON file.",
    )
    args = parser.parse_args()

    sdk_runs = [json.loads(s) for s in args.sdk_runs]
    server_runs = [json.loads(s) for s in args.server_runs]

    sdk_stats = aggregate(sdk_runs)
    server_stats = aggregate(server_runs)

    # Build the full output record
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output = {
        "timestamp": timestamp,
        "sdk_runs": len(sdk_runs),
        "server_runs": len(server_runs),
        "sdk": sdk_stats,
        "server": server_stats,
        "raw": {
            "sdk_runs": sdk_runs,
            "server_runs": server_runs,
        },
    }

    # Write timestamped results file
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"startup-{timestamp}.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Results written to: {out_path}", file=sys.stderr)

    # Print human-readable summary
    print_table(sdk_stats, server_stats)

    # Also echo the output path to stdout so shell can capture it if needed
    print(f"results_file={out_path}")


if __name__ == "__main__":
    main()
