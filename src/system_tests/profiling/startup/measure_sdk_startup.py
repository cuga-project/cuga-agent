"""
measure_sdk_startup.py — measure CUGA SDK cold-start time.

Runs the import and construction phases in a fresh subprocess so sys.modules
is empty (true cold start). Prints the timing result as a JSON record.

Usage:
    uv run python src/system_tests/profiling/startup/measure_sdk_startup.py
"""

import json
import subprocess
import sys

# Worker code executed in a fresh subprocess.  The JSON line is always the
# last line of stdout, so any log noise emitted by cuga during import is
# harmlessly ignored by the parent.
_WORKER_CODE = (
    "import time, json\n"
    "\n"
    "t0 = time.perf_counter()\n"
    "import cuga.sdk\n"
    "from cuga.sdk import CugaAgent\n"
    "t1 = time.perf_counter()\n"
    "\n"
    "from langchain_core.tools import tool\n"
    "\n"
    "@tool\n"
    "def echo(x: str) -> str:\n"
    '    """Echo x back."""\n'
    "    return x\n"
    "\n"
    "agent = CugaAgent(tools=[echo])\n"
    "t2 = time.perf_counter()\n"
    "\n"
    "import_s = t1 - t0\n"
    "construct_s = t2 - t1\n"
    'print(json.dumps({"import_s": import_s, "construct_s": construct_s,'
    ' "ready_s": import_s + construct_s}))\n'
)


def measure() -> dict:
    """Run the worker in a subprocess and return the parsed timing dict."""
    result = subprocess.run(
        [sys.executable, "-c", _WORKER_CODE],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Worker subprocess failed (exit {result.returncode})")

    # Forward any log noise from the worker to stderr so it doesn't clutter stdout.
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    # The JSON record is always the last stdout line.
    last_line = result.stdout.strip().splitlines()[-1]
    return json.loads(last_line)


def main() -> None:
    print("Measuring CUGA SDK cold-start …", file=sys.stderr)
    timing = measure()
    print(json.dumps(timing))


if __name__ == "__main__":
    main()
