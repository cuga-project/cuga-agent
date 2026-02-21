#!/usr/bin/env python3
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from typing import List, Optional

import httpx
import psutil
import typer
from loguru import logger
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cuga.config import PACKAGE_ROOT, TRAJECTORY_DATA_DIR, get_user_data_path, settings
from cuga.configurations.instructions_manager import InstructionsManager
from cuga.backend.cuga_graph.policy.cli import app as policy_app
from cuga.backend.server.demo_manage_setup import (
    build_tools_from_apps,
    get_default_apps_for_preset,
    setup_demo_manage_config,
)
from cuga.backend.server.managed_mcp import ensure_managed_mcp_file_exists, get_managed_mcp_path
from cuga.cli.app_manager import AppManager

instructions_manager = InstructionsManager()


def _build_workspace_policies(workspace_abs: str, include_email: bool = False) -> str:
    """Build full policy content for workspace: filesystem scope, cuga knowledge, email templates."""
    policy = f"""## Plan
For the filesystem application: write or read files only from `{workspace_abs}`
when user asks questions about cuga then answer the question by first reading the filesystem information inside the file `{workspace_abs}/cuga_knowledge.md` then answer the question
When user asks to use email templates assume it has <results> placeholder to replace with the results
The email of my assistant is jane@example.com"""
    if include_email:
        policy += "\nFor the email application: send emails only using the local SMTP sink"
    return policy


def _make_app_manager() -> AppManager:
    return AppManager(
        process_registry=direct_processes,
        run_service=lambda n, c, e: run_direct_service(n, c, env_vars=e),
        kill_ports=kill_processes_by_port,
        kill_process=kill_process_tree,
        wait_tcp=lambda p, lbl, r, i: wait_for_tcp_port(p, lbl, max_retries=r, retry_interval=i),
        wait_http=wait_for_server,
    )


console = Console()

os.environ["DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED"] = "true"

app = typer.Typer(
    help="Cuga CLI for managing services with direct execution",
    short_help="Service management tool for Cuga components",
)

if settings.advanced_features.enable_memory:
    from cuga.backend.memory.cli import memory_app

    app.add_typer(memory_app, name="memory")

app.add_typer(policy_app, name="policy")

# Global variables to track running direct processes (registry/demo)
direct_processes = {}
shutdown_event = threading.Event()

# OS detection
IS_WINDOWS = platform.system().lower().startswith("win")

# Playwright launcher state (for extension mode)
_playwright_thread: Optional[threading.Thread] = None
_playwright_started: bool = False


def kill_processes_by_port(ports: List[int], silent: bool = False):
    """Kill processes listening on specified ports.

    Args:
        ports: List of port numbers to check
        silent: If True, don't log (useful when called from signal handlers)
    """
    killed_any = False
    for port in ports:
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    # Get connections separately to handle cases where it's not available
                    try:
                        connections = proc.net_connections()
                    except (psutil.AccessDenied, AttributeError):
                        connections = []

                    for conn in connections:
                        if hasattr(conn, 'laddr') and conn.laddr and conn.laddr.port == port:
                            if not silent:
                                logger.info(
                                    f"🔄 Killing existing process {proc.info['name']} (PID: {proc.info['pid']}) on port {port}"
                                )
                            psutil.Process(proc.info['pid']).terminate()
                            killed_any = True
                            time.sleep(0.5)
                            try:
                                psutil.Process(proc.info['pid']).kill()
                            except psutil.NoSuchProcess:
                                pass
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception as e:
            if not silent:
                logger.debug(f"Error killing processes on port {port}: {e}")

    if killed_any:
        if not silent:
            logger.info("✨ Cleaned up existing processes")
        time.sleep(1)


def wait_for_tcp_port(
    port: int, server_name: str = "Server", max_retries: int = 20, retry_interval: float = 0.5
):
    """
    Wait for a TCP port to be listening (useful for non-HTTP servers like SMTP).

    Args:
        port: The port number to check
        server_name: Name of the server for logging (default: "Server")
        max_retries: Maximum number of retry attempts (default: 20)
        retry_interval: Time in seconds between retries (default: 0.5)

    Raises:
        TimeoutError: If the port doesn't become ready within max_retries attempts
    """
    import socket

    for attempt in range(max_retries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(('127.0.0.1', port))
                if result == 0:
                    logger.info(f"{server_name} is ready on port {port}!")
                    return
        except Exception:
            pass

        if attempt < max_retries - 1:
            time.sleep(retry_interval)
        else:
            raise TimeoutError(
                f"{server_name} did not become ready after {max_retries * retry_interval:.1f} seconds. "
                f"Please check if the server started correctly on port {port}."
            )


def wait_for_server(
    port: int, server_name: str = "Server", max_retries: int = None, retry_interval: float = 0.5
):
    """
    Wait for a server to be ready by pinging its health endpoint.

    Args:
        port: The port number the server is running on
        server_name: Name of the server for logging (default: "Server")
        max_retries: Maximum number of retry attempts (default: 120 on Unix, 300 on Windows)
        retry_interval: Time in seconds between retries (default: 0.5)

    Raises:
        TimeoutError: If the server doesn't become ready within max_retries attempts
    """
    # Use longer timeout on Windows due to slower package installation and process startup
    if max_retries is None:
        max_retries = 300 if platform.system() == "Windows" else 120

    url = f"http://127.0.0.1:{port}/"

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=1.0) as client:
                response = client.get(url)
                if response.status_code == 200:
                    logger.info(f"{server_name} is ready!")
                    return
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
            if attempt < max_retries - 1:
                time.sleep(retry_interval)
            else:
                raise TimeoutError(
                    f"{server_name} did not become ready after {max_retries * retry_interval:.1f} seconds. "
                    f"Please check if the server started correctly on port {port}."
                )


