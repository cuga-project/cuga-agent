"""
measure_sdk_memory.py — measure CUGA SDK memory at six checkpoints.

Runs each checkpoint in a fresh subprocess (--isolated, default) or a single
sequential subprocess (--walk) so no import state leaks between checkpoints.
Prints exactly one JSON object as the last line of stdout; all progress/log
messages go to stderr.

Usage:
    uv run python measure_sdk_memory.py              # isolated mode (default)
    uv run python measure_sdk_memory.py --isolated   # explicit
    uv run python measure_sdk_memory.py --walk       # single-process walk
"""

import argparse
import json
import subprocess
import sys

WORKER_TIMEOUT_S = 300.0

# ---------------------------------------------------------------------------
# Worker code snippets (executed in fresh subprocesses; must be self-contained)
# ---------------------------------------------------------------------------
# memlib is injected via PYTHONPATH set by _run_worker; workers just `import memlib`.

# ---------------------------------------------------------------------------
# Worker: one checkpoint per subprocess (--isolated)
# ---------------------------------------------------------------------------

# Each worker imports memlib from sys.path (parent sets PYTHONPATH),
# does work up to the target checkpoint, samples, and emits JSON to stdout.

_WORKER_BASELINE = """\
import sys
import memlib
rec = memlib.sample('baseline')
memlib.emit(rec)
"""

_WORKER_IMPORT_CUGA = """\
import sys
import memlib
import cuga  # noqa: F401
rec = memlib.sample('import_cuga')
memlib.emit(rec)
"""

_WORKER_IMPORT_SDK = """\
import sys
import memlib
import cuga  # noqa: F401
from cuga import CugaAgent  # noqa: F401
rec = memlib.sample('import_sdk')
memlib.emit(rec)
"""

_WORKER_CONSTRUCTED = """\
import itertools
import sys
import memlib
from cuga import CugaAgent
from langchain_core.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

@tool
def echo(x: str) -> str:
    \"\"\"Echo x back.\"\"\"
    return x

_canned = itertools.cycle([AIMessage(content='ok')])
fake_model = GenericFakeChatModel(messages=_canned)
agent = CugaAgent(tools=[echo], model=fake_model)
rec = memlib.sample('constructed')
memlib.emit(rec)
"""

_WORKER_CONVERGED = """\
import asyncio
import itertools
import sys
import memlib
from cuga import CugaAgent
from langchain_core.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

@tool
def echo(x: str) -> str:
    \"\"\"Echo x back.\"\"\"
    return x

_canned = itertools.cycle([AIMessage(content='ok')])
fake_model = GenericFakeChatModel(messages=_canned)
agent = CugaAgent(tools=[echo], model=fake_model)
asyncio.run(agent.invoke('hello'))
rec = memlib.sample('converged')
memlib.emit(rec)
"""

_WORKER_GROWTH = """\
import asyncio
import itertools
import sys
import memlib
from cuga import CugaAgent
from langchain_core.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

@tool
def echo(x: str) -> str:
    \"\"\"Echo x back.\"\"\"
    return x

_canned = itertools.cycle([AIMessage(content='ok')])
fake_model = GenericFakeChatModel(messages=_canned)
agent = CugaAgent(tools=[echo], model=fake_model)
for _ in range(10):
    asyncio.run(agent.invoke('hello'))
rec = memlib.sample('growth')
memlib.emit(rec)
"""

# Map checkpoint name → worker code (isolated mode)
_ISOLATED_WORKERS: dict[str, str] = {
    "baseline": _WORKER_BASELINE,
    "import_cuga": _WORKER_IMPORT_CUGA,
    "import_sdk": _WORKER_IMPORT_SDK,
    "constructed": _WORKER_CONSTRUCTED,
    "converged": _WORKER_CONVERGED,
    "growth": _WORKER_GROWTH,
}

# ---------------------------------------------------------------------------
# Worker: single subprocess visiting all checkpoints sequentially (--walk)
# ---------------------------------------------------------------------------

