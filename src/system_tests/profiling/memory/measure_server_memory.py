"""
measure_server_memory.py — single-process server memory measurement.

Launches the CUGA uvicorn server (or a trivial baseline FastAPI app) in a
background subprocess, waits for /health/readiness, samples the server process
RSS/USS at each checkpoint, then kills the server and emits one JSON record.

Checkpoints
-----------
- ``uvicorn_baseline``  — trivial FastAPI() app, no CUGA code; isolates uvicorn's own cost
- ``cold``              — at /health/readiness = true (server ready, no requests yet)
- ``converged``         — after one GET /api/commands request
                          (forces lazy import of cuga.backend.slash_commands +
                           SkillRegistry; no LLM credentials needed)
- ``growth``            — after 21 GET /api/commands requests (1 converged + 20 growth)

Open question — "converged" request choice:
  Route: GET /api/commands
  Why:   On first call the server executes `from cuga.backend.slash_commands import
         build_slash_registry` and `_build_slash_skill_registry()`, hydrating the
         slash-command / skills subsystem lazily. These imports pull in a meaningful
         chunk of the backend package without requiring real LLM credentials. The
         endpoint returns 200 regardless of whether any .cuga skills folder exists.
  Credentials: none required. Auth is disabled by default (no CUGA_AUTH_* env vars),
               so require_chat_access passes through.

Stdout contract
---------------
Exactly one JSON object as the last line of stdout; all progress → stderr.

Usage
-----
    cd src/system_tests/profiling/memory
    uv run python measure_server_memory.py
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

# ---------------------------------------------------------------------------
# Ensure memlib is importable regardless of working directory
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from memlib import capture_config, emit, process_sample  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TIMEOUT_S = 120.0
POLL_INTERVAL_S = 0.25
STARTUP_LOG_NEEDLE = "Application finished starting up..."

# Number of requests for the "growth" checkpoint
GROWTH_N = 20

# Route used for converged / growth sampling
CONVERGED_ROUTE = "/api/commands"


# ---------------------------------------------------------------------------
# Port helpers (copied from measure_server_startup.py — no cross-dir import)
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Bind to port 0 and let the OS pick a free ephemeral port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _try_httpx(url: str) -> Optional[bool]:
    """Return True if ready, False if server responded but not ready, None on error."""
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url)
        data = resp.json()
        return bool(data.get("ready"))
    except Exception:
        return None


def _try_urllib(url: str) -> Optional[bool]:
    """Return True if ready, False if server responded but not ready, None on error."""
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
        return bool(data.get("ready"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read())
            return bool(data.get("ready"))
        except Exception:
            return False
    except Exception:
        return None


def _poll_readiness(url: str) -> Optional[bool]:
    """Try httpx first, fall back to urllib."""
    result = _try_httpx(url)
    if result is None:
        result = _try_urllib(url)
    return result


def _get(url: str) -> int:
    """Fire a GET request and return the HTTP status code (0 on connection error)."""
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(timeout=5.0) as client:
            return client.get(url).status_code
    except Exception:
        pass
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Process-group kill helper (copied from measure_server_startup.py)
# ---------------------------------------------------------------------------


def _kill_proc(proc: subprocess.Popen) -> None:
    """SIGTERM the process group; escalate to SIGKILL on timeout."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# Wait-for-ready helper
# ---------------------------------------------------------------------------


def _wait_for_ready(
    proc: subprocess.Popen,
    readiness_url: str,
    stdout_lines: list[str],
    fallback_event: threading.Event,
) -> bool:
    """Poll until the server reports ready; return True on success."""
    deadline = time.perf_counter() + TIMEOUT_S
    while time.perf_counter() < deadline:
        ready = _poll_readiness(readiness_url)
        if ready is True:
            return True
        if fallback_event.is_set():
            print(
                "  Fallback log line detected (HTTP endpoint did not report ready)",
                file=sys.stderr,
            )
            return True
        if proc.poll() is not None:
            print(
                f"  Server process exited early (rc={proc.returncode})",
                file=sys.stderr,
            )
            if stdout_lines:
                print("  --- server output (last 20 lines) ---", file=sys.stderr)
                for ln in stdout_lines[-20:]:
                    print(f"  | {ln}", file=sys.stderr)
            return False
        time.sleep(POLL_INTERVAL_S)
    print(f"  Timed out after {TIMEOUT_S}s waiting for readiness", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Baseline: trivial FastAPI app (no CUGA code)
# ---------------------------------------------------------------------------


def _write_baseline_app(tmp_dir: str) -> str:
    """Write a minimal FastAPI app to a temp file; return the module path."""
    app_src = textwrap.dedent(
        """\
        from fastapi import FastAPI
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def lifespan(app):
            yield

        app = FastAPI(lifespan=lifespan)

        @app.get("/health/readiness")
        async def readiness():
            return {"ready": True, "status": "ready"}
        """
    )
    path = os.path.join(tmp_dir, "baseline_app.py")
    with open(path, "w") as fh:
        fh.write(app_src)
    return path


def _measure_baseline() -> dict:
    """Start trivial FastAPI app, sample RSS at readiness, return record."""
    port = _find_free_port()
    readiness_url = f"http://localhost:{port}/health/readiness"

    with tempfile.TemporaryDirectory() as tmp_dir:
        _write_baseline_app(tmp_dir)
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "baseline_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        print(f"  [baseline] launching on port {port}…", file=sys.stderr)

        stdout_lines: list[str] = []
        fallback_event = threading.Event()

        def _reader(stream):
            for raw in stream:
                line = raw.rstrip("\n")
                stdout_lines.append(line)
                if STARTUP_LOG_NEEDLE in line:
                    fallback_event.set()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=tmp_dir,
            start_new_session=True,
        )
        reader_thread = threading.Thread(target=_reader, args=(proc.stdout,), daemon=True)
        reader_thread.start()

        try:
            ready = _wait_for_ready(proc, readiness_url, stdout_lines, fallback_event)
            if ready:
                rec = process_sample(proc.pid, "uvicorn_baseline")
                print(
                    f"  [baseline] rss_mb={rec['rss_mb']:.1f}",
                    file=sys.stderr,
                )
            else:
                rec = {
                    "label": "uvicorn_baseline",
                    "pid": None,
                    "rss_mb": None,
                    "uss_mb": None,
                    "available": False,
                }
        finally:
            _kill_proc(proc)
            reader_thread.join(timeout=2)

    return rec