def wait_for_registry_server(port: int, max_retries: int = None, retry_interval: float = 0.5):
    """
    Wait for the registry server to be ready by pinging its health endpoint.

    Args:
        port: The port number the registry server is running on
        max_retries: Maximum number of retry attempts (default: 120)
        retry_interval: Time in seconds between retries (default: 0.5)

    Raises:
        TimeoutError: If the server doesn't become ready within max_retries attempts
    """
    wait_for_server(port, "Registry server", max_retries, retry_interval)


def kill_process_tree(pid):
    """Kill a process and all its children.

    Note: No logging in this function to avoid deadlock when called from signal handler.
    """
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)

        # Terminate children first
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass

        # Wait a bit for graceful termination
        psutil.wait_procs(children, timeout=3)

        # Kill any remaining children
        for child in children:
            try:
                if child.is_running():
                    child.kill()
            except psutil.NoSuchProcess:
                pass

        # Now terminate the parent
        try:
            parent.terminate()
            parent.wait(timeout=3)
        except psutil.TimeoutExpired:
            parent.kill()
    except psutil.NoSuchProcess:
        pass
    except Exception:
        # Silently ignore errors to avoid deadlock in signal handler
        pass


def start_extension_browser_if_configured():
    """Start a Chromium instance with the MV3 extension if config enables it.

    Uses Playwright persistent context to load the extension from
    `frontend_workspaces/extension/releases/chrome-mv3`.
    Runs in a daemon thread and stops when the CLI receives a shutdown signal.
    """
    global _playwright_thread, _playwright_started

    use_extension = getattr(getattr(settings, "advanced_features", {}), "use_extension", False)
    if not use_extension:
        return

    if _playwright_started and _playwright_thread and _playwright_thread.is_alive():
        logger.info("Extension browser already running.")
        return

    extension_dir = os.path.join(
        PACKAGE_ROOT, "..", "frontend_workspaces", "extension", "releases", "chrome-mv3"
    )
    if not os.path.isdir(extension_dir):
        logger.error(
            f"Chrome MV3 extension directory not found: {extension_dir}. "
            "Build the extension or adjust your installation."
        )
        return

    def _runner():
        try:
            # Import here to avoid hard dependency if feature is off
            from playwright.sync_api import sync_playwright

            user_data_dir = get_user_data_path() or os.path.join(os.getcwd(), "logging", "pw_user_data")
            os.makedirs(user_data_dir, exist_ok=True)

            logger.info("Launching Chromium with extension (Playwright persistent context)...")
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=False,
                    args=[
                        f"--disable-extensions-except={extension_dir}",
                        f"--load-extension={extension_dir}",
                    ],
                    no_viewport=True,
                )
                # Open a page to the demo start URL (if available), otherwise about:blank
                try:
                    start_url = getattr(getattr(settings, "demo_mode", {}), "start_url", None)
                except Exception:
                    start_url = None
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if start_url:
                    page.goto(start_url, timeout=20000)
                else:
                    page.goto("about:blank", timeout=20000)

                # Keep context alive until shutdown
                while not shutdown_event.is_set():
                    time.sleep(0.2)

                try:
                    ctx.close()
                except Exception:
                    pass
        except ImportError:
            logger.error(
                "Playwright is not installed. Install with 'pip install playwright' "
                "and run 'playwright install chromium'."
            )
        except Exception as e:
            logger.error(f"Failed to launch Playwright with extension: {e}")

    _playwright_thread = threading.Thread(target=_runner, name="playwright-extension", daemon=True)
    _playwright_thread.start()
    _playwright_started = True


def signal_handler(signum, frame):
    """Handle SIGINT (Ctrl+C) to gracefully shutdown direct processes."""
    shutdown_event.set()

    # Force stop direct processes
    stop_direct_processes()

    # Only kill processes on ports that are actually being used by running services
    ports_to_kill = []
    if "registry" in direct_processes:
        ports_to_kill.append(settings.server_ports.registry)
    if "demo" in direct_processes:
        ports_to_kill.append(settings.server_ports.demo)
    if "memory" in direct_processes:
        ports_to_kill.append(settings.server_ports.memory)
    if "appworld-environment" in direct_processes:
        ports_to_kill.append(settings.server_ports.environment_url)
    if "appworld-api" in direct_processes:
        ports_to_kill.append(settings.server_ports.apis_url)

    if ports_to_kill:
        kill_processes_by_port(ports_to_kill, silent=True)

    # Don't use logger here - signal handlers can't safely use loguru
    # Use print to stderr instead to avoid deadlock
    print("All processes stopped.", file=sys.stderr)
    sys.exit(0)


