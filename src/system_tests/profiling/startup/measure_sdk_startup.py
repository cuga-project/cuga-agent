"""
measure_sdk_startup.py — measure CUGA SDK cold-start time.

Runs the import and construction phases in a fresh subprocess so sys.modules
is empty (true cold start). Prints the timing result as a JSON record.

Usage:
    uv run python src/system_tests/profiling/startup/measure_sdk_startup.py
    uv run python src/system_tests/profiling/startup/measure_sdk_startup.py --with-invoke
"""

import argparse
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

# Extended worker that also times the first agent.invoke() call using a fake
# LangChain chat model so no real LLM provider is contacted.
_WORKER_CODE_WITH_INVOKE = (
    "import asyncio, time, json\n"
    "\n"
    "t0 = time.perf_counter()\n"
    "import cuga.sdk\n"
    "from cuga.sdk import CugaAgent\n"
    "t1 = time.perf_counter()\n"
    "\n"
    "from langchain_core.tools import tool\n"
    "from langchain_core.language_models.fake_chat_models import GenericFakeChatModel\n"
    "from langchain_core.messages import AIMessage\n"
    "\n"
    "@tool\n"
    "def echo(x: str) -> str:\n"
    '    """Echo x back."""\n'
    "    return x\n"
    "\n"
    "# Construct with an infinite-cycling fake model so the agent can always\n"
    "# get a response without hitting a real LLM provider.\n"
    "import itertools\n"
    "_canned = itertools.cycle([AIMessage(content='ok')])\n"
    "fake_model = GenericFakeChatModel(messages=_canned)\n"
    "agent = CugaAgent(tools=[echo], model=fake_model)\n"
    "t2 = time.perf_counter()\n"
    "\n"
    "import_s = t1 - t0\n"
    "construct_s = t2 - t1\n"
    "# ready_s is identical to the no-invoke path — invoke time is NOT included.\n"
    "ready_s = import_s + construct_s\n"
    "\n"
    "llm_first_call_s = None\n"
    "try:\n"
    "    t3 = time.perf_counter()\n"
    "    asyncio.run(agent.invoke('hello'))\n"
    "    t4 = time.perf_counter()\n"
    "    llm_first_call_s = t4 - t3\n"
    "except Exception:\n"
    "    pass\n"
    "\n"
    'print(json.dumps({"import_s": import_s, "construct_s": construct_s,'
    ' "ready_s": ready_s, "llm_first_call_s": llm_first_call_s}))\n'
)


def measure(with_invoke: bool = False) -> dict:
    """Run the worker in a subprocess and return the parsed timing dict."""
    worker_code = _WORKER_CODE_WITH_INVOKE if with_invoke else _WORKER_CODE
    result = subprocess.run(
        [sys.executable, "-c", worker_code],
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
    parser = argparse.ArgumentParser(description="Measure CUGA SDK cold-start time.")
    parser.add_argument(
        "--with-invoke",
        action="store_true",
        help="Also time the first agent.invoke() call (uses a fake LLM, no real provider).",
    )
    args = parser.parse_args()

    print("Measuring CUGA SDK cold-start …", file=sys.stderr)
    timing = measure(with_invoke=args.with_invoke)
    print(json.dumps(timing))


if __name__ == "__main__":
    main()
