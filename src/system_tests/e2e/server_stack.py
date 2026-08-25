"""Start demo/CRM stacks once per test class (sync, subprocess-based)."""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cuga.config import settings

DEMO_COMMAND = ["uv", "run", "demo"]
REGISTRY_COMMAND = ["uv", "run", "registry"]
DIGITAL_SALES_MCP_COMMAND = ["uv", "run", "digital_sales_openapi"]


@dataclass
class StackHandles:
    processes: list[subprocess.Popen] = field(default_factory=list)
    log_handles: list = field(default_factory=list)
    log_dir: Optional[Path] = None


def get_preexec_fn():
    if hasattr(os, "setsid"):
        return os.setsid
    return None


def get_subprocess_env(overrides: Optional[dict[str, Optional[str]]] = None) -> dict[str, str]:
    env = os.environ.copy()
    env["UV_NO_SYNC"] = "1"
    if platform.system().lower().startswith("win"):
        env["PYTHONIOENCODING"] = "utf-8"
    if overrides:
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    return env


def get_sigkill():
    return getattr(signal, "SIGKILL", 9)


def kill_process_group(process, sig=None) -> None:
    if process is None or process.poll() is not None:
        return

    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        if sig is None:
            sig = signal.SIGTERM
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, OSError):
            pass
        return

    is_kill = False
    if sig is not None:
        if hasattr(signal, "SIGKILL"):
            is_kill = sig == signal.SIGKILL
        else:
            is_kill = sig == 9
    if is_kill:
        process.kill()
    else:
        process.terminate()


def wait_for_http_ready(
    port: int,
    process: Optional[subprocess.Popen] = None,
    log_file: Optional[str] = None,
    process_name: str = "server",
    timeout: Optional[float] = None,
    interval: float = 0.25,
) -> None:
    if timeout is None:
        timeout = 600.0 if platform.system() == "Windows" else 300.0

    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    last_error = None

    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            error_msg = f"{process_name} process died with return code {process.returncode}"
            if log_file and os.path.exists(log_file):
                try:
                    with open(log_file, encoding="utf-8", errors="replace") as f:
                        lines = f.read().split("\n")
                    error_msg += f"\n\nLast 50 lines of {process_name} log ({log_file}):\n" + "\n".join(
                        lines[-50:]
                    )
                except OSError as exc:
                    error_msg += f"\n\nCould not read log file {log_file}: {exc}"
            raise RuntimeError(error_msg)

        try:
            urllib.request.urlopen(url, timeout=1.0)
            print(f"Server on port {port} is ready!")
            return
        except urllib.error.HTTPError as exc:
            # 4xx means the app is up and routing (the probe path may not exist);
            # 5xx means it is still starting or broken, so keep waiting.
            if exc.code < 500:
                print(f"Server on port {port} is ready!")
                return
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(interval)

    error_msg = (
        f"{process_name} did not become ready after {timeout:.1f} seconds. "
        f"Please check if the server started correctly on port {port}."
    )
    if last_error is not None:
        error_msg += f" Last error: {last_error}"
    raise TimeoutError(error_msg)


def _class_log_dir(class_name: str) -> Path:
    log_dir = Path(__file__).resolve().parent / "logs" / class_name
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ["CUGA_LOGGING_DIR"] = str(log_dir / "logging")
    return log_dir


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8", buffering=1)


def start_digital_sales_stack(class_name: str) -> StackHandles:
    log_dir = _class_log_dir(class_name)
    handles = StackHandles(log_dir=log_dir)

    ds_log = log_dir / "digital_sales_mcp.log"
    registry_log = log_dir / "registry_server.log"
    demo_log = log_dir / "demo_server.log"

    ds_handle = _open_log(ds_log)
    registry_handle = _open_log(registry_log)
    demo_handle = _open_log(demo_log)
    handles.log_handles.extend([ds_handle, registry_handle, demo_handle])

    env = get_subprocess_env()
    preexec = get_preexec_fn()

    try:
        ds_proc = subprocess.Popen(
            DIGITAL_SALES_MCP_COMMAND,
            stdout=ds_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            preexec_fn=preexec,
        )
        handles.processes.append(ds_proc)
        wait_for_http_ready(
            settings.server_ports.digital_sales_api,
            process=ds_proc,
            log_file=str(ds_log),
            process_name="Digital sales MCP server",
        )

        registry_proc = subprocess.Popen(
            REGISTRY_COMMAND,
            stdout=registry_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            preexec_fn=preexec,
        )
        handles.processes.append(registry_proc)
        wait_for_http_ready(
            settings.server_ports.registry,
            process=registry_proc,
            log_file=str(registry_log),
            process_name="Registry server",
        )

        demo_proc = subprocess.Popen(
            DEMO_COMMAND,
            stdout=demo_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            preexec_fn=preexec,
        )
        handles.processes.append(demo_proc)
        wait_for_http_ready(
            settings.server_ports.demo,
            process=demo_proc,
            log_file=str(demo_log),
            process_name="Demo server",
        )
        return handles
    except Exception:
        stop_stack(handles)
        raise


def start_crm_stack(class_name: str, mode: str = "default") -> StackHandles:
    log_dir = _class_log_dir(class_name)
    handles = StackHandles(log_dir=log_dir)
    demo_log = log_dir / "demo_server.log"
    demo_handle = _open_log(demo_log)
    handles.log_handles.append(demo_handle)

    command = ["uv", "run", "cuga", "start", "demo_crm"]
    if mode in ("hf",):
        command.extend(["--no-email", "--read-only"])

    try:
        demo_proc = subprocess.Popen(
            command,
            stdout=demo_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=get_subprocess_env(),
            preexec_fn=get_preexec_fn(),
        )
        handles.processes.append(demo_proc)
        wait_for_http_ready(
            settings.server_ports.demo,
            process=demo_proc,
            log_file=str(demo_log),
            process_name="Demo CRM server",
        )
        return handles
    except Exception:
        stop_stack(handles)
        raise


def stop_stack(handles: Optional[StackHandles]) -> None:
    if handles is None:
        return
    for process in handles.processes:
        try:
            if process.poll() is None:
                kill_process_group(process, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    kill_process_group(process, get_sigkill())
                    process.wait(timeout=5)
        except (ProcessLookupError, OSError):
            pass
    for handle in handles.log_handles:
        try:
            handle.close()
        except OSError:
            pass
    handles.processes.clear()
    handles.log_handles.clear()