def stop_direct_processes():
    """Stop all direct processes gracefully, then forcefully.

    Note: No logging in this function to avoid deadlock when called from signal handler.
    """
    for service_name, process in direct_processes.items():
        if process and process.poll() is None:
            try:
                # First try to kill the entire process tree
                kill_process_tree(process.pid)
            except Exception:
                # Fallback to original method
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                except Exception:
                    # Silently ignore to avoid deadlock
                    pass

    direct_processes.clear()


def run_direct_service(
    service_name: str,
    command: List[str],
    cwd: Optional[str] = None,
    log_file: Optional[str] = None,
    env_vars: Optional[dict] = None,
):
    """Run a service command directly and return the process."""
    try:
        logger.info(f"Starting {service_name} directly with command: {' '.join(command)}")

        # Force colored output and ensure proper environment variables
        env = os.environ.copy()
        env['FORCE_COLOR'] = '1'

        # On Windows, set UTF-8 encoding to handle Unicode characters in subprocess output
        if IS_WINDOWS:
            env['PYTHONIOENCODING'] = 'utf-8'

        # Add any additional environment variables
        if env_vars:
            env.update(env_vars)

        # Ensure APPWORLD_ROOT is used only for appworld commands
        joined = ' '.join(command).lower()
        if 'appworld' in joined:
            cwd = env.get('APPWORLD_ROOT')
        else:
            # Keep current working dir for non-appworld services (e.g., memory)
            cwd = None
        # Log environment variables for debugging
        logger.debug(f"APPWORLD_ROOT: {env.get('APPWORLD_ROOT')}")
        logger.debug(f"Working directory: {cwd or os.getcwd()}")

        # Start the process with a new process group to make it easier to kill
        kwargs = {'cwd': cwd, 'env': env, 'preexec_fn': os.setsid if not IS_WINDOWS else None}

        # Redirect output to log file if provided
        if log_file:
            log_path = os.path.abspath(log_file)
            log_dir = os.path.dirname(log_path)
            os.makedirs(log_dir, exist_ok=True)
            log_handle = open(log_path, 'a', encoding='utf-8')
            kwargs['stdout'] = log_handle
            kwargs['stderr'] = subprocess.STDOUT
            logger.info(f"Redirecting {service_name} output to {log_path}")

        process = subprocess.Popen(command, **kwargs)

        direct_processes[service_name] = process
        return process

    except Exception as e:
        logger.error(f"Error starting {service_name}: {e}")
        return None


def wait_for_direct_processes():
    """Wait for all direct processes to complete or be interrupted."""
    try:
        while direct_processes and not shutdown_event.is_set():
            # Check if any process has terminated
            terminated = []
            for service_name, process in direct_processes.items():
                if process.poll() is not None:
                    terminated.append(service_name)
                    logger.info(f"{service_name} has terminated")

            # Remove terminated processes
            for service_name in terminated:
                del direct_processes[service_name]

            if not direct_processes:
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        stop_direct_processes()


