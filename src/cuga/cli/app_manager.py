"""App lifecycle manager: start/stop demo apps (email, filesystem, CRM) and core services (registry, demo, appworld, memory)."""

import os
import shutil
import time
from typing import Any, Callable

from loguru import logger

from cuga.config import PACKAGE_ROOT, settings


def _port(key: str, default: str) -> int:
    return int(os.environ.get(f"DYNACONF_SERVER_PORTS__{key}", default))


class AppManager:
    """Manages demo apps and core services: start, stop, ports."""

    def __init__(
        self,
        process_registry: dict[str, Any],
        run_service: Callable[[str, list[str], dict | None], Any],
        kill_ports: Callable[[list[int], bool], None],
        kill_process: Callable[[int], None],
        wait_tcp: Callable[[int, str, int, float], None],
        wait_http: Callable[[int, str], None],
    ):
        self._processes = process_registry
        self._run = run_service
        self._kill_ports = kill_ports
        self._kill_process = kill_process
        self._wait_tcp = wait_tcp
        self._wait_http = wait_http

    @property
    def fs_port(self) -> int:
        return _port("FILESYSTEM_MCP", "8112")

    @property
    def email_sink_port(self) -> int:
        return _port("EMAIL_SINK", "1025")

    @property
    def email_mcp_port(self) -> int:
        return _port("EMAIL_MCP", "8000")

    @property
    def crm_port(self) -> int:
        return int(os.environ.get("DYNACONF_SERVER_PORTS__CRM_API", str(settings.server_ports.crm_api)))

    @property
    def registry_port(self) -> int:
        return settings.server_ports.registry

    @property
    def demo_port(self) -> int:
        return settings.server_ports.demo

    def ports_for_apps(
        self,
        email: bool = False,
        filesystem: bool = False,
        crm: bool = False,
    ) -> list[int]:
        """Return ports to clean for given app flags."""
        ports: list[int] = []
        if filesystem:
            ports.append(self.fs_port)
        if email:
            ports.extend([self.email_sink_port, self.email_mcp_port])
        if crm:
            ports.append(self.crm_port)
        return ports

    def start_email(self, use_cache: bool = True) -> tuple[int, int]:
        """Start email sink and MCP server. Returns (sink_port, mcp_port)."""
        cmd = ["uvx"] + ([] if use_cache else ["--no-cache"])
        cmd.extend(["--from", "./docs/examples/demo_apps/email_mcp/mail_sink", "email_sink"])
        self._run("email-sink", cmd, {"DYNACONF_SERVER_PORTS__EMAIL_SINK": str(self.email_sink_port)})
        logger.info("Email sink started, waiting for it to be ready...")
        self._wait_tcp(self.email_sink_port, "Email sink", 60, 0.5)
        time.sleep(1)

        cmd = ["uvx"] + ([] if use_cache else ["--no-cache"])
        cmd.extend(["--from", "./docs/examples/demo_apps/email_mcp/mcp_server", "email_mcp"])
        self._run(
            "email-mcp",
            cmd,
            {
                "DYNACONF_SERVER_PORTS__EMAIL_SINK": str(self.email_sink_port),
                "DYNACONF_SERVER_PORTS__EMAIL_MCP": str(self.email_mcp_port),
            },
        )
        logger.info("Email MCP server started")
        time.sleep(2)
        return self.email_sink_port, self.email_mcp_port

    def start_filesystem(
        self,
        workspace_path: str,
        read_only: bool = False,
        use_cache: bool = True,
    ) -> int:
        """Start filesystem MCP server. Returns fs_port."""
        cmd = ["uvx"] + ([] if use_cache else ["--no-cache"])
        cmd.extend(["--from", "./docs/examples/demo_apps/file_system", "filesystem-server"])
        if read_only:
            cmd.append("--read-only")
        cmd.append(workspace_path)
        self._run("filesystem-server", cmd, {"DYNACONF_SERVER_PORTS__FILESYSTEM_MCP": str(self.fs_port)})
        logger.info("Filesystem MCP server started")
        time.sleep(2)
        return self.fs_port

    def start_crm(self, crm_db_path: str, use_cache: bool = True) -> int:
        """Start CRM API server. Returns crm_port."""
        port = settings.server_ports.crm_api
        logger.info(f"Starting CRM server on port {port}")
        cmd = ["uvx"] + ([] if use_cache else ["--no-cache"])
        cmd.extend(["--from", "./docs/examples/demo_apps/crm", "crm-server", "--port", str(port)])
        self._run(
            "crm-server",
            cmd,
            {"DYNACONF_SERVER_PORTS__CRM_API": str(port), "DYNACONF_CRM_DB_PATH": crm_db_path},
        )
        logger.info("CRM API server started")
        self._wait_http(port, "CRM API server")
        return port

    def start_registry(self, host: str = "0.0.0.0"):
        """Start registry server. Returns process."""
        cmd = [
            "uvicorn",
            "cuga.backend.tools_env.registry.registry.api_registry_server:app",
            "--host",
            host,
            "--port",
            str(self.registry_port),
        ]
        proc = self._run("registry", cmd, None)
        if proc:
            self._wait_http(self.registry_port, "Registry server")
        return proc

    def start_demo(self, host: str = "0.0.0.0", sandbox: bool = False):
        """Start demo server. Returns process."""
        server_path = os.path.join(PACKAGE_ROOT, "backend", "server", "main.py")
        if sandbox:
            cmd = [
                "uv",
                "run",
                "--group",
                "sandbox",
                "fastapi",
                "dev",
                server_path,
                "--host",
                host,
                "--no-reload",
                "--port",
                str(self.demo_port),
            ]
        else:
            cmd = [
                "fastapi",
                "dev",
                server_path,
                "--host",
                host,
                "--no-reload",
                "--port",
                str(self.demo_port),
            ]
        proc = self._run("demo", cmd, None)
        if proc:
            self._wait_http(self.demo_port, "Demo server")
        return proc

    def start_appworld(self) -> None:
        """Start AppWorld environment and API servers."""
        env_port = settings.server_ports.environment_url
        api_port = settings.server_ports.apis_url
        self._run(
            "appworld-environment",
            ["appworld", "serve", "environment", "--port", str(env_port)],
            None,
        )
        logger.info("Waiting for AppWorld environment server to start...")
        time.sleep(5)
        self._run("appworld-api", ["appworld", "serve", "apis", "--port", str(api_port)], None)

    def start_memory(self, host: str = "0.0.0.0"):
        """Start memory service. Returns process."""
        cmd = [
            "uv",
            "run",
            "--active",
            "--extra",
            "memory",
            "uvicorn",
            "cuga.backend.memory.agentic_memory.main:app",
            "--host",
            host,
            "--port",
            str(settings.server_ports.memory),
        ]
        return self._run("memory", cmd, None)

    def stop_email(self) -> None:
        """Stop email sink and MCP if running."""
        for name in ("email-sink", "email-mcp"):
            if name in self._processes:
                proc = self._processes[name]
                if proc and proc.poll() is None:
                    self._kill_process(proc.pid)
                del self._processes[name]

    def stop_filesystem(self) -> None:
        """Stop filesystem server if running."""
        if "filesystem-server" in self._processes:
            proc = self._processes["filesystem-server"]
            if proc and proc.poll() is None:
                self._kill_process(proc.pid)
            del self._processes["filesystem-server"]

    def stop_crm(self) -> None:
        """Stop CRM server if running."""
        if "crm-server" in self._processes:
            proc = self._processes["crm-server"]
            if proc and proc.poll() is None:
                self._kill_process(proc.pid)
            del self._processes["crm-server"]

    def stop_apps(
        self,
        email: bool = False,
        filesystem: bool = False,
        crm: bool = False,
    ) -> None:
        """Stop specified app servers."""
        if email:
            self.stop_email()
        if filesystem:
            self.stop_filesystem()
        if crm:
            self.stop_crm()

    def prepare_workspace(self, workspace_path: str, copy_examples: bool = True) -> list[str]:
        """Create workspace dir and optionally copy example files. Returns list of copied paths."""
        os.makedirs(workspace_path, exist_ok=True)
        if not copy_examples:
            return []
        source = PACKAGE_ROOT.parent.parent / "docs" / "examples" / "huggingface"
        examples = ["contacts.txt", "cuga_knowledge.md", "cuga_playbook.md", "email_template.md"]
        copied: list[str] = []
        for name in examples:
            src = source / name
            dst = os.path.join(workspace_path, name)
            if src.exists() and not os.path.exists(dst):
                shutil.copy2(str(src), dst)
                logger.info(f"   📄 Copied {name} → {dst}")
                copied.append(dst)
        return copied

    def prepare_crm_db(self, workspace_path: str) -> str:
        """Prepare CRM DB path, clean if exists. Returns crm_db_path."""
        path = os.environ.get(
            "DYNACONF_CRM_DB_PATH",
            os.path.join(os.getcwd(), "crm_tmp", "crm_db_default"),
        )
        path = os.path.abspath(path)
        os.environ["DYNACONF_CRM_DB_PATH"] = path
        if os.path.exists(path):
            logger.info(f"🧹 Cleaning up existing CRM DB at {path}")
            try:
                os.remove(path)
                logger.info("✅ CRM DB cleaned up")
            except OSError as e:
                logger.warning(f"⚠️  Could not remove CRM DB: {e}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def create_demo_crm_samples(self, workspace_path: str) -> list[str]:
        """Create sample CRM files (cities.txt, company.txt). Returns created paths."""
        os.makedirs(workspace_path, exist_ok=True)
        samples = {"cities.txt": ["Barcelona", "Bangalore", "Boulder"], "company.txt": ["Bangalore"]}
        created: list[str] = []
        for name, lines in samples.items():
            p = os.path.join(workspace_path, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            created.append(p)
        return created