_WORKER_WALK = """\
import asyncio
import itertools
import sys
import memlib

# --- baseline ---
rec0 = memlib.sample('baseline')

# --- import_cuga ---
import cuga  # noqa: F401
rec1 = memlib.sample('import_cuga')

# --- import_sdk ---
from cuga import CugaAgent
rec2 = memlib.sample('import_sdk')

# --- constructed ---
from langchain_core.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

@tool
def echo(x: str) -> str:
    \"\"\"Echo x back.\"\"\"
    return x

_canned = itertools.cycle([AIMessage(content='ok')])
fake_model = GenericFakeChatModel(messages=_canned)
agent = CugaAgent(tools=[echo], model=fake_model)
rec3 = memlib.sample('constructed')

# --- converged (1 invoke) ---
asyncio.run(agent.invoke('hello'))
rec4 = memlib.sample('converged')

# --- growth (9 more invokes = 10 total) ---
for _ in range(9):
    asyncio.run(agent.invoke('hello'))
rec5 = memlib.sample('growth')

import json as _json
checkpoints = [rec0, rec1, rec2, rec3, rec4, rec5]
for cp in checkpoints:
    cp['checkpoint'] = cp.pop('label')
print(_json.dumps({'surface': 'sdk', 'checkpoints': checkpoints,
                   'config': memlib.capture_config(), 'mode': 'walk'}))
"""


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def _run_worker(code: str, memlib_dir: str) -> dict:
    """Execute *code* in a fresh subprocess with *memlib_dir* on PYTHONPATH.

    Returns the parsed JSON record from the last stdout line.
    Raises RuntimeError if the subprocess exits non-zero.
    """
    import os

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{memlib_dir}:{existing}" if existing else memlib_dir

    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            timeout=WORKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Worker subprocess timed out after {WORKER_TIMEOUT_S}s") from exc

    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if result.returncode != 0:
        raise RuntimeError(f"Worker subprocess failed (exit {result.returncode})\n{result.stderr}")

    lines = result.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"Worker produced no stdout (exit={result.returncode})")
    last_line = lines[-1]
    return json.loads(last_line)


def _checkpoint_name(record: dict) -> dict:
    """Normalise a sample record: rename 'label' → 'checkpoint'."""
    rec = dict(record)
    if "label" in rec and "checkpoint" not in rec:
        rec["checkpoint"] = rec.pop("label")
    return rec


# ---------------------------------------------------------------------------
# Measurement modes
# ---------------------------------------------------------------------------


def measure_isolated(memlib_dir: str) -> list[dict]:
    """Run one subprocess per checkpoint; return list of checkpoint records."""
    records: list[dict] = []
    for name, code in _ISOLATED_WORKERS.items():
        print(f"  checkpoint: {name} …", file=sys.stderr)
        raw = _run_worker(code, memlib_dir)
        rec = _checkpoint_name(raw)
        rec["checkpoint"] = name  # ensure the name matches the key
        records.append(rec)
    return records


def measure_walk(memlib_dir: str) -> list[dict]:
    """Run a single subprocess that walks all checkpoints; return list of records."""
    print("  running walk subprocess …", file=sys.stderr)
    raw = _run_worker(_WORKER_WALK, memlib_dir)
    # walk worker already emits the full envelope; return checkpoints list
    return raw["checkpoints"]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure CUGA SDK memory at six checkpoints.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--isolated",
        action="store_true",
        default=False,
        help="One fresh subprocess per checkpoint (default mode).",
    )
    mode.add_argument(
        "--walk",
        action="store_true",
        default=False,
        help="Single subprocess walking all checkpoints sequentially.",
    )
    args = parser.parse_args()

    # Default to isolated when neither flag is given.
    use_walk = args.walk

    # Locate the directory that contains memlib.py (same dir as this script).
    import os

    memlib_dir = os.path.dirname(os.path.abspath(__file__))

    mode_label = "walk" if use_walk else "isolated"
    print(f"Measuring CUGA SDK memory ({mode_label} mode) …", file=sys.stderr)

    if use_walk:
        checkpoints = measure_walk(memlib_dir)
    else:
        checkpoints = measure_isolated(memlib_dir)

    # Import capture_config from memlib for the envelope (safe here — we're
    # not measuring this process, only assembling the output envelope).
    sys.path.insert(0, memlib_dir)
    import memlib  # noqa: PLC0415

    envelope = {
        "surface": "sdk",
        "checkpoints": checkpoints,
        "config": memlib.capture_config(),
        "mode": mode_label,
    }
    print(json.dumps(envelope))


if __name__ == "__main__":
    main()