@app.callback()
def callback(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose output with detailed logging information"
    ),
):
    """
    Cuga CLI: A management tool for Cuga services with direct execution.

    This tool helps you control various components of the Cuga ecosystem:

    - demo: Both registry and demo agent (runs directly)
    - demo_crm: CRM demo with email MCP, mail sink, and CRM API (runs directly)
    - demo_supervisor: Same as demo_crm but with CugaSupervisor multi-agent coordination
    - registry: The MCP registry service only (runs directly)
    - appworld: AppWorld environment and API servers (runs directly)
    - memory: The memory service (runs directly)

    Examples:
      cuga start demo           # Start both registry and demo agent directly
      cuga start demo_crm       # Start CRM demo with all required services
      cuga start demo_supervisor # Start CRM demo with supervisor multi-agent mode
      cuga start registry       # Start registry only
      cuga start appworld       # Start AppWorld servers
      cuga start memory         # Start memory service
    """
    if verbose:
        logger.level("DEBUG")

    # Set up signal handler for graceful shutdown of direct processes
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def _start_demo_crm_services(
    host: str,
    sandbox: bool,
    read_only: bool,
    sample_memory_data: bool,
    no_email: bool,
    enable_supervisor: bool = False,
    tools: list | None = None,
    cuga_workspace: str | None = None,
):
    """Shared startup logic for demo_crm and demo_supervisor services.

    Args:
        enable_supervisor: If True, enables CugaSupervisor multi-agent coordination.
    """
    service_label = "Supervisor Demo" if enable_supervisor else "CRM Demo"

    try:
        os.environ["CUGA_MANAGER_MODE"] = "true"
        os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
        os.environ["MCP_SERVERS_FILE"] = "none"
        ensure_managed_mcp_file_exists(get_managed_mcp_path())
        logger.info("🧹 Resetting config db and setting up manage demo_crm...")
        setup_demo_manage_config("demo_crm", no_email=no_email, tools=tools)

        # Configure supervisor mode
        if enable_supervisor:
            os.environ["DYNACONF_SUPERVISOR__ENABLED"] = "true"
            supervisor_config_path = os.path.join(
                PACKAGE_ROOT, "backend", "tools_env", "registry", "config", "supervisor_demo_crm.yaml"
            )
            os.environ["DYNACONF_SUPERVISOR__CONFIG_PATH"] = supervisor_config_path
            logger.info(f"Supervisor enabled with config: {supervisor_config_path}")
        else:
            os.environ["DYNACONF_SUPERVISOR__ENABLED"] = "false"

        workspace_path = cuga_workspace or os.path.join(os.getcwd(), "cuga_workspace")
        workspace_abs = os.path.abspath(workspace_path)
        app_mgr = _make_app_manager()
        app_mgr.prepare_workspace(workspace_path)
        if sample_memory_data:
            logger.info("📝 Generating sample CRM workspace files...")
            for p in app_mgr.create_demo_crm_samples(workspace_path):
                logger.info(f"   • {p}")

        tool_names = {t.get("name") for t in (tools or [])}
        start_email = (not no_email) and ("email" in tool_names if tools else True)
        policies_content = _build_workspace_policies(workspace_abs, include_email=start_email)
        os.environ["CUGA_POLICIES_CONTENT"] = policies_content
        os.environ["CUGA_LOAD_POLICIES"] = "true"
        logger.info(f"📋 Policies configured for {service_label}")

        start_filesystem = "filesystem" in tool_names if tools else True
        start_crm = "crm" in tool_names if tools else True

        ports_to_clean = app_mgr.ports_for_apps(start_email, start_filesystem, start_crm)
        ports_to_clean.extend([settings.server_ports.registry, settings.server_ports.demo])
        logger.info("🧹 Checking for existing processes on required ports...")
        kill_processes_by_port(ports_to_clean)

        os.environ["CUGA_HOST"] = host
        if sandbox:
            logger.info(
                f"Starting {service_label} with remote sandbox mode enabled (features.local_sandbox=false)"
            )
            os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"

        if start_email:
            app_mgr.start_email()
        else:
            logger.info("Email services disabled (--no-email flag or not in tools)")

        if start_filesystem:
            app_mgr.start_filesystem(workspace_path, read_only=read_only)

        if start_crm:
            crm_db_path = app_mgr.prepare_crm_db(workspace_path)
            app_mgr.start_crm(crm_db_path)

        registry_process = app_mgr.start_registry(host)
        if registry_process is None or registry_process.poll() is not None:
            logger.error("Registry service failed to start. Exiting.")
            stop_direct_processes()
            raise typer.Exit(1)

        demo_process = app_mgr.start_demo(host, sandbox=sandbox)
        if demo_process is None or demo_process.poll() is not None:
            logger.error("Demo service failed to start. Exiting.")
            stop_direct_processes()
            raise typer.Exit(1)

        if direct_processes:
            workspace_abs_path = os.path.abspath(workspace_path)

            services_table = Table(show_header=False, box=None, padding=(0, 1))
            services_table.add_column("Service", style="bold white", no_wrap=True)
            services_table.add_column("URL", style="cyan")
            if start_email:
                services_table.add_row("• Email Sink", f"smtp://localhost:{app_mgr.email_sink_port}")
                services_table.add_row("• Email MCP Server", f"http://localhost:{app_mgr.email_mcp_port}/sse")
            if start_filesystem:
                services_table.add_row("• Filesystem MCP Server", f"http://localhost:{app_mgr.fs_port}/sse")
            if start_crm:
                services_table.add_row("• CRM API Server", f"http://localhost:{app_mgr.crm_port}")
            services_table.add_row("• Registry Server", f"http://localhost:{settings.server_ports.registry}")
            services_table.add_row("• Demo Server", f"http://localhost:{settings.server_ports.demo}")

            filesystem_text = Text()
            filesystem_text.append("  Read/Write allowed in:\n", style="bold white")
            filesystem_text.append(f"  {workspace_abs_path}", style="yellow")

            groups = [
                Text("📦 Started Services:", style="bold green"),
                services_table,
                Text(),
                Text("📁 Filesystem Access:", style="bold green"),
                filesystem_text,
            ]

            if enable_supervisor:
                groups.append(Text())
                groups.append(Text("🤖 Supervisor: enabled (multi-agent coordination)", style="bold magenta"))

            panel_content = Group(*groups)

            console.print()
            console.print(
                Panel(
                    panel_content,
                    title=f"[bold yellow]✅ {service_label} services are running. Press Ctrl+C to stop[/bold yellow]",
                    border_style="cyan",
                    padding=(1, 2),
                    expand=False,
                )
            )
            wait_for_direct_processes()

    except Exception as e:
        logger.error(f"Error starting {service_label} services: {e}")
        stop_direct_processes()
        raise typer.Exit(1)