# ---------------------------------------------------------------------------
# CUGA server measurement
# ---------------------------------------------------------------------------


def _measure_cuga(port: int) -> tuple[dict, dict, dict]:
    """Start the CUGA server; return (cold, converged, growth) records."""
    readiness_url = f"http://localhost:{port}/health/readiness"
    converged_url = f"http://localhost:{port}{CONVERGED_ROUTE}"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "cuga.backend.server.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    print(f"  [cuga] launching on port {port}…", file=sys.stderr)
    print(f"  cmd: {' '.join(cmd)}", file=sys.stderr)

    stdout_lines: list[str] = []
    fallback_event = threading.Event()

    def _reader(stream):
        for raw in stream:
            line = raw.rstrip("\n")
            stdout_lines.append(line)
            if STARTUP_LOG_NEEDLE in line:
                fallback_event.set()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    reader_thread = threading.Thread(target=_reader, args=(proc.stdout,), daemon=True)
    reader_thread.start()

    cold_rec: dict = {}
    converged_rec: dict = {}
    growth_rec: dict = {}

    try:
        ready = _wait_for_ready(proc, readiness_url, stdout_lines, fallback_event)
        if not ready:
            cold_rec = process_sample(proc.pid, "cold")
            cold_rec["rss_mb"] = None
            converged_rec = dict(cold_rec)
            converged_rec["label"] = "converged"
            growth_rec = dict(cold_rec)
            growth_rec["label"] = "growth"
            return cold_rec, converged_rec, growth_rec

        def _fmt_rss(v: object) -> str:
            return f"{v:.1f}" if v is not None else "n/a"

        cold_rec = process_sample(proc.pid, "cold")
        print(f"  [cuga] cold rss_mb={_fmt_rss(cold_rec['rss_mb'])}", file=sys.stderr)

        # converged — one request to CONVERGED_ROUTE
        status = _get(converged_url)
        print(f"  [cuga] converged request → HTTP {status}", file=sys.stderr)
        converged_rec = process_sample(proc.pid, "converged")
        print(f"  [cuga] converged rss_mb={_fmt_rss(converged_rec['rss_mb'])}", file=sys.stderr)

        # growth — GROWTH_N additional requests (total = 1 converged + GROWTH_N growth)
        for i in range(GROWTH_N):
            _get(converged_url)
        growth_rec = process_sample(proc.pid, "growth")
        print(
            f"  [cuga] growth rss_mb={_fmt_rss(growth_rec['rss_mb'])} (after {GROWTH_N + 1} requests: 1 converged + {GROWTH_N} growth)",
            file=sys.stderr,
        )

    finally:
        _kill_proc(proc)
        reader_thread.join(timeout=2)

    return cold_rec, converged_rec, growth_rec


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("Measuring CUGA server memory…", file=sys.stderr)
    print("Step 1/2: uvicorn_baseline", file=sys.stderr)
    baseline_rec = _measure_baseline()

    print("Step 2/2: CUGA server (cold → converged → growth)", file=sys.stderr)
    port = _find_free_port()
    cold_rec, converged_rec, growth_rec = _measure_cuga(port)

    def _cp(rec: dict) -> dict:
        """Flatten a process_sample record into the checkpoint schema."""
        return {
            "checkpoint": rec.get("label"),
            "rss_mb": rec.get("rss_mb"),
            "uss_mb": rec.get("uss_mb"),
            "pid": rec.get("pid"),
            "ts": rec.get("ts"),
            "platform": rec.get("platform"),
        }

    result = {
        "schema_version": 1,
        "converged_route": CONVERGED_ROUTE,
        "converged_route_note": (
            "GET /api/commands forces lazy import of cuga.backend.slash_commands "
            "and SkillRegistry; no LLM credentials required."
        ),
        "config": capture_config(),
        "checkpoints": [
            _cp(baseline_rec),
            _cp(cold_rec),
            _cp(converged_rec),
            _cp(growth_rec),
        ],
    }

    emit(result)


if __name__ == "__main__":
    main()
