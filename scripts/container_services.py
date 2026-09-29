"""Run bundled Evolve and CUGA together, with Kubernetes owning restarts."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import shutil
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable


class Shutdown(Exception):
    """The container received a termination signal."""


def evolve_ready() -> bool:
    # Opening SSE checks the real transport; a listening port alone is not ready.
    try:
        with urllib.request.urlopen("http://127.0.0.1:8201/sse", timeout=1) as response:
            return response.readline().strip() == b"event: endpoint"
    except (OSError, urllib.error.URLError):
        return False


def supervise(
    cuga_command: list[str],
    evolve_command: list[str],
    *,
    ready: Callable[[], bool] = evolve_ready,
    startup_timeout: float = 120,
    shutdown_timeout: float = 20,
) -> int:
    children: list[subprocess.Popen] = []

    def shutdown(_signum: int, _frame: object) -> None:
        raise Shutdown

    previous = {sig: signal.signal(sig, shutdown) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        children.append(subprocess.Popen(evolve_command, start_new_session=True))
        deadline = time.monotonic() + startup_timeout
        while not ready():
            if children[0].poll() is not None:
                raise RuntimeError(f"Evolve exited during startup ({children[0].returncode})")
            if time.monotonic() >= deadline:
                raise RuntimeError("Evolve did not become ready before the startup timeout")
            time.sleep(0.1)
        if children[0].poll() is not None:
            raise RuntimeError("Evolve exited during startup")
        print("Evolve ready; starting CUGA", flush=True)
        children.append(subprocess.Popen(cuga_command, start_new_session=True))
        while True:
            for name, child in zip(("Evolve", "CUGA"), children, strict=True):
                if child.poll() is not None:
                    raise RuntimeError(f"{name} exited ({child.returncode}); stopping the container")
            time.sleep(0.1)
    except Shutdown:
        return 0
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        # Signal groups so CUGA's registry and other descendants stop too.
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        for child in reversed(children):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + shutdown_timeout
        for child in reversed(children):
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        # When running as PID 1, reap adopted grandchildren after shutdown.
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def evolve_endpoint() -> tuple[str, str]:
    """Resolve the same file and environment settings as the CUGA process."""
    from cuga.config import settings

    return str(settings.evolve.mode).lower(), str(settings.evolve.url).rstrip("/")


def main() -> int:
    command = sys.argv[1:]
    if not command:
        raise SystemExit("Missing CUGA command")
    # Explicit operator endpoints remain external, even when the image includes
    # Evolve. Never replace their storage/namespace with the bundled service.
    mode, url = evolve_endpoint()
    if mode == "registry" or (url and url != "http://127.0.0.1:8201/sse"):
        os.execvp(command[0], command)
    evolve_command = shutil.which("evolve-mcp")
    if evolve_command is None:
        os.execvp(command[0], command)
    data_dir = Path(os.environ.setdefault("EVOLVE_DATA_DIR", "/data/dbs/evolve"))
    data_dir.mkdir(parents=True, exist_ok=True)
    # The deployer owns the integration toggle. Provision the installed service
    # and its connection settings without enabling or disabling CUGA memory.
    os.environ["DYNACONF_EVOLVE__MODE"] = "direct"
    os.environ["DYNACONF_EVOLVE__URL"] = "http://127.0.0.1:8201/sse"
    # A fresh credential authenticates CUGA-supplied scope on the private API.
    # It is never sent to the browser or reused for external Evolve deployments.
    os.environ["CUGA_EVOLVE_API_TOKEN"] = secrets.token_urlsafe(32)
    return supervise(
        command,
        [sys.executable, "-m", "cuga.backend.evolve.http_worker"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