# Helper function to validate service
def validate_service(service: str):
    """Validate service name."""
    valid_services = ["demo", "demo_crm", "demo_supervisor", "manager", "registry", "appworld", "memory"]

    if service not in valid_services:
        logger.error(f"Unknown service: {service}. Valid options are: {', '.join(valid_services)}")
        raise typer.Exit(1)


def _resolve_apps(
    service: str,
    crm: bool,
    email: bool,
    digital_sales: bool,
    filesystem: bool,
    no_email: bool,
) -> tuple[bool, bool, bool, bool]:
    """Resolve app flags from preset + optional overrides. Returns (crm, email, digital_sales, filesystem)."""
    defaults = get_default_apps_for_preset(service)
    email_default = defaults["email"] and not no_email
    return (
        defaults["crm"] or crm,
        email_default or email,
        defaults["digital_sales"] or digital_sales,
        defaults["filesystem"] or filesystem,
    )


@app.command(help="Start a specified service", short_help="Start service(s)")
def start(
    service: str = typer.Argument(
        ...,
        help="Service to start: demo, demo_crm, demo_supervisor, manager, registry, appworld, or memory",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Host to bind to (default: 127.0.0.1). Use 0.0.0.0 to allow external connections.",
    ),
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Enable remote sandbox mode with llm-sandbox (requires --group sandbox to be installed)",
    ),
    read_only: bool = typer.Option(
        False,
        "--read-only",
        help="For demo_crm: Start filesystem server in read-only mode (only read_text_file tool exposed)",
    ),
    sample_memory_data: bool = typer.Option(
        False,
        "--sample-memory-data/--no-sample-memory-data",
        help="For demo_crm: Generate sample workspace files (cities.txt, company.txt) in cuga_workspace",
    ),
    no_email: bool = typer.Option(
        False,
        "--no-email",
        help="For demo_crm: Disable email services (email sink and email MCP server)",
    ),
    crm: bool = typer.Option(
        False,
        "--crm",
        help="Enable CRM app (demo_crm preset includes it by default)",
    ),
    email: bool = typer.Option(
        False,
        "--email",
        help="Enable email app (demo_crm preset includes it by default)",
    ),
    digital_sales: bool = typer.Option(
        False,
        "--digital-sales",
        help="Enable Digital Sales app (demo preset includes it by default)",
    ),
    filesystem: bool = typer.Option(
        False,
        "--filesystem",
        help="Enable filesystem app (included by default for demo/demo_crm/manager)",
    ),
    cuga_workspace: str | None = typer.Option(
        None,
        "--cuga-workspace",
        help="Path to cuga workspace; when set, configures policy env so all file operations use this dir (manager/demo_crm)",
    ),
):
    """
    Start the specified service.

    Available services:
      - demo: Starts both registry and demo agent directly (registry on port 8001, demo on port 7860)
      - demo_crm: Starts CRM demo with email MCP, mail sink, and CRM API servers
      - demo_supervisor: Same as demo_crm but with CugaSupervisor multi-agent coordination enabled
      - manager: Manage-config mode: registry uses managed MCP YAML, policy filesync off, demo on 7860
      - registry: Starts only the registry service directly (uvicorn on port 8001)
      - appworld: Starts AppWorld environment and API servers (environment on port 8000, api on port 9000)
      - memory: Starts the memory service directly (uvicorn on port 8888)

    App flags (--crm, --email, --digital-sales, --filesystem) add apps to the preset:
      - demo: default = digital_sales + filesystem
      - demo_crm: default = crm + filesystem + email
      - manager: default = filesystem only

    Examples:
      cuga start demo                     # digital_sales + filesystem
      cuga start demo --crm               # add CRM to demo
      cuga start demo_crm                 # crm + filesystem + email
      cuga start demo_crm --no-email      # crm + filesystem only
      cuga start manager --crm --email    # filesystem + crm + email
      cuga start manager --digital-sales  # filesystem + digital_sales
      cuga start manager --cuga-workspace /path/to/workspace  # custom workspace + policy
      cuga start demo --sandbox           # with remote sandbox
      cuga start registry                 # registry only
      cuga start appworld                 # AppWorld servers
      cuga start memory                   # memory service
    """
    validate_service(service)

    app_crm, app_email, app_digital_sales, app_filesystem = _resolve_apps(
        service, crm, email, digital_sales, filesystem, no_email
    )
    resolved_tools = build_tools_from_apps(
        crm=app_crm,
        email=app_email,
        digital_sales=app_digital_sales,
        filesystem=app_filesystem,
    )

    if service == "manager":
        try:
            os.environ["CUGA_MANAGER_MODE"] = "true"
            os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
            managed_path = ensure_managed_mcp_file_exists(get_managed_mcp_path())
            os.environ["MCP_SERVERS_FILE"] = "none"
            logger.info("Manager mode: policy filesystem sync disabled, MCP_SERVERS_FILE=%s", managed_path)
            setup_demo_manage_config("manager", tools=resolved_tools)

            app_mgr = _make_app_manager()
            workspace_path = cuga_workspace or os.path.join(os.getcwd(), "cuga_workspace")
            workspace_abs = os.path.abspath(workspace_path)
            os.environ["CUGA_POLICIES_CONTENT"] = _build_workspace_policies(
                workspace_abs, include_email=app_email
            )
            os.environ["CUGA_LOAD_POLICIES"] = "true"
            ports_to_kill = app_mgr.ports_for_apps(app_email, app_filesystem, app_crm)
            ports_to_kill.extend([settings.server_ports.registry, settings.server_ports.demo])
            kill_processes_by_port(ports_to_kill)
            os.environ["CUGA_HOST"] = host

            if app_filesystem or app_crm:
                app_mgr.prepare_workspace(workspace_path)
            if app_email:
                app_mgr.start_email()
            if app_filesystem:
                app_mgr.start_filesystem(workspace_path)
            if app_crm:
                crm_db_path = app_mgr.prepare_crm_db(workspace_path)
                app_mgr.start_crm(crm_db_path)

            registry_process = app_mgr.start_registry(host)
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)
            demo_process = app_mgr.start_demo(host)
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)
            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                if app_email:
                    table.add_row("Email Sink:", f"smtp://localhost:{app_mgr.email_sink_port}")
                    table.add_row("Email MCP:", f"http://localhost:{app_mgr.email_mcp_port}/sse")
                if app_filesystem:
                    table.add_row("Filesystem MCP:", f"http://localhost:{app_mgr.fs_port}/sse")
                if app_crm:
                    table.add_row("CRM API:", f"http://localhost:{app_mgr.crm_port}")
                table.add_row("Registry:", f"http://localhost:{settings.server_ports.registry}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")
                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]Manager mode. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()
        except Exception as e:
            logger.error(f"Error starting manager services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    # Handle direct execution services (demo and registry)
    if service == "demo":
        os.environ["CUGA_DEMO_ADVANCED"] = "true"
        os.environ["CUGA_MANAGER_MODE"] = "true"
        os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
        os.environ["MCP_SERVERS_FILE"] = "none"
        ensure_managed_mcp_file_exists(get_managed_mcp_path())

        try:
            logger.info("🧹 Resetting config db and setting up manage demo...")
            setup_demo_manage_config("demo", tools=resolved_tools)
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            workspace_path = os.path.join(os.getcwd(), "cuga_workspace")
            ports_to_clean = [settings.server_ports.registry, settings.server_ports.demo]
            ports_to_clean.extend(app_mgr.ports_for_apps(False, True, False))
            kill_processes_by_port(ports_to_clean)

            os.environ["CUGA_HOST"] = host
            if sandbox:
                logger.info("Starting demo with remote sandbox mode enabled (features.local_sandbox=false)")
                os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"

            app_mgr.prepare_workspace(workspace_path)
            app_mgr.start_filesystem(workspace_path)

            registry_process = app_mgr.start_registry(host)
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            demo_process = app_mgr.start_demo(host, sandbox=sandbox)
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)
            # Optionally start Chromium with MV3 extension if configured

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                table.add_row("Filesystem MCP:", f"http://localhost:{app_mgr.fs_port}/sse")
                table.add_row("Registry:", f"http://localhost:{settings.server_ports.registry}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")

                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]Demo (manage mode) services are running. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting demo services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    elif service in ("demo_crm", "demo_supervisor"):
        _start_demo_crm_services(
            host=host,
            sandbox=sandbox,
            read_only=read_only,
            sample_memory_data=sample_memory_data,
            no_email=no_email,
            enable_supervisor=(service == "demo_supervisor"),
            tools=resolved_tools,
            cuga_workspace=cuga_workspace,
        )
        return

    elif service == "registry":
        try:
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            kill_processes_by_port([app_mgr.registry_port])
            app_mgr.start_registry(host)

            if direct_processes:
                console.print()
                console.print(
                    Panel(
                        f"[bold white]Registry:[/bold white] [cyan]http://localhost:{app_mgr.registry_port}[/cyan]",
                        title="[bold yellow]Registry service is running. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()
        except Exception as e:
            logger.error(f"Error starting registry service: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    elif service == "appworld":
        try:
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            kill_processes_by_port([settings.server_ports.environment_url, settings.server_ports.apis_url])
            app_mgr.start_appworld()

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                table.add_row("Environment:", f"http://localhost:{settings.server_ports.environment_url}")
                table.add_row("API:", f"http://localhost:{settings.server_ports.apis_url}")

                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]AppWorld services are running. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting AppWorld services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    elif service == "memory":
        try:
            app_mgr = _make_app_manager()
            app_mgr.start_memory(host)

            if direct_processes:
                console.print()
                console.print(
                    Panel(
                        f"[bold white]Memory:[/bold white] [cyan]http://localhost:{settings.server_ports.memory}[/cyan]",
                        title="[bold yellow]Memory service is running. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting memory service: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return


def manage_service(action: str, service: str):
    """Common function for stopping or restarting services."""
    validate_service(service)

    if action == "stop":
        if service in ("demo", "manager"):
            stopped_any = False
            for service_name in ["filesystem-server", "registry", "demo"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info("Demo/manager services are not running")
        elif service in ("demo_crm", "demo_supervisor"):
            # Stop all CRM/supervisor demo services
            stopped_any = False
            for service_name in [
                "email-sink",
                "email-mcp",
                "filesystem-server",
                "crm-server",
                "registry",
                "demo",
            ]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info(f"{service} services are not running")
        elif service == "registry":
            # Stop only registry for registry service
            if "registry" in direct_processes:
                process = direct_processes["registry"]
                if process and process.poll() is None:
                    logger.info("Stopping registry...")
                    kill_process_tree(process.pid)
                del direct_processes["registry"]
            else:
                logger.info("Registry service is not running")
        elif service == "appworld":
            # Stop both appworld services
            stopped_any = False
            for service_name in ["appworld-environment", "appworld-api"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info("AppWorld services are not running")
        elif service == "memory":
            # Stop memory service
            if "memory" in direct_processes:
                process = direct_processes["memory"]
                if process and process.poll() is None:
                    logger.info("Stopping memory...")
                    kill_process_tree(process.pid)
                del direct_processes["memory"]
            else:
                logger.info("Memory service is not running")
    elif action == "restart":
        # Stop if running, then start
        manage_service("stop", service)
        time.sleep(1)
        # Call start command
        start(service)


@app.command(help="Stop a specified service", short_help="Stop service(s)")
def stop(
    service: str = typer.Argument(
        ...,
        help="Service to stop: demo, demo_crm, demo_supervisor, registry, appworld, or memory",
    ),
):
    """
    Stop the specified service.

    Available services:
      - demo: Stops both registry and demo agent (direct processes)
      - demo_crm: Stops all CRM demo services (email sink, email MCP, CRM API, registry, demo)
      - demo_supervisor: Same as demo_crm
      - registry: Stops only the registry service (direct process)
      - appworld: Stops both AppWorld environment and API servers (direct processes)
      - memory: Stops the memory service (direct process)

    Examples:
      cuga stop demo             # Stop both registry and demo services
      cuga stop demo_crm         # Stop all CRM demo services
      cuga stop demo_supervisor  # Stop all supervisor demo services
      cuga stop registry         # Stop only the registry service
      cuga stop appworld         # Stop AppWorld servers
      cuga stop memory           # Stop memory service
    """
    manage_service("stop", service)


@app.command(help="Start trajectory viewer", short_help="Start trajectory viewer")
def viz():
    """
    Start the trajectory viewer.

    This command launches a web-based dashboard for viewing and analyzing trajectory data from agent executions.

    Example:
      cuga viz         # Start the trajectory viewer
    """
    try:
        trajectory_data_path = TRAJECTORY_DATA_DIR
        subprocess.run(
            ["uv", "run", "--group", "dev", "cuga-viz", "run", trajectory_data_path],
            capture_output=False,
            text=False,
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Error starting dashboard: {e}")
        raise typer.Exit(1)
    except Exception as e:
        logger.error(f"Error starting dashboard: {e}")
        return False


@app.command(help="Show status of services", short_help="Display service status")
def status(
    service: str = typer.Argument(
        "all",
        help="Service to check status: demo, demo_crm, demo_supervisor, registry, appworld, memory, or all",
    ),
):
    """
    Display the current status of services.

    Available services:
      - demo: Shows status of both registry and demo agent (direct processes)
      - demo_crm: Shows status of all CRM demo services (email sink, email MCP, CRM API, registry, demo)
      - demo_supervisor: Same as demo_crm
      - registry: Shows status of registry service only (direct process)
      - appworld: Shows status of both AppWorld environment and API servers (direct processes)
      - memory: Shows status of memory service (direct process)
      - all: Shows status of all services (default)

    Examples:
      cuga status              # Show status of all services
      cuga status demo         # Show status of demo services (registry + demo)
      cuga status demo_crm     # Show status of CRM demo services
      cuga status registry     # Show status of registry only
      cuga status appworld     # Show status of AppWorld servers
      cuga status memory       # Show status of memory service
    """
    if service in ("demo", "manager"):
        for service_name in ["registry", "demo"]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    logger.info(f"{service_name.capitalize()} service: Running (PID: {process.pid})")
                else:
                    logger.info(f"{service_name.capitalize()} service: Terminated")
            else:
                logger.info(f"{service_name.capitalize()} service: Not running")
        return

    elif service in ("demo_crm", "demo_supervisor"):
        # Show status of all CRM/supervisor demo services
        for service_name in ["email-sink", "email-mcp", "crm-api", "registry", "demo"]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    logger.info(f"{service_name} service: Running (PID: {process.pid})")
                else:
                    logger.info(f"{service_name} service: Terminated")
            else:
                logger.info(f"{service_name} service: Not running")
        return

    elif service == "registry":
        if "registry" in direct_processes:
            process = direct_processes["registry"]
            if process.poll() is None:
                logger.info(f"Registry service: Running (PID: {process.pid})")
            else:
                logger.info("Registry service: Terminated")
        else:
            logger.info("Registry service: Not running")
        return

    elif service == "appworld":
        # Show status of both appworld services
        for service_name in ["appworld-environment", "appworld-api"]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    logger.info(
                        f"{service_name.replace('appworld-', '').capitalize()} service: Running (PID: {process.pid})"
                    )
                else:
                    logger.info(f"{service_name.replace('appworld-', '').capitalize()} service: Terminated")
            else:
                logger.info(f"{service_name.replace('appworld-', '').capitalize()} service: Not running")
        return

    elif service == "memory":
        if "memory" in direct_processes:
            process = direct_processes["memory"]
            if process.poll() is None:
                logger.info(f"Memory service: Running (PID: {process.pid})")
            else:
                logger.info("Memory service: Terminated")
        else:
            logger.info("Memory service: Not running")
        return

    elif service == "all":
        # Show direct processes status
        logger.info("Services:")
        for service_name in [
            "demo",
            "registry",
            "email-sink",
            "email-mcp",
            "crm-api",
            "appworld-environment",
            "appworld-api",
            "memory",
        ]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    display_name = (
                        service_name.replace('appworld-', 'appworld-')
                        if 'appworld-' in service_name
                        else service_name
                    )
                    logger.info(f"  {display_name}: Running (PID: {process.pid})")
                else:
                    display_name = (
                        service_name.replace('appworld-', 'appworld-')
                        if 'appworld-' in service_name
                        else service_name
                    )
                    logger.info(f"  {display_name}: Terminated")
            else:
                display_name = (
                    service_name.replace('appworld-', 'appworld-')
                    if 'appworld-' in service_name
                    else service_name
                )
                logger.info(f"  {display_name}: Not running")
        return

    # Validate service for any other service
    validate_service(service)


@app.command(help="Test sandbox execution", short_help="Test sandbox")
def test_sandbox(
    remote: bool = typer.Option(
        False,
        "--remote",
        help="Test with remote sandbox (Docker/Podman) instead of local execution",
    ),
):
    """
    Test sandbox execution to verify code execution works correctly.

    Examples:
      cuga test-sandbox           # Test local sandbox (default)
      cuga test-sandbox --remote  # Test remote sandbox with Docker/Podman
    """
    try:
        from scripts.commands import test_sandbox as run_test

        if remote:
            # Ensure sandbox dependencies are available
            logger.info("Testing remote sandbox mode (requires --group sandbox)")
            run_test(remote=True)
        else:
            logger.info("Testing local sandbox mode")
            run_test(remote=False)

        logger.info("✅ Sandbox test completed successfully")
    except Exception as e:
        logger.error(f"❌ Sandbox test failed: {e}")
        raise typer.Exit(1)


@app.command(help="Evaluate Cuga on your test cases", short_help="Run Cuga Evaluation")
def evaluate(
    test_cases_file_path: str = typer.Argument(
        "",
        help="Path to your test cases file",
    ),
    output_file_path: str = typer.Argument(
        default="results.json",
        help="Path to your output file, it defaults to 'results.json'",
    ),
):
    """
    Run Cuga on your test cases.
    """
    # start the registry
    try:
        run_direct_service(
            "registry",
            [
                "uvicorn",
                "cuga.backend.tools_env.registry.registry.api_registry_server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(settings.server_ports.registry),
            ],
        )

        if direct_processes:
            console.print()
            console.print(
                Panel(
                    f"[bold white]Registry:[/bold white] [cyan]http://localhost:{settings.server_ports.registry}[/cyan]",
                    title="[bold yellow]Registry service is running. Press Ctrl+C to stop[/bold yellow]",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )
            # Wait for registry to start
            logger.info("Waiting for registry to start...")
            wait_for_registry_server(settings.server_ports.registry)

            # Then start demo - using explicit fastapi command
            run_direct_service(
                "evaluation",
                [
                    "uv",
                    "run",
                    "--group",
                    "dev",
                    os.path.join(PACKAGE_ROOT, "evaluation/evaluate_cuga.py"),
                    "-t",
                    test_cases_file_path,
                    "-r",
                    output_file_path,
                ],
            )
        wait_for_direct_processes()

    except Exception as e:
        logger.error(f"Error starting registry service: {e}")
        stop_direct_processes()
        raise typer.Exit(1)
    return


if __name__ == "__main__":
    app()
