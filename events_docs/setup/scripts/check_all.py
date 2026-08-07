#!/usr/bin/env python3
"""Run every pre-req check and print a summary — a mini "verify everything" for the setup stage.

Each check reads its keys from `.env` and talks straight to the vendor API. A connector whose keys
aren't set is SKIPPED (not failed), so this is safe to run with only some integrations configured.

Usage:
    python3 check_all.py                 # every check
    python3 check_all.py box gmail       # just these
"""

import os
import subprocess
import sys
from pathlib import Path

from _common import bold, green, red, yellow

CHECKS = ["ngrok", "telegram", "discord", "slack", "box", "github", "gmail"]
HERE = Path(__file__).resolve().parent


def main():
    names = sys.argv[1:] or CHECKS
    # Gmail's first-ever run needs a browser; skip that interactive leg inside the batch run.
    child_env = {**os.environ, "CUGA_PREREQ_NONINTERACTIVE": "1"}

    results = []
    for n in names:
        script = HERE / f"check_{n}.py"
        if not script.is_file():
            print(red(f"no check_{n}.py"))
            continue
        rc = subprocess.run([sys.executable, str(script)], env=child_env).returncode
        results.append((n, rc))

    print(bold("\n── summary ──"))
    label = {0: green("PASS"), 1: red("FAIL"), 3: yellow("SKIP")}
    for n, rc in results:
        print(f"  {label.get(rc, red('ERR ')):<6} {n}")

    fails = [n for n, rc in results if rc == 1]
    if fails:
        print(red(f"\n{len(fails)} failed: {', '.join(fails)}"))
        return 1
    print(green("\nall configured integrations verified"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
