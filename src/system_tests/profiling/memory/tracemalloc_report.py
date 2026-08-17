"""
tracemalloc_report.py -- Lens B: Python-heap attribution via tracemalloc.

WARNING: tracemalloc captures Python heap only.
C extensions (torch, onnxruntime, sqlite-vec, fastembed) are NOT included.
Do NOT read tracemalloc totals as process memory.
The 'native_gap_mb' field shows what tracemalloc cannot see.

Runs the import + agent work in a fresh subprocess so sys.modules is clean.
tracemalloc runs INSIDE that subprocess.  This is intentionally a separate
run from RSS-only runs because tracemalloc inflates RSS by ~10-20%.

Usage:
    uv run python tracemalloc_report.py
    uv run python tracemalloc_report.py --start-checkpoint import_sdk --top 10
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

# ---------------------------------------------------------------------------
# Worker code -- runs inside a fresh subprocess.
# Built with string concatenation (like measure_sdk_startup.py) to avoid
# triple-quote / backslash escaping issues.
# tracemalloc is started before any imports so it captures allocations from
# the very beginning of the process.
# ---------------------------------------------------------------------------

_WORKER_CODE = (
    "import gc, itertools, json, os, sys, tracemalloc\n"
    "import psutil\n"
    "\n"
    "# Start tracemalloc BEFORE any cuga import so we capture everything.\n"
    "tracemalloc.start(25)\n"
    "\n"
    "def _rss_mb():\n"
    "    return psutil.Process().memory_info().rss / (1024 * 1024)\n"
    "\n"
    "def _snap():\n"
    "    gc.collect()\n"
    "    return tracemalloc.take_snapshot(), _rss_mb()\n"
    "\n"
    "def _total_mb(snapshot):\n"
    "    return sum(s.size for s in snapshot.statistics('lineno')) / (1024 * 1024)\n"
    "\n"
    "# snap_diff is the list returned by snapshot.compare_to(prev, key_type)\n"
    "def _top_lineno(snap_diff, n):\n"
    "    out = []\n"
    "    for s in snap_diff[:n]:\n"
    "        f = s.traceback[0]\n"
    "        out.append({'filename': f.filename, 'lineno': f.lineno,\n"
    "                    'size_mb': round(s.size / (1024 * 1024), 4),\n"
    "                    'count': s.count})\n"
    "    return out\n"
    "\n"
    "def _top_module(snap_diff, n):\n"
    "    from collections import defaultdict\n"
    "    by_mod = defaultdict(float)\n"
    "    for stat in snap_diff:\n"
    "        fname = stat.traceback[0].filename.replace('\\\\\\\\', '/')\n"
    "        parts = fname.split('/')\n"
    "        mod = 'unknown'\n"
    "        for marker in ('site-packages', 'dist-packages'):\n"
    "            try:\n"
    "                idx = parts.index(marker)\n"
    "                if idx + 1 < len(parts):\n"
    "                    mod = parts[idx + 1].split('.')[0]\n"
    "                    break\n"
    "            except ValueError:\n"
    "                continue\n"
    "        else:\n"
    "            for part in reversed(parts[:-1]):\n"
    "                if part and part not in ('', '.', '..'):\n"
    "                    mod = part\n"
    "                    break\n"
    "        by_mod[mod] += stat.size / (1024 * 1024)\n"
    "    ranked = sorted(by_mod.items(), key=lambda kv: kv[1], reverse=True)\n"
    "    return [{'module': m, 'size_mb': round(v, 4)} for m, v in ranked[:n]]\n"
    "\n"
    "# ── checkpoint: baseline ────────────────────────────────────────────────\n"
    "snap_baseline, rss_baseline = _snap()\n"
    "\n"
    "# ── checkpoint: import_cuga ─────────────────────────────────────────────\n"
    "import cuga\n"
    "snap_import_cuga, rss_import_cuga = _snap()\n"
    "\n"
    "# ── checkpoint: import_sdk ──────────────────────────────────────────────\n"
    "from cuga.sdk import CugaAgent\n"
    "snap_import_sdk, rss_import_sdk = _snap()\n"
    "\n"
    "# ── checkpoint: constructed ─────────────────────────────────────────────\n"
    "from langchain_core.language_models.fake_chat_models import GenericFakeChatModel\n"
    "from langchain_core.messages import AIMessage\n"
    "from langchain_core.tools import tool\n"
    "\n"
    "_canned = itertools.cycle([AIMessage(content='ok')])\n"
    "fake_model = GenericFakeChatModel(messages=_canned)\n"
    "\n"
    "@tool\n"
    "def echo(x: str) -> str:\n"
    "    'Echo x back.'\n"
    "    return x\n"
    "\n"
    "agent = CugaAgent(tools=[echo], model=fake_model)\n"
    "snap_constructed, rss_constructed = _snap()\n"
    "\n"
    "# ── checkpoint: converged ───────────────────────────────────────────────\n"
    "import asyncio\n"
    "invoke_error = None\n"
    "try:\n"
    "    asyncio.run(agent.invoke('hello'))\n"
    "except Exception as _exc:\n"
    "    invoke_error = str(_exc)\n"
    "snap_converged, rss_converged = _snap()\n"
    "\n"
    "tracemalloc.stop()\n"
    "\n"
    "# ── build output ────────────────────────────────────────────────────────\n"
    "TOP_N = int(os.environ.get('_TRACEMALLOC_TOP_N', '25'))\n"
    "START_CP = os.environ.get('_TRACEMALLOC_START_CP', 'baseline')\n"
    "\n"
    "_ALL = [\n"
    "    ('baseline',    snap_baseline,    rss_baseline),\n"
    "    ('import_cuga', snap_import_cuga, rss_import_cuga),\n"
    "    ('import_sdk',  snap_import_sdk,  rss_import_sdk),\n"
    "    ('constructed', snap_constructed, rss_constructed),\n"
    "    ('converged',   snap_converged,   rss_converged),\n"
    "]\n"
    "\n"
    "_start_idx = next((i for i, (n, _, _) in enumerate(_ALL) if n == START_CP), 0)\n"
    "prev_snap = _ALL[_start_idx][1]\n"
    "\n"
    "results = []\n"
    "for name, snap, rss in _ALL[_start_idx:]:\n"
    "    total = _total_mb(snap)\n"
    "    diff_lineno = snap.compare_to(prev_snap, 'lineno')\n"
    "    diff_fname  = snap.compare_to(prev_snap, 'filename')\n"
    "    results.append({\n"
    "        'checkpoint': name,\n"
    "        'tracemalloc_total_mb': round(total, 3),\n"
    "        'rss_mb_at_checkpoint': round(rss, 3),\n"
    "        'native_gap_mb': round(rss - total, 3),\n"
    "        'top_by_lineno': _top_lineno(diff_lineno, TOP_N),\n"
    "        'top_by_module': _top_module(diff_fname,  TOP_N),\n"
    "    })\n"
    "    prev_snap = snap\n"
    "\n"
    "_WARN = (\n"
    "    'WARNING: tracemalloc captures Python heap only.\\n'\n"
    "    'C extensions (torch, onnxruntime, sqlite-vec, fastembed) are NOT included.\\n'\n"
    "    'Do NOT read tracemalloc totals as process memory.\\n'\n"
    "    \"The 'native_gap_mb' field shows what tracemalloc cannot see.\"\n"
    ")\n"
    "print(json.dumps({'surface': 'tracemalloc', 'warning': _WARN,\n"
    "                  'start_checkpoint': START_CP, 'top_n': TOP_N,\n"
    "                  'invoke_error': invoke_error,\n"
    "                  'checkpoints': results}))\n"
)

_TRACEMALLOC_WARNING = (
    "WARNING: tracemalloc captures Python heap only.\n"
    "C extensions (torch, onnxruntime, sqlite-vec, fastembed) are NOT included.\n"
    "Do NOT read tracemalloc totals as process memory.\n"
    "The 'native_gap_mb' field shows what tracemalloc cannot see."
)

_BANNER = (
    "\n"
    "╔══════════════════════════════════════════════════════════════════════════════╗\n"
    "║  TRACEMALLOC -- PYTHON HEAP ONLY                                             ║\n"
    "║  C extensions (torch / onnxruntime / sqlite-vec / fastembed) NOT INCLUDED.  ║\n"
    "║  Do NOT read these totals as process memory.                                 ║\n"
    "║  'native_gap_mb' = RSS - tracemalloc total (what the profiler cannot see).  ║\n"
    "╚══════════════════════════════════════════════════════════════════════════════╝"
)


def run_worker(start_checkpoint: str, top_n: int) -> dict:
    """Run the worker in a subprocess and return the parsed JSON result."""
    import os

    env = os.environ.copy()
    env["_TRACEMALLOC_START_CP"] = start_checkpoint
    env["_TRACEMALLOC_TOP_N"] = str(top_n)

    try:
        result = subprocess.run(
            [sys.executable, "-c", _WORKER_CODE],
            capture_output=True,
            text=True,
            env=env,
            timeout=300.0,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Worker subprocess timed out after 300s") from exc

    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Worker subprocess failed (exit {result.returncode})")

    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    lines = result.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError("Worker subprocess produced no stdout")
    return json.loads(lines[-1])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Lens B: tracemalloc Python-heap attribution at SDK checkpoints. "
            "NOTE: tracemalloc captures Python heap only -- C extensions are excluded."
        )
    )
    parser.add_argument(
        "--start-checkpoint",
        default="baseline",
        choices=["baseline", "import_cuga", "import_sdk", "constructed", "converged"],
        help="First checkpoint to start diffing from (default: baseline).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=25,
        metavar="N",
        help=("Number of top entries per checkpoint in by-lineno and by-module tables (default: 25)."),
    )
    args = parser.parse_args()

    print(_BANNER, file=sys.stderr)
    print("", file=sys.stderr)
    print(_TRACEMALLOC_WARNING, file=sys.stderr)
    print("", file=sys.stderr)
    print(
        f"Running tracemalloc worker (start_checkpoint={args.start_checkpoint}, top={args.top}) ...",
        file=sys.stderr,
    )

    data = run_worker(start_checkpoint=args.start_checkpoint, top_n=args.top)

    if data.get("invoke_error"):
        print(f"WARNING: agent.invoke failed in worker: {data['invoke_error']}", file=sys.stderr)

    # Overwrite warning with the canonical string.
    data["warning"] = _TRACEMALLOC_WARNING

    # Human-readable summary to stderr.
    print("", file=sys.stderr)
    print(
        f"{'checkpoint':<22} {'tracemalloc_MB':>15} {'rss_MB':>10} {'native_gap_MB':>15}",
        file=sys.stderr,
    )
    print("-" * 65, file=sys.stderr)
    for cp in data.get("checkpoints", []):
        print(
            f"{cp['checkpoint']:<22}"
            f" {cp['tracemalloc_total_mb']:>15.1f}"
            f" {cp['rss_mb_at_checkpoint']:>10.1f}"
            f" {cp['native_gap_mb']:>15.1f}",
            file=sys.stderr,
        )

    # stdout contract: exactly one JSON object as the last line.
    print(json.dumps(data))


if __name__ == "__main__":
    main()
