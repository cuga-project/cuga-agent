"""App lifecycle manager: start/stop demo apps (email, filesystem, CRM) and core services (registry, demo, appworld)."""

import os
import shutil
import sys
import time
from typing import Any, Callable

from loguru import logger

from cuga.config import DEMO_TOOLS_ROOT, PACKAGE_ROOT, settings


def _demo_app_path(*parts: str) -> str:
    return str(DEMO_TOOLS_ROOT.joinpath(*parts).resolve())


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
    def docs_port(self) -> int:
        return _port("DOCS_MCP", str(getattr(settings.server_ports, "docs_mcp", 8113)))

    @property
    def oak_health_port(self) -> int:
        return int(
            os.environ.get(
                "DYNACONF_SERVER_PORTS__OAK_HEALTH_API",
                str(getattr(settings.server_ports, "oak_health_api", 8090)),
            )
        )

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
        docs: bool = False,
        oak_health: bool = False,
    ) -> list[int]:
        """Return ports to clean for given app flags."""
        ports: list[int] = []
        if filesystem:
            ports.append(self.fs_port)
        if email:
            ports.extend([self.email_sink_port, self.email_mcp_port])
        if crm:
            ports.append(self.crm_port)
        if docs:
            ports.append(self.docs_port)
        if oak_health:
            ports.append(self.oak_health_port)
        return ports

    def start_email(self, use_cache: bool = True) -> tuple[int, int]:
        """Start email sink and MCP server. Returns (sink_port, mcp_port)."""
        sink_script = str(DEMO_TOOLS_ROOT / "email_mcp" / "mail_sink" / "server.py")
        self._run(
            "email-sink",
            [sys.executable, sink_script],
            {"DYNACONF_SERVER_PORTS__EMAIL_SINK": str(self.email_sink_port)},
        )
        logger.info("Email sink started, waiting for it to be ready...")
        self._wait_tcp(self.email_sink_port, "Email sink", 60, 0.5)
        time.sleep(1)

        mcp_script = str(DEMO_TOOLS_ROOT / "email_mcp" / "mcp_server" / "server.py")
        cmd = [sys.executable, mcp_script]
        self._run(
            "email-mcp",
            cmd,
            {
                "DYNACONF_SERVER_PORTS__EMAIL_SINK": str(self.email_sink_port),
                "DYNACONF_SERVER_PORTS__EMAIL_MCP": str(self.email_mcp_port),
            },
        )
        logger.info("Email MCP server started, waiting for it to be ready...")
        self._wait_http(self.email_mcp_port, "Email MCP server")
        return self.email_sink_port, self.email_mcp_port

    def start_filesystem(
        self,
        workspace_path: str,
        read_only: bool = False,
        use_cache: bool = True,
    ) -> int:
        """Start filesystem MCP server. Returns fs_port."""
        fs_script = str(DEMO_TOOLS_ROOT / "file_system" / "main.py")
        cmd = [sys.executable, fs_script]
        if read_only:
            cmd.append("--read-only")
        cmd.append(workspace_path)
        self._run("filesystem-server", cmd, {"DYNACONF_SERVER_PORTS__FILESYSTEM_MCP": str(self.fs_port)})
        logger.info("Filesystem MCP subprocess started; waiting until port %s accepts HTTP…", self.fs_port)
        self._wait_http(self.fs_port, "Filesystem MCP server")
        return self.fs_port

    def start_docs(self, use_cache: bool = True) -> int:
        """Start docs MCP server. Returns docs_port."""
        port = self.docs_port
        logger.info(f"Starting docs MCP server on port {port}")
        docs_script = DEMO_TOOLS_ROOT / "docs_mcp" / "docs_mcp_server.py"
        cmd = [sys.executable, str(docs_script)]
        self._run("docs-mcp", cmd, {"DYNACONF_SERVER_PORTS__DOCS_MCP": str(port)})
        logger.info("Docs MCP server started, waiting for it to be ready...")
        self._wait_http(port, "Docs MCP server")
        return port

    def start_crm(self, crm_db_path: str, use_cache: bool = True) -> int:
        """Start CRM API server. Returns crm_port."""
        port = settings.server_ports.crm_api
        logger.info(f"Starting CRM server on port {port}")
        cmd = [sys.executable, "-m", "cuga.demo_tools.crm.crm_api.main", "--port", str(port)]
        self._run(
            "crm-server",
            cmd,
            {"DYNACONF_SERVER_PORTS__CRM_API": str(port), "DYNACONF_CRM_DB_PATH": crm_db_path},
        )
        logger.info("CRM API server started")
        self._wait_http(port, "CRM API server")
        return port

    def start_oak_health(self, use_cache: bool = True) -> int:
        """Start cuga-oak-health OpenAPI server (pre-installed). Returns port."""
        port = self.oak_health_port
        logger.info("Starting cuga-oak-health OpenAPI server")
        cmd = ["uv", "run", "--no-sync", "cuga-oak-health"]
        self._run(
            "oak-health",
            cmd,
            {"DYNACONF_SERVER_PORTS__OAK_HEALTH_API": str(port), "PORT": str(port)},
        )
        self._wait_http(port, "Oak Health API")
        return port

    def start_registry(self, host: str = "0.0.0.0"):
        """Start registry server. Returns process."""
        cmd = [
            sys.executable,
            "-m",
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
        ssl_keyfile = os.environ.get("SSL_KEYFILE", "").strip()
        ssl_certfile = os.environ.get("SSL_CERTFILE", "").strip()
        use_ssl = bool(ssl_keyfile and ssl_certfile)

        app_import = "cuga.backend.server.main:app"
        uvicorn_args = [
            app_import,
            "--host",
            host,
            "--port",
            str(self.demo_port),
        ]
        if use_ssl:
            uvicorn_args += ["--ssl-keyfile", ssl_keyfile, "--ssl-certfile", ssl_certfile]

        # Use PACKAGE_ROOT to find the root directory consistently
        project_root = os.path.abspath(os.path.join(PACKAGE_ROOT, "..", ".."))

        if sandbox:
            # Sandbox needs uv run for group isolation
            cmd = [
                "uv",
                "run",
                "--no-sync",
                "--directory",
                project_root,
                "--group",
                "sandbox",
                "uvicorn",
            ] + uvicorn_args
        else:
            cmd = [sys.executable, "-m", "uvicorn"] + uvicorn_args

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

    def stop_docs(self) -> None:
        """Stop docs MCP server if running."""
        if "docs-mcp" in self._processes:
            proc = self._processes["docs-mcp"]
            if proc and proc.poll() is None:
                self._kill_process(proc.pid)
            del self._processes["docs-mcp"]

    def stop_oak_health(self) -> None:
        if "oak-health" in self._processes:
            proc = self._processes["oak-health"]
            if proc and proc.poll() is None:
                self._kill_process(proc.pid)
            del self._processes["oak-health"]

    def stop_apps(
        self,
        email: bool = False,
        filesystem: bool = False,
        crm: bool = False,
        docs: bool = False,
        oak_health: bool = False,
    ) -> None:
        """Stop specified app servers."""
        if email:
            self.stop_email()
        if filesystem:
            self.stop_filesystem()
        if crm:
            self.stop_crm()
        if docs:
            self.stop_docs()
        if oak_health:
            self.stop_oak_health()

    def prepare_workspace(self, workspace_path: str, copy_examples: bool = True) -> list[str]:
        """Create workspace dir and optionally copy example files. Returns list of copied paths."""
        os.makedirs(workspace_path, exist_ok=True)
        if not copy_examples:
            return []
        source = DEMO_TOOLS_ROOT / "huggingface"
        examples = [
            "contacts.txt",
            "cuga_knowledge.md",
            "cuga_playbook.md",
            "email_template.md",
            "sovereign_core_overview.pdf",
        ]
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
