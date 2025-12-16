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

instructions_manager = InstructionsManager()

console = Console()

os.environ["DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED"] = "true"

app = typer.Typer(
    help="Cuga CLI for managing services with direct execution",
    short_help="Service management tool for Cuga components",
)

if settings.advanced_features.enable_memory:
    from cuga.backend.memory.cli import memory_app

    app.add_typer(memory_app, name="memory")

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


def create_demo_crm_sample_files(workspace_path: str) -> List[str]:
    """Create sample CRM demo files in the provided workspace path."""
    os.makedirs(workspace_path, exist_ok=True)
    sample_contents = {
        "cities.txt": ["Barcelona", "Bangalore", "Boulder"],
        "company.txt": ["Bangalore"],
    }
    created_files: List[str] = []
    for filename, entries in sample_contents.items():
        file_path = os.path.join(workspace_path, filename)
        with open(file_path, "w", encoding="utf-8") as file_handle:
            file_handle.write("\n".join(entries) + "\n")
        created_files.append(file_path)
    return created_files


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
    - registry: The MCP registry service only (runs directly)
    - appworld: AppWorld environment and API servers (runs directly)
    - memory: The memory service (runs directly)

    Examples:
      cuga start demo           # Start both registry and demo agent directly
      cuga start demo_crm       # Start CRM demo with all required services
      cuga start registry       # Start registry only
      cuga start appworld       # Start AppWorld servers
      cuga start memory         # Start memory service
    """
    if verbose:
        logger.level("DEBUG")

    # Set up signal handler for graceful shutdown of direct processes
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


# Helper function to validate service
def validate_service(service: str):
    """Validate service name."""
    valid_services = ["demo", "demo_crm", "registry", "appworld", "memory"]

    if service not in valid_services:
        logger.error(f"Unknown service: {service}. Valid options are: {', '.join(valid_services)}")
        raise typer.Exit(1)


@app.command(help="Start a specified service", short_help="Start service(s)")
def start(
    service: str = typer.Argument(
        ...,
        help="Service to start: demo (registry + demo agent), demo_crm (CRM demo with email), registry (registry only), appworld (environment + api servers), or memory (memory service)",
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
):
    """
    Start the specified service.

    Available services:
      - demo: Starts both registry and demo agent directly (registry on port 8001, demo on port 7860)
      - demo_crm: Starts CRM demo with email MCP, mail sink, and CRM API servers
      - registry: Starts only the registry service directly (uvicorn on port 8001)
      - appworld: Starts AppWorld environment and API servers (environment on port 8000, api on port 9000)
      - memory: Starts the memory service directly (uvicorn on port 8888)

    Examples:
      cuga start demo                # Start with local sandbox (default)
      cuga start demo --sandbox      # Start with remote sandbox (Docker/Podman)
      cuga start demo_crm            # Start CRM demo with all required services
      cuga start demo_crm --read-only  # Start CRM demo with read-only filesystem
      cuga start demo_crm --sample-memory-data  # Seed workspace with sample memory data before CRM demo
      cuga start demo_crm --no-email  # Start CRM demo without email services
      cuga start registry            # Start registry only
      cuga start appworld            # Start AppWorld servers
      cuga start memory              # Start memory service
    """
    validate_service(service)

    # Handle direct execution services (demo and registry)
    if service == "demo":
        try:
            # Clean up any existing processes on the ports we need
            logger.info("🧹 Checking for existing processes on required ports...")
            kill_processes_by_port([settings.server_ports.registry, settings.server_ports.demo])

            # Set environment variable for host
            os.environ["CUGA_HOST"] = host

            # If sandbox mode is enabled, update settings dynamically
            if sandbox:
                logger.info("Starting demo with remote sandbox mode enabled (features.local_sandbox=false)")
                os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"
            else:
                # No override - let default configuration be used
                pass

            # Start registry first - using explicit uvicorn command
            registry_process = run_direct_service(
                "registry",
                [
                    "uvicorn",
                    "cuga.backend.tools_env.registry.registry.api_registry_server:app",
                    "--host",
                    host,
                    "--port",
                    str(settings.server_ports.registry),
                ],
            )

            # Check if registry failed to start
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            # Wait for registry to start
            logger.info("Waiting for registry to start...")
            wait_for_server(settings.server_ports.registry)

            # Double-check registry is still running after wait
            if registry_process.poll() is not None:
                logger.error("Registry service terminated during startup. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            # Then start demo - using explicit command with optional sandbox group
            demo_command = []
            if sandbox:
                demo_command = [
                    "uv",
                    "run",
                    "--group",
                    "sandbox",
                    "fastapi",
                    "dev",
                    os.path.join(PACKAGE_ROOT, "backend", "server", "main.py"),
                    "--host",
                    host,
                    "--no-reload",
                    "--port",
                    str(settings.server_ports.demo),
                ]
            else:
                demo_command = [
                    "fastapi",
                    "dev",
                    os.path.join(PACKAGE_ROOT, "backend", "server", "main.py"),
                    "--host",
                    host,
                    "--no-reload",
                    "--port",
                    str(settings.server_ports.demo),
                ]

            run_direct_service("demo", demo_command)
            wait_for_server(settings.server_ports.demo)
            # Optionally start Chromium with MV3 extension if configured

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                table.add_row("Registry:", f"http://localhost:{settings.server_ports.registry}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")

                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]Demo services are running. Press Ctrl+C to stop[/bold yellow]",
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

    elif service == "demo_crm":
        try:
            # Check if cuga_workspace folder exists
            workspace_path = os.path.join(os.getcwd(), "cuga_workspace")
            if not os.path.exists(workspace_path):
                logger.warning(f"📁 Creating cuga_workspace directory at {workspace_path}")
                os.makedirs(workspace_path, exist_ok=True)
                logger.info("✅ cuga_workspace directory created")
            else:
                logger.info(f"✅ cuga_workspace directory found at {workspace_path}")

            if sample_memory_data:
                logger.info("📝 Generating sample CRM workspace files...")
                created_files = create_demo_crm_sample_files(workspace_path)
                for file_path in created_files:
                    logger.info(f"   • {file_path}")

            # Set hardcoded policies for demo_crm
            policies_content = "## Plan\nwhen using filesystem use the `./cuga_workspace` dir only\nwhen using crm make sure to go through all pages on queries that requires paginating all accounts etc\nwhen user asks questions about cuga then answer the question by first reading the filesystem information inside the file `./cuga_workspace/cuga_knowledge.md` then answer the question\nWhen user asks to use email templates assume it has <results> placehoder to replace with the results"
            os.environ["CUGA_POLICIES_CONTENT"] = policies_content
            os.environ["CUGA_LOAD_POLICIES"] = "true"
            logger.info("📋 Policies configured for demo_crm")

            # Set default CRM DB path with cwd if not already set
            if "DYNACONF_CRM_DB_PATH" not in os.environ:
                # Default: ${workdir}/crm_tmp/crm_db_default
                workdir = os.getcwd()
                crm_db_path = os.path.join(workdir, "crm_tmp", "crm_db_default")
                crm_db_path = os.path.abspath(crm_db_path)
                os.environ["DYNACONF_CRM_DB_PATH"] = crm_db_path
            else:
                crm_db_path = os.path.abspath(os.environ["DYNACONF_CRM_DB_PATH"])

            # Clean up CRM DB path before starting
            if os.path.exists(crm_db_path):
                logger.info(f"🧹 Cleaning up existing CRM DB at {crm_db_path}")
                try:
                    os.remove(crm_db_path)
                    logger.info("✅ CRM DB cleaned up")
                except Exception as e:
                    logger.warning(f"⚠️  Could not remove CRM DB: {e}")

            # Ensure parent directory exists
            parent_dir = os.path.dirname(crm_db_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            # Clean up any existing processes on the ports we need
            # Clean up any existing processes on the ports we need
            logger.info("🧹 Checking for existing processes on required ports...")

            # Define ports with env var support
            crm_port = int(os.environ.get("DYNACONF_SERVER_PORTS__CRM_API", "8007"))
            fs_port = int(os.environ.get("DYNACONF_SERVER_PORTS__FILESYSTEM_MCP", "8112"))
            email_sink_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_SINK", "1025"))
            email_mcp_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_MCP", "8000"))

            ports_to_clean = [
                crm_port,
                fs_port,
                settings.server_ports.registry,
                settings.server_ports.demo,
            ]
            if not no_email:
                ports_to_clean.extend([email_sink_port, email_mcp_port])
            kill_processes_by_port(ports_to_clean)

            # Set environment variable for host
            os.environ["CUGA_HOST"] = host

            # If sandbox mode is enabled, update settings dynamically
            if sandbox:
                logger.info(
                    "Starting CRM demo with remote sandbox mode enabled (features.local_sandbox=false)"
                )
                os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"
            else:
                pass

            # Determine if we should use cache
            # Note: For local paths (--from ./docs/...), caching may not work reliably
            # So we only enable cache in CI environments (GitHub Actions, etc.) where
            # the environment is more controlled and packages are pre-installed
            # In local test runs, we use --no-cache to ensure reliable package installation
            use_cache = os.environ.get("CI") is not None

            if use_cache:
                logger.debug("Using uvx cache for faster startup in CI environment")
            else:
                logger.debug("Using --no-cache for reliable package installation")

            # Start email services if not disabled
            if not no_email:
                # Get email service ports from environment or use defaults
                email_sink_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_SINK", "1025"))
                email_mcp_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_MCP", "8000"))
                logger.info(f"Starting email services - Sink: {email_sink_port}, MCP: {email_mcp_port}")

                # Start email sink first
                email_sink_cmd = ["uvx"]
                if not use_cache:
                    email_sink_cmd.append("--no-cache")
                email_sink_cmd.extend(
                    [
                        "--from",
                        "./docs/examples/demo_apps/email_mcp/mail_sink",
                        "email_sink",
                    ]
                )
                run_direct_service(
                    "email-sink",
                    email_sink_cmd,
                    env_vars={"DYNACONF_SERVER_PORTS__EMAIL_SINK": str(email_sink_port)},
                )
                logger.info("Email sink started, waiting for it to be ready...")
                wait_for_tcp_port(email_sink_port, "Email sink", max_retries=60, retry_interval=0.5)
                time.sleep(1)  # Extra buffer

                # Start email MCP server (needs to know both ports)
                email_mcp_cmd = ["uvx"]
                if not use_cache:
                    email_mcp_cmd.append("--no-cache")
                email_mcp_cmd.extend(
                    [
                        "--from",
                        "./docs/examples/demo_apps/email_mcp/mcp_server",
                        "email_mcp",
                    ]
                )
                run_direct_service(
                    "email-mcp",
                    email_mcp_cmd,
                    env_vars={
                        "DYNACONF_SERVER_PORTS__EMAIL_SINK": str(email_sink_port),
                        "DYNACONF_SERVER_PORTS__EMAIL_MCP": str(email_mcp_port),
                    },
                )
                logger.info("Email MCP server started")
                time.sleep(2)
            else:
                logger.info("Email services disabled (--no-email flag set)")

            # Start filesystem MCP server
            filesystem_cmd = ["uvx"]
            if not use_cache:
                filesystem_cmd.append("--no-cache")
            filesystem_cmd.extend(
                [
                    "--from",
                    "./docs/examples/demo_apps/file_system",
                    "filesystem-server",
                ]
            )
            if read_only:
                filesystem_cmd.append("--read-only")
            filesystem_cmd.append(workspace_path)
            run_direct_service(
                "filesystem-server",
                filesystem_cmd,
                env_vars={"DYNACONF_SERVER_PORTS__FILESYSTEM_MCP": str(fs_port)},
            )
            logger.info("Filesystem MCP server started")
            time.sleep(2)

            # Start CRM API server
            # Pass port as command-line argument to avoid uvx environment variable issues
            crm_port = settings.server_ports.crm_api
            logger.info(f"Starting CRM server on port {crm_port}")

            crm_cmd = ["uvx"]
            if not use_cache:
                crm_cmd.append("--no-cache")
            crm_cmd.extend(
                [
                    "--from",
                    "./docs/examples/demo_apps/crm",
                    "crm-server",
                    "--port",
                    str(crm_port),
                ]
            )
            run_direct_service(
                "crm-server",
                crm_cmd,
                env_vars={
                    "DYNACONF_SERVER_PORTS__CRM_API": str(crm_port),
                    "DYNACONF_CRM_DB_PATH": crm_db_path,
                },
            )
            logger.info("CRM API server started")
            wait_for_server(crm_port, "CRM API server")

            # Start registry with CRM configuration
            # Only set MCP_SERVERS_FILE if not already set (e.g., by tests)
            if "MCP_SERVERS_FILE" not in os.environ:
                config_file = "mcp_servers_hf.yaml" if no_email else "mcp_servers_crm.yaml"
                os.environ["MCP_SERVERS_FILE"] = os.path.join(
                    PACKAGE_ROOT, "backend", "tools_env", "registry", "config", config_file
                )
            registry_process = run_direct_service(
                "registry",
                [
                    "uvicorn",
                    "cuga.backend.tools_env.registry.registry.api_registry_server:app",
                    "--host",
                    host,
                    "--port",
                    str(settings.server_ports.registry),
                ],
            )

            # Check if registry failed to start
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            # Wait for registry to be ready
            logger.info("Waiting for registry to start...")
            try:
                wait_for_registry_server(settings.server_ports.registry)
            except TimeoutError as e:
                logger.error(str(e))
                stop_direct_processes()
                raise typer.Exit(1)

            # Double-check registry is still running after wait
            if registry_process.poll() is not None:
                logger.error("Registry service terminated during startup. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            # Then start demo
            demo_command = []
            if sandbox:
                demo_command = [
                    "uv",
                    "run",
                    "--group",
                    "sandbox",
                    "fastapi",
                    "dev",
                    os.path.join(PACKAGE_ROOT, "backend", "server", "main.py"),
                    "--host",
                    host,
                    "--no-reload",
                    "--port",
                    str(settings.server_ports.demo),
                ]
            else:
                demo_command = [
                    "fastapi",
                    "dev",
                    os.path.join(PACKAGE_ROOT, "backend", "server", "main.py"),
                    "--host",
                    host,
                    "--no-reload",
                    "--port",
                    str(settings.server_ports.demo),
                ]

            demo_process = run_direct_service("demo", demo_command)

            # Check if demo failed to start
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            # Wait for demo server to be ready (waiting for "Uvicorn running" message)
            logger.info("Waiting for demo server to start...")
            try:
                wait_for_server(settings.server_ports.demo, "Demo server")
            except TimeoutError as e:
                logger.error(str(e))
                stop_direct_processes()
                raise typer.Exit(1)

            # Double-check demo is still running after wait
            if demo_process.poll() is not None:
                logger.error("Demo service terminated during startup. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            if direct_processes:
                workspace_abs_path = os.path.abspath(workspace_path)

                services_table = Table(show_header=False, box=None, padding=(0, 1))
                services_table.add_column("Service", style="bold white", no_wrap=True)
                services_table.add_column("URL", style="cyan")
                if not no_email:
                    services_table.add_row("• Email Sink", f"smtp://localhost:{email_sink_port}")
                    services_table.add_row("• Email MCP Server", f"http://localhost:{email_mcp_port}/sse")
                services_table.add_row("• Filesystem MCP Server", f"http://localhost:{fs_port}/sse")
                services_table.add_row("• CRM API Server", f"http://localhost:{crm_port}")
                services_table.add_row(
                    "• Registry Server", f"http://localhost:{settings.server_ports.registry}"
                )
                services_table.add_row("• Demo Server", f"http://localhost:{settings.server_ports.demo}")

                filesystem_text = Text()
                filesystem_text.append("  Read/Write allowed in:\n", style="bold white")
                filesystem_text.append(f"  {workspace_abs_path}", style="yellow")

                content = Group(
                    Text("📦 Started Services:", style="bold green"),
                    services_table,
                    Text(),
                    Text("📁 Filesystem Access:", style="bold green"),
                    filesystem_text,
                )

                console.print()
                console.print(
                    Panel(
                        content,
                        title="[bold yellow]✅ CRM Demo services are running. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                        expand=False,
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting CRM demo services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    elif service == "registry":
        try:
            # Clean up any existing processes on the port we need
            logger.info("🧹 Checking for existing processes on required ports...")
            kill_processes_by_port([settings.server_ports.registry])

            run_direct_service(
                "registry",
                [
                    "uvicorn",
                    "cuga.backend.tools_env.registry.registry.api_registry_server:app",
                    "--host",
                    host,
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
                wait_for_direct_processes()
        except Exception as e:
            logger.error(f"Error starting registry service: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    elif service == "appworld":
        try:
            # Clean up any existing processes on the ports we need
            logger.info("🧹 Checking for existing processes on required ports...")
            kill_processes_by_port([settings.server_ports.environment_url, settings.server_ports.apis_url])

            # Start environment server first
            run_direct_service(
                "appworld-environment",
                ["appworld", "serve", "environment", "--port", str(settings.server_ports.environment_url)],
            )

            # Wait for environment server to start
            logger.info("Waiting for AppWorld environment server to start...")
            time.sleep(5)

            # Then start API server
            run_direct_service(
                "appworld-api", ["appworld", "serve", "apis", "--port", str(settings.server_ports.apis_url)]
            )

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
            # Start memory service using uvicorn with memory group dependencies
            run_direct_service(
                "memory",
                [
                    "uv",
                    "run",
                    "--active",
                    "--group",
                    "memory",
                    "uvicorn",
                    "cuga.backend.memory.agentic_memory.main:app",
                    "--host",
                    host,
                    "--port",
                    str(settings.server_ports.memory),
                ],
            )

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
        if service == "demo":
            # Stop both registry and demo for demo service
            stopped_any = False
            for service_name in ["registry", "demo"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info("Demo services are not running")
        elif service == "demo_crm":
            # Stop all CRM demo services
            stopped_any = False
            for service_name in ["email-sink", "email-mcp", "crm-api", "registry", "demo"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info("CRM demo services are not running")
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
        help="Service to stop: demo (registry + demo agent), demo_crm (CRM demo services), registry (registry only), appworld (environment + api servers), or memory (memory service)",
    ),
):
    """
    Stop the specified service.

    Available services:
      - demo: Stops both registry and demo agent (direct processes)
      - demo_crm: Stops all CRM demo services (email sink, email MCP, CRM API, registry, demo)
      - registry: Stops only the registry service (direct process)
      - appworld: Stops both AppWorld environment and API servers (direct processes)
      - memory: Stops the memory service (direct process)

    Examples:
      cuga stop demo       # Stop both registry and demo services
      cuga stop demo_crm   # Stop all CRM demo services
      cuga stop registry   # Stop only the registry service
      cuga stop appworld   # Stop AppWorld servers
      cuga stop memory     # Stop memory service
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
        help="Service to check status: demo (registry + demo agent), demo_crm (CRM demo services), registry (registry only), appworld (environment + api servers), memory (memory service), or all (all services)",
    ),
):
    """
    Display the current status of services.

    Available services:
      - demo: Shows status of both registry and demo agent (direct processes)
      - demo_crm: Shows status of all CRM demo services (email sink, email MCP, CRM API, registry, demo)
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
    if service == "demo":
        # Show status of both registry and demo for demo service
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

    elif service == "demo_crm":
        # Show status of all CRM demo services
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
