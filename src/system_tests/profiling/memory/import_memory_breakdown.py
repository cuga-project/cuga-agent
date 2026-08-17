"""
import_memory_breakdown.py — rank the heaviest imports by RSS delta (memory analogue of -X importtime).

Usage:
    uv run python src/system_tests/profiling/memory/import_memory_breakdown.py
    uv run python src/system_tests/profiling/memory/import_memory_breakdown.py --stmt "import cuga.backend.server.main"
    uv run python src/system_tests/profiling/memory/import_memory_breakdown.py --top 50
    uv run python src/system_tests/profiling/memory/import_memory_breakdown.py --cross-check

This script does NOT import cuga itself; it delegates to a subprocess so that
the profiling measurement is never polluted by the parent process's own imports.

Stdout contract: exactly one JSON object as the last line of stdout.
All logs/progress go to stderr.
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import subprocess
import sys

WORKER_TIMEOUT_S = 300.0

# ---------------------------------------------------------------------------
# Subprocess payload — injected as a -c script string
# ---------------------------------------------------------------------------

# The worker script reads the statement from sys.argv[1] to avoid
# any string-formatting issues with curly braces in the template.
_WORKER_SCRIPT = """\
import gc
import json
import sys

import psutil

# Ensure tracemalloc is NOT active during measurements
try:
    import tracemalloc
    if tracemalloc.is_tracing():
        tracemalloc.stop()
except ImportError:
    pass


class _MemoryTrackingLoader:
    def __init__(self, real_loader, module_name, tracker):
        self._real = real_loader
        self._name = module_name
        self._tracker = tracker

    def __getattr__(self, name):
        return getattr(self._real, name)

    def exec_module(self, module):
        gc.collect()
        before_rss = psutil.Process().memory_info().rss
        self._real.exec_module(module)
        gc.collect()
        after_rss = psutil.Process().memory_info().rss
        delta_mb = max(0.0, (after_rss - before_rss) / 1024 / 1024)
        self._tracker[self._name] = self._tracker.get(self._name, 0.0) + delta_mb


class _MemoryTrackingFinder:
    def __init__(self, real_finder, tracker):
        self._real = real_finder
        self._tracker = tracker

    def __getattr__(self, name):
        return getattr(self._real, name)

    def find_spec(self, fullname, path, target=None):
        spec = self._real.find_spec(fullname, path, target)
        if spec is not None and spec.loader is not None:
            spec.loader = _MemoryTrackingLoader(spec.loader, fullname, self._tracker)
        return spec

    def find_module(self, fullname, path=None):
        if hasattr(self._real, "find_module"):
            return self._real.find_module(fullname, path)
        return None


def run_once(stmt):
    tracker = {}
    wrapped = [_MemoryTrackingFinder(f, tracker) for f in sys.meta_path]
    sys.meta_path[:] = wrapped
    exec(stmt)  # noqa: S102
    sys.meta_path[:] = [f._real for f in sys.meta_path if hasattr(f, "_real")]
    return tracker


stmt = sys.argv[1]
tracker = run_once(stmt)
print(json.dumps(tracker))
"""

# ---------------------------------------------------------------------------
# Parent-side helpers
# ---------------------------------------------------------------------------

# Reads statement from sys.argv[1]
_BASELINE_RSS_SCRIPT = """\
import gc, json, sys, psutil
gc.collect()
b = psutil.Process().memory_info().rss
exec(sys.argv[1])
gc.collect()
a = psutil.Process().memory_info().rss
print(json.dumps({"delta_mb": max(0.0, (a - b) / 1024 / 1024)}))
"""


def _run_worker(stmt: str) -> dict[str, float]:
    """Run the memory-tracking worker subprocess and return {module: self_mb}."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _WORKER_SCRIPT, stmt],
            capture_output=True,
            text=True,
            timeout=WORKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Worker subprocess timed out after {WORKER_TIMEOUT_S}s") from exc
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(f"Worker subprocess failed with exit code {result.returncode}")

    stdout_lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    if not stdout_lines:
        sys.exit("Worker subprocess produced no output")

    return json.loads(stdout_lines[-1])


