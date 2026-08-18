#!/usr/bin/env python3
"""Compare a CodeQL SARIF run against the alerts a branch claims to close.

Two independent checks, because either one alone is misleading:

* **Expected-closed** — every ``<rule-id>\\t<path>`` row in the manifest must
  produce no result. This is the "did the fix work" half.
* **No new alerts** — with ``--baseline-sarif``, any ``(rule, path)`` pair not
  present in the baseline fails the run. This is the "did the fix just move the
  problem" half, and it is the one that catches a sanitizer that quietly routes
  the tainted value to a different sink.

Rows match on rule id and file, never line number: the line moves the moment the
file is edited, so pinning it would make every run a false pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load_results(sarif_path: Path) -> dict[tuple[str, str], list[int]]:
    """Map (rule id, file path) -> the lines it fired on."""
    data = json.loads(sarif_path.read_text())
    found: dict[tuple[str, str], list[int]] = defaultdict(list)
    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule = result.get("ruleId", "")
            for location in result.get("locations", []):
                phys = location.get("physicalLocation", {})
                path = phys.get("artifactLocation", {}).get("uri", "")
                line = phys.get("region", {}).get("startLine", 0)
                found[(rule, path)].append(line)
    return found


def load_manifest(path: Path) -> list[tuple[str, str]]:
    rows = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in line.split("\t") if p.strip()]
        if len(parts) != 2:
            raise SystemExit(f"malformed manifest row (need rule<TAB>path): {raw!r}")
        rows.append((parts[0].strip(), parts[1].strip()))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--sarif", required=True, type=Path)
    ap.add_argument("--baseline-sarif", type=Path)
    args = ap.parse_args()

    head = load_results(args.sarif)
    expected_closed = load_manifest(args.manifest)

    failures: list[str] = []

    print("== alerts expected to be closed ==")
    for rule, path in expected_closed:
        lines = head.get((rule, path))
        if lines:
            failures.append(f"{rule} still fires in {path} at line(s) {sorted(lines)}")
            print(f"  STILL OPEN  {rule}  {path}  lines={sorted(lines)}")
        else:
            print(f"  closed      {rule}  {path}")

    if args.baseline_sarif:
        base = load_results(args.baseline_sarif)
        new = sorted(set(head) - set(base))
        print("\n== new alerts vs baseline ==")
        if new:
            for rule, path in new:
                failures.append(f"NEW alert {rule} in {path}")
                print(f"  NEW         {rule}  {path}  lines={sorted(head[(rule, path)])}")
        else:
            print("  none")

        fixed = sorted(set(base) - set(head))
        if fixed:
            print("\n== also newly clean (informational) ==")
            for rule, path in fixed:
                print(f"  cleared     {rule}  {path}")

    print()
    if failures:
        print(f"FAIL ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS: every targeted alert is closed and no new alert appeared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
