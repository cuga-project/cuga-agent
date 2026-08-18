"""
aggregate_memory_results.py — aggregate memory benchmark runs into statistics.

Reads per-run JSON files passed as CLI arguments, computes median/min/max per
(surface, checkpoint) tuple, writes a timestamped results file, and prints a
human-readable summary table.

Each JSON file may contain either:
  - A flat record:  {"surface": "sdk", "checkpoint": "...", "rss_mb": ...}
  - A multi-checkpoint record: {"surface": "sdk", "checkpoints": [...]}

Usage (called by run_memory_bench.sh):
    uv run python aggregate_memory_results.py results/run_*.json
    uv run python aggregate_memory_results.py --keep-first results/run_*.json
"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


def _median(values: list[float]) -> float:
    return statistics.median(values)


def _load_records(path: Path) -> list[dict]:
    """Load one JSON file and return a flat list of checkpoint records."""
    raw = json.loads(path.read_text())
    # Multi-checkpoint shape: {"surface": "sdk", "checkpoints": [...]}
    if "checkpoints" in raw and isinstance(raw["checkpoints"], list):
        surface = raw.get("surface", "unknown")
        records = []
        for cp in raw["checkpoints"]:
            rec = dict(cp)
            rec.setdefault("surface", surface)
            records.append(rec)
        return records
    # Flat shape: {"surface": "sdk", "checkpoint": "...", "rss_mb": ...}
    return [raw]


def aggregate(
    runs: list[list[dict]],
) -> dict[tuple[str, str], dict[str, dict]]:
    """Compute median/min/max per (surface, checkpoint) across N runs.

    Returns:
        {(surface, checkpoint): {metric: {median, min, max}}}
    """
    # Group values by (surface, checkpoint) -> metric -> [values]
    grouped: dict[tuple[str, str], dict[str, list[float]]] = {}

    metrics = ["rss_mb", "uss_mb", "peak_rss_mb", "modules"]

    for run_records in runs:
        for rec in run_records:
            surface = rec.get("surface", "unknown")
            checkpoint = rec.get("checkpoint") or rec.get("label", "unknown")
            key = (surface, checkpoint)
            if key not in grouped:
                grouped[key] = {m: [] for m in metrics}
            for m in metrics:
                val = rec.get(m)
                if val is not None:
                    grouped[key][m].append(float(val))

    result: dict[tuple[str, str], dict[str, dict]] = {}
    for key, metric_values in grouped.items():
        result[key] = {}
        for m, vals in metric_values.items():
            if not vals:
                result[key][m] = {"median": None, "min": None, "max": None}
            else:
                result[key][m] = {
                    "median": _median(vals),
                    "min": min(vals),
                    "max": max(vals),
                }
    return result


def _platform_block(runs: list[list[dict]]) -> dict:
    """Extract platform/config from the first available record."""
    for run_records in runs:
        for rec in run_records:
            info: dict = {}
            if "platform" in rec:
                info["platform"] = rec["platform"]
            if "config" in rec:
                info["config"] = rec["config"]
            if info:
                return info
    return {}


def print_table(
    stats: dict[tuple[str, str], dict[str, dict]],
    meta: dict,
    n_runs: int,
) -> None:
    """Print a readable summary table to stdout."""
    col_w = 10
    header = (
        f"{'Surface/Checkpoint':<30} {'Metric':<12} {'Median':>{col_w}} {'Min':>{col_w}} {'Max':>{col_w}}"
    )
    sep = "-" * len(header)
    bar = "=" * len(header)

    print()
    print(bar)
    print("  CUGA Memory Benchmark Summary")
    print(bar)

    # Platform/config block
    platform = meta.get("platform", {})
    config = meta.get("config", {})
    if platform:
        print(
            f"  Platform : {platform.get('system', '?')} "
            f"{platform.get('machine', '?')}  "
            f"Python {platform.get('python', '?')}"
        )
    if config:
        print(
            f"  Config   : llm={config.get('llm_platform', '?')}  "
            f"policy={config.get('policy_enabled', '?')}  "
            f"knowledge={config.get('knowledge_enabled', '?')}  "
            f"malloc={config.get('python_malloc', '') or 'default'}"
        )
    print(f"  Runs     : {n_runs} (run #1 discarded unless --keep-first)")
    print(bar)
    print(header)
    print(sep)

    def _fmt_mb(v) -> str:
        if v is None:
            return "n/a"
        return f"{v:.1f} MB"

    def _fmt_modules(v) -> str:
        if v is None:
            return "n/a"
        return f"{v:.0f}"

    display_metrics = [
        ("rss_mb", _fmt_mb),
        ("uss_mb", _fmt_mb),
        ("peak_rss_mb", _fmt_mb),
        ("modules", _fmt_modules),
    ]

    prev_surface = None
    for surface, checkpoint in sorted(stats.keys()):
        if prev_surface is not None and surface != prev_surface:
            print(sep)
        prev_surface = surface
        label = f"{surface}/{checkpoint}"
        for metric, fmt in display_metrics:
            s = stats[(surface, checkpoint)].get(metric, {})
            median_v = s.get("median")
            min_v = s.get("min")
            max_v = s.get("max")
            if median_v is None and min_v is None and max_v is None:
                continue
            print(
                f"  {label:<28} {metric:<12}"
                f" {fmt(median_v):>{col_w}} {fmt(min_v):>{col_w}} {fmt(max_v):>{col_w}}"
            )
            label = ""  # only show label on first metric row

    print(bar)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate memory benchmark runs.")
    parser.add_argument(
        "files",
        nargs="+",
        metavar="JSON_FILE",
        help="Per-run JSON files produced by measurement scripts.",
    )
    parser.add_argument(
        "--keep-first",
        action="store_true",
        help="Keep run #1 (default: discard to skip cold-cache noise).",
    )
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory to write the timestamped JSON file.",
    )
    args = parser.parse_args()

    # Load each file, retaining its source path so we can group by run index.
    import re as _re

    loaded: list[tuple[str, list[dict]]] = []
    for fpath in args.files:
        try:
            records = _load_records(Path(fpath))
            loaded.append((fpath, records))
        except Exception as exc:
            print(f"Warning: could not load {fpath}: {exc}", file=sys.stderr)

    if not args.keep_first and loaded:
        # Filenames follow run-<script>-<i>-<ts>.json.  Extract the numeric run
        # index (third dash-separated token) and drop every file whose index
        # equals the minimum, i.e. run #1.
        def _run_idx(fpath: str) -> int:
            m = _re.search(r"-(\d+)-\d{8}T", Path(fpath).name)
            return int(m.group(1)) if m else 0

        min_idx = min(_run_idx(fp) for fp, _ in loaded)
        loaded = [(fp, recs) for fp, recs in loaded if _run_idx(fp) != min_idx]

    all_runs: list[list[dict]] = [recs for _, recs in loaded]

    if not all_runs:
        print("Error: no valid run files found.", file=sys.stderr)
        sys.exit(1)

    stats = aggregate(all_runs)
    meta = _platform_block(all_runs)

    # Distinct run indices remaining (for reporting)
    n_distinct_runs = len({_run_idx(fp) for fp, _ in loaded}) if loaded else len(all_runs)  # type: ignore[possibly-undefined]

    # Build output record
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output = {
        "timestamp": timestamp,
        "n_runs": n_distinct_runs,
        "keep_first": args.keep_first,
        "stats": {f"{s}/{c}": v for (s, c), v in stats.items()},
        "meta": meta,
        "raw_runs": all_runs,
    }

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"memory-{timestamp}.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Results written to: {out_path}", file=sys.stderr)

    print_table(stats, meta, n_distinct_runs)

    print(f"results_file={out_path}")


if __name__ == "__main__":
    main()
