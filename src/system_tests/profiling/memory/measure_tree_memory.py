"""
measure_tree_memory.py — full process-tree footprint for ``cuga start demo``.

Launches ``cuga start demo`` (or with extra flags via ``--flags``), waits until
the demo server reports ready, then walks the entire process tree with
``memlib.tree_sample``.  Tears down the whole process group on exit — even on
failure paths — so no orphans remain.

Stdout contract: exactly ONE JSON object as the *last* line of stdout.
All logs / progress go to stderr.

Usage (run from the repo root):
    cd src/system_tests/profiling/memory
    uv run python measure_tree_memory.py
    uv run python measure_tree_memory.py --flags "--crm --email"
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import threading
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Bootstrap: make memlib importable regardless of cwd
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from memlib import capture_config, emit, tree_sample  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# demo server is on port 7860, registry on 8001 (from cuga settings defaults)
DEMO_PORT = int(os.environ.get("DYNACONF_SERVER_PORTS__DEMO", "7860"))
REGISTRY_PORT = int(os.environ.get("DYNACONF_SERVER_PORTS__REGISTRY", "8001"))
TIMEOUT_S = 240.0  # generous: many services to start
POLL_INTERVAL_S = 1.0
DEMO_READY_URL = f"http://localhost:{DEMO_PORT}/health/readiness"
REGISTRY_READY_URL = f"http://localhost:{REGISTRY_PORT}/health"
STARTUP_LOG_NEEDLE = "Application finished starting up..."


# ---------------------------------------------------------------------------
# Short label derivation from cmdline
# ---------------------------------------------------------------------------

_LABEL_PATTERNS: list[tuple[str, str]] = [
    # pattern substring → label
    ("email_mcp/mcp_server", "email-mcp"),
    ("email_mcp/mail_sink", "email-sink"),
    ("docs_mcp", "docs-mcp"),
    ("crm_api", "crm-server"),
    ("oak-health", "oak-health"),
    ("cuga-oak-health", "oak-health"),
    ("api_registry_server", "registry"),
    ("registry.api_registry", "registry"),
    ("cuga.backend.server.main", "demo"),
    ("uvicorn", "uvicorn"),
]


def _label_from_cmdline(cmdline: list[str]) -> str:
    """Derive a short human-readable label from the process cmdline."""
    joined = " ".join(cmdline)
    for pattern, label in _LABEL_PATTERNS:
        if pattern in joined:
            return label
    # Fallback: last non-flag token of cmdline
    tokens = [t for t in cmdline if not t.startswith("-")]
    if tokens:
        return os.path.basename(tokens[-1])[:24]
    return "unknown"


# ---------------------------------------------------------------------------
# Readiness polling (reused from startup harness)
# ---------------------------------------------------------------------------


def _try_url(url: str) -> bool | None:
    """Return True if HTTP GET returns 2xx, False on HTTP error, None on conn error."""
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            try:
                data = json.loads(resp.read())
                return bool(data.get("ready", True))
            except Exception:
                return True  # got a 2xx — good enough
    except urllib.error.HTTPError:
        return False
    except Exception:
        return None


def _wait_ready(url: str, label: str, deadline: float) -> bool:
    """Poll *url* until ready or deadline.  Returns True on success."""
    while time.perf_counter() < deadline:
        result = _try_url(url)
        if result is True:
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


# ---------------------------------------------------------------------------
# Process-group teardown
# ---------------------------------------------------------------------------


def _kill_group(proc: subprocess.Popen) -> None:
    """Send SIGTERM to the whole process group; escalate to SIGKILL if needed."""
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except Exception:
                pass
        proc.wait()


# ---------------------------------------------------------------------------
# Enrichment: add label to each process record from tree_sample
# ---------------------------------------------------------------------------


def _enrich_processes(processes: list[dict]) -> list[dict]:
    """Add a ``label`` derived from actual cmdline to each process record."""
    import psutil

    enriched: list[dict] = []
    for rec in processes:
        pid = rec.get("pid")
        label = "unknown"
        name = "unknown"
        try:
            p = psutil.Process(pid)
            name = p.name()
            cmdline = p.cmdline()
            if cmdline:
                label = _label_from_cmdline(cmdline)
            else:
                label = name
        except Exception:
            pass
        new_rec = dict(rec)
        new_rec["name"] = name
        new_rec["label"] = label
        enriched.append(new_rec)
    return enriched


# ---------------------------------------------------------------------------
# Main measurement
# ---------------------------------------------------------------------------


def measure(extra_flags: list[str]) -> dict:
    """
    Start ``cuga start demo [extra_flags]``, wait for readiness, sample tree.

    Returns the full result dict (to be emitted as JSON).  Teardown of the
    process group is guaranteed via a try/finally block.
    """
    cmd = [sys.executable, "-m", "cuga.cli.main", "start", "demo"] + extra_flags
    print(f"[measure_tree_memory] Launching: {' '.join(cmd)}", file=sys.stderr)

    stdout_lines: list[str] = []
    fallback_event = threading.Event()

    def _reader(stream: object) -> None:
        for raw in stream:  # type: ignore[union-attr]
            line = raw.rstrip("\n")
            stdout_lines.append(line)
            print(f"[cuga] {line}", file=sys.stderr)
            if STARTUP_LOG_NEEDLE in line:
                fallback_event.set()

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # own process group → clean kill
    )
    print(f"[measure_tree_memory] PID={proc.pid}", file=sys.stderr)

    reader_thread = threading.Thread(target=_reader, args=(proc.stdout,), daemon=True)
    reader_thread.start()

    result: dict = {}
    deadline = t0 + TIMEOUT_S

    try:
        # Wait for demo server to be ready (primary signal: HTTP readiness)
        print(f"[measure_tree_memory] Waiting for demo ({DEMO_READY_URL}) …", file=sys.stderr)
        demo_ready = False

        while time.perf_counter() < deadline:
            # Check if process died unexpectedly
            if proc.poll() is not None:
                reader_thread.join(timeout=2)
                print(
                    f"[measure_tree_memory] Process exited early (rc={proc.returncode})",
                    file=sys.stderr,
                )
                if stdout_lines:
                    print("[measure_tree_memory] Last 30 lines:", file=sys.stderr)
                    for ln in stdout_lines[-30:]:
                        print(f"  | {ln}", file=sys.stderr)
                break

            ready = _try_url(DEMO_READY_URL)
            if ready is True:
                elapsed = time.perf_counter() - t0
                print(
                    f"[measure_tree_memory] Demo ready after {elapsed:.1f}s",
                    file=sys.stderr,
                )
                demo_ready = True
                break

            if fallback_event.is_set():
                elapsed = time.perf_counter() - t0
                print(
                    f"[measure_tree_memory] Startup log line seen after {elapsed:.1f}s (HTTP not ready yet)",
                    file=sys.stderr,
                )
                demo_ready = True
                break

            time.sleep(POLL_INTERVAL_S)
        else:
            print(
                f"[measure_tree_memory] Timed out after {TIMEOUT_S}s",
                file=sys.stderr,
            )

        # Sample the process tree
        print("[measure_tree_memory] Sampling process tree …", file=sys.stderr)
        tree = tree_sample(proc.pid)
        enriched = _enrich_processes(tree["processes"])

        # Build output schema
        total_info = tree["total"]
        metric = total_info["metric"]
        total_metric_mb = total_info.get(f"{metric}_mb", 0.0)
        total_rss_mb = total_info.get("rss_mb_sum", 0.0)

        # On macOS, task_for_pid restrictions mean USS is 0 for subprocesses
        # owned by a different session.  Fall back to reporting RSS sum with a
        # note so the output is still useful.
        uss_unavailable = total_metric_mb == 0.0 and total_rss_mb > 0.0
        effective_total_mb = total_rss_mb if uss_unavailable else total_metric_mb
        effective_metric = "rss_sum_fallback" if uss_unavailable else metric

        import platform as _plat
        from datetime import datetime, timezone

        result = {
            "surface": "tree",
            "checkpoint": "demo_ready" if demo_ready else "demo_timeout",
            "processes": [
                {
                    "name": r["name"],
                    "label": r["label"],
                    "pid": r["pid"],
                    "rss_mb": round(r["rss_mb"] or 0.0, 2),
                    "uss_mb": round(r["uss_mb"] or 0.0, 2),
                }
                for r in enriched
            ],
            "total_mb": round(effective_total_mb, 2),
            "metric_used": effective_metric,
            "total_rss_mb_sum": round(total_rss_mb, 2),
            "config": capture_config(),
            "platform": {
                "system": _plat.system(),
                "machine": _plat.machine(),
                "python": _plat.python_version(),
            },
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    finally:
        print("[measure_tree_memory] Tearing down process group …", file=sys.stderr)
        _kill_group(proc)
        reader_thread.join(timeout=5)
        print("[measure_tree_memory] Teardown complete.", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure full process-tree memory footprint of `cuga start demo`."
    )
    parser.add_argument(
        "--flags",
        default="",
        help='Extra flags forwarded to `cuga start demo`, e.g. "--crm --email"',
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    extra_flags = shlex.split(args.flags) if args.flags.strip() else []

    print("[measure_tree_memory] Starting tree-memory measurement …", file=sys.stderr)
    result = measure(extra_flags)

    # Print labelled table to stderr for human readability
    print("\n[measure_tree_memory] ── Per-process table ──", file=sys.stderr)
    print(f"  {'label':<20} {'pid':>7}  {'rss_mb':>9}  {'uss_mb':>9}", file=sys.stderr)
    print(f"  {'-' * 20} {'-' * 7}  {'-' * 9}  {'-' * 9}", file=sys.stderr)
    for p in result.get("processes", []):
        print(
            f"  {p['label']:<20} {p['pid']:>7}  {p['rss_mb']:>9.1f}  {p['uss_mb']:>9.1f}",
            file=sys.stderr,
        )
    metric = result.get("metric_used", "uss")
    total = result.get("total_mb", 0.0)
    print(f"\n  Total {metric.upper()}: {total:.1f} MB  (metric_used={metric})", file=sys.stderr)
    print("[measure_tree_memory] ───────────────────────\n", file=sys.stderr)

    emit(result)


if __name__ == "__main__":
    main()
