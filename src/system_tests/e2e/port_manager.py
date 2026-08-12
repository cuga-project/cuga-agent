"""Dynamic port allocation for stability e2e tests."""

from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import threading
import time
import uuid

DYNAMIC_PORT_VARS = [
    "DYNACONF_SERVER_PORTS__CRM_API",
    "DYNACONF_SERVER_PORTS__CRM_MCP",
    "DYNACONF_SERVER_PORTS__DIGITAL_SALES_API",
    "DYNACONF_SERVER_PORTS__FILESYSTEM_MCP",
    "DYNACONF_SERVER_PORTS__EMAIL_SINK",
    "DYNACONF_SERVER_PORTS__EMAIL_MCP",
    "DYNACONF_SERVER_PORTS__DEMO",
    "DYNACONF_SERVER_PORTS__REGISTRY",
    "DYNACONF_SERVER_PORTS__MEMORY",
]


class PortManager:
    """Manages allocation of free ports to avoid conflicts."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reserved_ports: set[int] = set()

    def get_free_port(self) -> int:
        with self._lock:
            while True:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind(("127.0.0.1", 0))
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    port = sock.getsockname()[1]
                    if port not in self._reserved_ports:
                        self._reserved_ports.add(port)
                        return port
                time.sleep(0.01)

    def allocate_ports(self, *, e2b_mode: bool = False) -> dict[str, str]:
        port_vars = [
            var_name
            for var_name in DYNAMIC_PORT_VARS
            if not (e2b_mode and var_name == "DYNACONF_SERVER_PORTS__REGISTRY")
        ]
        allocations = {var_name: str(self.get_free_port()) for var_name in port_vars}
        if e2b_mode:
            allocations["DYNACONF_SERVER_PORTS__REGISTRY"] = "8001"
        return allocations


port_manager = PortManager()


def kill_process_on_port(port: int) -> None:
    if platform.system() == "Windows":
        try:
            import psutil

            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    for conn in proc.net_connections() or []:
                        if (
                            hasattr(conn, "laddr")
                            and conn.laddr
                            and conn.laddr.port == port
                            and conn.status == "LISTEN"
                        ):
                            proc.terminate()
                            try:
                                proc.wait(timeout=2)
                            except psutil.TimeoutExpired:
                                proc.kill()
                                proc.wait()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except ImportError:
            try:
                result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
                for line in result.stdout.split("\n"):
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        if len(parts) > 4:
                            subprocess.run(
                                ["taskkill", "/F", "/PID", parts[-1]],
                                check=False,
                                capture_output=True,
                            )
            except Exception:
                pass
        return

    try:
        result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            for pid in result.stdout.strip().split("\n"):
                if pid:
                    subprocess.run(["kill", "-9", pid], check=False)
    except FileNotFoundError:
        try:
            subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    ["ss", "-lptn", f"sport = :{port}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0 and result.stdout:
                    for line in result.stdout.split("\n"):
                        match = re.search(r"pid=(\d+)", line)
                        if match:
                            subprocess.run(["kill", "-9", match.group(1)], check=False)
            except FileNotFoundError:
                pass


def cleanup_ports(env_ports: dict[str, str]) -> None:
    for var_name, port in env_ports.items():
        try:
            kill_process_on_port(int(port))
        except (ValueError, TypeError):
            continue


def allocate_stability_env(*, e2b_mode: bool = False) -> dict[str, str]:
    env_ports = port_manager.allocate_ports(e2b_mode=e2b_mode)
    crm_db_path = os.path.join(os.getcwd(), "crm_tmp", f"crm_db_{uuid.uuid4()}")
    env_ports["DYNACONF_CRM_DB_PATH"] = crm_db_path
    return env_ports
