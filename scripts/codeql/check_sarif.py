#!/usr/bin/env python3
"""Compare a CodeQL results file against the alerts a branch claims to close.

There are two checks here, because either one on its own can mislead:

* **Expected to be closed.** Every ``<rule id>`` and ``<file>`` entry in the list
  must produce no result. This answers "did the fix work".
* **Nothing new.** When a starting-point results file is supplied, any rule and
  file pair not present in it causes a failure. This answers "did the fix break
  something else", and it is what catches a change that stops one report by
  moving the problem to a different place.

Entries are matched on the rule and the file, never on the line number. The line
moves as soon as the file is edited, so recording it would make every run pass
without really checking anything.

CodeQL writes its results in a format called SARIF, which is JSON. That is what
this script reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load_results(sarif_path: Path) -> dict[tuple[str, str], list[int]]:
    """Return a mapping of (rule id, file path) to the lines that were reported."""
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

    base = load_results(args.baseline_sarif) if args.baseline_sarif else None

    print("== alerts expected to be closed ==")
    for rule, path in expected_closed:
        lines = head.get((rule, path))
        if lines:
            failures.append(f"{rule} is still reported in {path} at line(s) {sorted(lines)}")
            print(f"  STILL OPEN  {rule}  {path}  lines={sorted(lines)}")
        elif base is not None and (rule, path) not in base:
            # A missing result only means "fixed" if it was there to begin with.
            # Without this check, a misspelled rule id, a file skipped during
            # scanning, or a scan that produced nothing at all would each be
            # reported as a success.
            failures.append(
                f"{rule} / {path} was not reported at the starting point either, "
                "so this entry is out of date, misspelled, or names a file that "
                "was not scanned"
            )
            print(f"  NOT AT START {rule}  {path}  (so this branch cannot have closed it)")
        else:
            suffix = "" if base is None else f"  (was reported at lines {sorted(base[(rule, path)])})"
            print(f"  closed      {rule}  {path}{suffix}")

    if base is None:
        print("\n  note: no starting point given, so 'closed' only means 'not reported now'.")

    if base is not None:
        new = sorted(set(head) - set(base))
        print("\n== alerts not present at the starting point ==")
        if new:
            for rule, path in new:
                failures.append(f"new alert {rule} in {path}")
                print(f"  NEW         {rule}  {path}  lines={sorted(head[(rule, path)])}")
        else:
            print("  none")

        fixed = sorted(set(base) - set(head))
        if fixed:
            print("\n== also no longer reported, for information ==")
            for rule, path in fixed:
                print(f"  cleared     {rule}  {path}")

    print()
    if failures:
        print(f"FAIL ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS: every listed alert is closed, and no new alert appeared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