def _cross_check_module(mod_name: str) -> float | None:
    """Run `python -c "import <mod>"` standalone and return RSS delta in MB, or None on failure."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _BASELINE_RSS_SCRIPT, f"import {mod_name}"],
            capture_output=True,
            text=True,
            timeout=WORKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    try:
        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        return json.loads(lines[-1])["delta_mb"]
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def _compute_cumulative(self_mb: dict[str, float]) -> dict[str, float]:
    """Compute cumulative MB by walking sys.modules parent–child relationships.

    Receives the *complete* unfiltered self_mb mapping so that intermediate
    packages excluded by MIN_SELF_MB filtering do not break the parent chain.
    A module is a child of its immediate dotted-name parent; contributions are
    walked from deepest to shallowest so each parent accumulates the full
    subtree exactly once.
    cumulative[parent] = self(parent) + sum of self() for all direct/indirect children.
    """
    mods = list(self_mb.keys())
    # Sort longest first so children are processed before parents.
    mods_sorted = sorted(mods, key=lambda m: -len(m))

    cumulative: dict[str, float] = {m: self_mb[m] for m in mods}

    for mod in mods_sorted:
        parts = mod.rsplit(".", 1)
        if len(parts) == 2:
            parent = parts[0]
            # Walk up the chain until we find a registered ancestor.
            while parent and parent not in cumulative:
                parts2 = parent.rsplit(".", 1)
                parent = parts2[0] if len(parts2) == 2 else ""
            if parent:
                cumulative[parent] = cumulative[parent] + cumulative[mod]

    return cumulative


def _print_table(rows: list[dict], top: int) -> None:
    """Print table sorted by self_mb descending — mirrors import_breakdown.py style."""
    sorted_rows = sorted(rows, key=lambda r: -r["self_mb"])[:top]

    if not sorted_rows:
        print("No memory data found.", file=sys.stderr)
        return

    has_cross = any(r.get("cross_check_mb") is not None for r in sorted_rows)

    w_rank = len(str(top))
    w_self = max(len("self_mb"), max(len(f"{r['self_mb']:.1f}") for r in sorted_rows))
    w_cum = max(len("cum_mb"), max(len(f"{r['cumulative_mb']:.1f}") for r in sorted_rows))
    w_mod = max(len("module"), max(len(r["module"]) for r in sorted_rows))

    if has_cross:
        w_cross = max(
            len("cross_mb"),
            max(
                len(f"{r['cross_check_mb']:.1f}") if r.get("cross_check_mb") is not None else len("n/a")
                for r in sorted_rows
            ),
        )
        header = (
            f"{'#':>{w_rank}}  {'self_mb':>{w_self}}  {'cum_mb':>{w_cum}}"
            f"  {'cross_mb':>{w_cross}}  {'module':<{w_mod}}"
        )
    else:
        header = f"{'#':>{w_rank}}  {'self_mb':>{w_self}}  {'cum_mb':>{w_cum}}  {'module':<{w_mod}}"

    separator = "-" * len(header)
    print(separator, file=sys.stderr)
    print(header, file=sys.stderr)
    print(separator, file=sys.stderr)

    for i, row in enumerate(sorted_rows, start=1):
        self_s = f"{row['self_mb']:.1f}"
        cum_s = f"{row['cumulative_mb']:.1f}"
        if has_cross:
            cross_val = row.get("cross_check_mb")
            cross_s = f"{cross_val:.1f}" if cross_val is not None else "n/a"
            print(
                f"{i:>{w_rank}}  {self_s:>{w_self}}  {cum_s:>{w_cum}}"
                f"  {cross_s:>{w_cross}}  {row['module']:<{w_mod}}",
                file=sys.stderr,
            )
        else:
            print(
                f"{i:>{w_rank}}  {self_s:>{w_self}}  {cum_s:>{w_cum}}  {row['module']:<{w_mod}}",
                file=sys.stderr,
            )

    print(separator, file=sys.stderr)
    print(
        f"Showing top {len(sorted_rows)} of {len(rows)} modules by self_mb (≥1 MB)",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

MIN_SELF_MB = 1.0  # filter threshold — modules below this are noise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank heaviest imports by RSS delta (memory analogue of -X importtime)."
    )
    parser.add_argument(
        "--stmt",
        default="import cuga.sdk",
        help='Python statement to profile (default: "import cuga.sdk")',
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Number of top entries to display (default: 30)",
    )
    parser.add_argument(
        "--cross-check",
        action="store_true",
        help="For the top N modules run a standalone import subprocess to cross-validate.",
    )
    parser.add_argument(
        "--cross-check-n",
        type=int,
        default=10,
        help="How many top modules to cross-check (default: 10, used with --cross-check)",
    )
    args = parser.parse_args()

    print(f"Profiling: {args.stmt!r}", file=sys.stderr)
    print("Running memory-tracking subprocess (3 runs, median) …", file=sys.stderr)

    # Run three separate subprocesses for independence; take median per module.
    run_results: list[dict[str, float]] = []
    for run_idx in range(3):
        print(f"  run {run_idx + 1}/3 …", file=sys.stderr)
        run_results.append(_run_worker(args.stmt))

    # Collect all observed modules.
    all_mods: set[str] = set()
    for run in run_results:
        all_mods.update(run.keys())

    # Compute per-module median across runs where the module actually appeared.
    # Zero-padding absent runs would bias the median downward and can suppress
    # modules that only appeared in 1 or 2 of the 3 runs.
    self_mb: dict[str, float] = {}
    for mod in all_mods:
        present_vals = [run[mod] for run in run_results if mod in run]
        self_mb[mod] = statistics.median(present_vals) if present_vals else 0.0

    # Compute roll-ups on the full unfiltered map so parent chains are intact.
    cumulative_mb = _compute_cumulative(self_mb)

    # Filter to modules ≥ MIN_SELF_MB for display only.
    filtered = {m: v for m, v in self_mb.items() if v >= MIN_SELF_MB}

    # Build rows.
    rows: list[dict] = [
        {
            "module": mod,
            "self_mb": filtered[mod],
            "cumulative_mb": cumulative_mb.get(mod, filtered[mod]),
            "cross_check_mb": None,
        }
        for mod in filtered
    ]

    # Cross-check top N modules if requested.
    if args.cross_check:
        top_mods = sorted(rows, key=lambda r: -r["self_mb"])[: args.cross_check_n]
        print(
            f"Cross-checking top {len(top_mods)} modules …",
            file=sys.stderr,
        )
        cross_map: dict[str, float] = {}
        for row in top_mods:
            mod = row["module"]
            print(f"  cross-check: {mod} …", file=sys.stderr)
            cross_map[mod] = _cross_check_module(mod)

        for row in rows:
            if row["module"] in cross_map:
                row["cross_check_mb"] = cross_map[row["module"]]

    # Print human-readable table to stderr.
    _print_table(rows, args.top)

    # Compute summary stats.
    total_self = sum(r["self_mb"] for r in rows)

    # Stdout contract: one JSON object as the last line.
    output = {
        "modules": [
            {
                "module": r["module"],
                "self_mb": round(r["self_mb"], 3),
                "cumulative_mb": round(r["cumulative_mb"], 3),
                "cross_check_mb": round(r["cross_check_mb"], 3) if r["cross_check_mb"] is not None else None,
            }
            for r in sorted(rows, key=lambda r: -r["self_mb"])
        ],
        "total_profiled_mb": round(total_self, 2),
        "stmt": args.stmt,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
