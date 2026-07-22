"""
measure_server_startup.py — measure CUGA server cold-start time.

Launches the CUGA uvicorn server in a background subprocess (exactly as
``cuga start`` does), then polls the /health/readiness endpoint every 0.25 s
until the server reports ``"ready": true``.  Falls back to watching stdout for
the log line ``"Application finished starting up..."`` if the HTTP endpoint is
consistently unreachable (e.g. missing config / env vars).

Emits a JSON record as the **last** stdout line:
    {"server_ready_s": <float>}   — on success
    {"server_ready_s": null}      — on timeout (90 s) or launch failure

Usage:
    uv run python src/system_tests/profiling/startup/measure_server_startup.py
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import threading
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TIMEOUT_S = 90.0
POLL_INTERVAL_S = 0.25
STARTUP_LOG_NEEDLE = "Application finished starting up..."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_free_port() -> int:
    """Bind to port 0 and let the OS pick a free ephemeral port."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _try_httpx(url: str) -> bool | None:
    """Return True if ready, False if not yet ready, None on connection error."""
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url)
        data = resp.json()
        return bool(data.get("ready"))
    except Exception:
        return None


def _try_urllib(url: str) -> bool | None:
    """Return True if ready, False if not yet ready, None on connection error."""
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
        return bool(data.get("ready"))
    except urllib.error.HTTPError as exc:
        # Server responded but with an error code — still reachable
        try:
            data = json.loads(exc.read())
            return bool(data.get("ready"))
        except Exception:
            return False
    except Exception:
        return None


def poll_readiness(url: str) -> bool | None:
    """Try httpx first, fall back to urllib."""
    result = _try_httpx(url)
    if result is None:
        result = _try_urllib(url)
    return result


# ---------------------------------------------------------------------------
# Main measurement
# ---------------------------------------------------------------------------


def measure() -> float | None:
    """
    Start the server, wait for readiness, return elapsed seconds or None.
    """
    port = find_free_port()
    url = f"http://localhost:{port}/health/readiness"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "cuga.backend.server.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        # Note: uvicorn does not have a --no-reload flag; omitting --reload
        # is the default (no auto-reload), which is what we want here.
    ]

    print(f"Launching server on port {port}…", file=sys.stderr)
    print(f"  cmd: {' '.join(cmd)}", file=sys.stderr)

    # Capture stdout/stderr so we can scan for the fallback log line.
    proc = None
    stdout_lines: list[str] = []
    fallback_event = threading.Event()

    def _reader(stream):
        """Background thread: read server output and watch for the fallback line."""
        for raw in stream:
            line = raw.rstrip("\n")
            stdout_lines.append(line)
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

    reader_thread = threading.Thread(target=_reader, args=(proc.stdout,), daemon=True)
    reader_thread.start()

    server_ready_s: float | None = None
    deadline = t0 + TIMEOUT_S

    try:
        while time.perf_counter() < deadline:
            # Primary signal: HTTP readiness endpoint
            ready = poll_readiness(url)
            if ready is True:
                server_ready_s = time.perf_counter() - t0
                print(f"  /health/readiness → ready after {server_ready_s:.3f}s", file=sys.stderr)
                break

            # Fallback signal: log line in stdout
            if fallback_event.is_set():
                server_ready_s = time.perf_counter() - t0
                print(
                    f"  Fallback log line detected after {server_ready_s:.3f}s"
                    " (HTTP endpoint did not report ready)",
                    file=sys.stderr,
                )
                break

            # Check if the process died early
            if proc.poll() is not None:
                reader_thread.join(timeout=2)
                print(
                    f"  Server process exited early (rc={proc.returncode})",
                    file=sys.stderr,
                )
                if stdout_lines:
                    print("  --- server output (last 20 lines) ---", file=sys.stderr)
                    for ln in stdout_lines[-20:]:
                        print(f"  | {ln}", file=sys.stderr)
                break

            time.sleep(POLL_INTERVAL_S)
        else:
            print(f"  Timed out after {TIMEOUT_S}s — server_ready_s = null", file=sys.stderr)

    finally:
        # Always kill the server and its process group
        if proc is not None and proc.poll() is None:
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
        reader_thread.join(timeout=2)

    return server_ready_s


def main() -> None:
    print("Measuring CUGA server cold-start…", file=sys.stderr)
    server_ready_s = measure()
    if server_ready_s is not None:
        print(f"server_ready_s = {server_ready_s:.3f}", file=sys.stderr)
    result = {"server_ready_s": server_ready_s}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
