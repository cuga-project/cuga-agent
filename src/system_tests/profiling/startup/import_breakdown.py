"""
import_breakdown.py — rank the heaviest imports by self time.

Usage:
    uv run python src/system_tests/profiling/startup/import_breakdown.py
    uv run python src/system_tests/profiling/startup/import_breakdown.py --stmt "import cuga.backend.server.main"
    uv run python src/system_tests/profiling/startup/import_breakdown.py --top 50

This script does NOT import cuga itself; it delegates to a subprocess so that
the profiling measurement is never polluted by the parent process's own imports.
"""

import argparse
import re
import subprocess
import sys

# Pattern for importtime stderr lines:
#   import time:  <self_us> | <cumulative_us> | <module>
# Indentation before the module name is optional (shows nesting level).
_IMPORTTIME_RE = re.compile(r"^import time:\s+(\d+)\s*\|\s*(\d+)\s*\|\s*(.+)$")


def run_importtime(stmt: str) -> str:
    """Run *stmt* in a subprocess with -X importtime and return stderr."""
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", stmt],
        capture_output=True,
        text=True,
    )
    return result.stderr


def parse_importtime(output: str) -> list[tuple[int, int, str]]:
    """Parse importtime stderr into a list of (self_us, cumulative_us, module)."""
    rows: list[tuple[int, int, str]] = []
    for line in output.splitlines():
        m = _IMPORTTIME_RE.match(line)
        if m:
            self_us = int(m.group(1))
            cum_us = int(m.group(2))
            module = m.group(3).strip()
            rows.append((self_us, cum_us, module))
    return rows


def print_table(rows: list[tuple[int, int, str]], top: int) -> None:
    """Print *rows* sorted descending by self_us, limited to *top* entries."""
    sorted_rows = sorted(rows, key=lambda r: r[0], reverse=True)[:top]

    if not sorted_rows:
        print("No importtime data found.")
        return

    # Column widths
    w_rank = len(str(top))
    w_self = max(len("self_us"), max(len(str(r[0])) for r in sorted_rows))
    w_cum = max(len("cum_us"), max(len(str(r[1])) for r in sorted_rows))
    w_mod = max(len("module"), max(len(r[2]) for r in sorted_rows))

    header = f"{'#':>{w_rank}}  {'self_us':>{w_self}}  {'cum_us':>{w_cum}}  {'module':<{w_mod}}"
    separator = "-" * len(header)

    print(separator)
    print(header)
    print(separator)

    for i, (self_us, cum_us, module) in enumerate(sorted_rows, start=1):
        print(f"{i:>{w_rank}}  {self_us:>{w_self}}  {cum_us:>{w_cum}}  {module:<{w_mod}}")

    print(separator)
    print(f"Showing top {len(sorted_rows)} of {len(rows)} modules by self_us")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank the heaviest imports by self time using -X importtime."
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
    args = parser.parse_args()

    print(f"Profiling: {args.stmt!r}")
    print("Running subprocess with -X importtime …")

    stderr = run_importtime(args.stmt)
    rows = parse_importtime(stderr)
    print_table(rows, args.top)


if __name__ == "__main__":
    main()
