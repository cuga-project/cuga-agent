#!/usr/bin/env python3
"""Run the optional embedded Evolve service alongside CUGA."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROCESSES: list[subprocess.Popen[bytes]] = []


def stop_processes() -> None:
    for process in reversed(PROCESSES):
        if process.poll() is None:
            process.terminate()

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and any(process.poll() is None for process in PROCESSES):
        time.sleep(0.2)

    for process in reversed(PROCESSES):
        if process.poll() is None:
            process.kill()


def handle_signal(signum: int, _frame: object) -> None:
    print(f"[embedded-evolve] received signal {signum}", flush=True)
    stop_processes()


def wait_for_evolve(process: subprocess.Popen[bytes]) -> None:
    for _ in range(90):
        if process.poll() is not None:
            raise RuntimeError(f"Evolve exited during startup with status {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", 8201), timeout=1):
                return
        except OSError:
            time.sleep(1)
    raise TimeoutError("Evolve did not listen on port 8201 within 90 seconds")


def main() -> int:
    Path("/data/evolve").mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    evolve = subprocess.Popen(
        [
            "/app/.venv/bin/evolve-mcp",
            "--transport",
            "sse",
            "--host",
            "127.0.0.1",
            "--port",
            "8201",
        ],
        cwd="/data/evolve",
    )
    PROCESSES.append(evolve)
    wait_for_evolve(evolve)
    print("[embedded-evolve] Evolve ready", flush=True)

    cuga_env = os.environ.copy()
    cuga_env.update(
        {
            "CUGA_EMBEDDED_EVOLVE": "false",
            "DYNACONF_EVOLVE__ENABLED": "true",
            "DYNACONF_EVOLVE__MODE": "direct",
            "DYNACONF_EVOLVE__URL": "http://127.0.0.1:8201/sse",
        }
    )
    cuga = subprocess.Popen(
        ["/bin/sh", "/app/scripts/docker-entrypoint.sh"],
        cwd="/app",
        env=cuga_env,
    )
    PROCESSES.append(cuga)

    while True:
        for process in PROCESSES:
            status = process.poll()
            if status is not None:
                stop_processes()
                return status
        time.sleep(1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[embedded-evolve] startup failed: {error}", file=sys.stderr, flush=True)
        stop_processes()
        raise
