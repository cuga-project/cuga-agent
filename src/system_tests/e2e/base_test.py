import asyncio
import json
import os
import platform
import shutil
import signal
import subprocess
import traceback
import unittest
from typing import Any, Dict, List, Optional, Tuple

import httpx
import psutil

from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    ActionResponse,
)
from cuga.config import settings

# Note: Windows event loop policy is configured in conftest.py for better asyncio performance

# Define server and registry commands
DEMO_COMMAND = ["uv", "run", "demo"]  # Assuming demo runs on port 7860 as per settings.toml
REGISTRY_COMMAND = ["uv", "run", "registry"]  # Assuming default port for registry
DIGITAL_SALES_MCP_COMMAND = ["uv", "run", "digital_sales_openapi"]  # Digital sales MCP server
MEMORY_COMMAND = [
    "uv",
    "run",
    "--group",
    "memory",
    "uvicorn",
    "cuga.backend.memory.agentic_memory.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    str(settings.server_ports.memory),
]

# Server URL
SERVER_URL = f"http://localhost:{settings.server_ports.demo}"
STREAM_ENDPOINT = f"{SERVER_URL}/stream"
STOP_ENDPOINT = f"{SERVER_URL}/stop"
os.environ["MCP_SERVERS_FILE"] = os.path.join(os.path.dirname(__file__), "config", "mcp_servers.yaml")
os.environ["CUGA_TEST_ENV"] = "true"
os.environ["DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED"] = "true"


def get_preexec_fn():
    """Returns a cross-platform preexec_fn for subprocess.Popen.
    On Unix systems, returns os.setsid to create a new process group.
    On Windows, returns None as setsid is not available.
    """
    if hasattr(os, "setsid"):
        return os.setsid
    return None


def get_subprocess_env():
    """Returns environment dict for subprocess with UTF-8 encoding on Windows.
    This ensures that subprocesses can handle Unicode characters (like emojis)
    that FastAPI's dev server prints.
    """
    env = os.environ.copy()
    # On Windows, set UTF-8 encoding to handle Unicode characters in subprocess output
    if platform.system().lower().startswith("win"):
        env["PYTHONIOENCODING"] = "utf-8"
    return env


def get_sigkill():
    """Returns SIGKILL signal value in a cross-platform way.
    On Unix, returns signal.SIGKILL.
    On Windows where SIGKILL doesn't exist, returns 9 (the numeric value).
    """
    return getattr(signal, "SIGKILL", 9)


def kill_process_group(process, sig=None):
    """Kills a process group in a cross-platform way.
    On Unix, uses os.killpg to kill the process group.
    On Windows, uses process.terminate() or process.kill() directly.

    Args:
        process: The subprocess.Popen process object
        sig: Signal to send (signal.SIGTERM or signal.SIGKILL on Unix).
             On Windows, None/TERM uses terminate(), KILL uses kill()
    """
    if process is None or process.poll() is not None:
        return

    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        if sig is None:
            sig = signal.SIGTERM
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, OSError):
            pass
    else:
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


class BaseTestServerStream(unittest.IsolatedAsyncioTestCase):
    """
    Base test class for FastAPI server's streaming endpoint.
    Contains shared functionality and setup/teardown logic.
    """

    # Override this in subclasses to set specific environment variables
    test_env_vars = {}
    enable_memory_service = False

    def _kill_process_by_port(self, port: int, service_name: str = "service") -> bool:
        """
        Kill processes listening on a specific port.
        Uses optimized methods per platform for better performance.

        Args:
            port: The port number to check
            service_name: Name of the service for logging purposes

        Returns:
            True if any processes were killed, False otherwise
        """
        killed_any = False

        if platform.system() == "Windows":
            # On Windows, use netstat + taskkill which is much faster than iterating all processes
            try:
                result = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                for line in result.stdout.split('\n'):
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        if len(parts) > 4:
                            pid = parts[-1]
                            try:
                                # Validate PID is numeric before using it
                                int(pid)  # Validate it's a number
                                print(f"Killing {service_name} process {pid} on port {port}")
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", pid],
                                    capture_output=True,
                                    timeout=5,
                                    check=False,
                                )
                                killed_any = True
                            except (ValueError, subprocess.TimeoutExpired):
                                pass
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                # Fallback to psutil if netstat fails
                print(f"Warning: netstat failed, using fallback method: {e}")
                killed_any = self._kill_process_by_port_psutil(port, service_name)
        else:
            # On Unix/Linux, use lsof which is fast
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{port}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if result.stdout.strip():
                    pids = result.stdout.strip().split('\n')
                    for pid in pids:
                        if pid:
                            try:
                                print(f"Killing {service_name} process {pid} on port {port}")
                                subprocess.run(["kill", "-9", pid], timeout=5, check=False)
                                killed_any = True
                            except (subprocess.TimeoutExpired, ValueError):
                                pass
            except (subprocess.TimeoutExpired, FileNotFoundError):
                # Fallback to psutil if lsof fails
                killed_any = self._kill_process_by_port_psutil(port, service_name)

        return killed_any

    def _kill_process_by_port_psutil(self, port: int, service_name: str = "service") -> bool:
        """
        Fallback method using psutil (slower but more reliable).
        Only used when platform-specific methods fail.
        """
        killed_any = False
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    connections = proc.net_connections()
                    if connections:
                        for conn in connections:
                            if (
                                hasattr(conn, 'laddr')
                                and conn.laddr
                                and conn.laddr.port == port
                                and conn.status == 'LISTEN'
                            ):
                                print(
                                    f"Killing {service_name} process {proc.info['pid']} ({proc.info['name']}) on port {port}"
                                )
                                proc.terminate()
                                try:
                                    proc.wait(timeout=5)
                                    print(f"{service_name} process {proc.info['pid']} terminated gracefully")
                                except psutil.TimeoutExpired:
                                    print(
                                        f"{service_name} process {proc.info['pid']} did not terminate gracefully, killing..."
                                    )
                                    proc.kill()
                                    proc.wait()
                                killed_any = True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception as e:
            print(f"Error while trying to kill {service_name} processes on port {port}: {e}")

        return killed_any

    async def wait_for_server(
        self,
        port: int,
        max_retries: int = None,
        retry_interval: float = 0.5,
        process: Optional[subprocess.Popen] = None,
        log_file: Optional[str] = None,
        process_name: str = "server",
    ):
        """
        Wait for a server to be ready by pinging its health endpoint.

        Args:
            port: The port number the server is running on
            max_retries: Maximum number of retry attempts (default: 600 on Unix, 1200 on Windows)
            retry_interval: Time in seconds between retries (default: 0.5)
            process: Optional subprocess.Popen object to check if process is still alive
            log_file: Optional path to log file to read errors from if process dies
            process_name: Name of the process for error messages (default: "server")

        Raises:
            TimeoutError: If the server doesn't become ready within max_retries attempts
            RuntimeError: If the process has died before the server became ready
        """
        # Use longer timeout on Windows due to slower package installation and process startup
        if max_retries is None:
            max_retries = 1200 if platform.system() == "Windows" else 600

        url = f"http://127.0.0.1:{port}/"

        for attempt in range(max_retries):
            # Check if process has died (every 10 attempts to avoid too frequent checks)
            if process is not None and attempt % 10 == 0 and attempt > 0:
                if process.poll() is not None:
                    error_msg = f"{process_name} process died with return code {process.returncode}"
                    if log_file and os.path.exists(log_file):
                        try:
                            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                                log_content = f.read()
                                if log_content:
                                    # Get last 50 lines of log
                                    log_lines = log_content.split('\n')
                                    last_lines = '\n'.join(log_lines[-50:])
                                    error_msg += (
                                        f"\n\nLast 50 lines of {process_name} log ({log_file}):\n{last_lines}"
                                    )
                                else:
                                    error_msg += f"\n\nLog file {log_file} is empty."
                        except Exception as e:
                            error_msg += f"\n\nCould not read log file {log_file}: {e}"
                    raise RuntimeError(error_msg)

            try:
                async with httpx.AsyncClient(timeout=1.0) as client:
                    response = await client.get(url)
                    if response.status_code == 200:
                        print(f"Server on port {port} is ready!")
                        return
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_interval)
                else:
                    error_msg = (
                        f"{process_name} did not become ready after {max_retries * retry_interval:.1f} seconds. "
                        f"Please check if the server started correctly on port {port}."
                    )
                    # Check process status one last time
                    if process is not None and process.poll() is not None:
                        error_msg += f"\n{process_name} process died with return code {process.returncode}"
                        if log_file and os.path.exists(log_file):
                            try:
                                with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                                    log_content = f.read()
                                    if log_content:
                                        log_lines = log_content.split('\n')
                                        last_lines = '\n'.join(log_lines[-50:])
                                        error_msg += f"\n\nLast 50 lines of {process_name} log ({log_file}):\n{last_lines}"
                            except Exception as e:
                                error_msg += f"\n\nCould not read log file {log_file}: {e}"
                    raise TimeoutError(error_msg)

    def _create_log_files(self):
        """Create log files for demo and registry processes per test method in separate folders."""
        # Create logs directory within e2e folder
        e2e_dir = os.path.dirname(__file__)
        base_log_dir = os.path.join(e2e_dir, "logs")

        # Get test class and method names for folder naming
        test_class_name = self.__class__.__name__
        test_method_name = getattr(self, '_testMethodName', 'unknown_test')

        # Create a unique folder for this specific test
        test_folder_name = f"{test_class_name}_{test_method_name}"
        self.test_log_dir = os.path.join(base_log_dir, test_folder_name)
        os.environ["CUGA_LOGGING_DIR"] = os.path.join(self.test_log_dir, "logging")

        # Remove existing test folder if it exists (to reset for rerun)
        if os.path.exists(self.test_log_dir):
            try:
                shutil.rmtree(self.test_log_dir)
                print(f"Removed existing test folder: {self.test_log_dir}")
            except (OSError, PermissionError) as e:
                print(f"Warning: Could not remove test folder {self.test_log_dir}: {e}")
                print("Attempting to clear log files individually...")
                # If folder removal fails, try to clear log files individually
                log_file_names = ["demo_server.log", "registry_server.log", "digital_sales_mcp.log"]
                if self.enable_memory_service:
                    log_file_names.append("memory_server.log")
                for log_name in log_file_names:
                    log_path = os.path.join(self.test_log_dir, log_name)
                    if os.path.exists(log_path):
                        try:
                            with open(log_path, 'w', encoding='utf-8') as f:
                                f.write('')  # Truncate the file
                            print(f"Cleared log file: {log_path}")
                        except (OSError, PermissionError) as log_error:
                            print(f"Warning: Could not clear log file {log_path}: {log_error}")

        # Create the test-specific folder
        os.makedirs(self.test_log_dir, exist_ok=True)
        print(f"Created test folder: {self.test_log_dir}")

        # Create log file paths within the test folder
        self.demo_log_file = os.path.join(self.test_log_dir, "demo_server.log")
        self.registry_log_file = os.path.join(self.test_log_dir, "registry_server.log")
        self.digital_sales_mcp_log_file = os.path.join(self.test_log_dir, "digital_sales_mcp.log")
        self.memory_log_file = (
            os.path.join(self.test_log_dir, "memory_server.log") if self.enable_memory_service else None
        )

        # Clear/truncate log files to ensure they start fresh for each test
        log_files = [self.demo_log_file, self.registry_log_file, self.digital_sales_mcp_log_file]
        if self.memory_log_file:
            log_files.append(self.memory_log_file)
        for log_file in log_files:
            try:
                with open(log_file, 'w', encoding='utf-8') as f:
                    f.write('')  # Clear the file
                print(f"Cleared log file: {log_file}")
            except (OSError, PermissionError) as e:
                print(f"Warning: Could not clear log file {log_file}: {e}")

        print(f"Demo server logs will be saved to: {self.demo_log_file}")
        print(f"Registry server logs will be saved to: {self.registry_log_file}")
        print(f"Digital sales MCP logs will be saved to: {self.digital_sales_mcp_log_file}")
        if self.memory_log_file:
            print(f"Memory server logs will be saved to: {self.memory_log_file}")

    async def asyncSetUp(self):
        """
        Sets up the test environment before each test method.
        Starts the demo server, registry, and digital sales MCP processes with configured environment.
        """
        print(f"\n--- Setting up test environment for {self.__class__.__name__} ---")
        self.demo_process = None
        self.registry_process = None
        self.digital_sales_mcp_process = None
        self.memory_process = None
        self.demo_log_handle = None
        self.registry_log_handle = None
        self.digital_sales_mcp_log_handle = None
        self.memory_log_handle = None

        # Create log files (this will also clear any existing ones)
        self._create_log_files()

        # Clean up any existing processes on our ports before starting
        print("Cleaning up any existing processes on target ports...")
        self._kill_process_by_port(settings.server_ports.digital_sales_api, "digital sales MCP")
        self._kill_process_by_port(settings.server_ports.demo, "demo server")
        self._kill_process_by_port(settings.server_ports.registry, "registry")
        if self.enable_memory_service:
            self._kill_process_by_port(settings.server_ports.memory, "memory service")
        if hasattr(settings.server_ports, 'saved_flows'):
            self._kill_process_by_port(settings.server_ports.saved_flows, "saved flows")

        # Wait a moment for ports to be freed
        await asyncio.sleep(2)

        # Set environment variables for this test class
        print(f"Configuring environment variables: {self.test_env_vars}")
        for key, value in self.test_env_vars.items():
            if value is None:
                os.environ.pop(key, None)
                print(f"  Removed {key}")
            else:
                os.environ[key] = value
                print(f"  Set {key} = {value}")

        # Open log files for writing with UTF-8 encoding
        self.registry_log_handle = open(
            self.registry_log_file, 'w', encoding='utf-8', buffering=1
        )  # Line buffered
        self.demo_log_handle = open(self.demo_log_file, 'w', encoding='utf-8', buffering=1)  # Line buffered
        self.digital_sales_mcp_log_handle = open(
            self.digital_sales_mcp_log_file, 'w', encoding='utf-8', buffering=1
        )  # Line buffered
        if self.enable_memory_service and self.memory_log_file:
            self.memory_log_handle = open(self.memory_log_file, 'w', encoding='utf-8', buffering=1)
        print("Starting digital sales MCP process...")
        self.digital_sales_mcp_process = subprocess.Popen(
            DIGITAL_SALES_MCP_COMMAND,
            stdout=self.digital_sales_mcp_log_handle,
            stderr=subprocess.STDOUT,  # Redirect stderr to stdout (and thus to log file)
            text=True,
            env=get_subprocess_env(),  # Pass the updated environment with UTF-8 encoding on Windows
            preexec_fn=get_preexec_fn(),  # For proper process group management
        )
        print("Starting registry process...")
        self.registry_process = subprocess.Popen(
            REGISTRY_COMMAND,
            stdout=self.registry_log_handle,
            stderr=subprocess.STDOUT,  # Redirect stderr to stdout (and thus to log file)
            text=True,
            env=get_subprocess_env(),  # Pass the updated environment with UTF-8 encoding on Windows
            preexec_fn=get_preexec_fn(),  # For proper process group management
        )
        print(f"Registry process started with PID: {self.registry_process.pid}")

        if self.enable_memory_service:
            print("Starting memory service process...")
            self.memory_process = subprocess.Popen(
                MEMORY_COMMAND,
                stdout=self.memory_log_handle,
                stderr=subprocess.STDOUT,  # Redirect stderr to stdout
                text=True,
                env=get_subprocess_env(),  # Pass the updated environment with UTF-8 encoding on Windows
                preexec_fn=os.setsid,
            )
            print(f"Memory service process started with PID: {self.memory_process.pid}")
            # Ensure memory API is ready before services like the tracker try to use it
            await self.wait_for_server(
                settings.server_ports.memory,
                max_retries=240,
                process=self.memory_process,
                log_file=self.memory_log_file,
                process_name="Memory service",
            )

        print("Starting demo server process...")
        self.demo_process = subprocess.Popen(
            DEMO_COMMAND,
            stdout=self.demo_log_handle,
            stderr=subprocess.STDOUT,  # Redirect stderr to stdout (and thus to log file)
            text=True,
            env=get_subprocess_env(),  # Pass the updated environment with UTF-8 encoding on Windows
            preexec_fn=get_preexec_fn(),  # For proper process group management
        )
        print(f"Demo server process started with PID: {self.demo_process.pid}")

        # Give processes some time to start up
        print("Waiting for servers to initialize...")
        await self.wait_for_server(
            settings.server_ports.registry,
            process=self.registry_process,
            log_file=self.registry_log_file,
            process_name="Registry server",
        )
        if self.enable_memory_service:
            await self.wait_for_server(
                settings.server_ports.memory,
                process=self.memory_process,
                log_file=self.memory_log_file,
                process_name="Memory service",
            )
        await self.wait_for_server(
            settings.server_ports.demo,
            process=self.demo_process,
            log_file=self.demo_log_file,
            process_name="Demo server",
        )
        print("Server initialization wait complete.")
        print("--- Test environment setup complete ---")

    async def asyncTearDown(self):
        """
        Cleans up the test environment after each test method.
        Stops the demo server, registry, and digital sales MCP processes by port and PID.
        """
        print(f"\n--- Tearing down test environment for {self.__class__.__name__} ---")
        print("Stopping processes...")

        # First, try to terminate processes gracefully by PID if they still exist
        if self.demo_process:
            try:
                if self.demo_process.poll() is None:  # Process is still running
                    # Send SIGTERM to the process group
                    kill_process_group(self.demo_process, signal.SIGTERM)
                    self.demo_process.wait(timeout=5)
                    print("Demo server process terminated gracefully.")
                else:
                    print("Demo server process already terminated.")
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                print("Demo server process did not terminate gracefully or was already gone.")
                try:
                    if self.demo_process.poll() is None:
                        kill_process_group(self.demo_process, get_sigkill())
                        self.demo_process.wait()
                except (ProcessLookupError, OSError):
                    pass  # Process was already gone
            self.demo_process = None

        if self.registry_process:
            try:
                if self.registry_process.poll() is None:  # Process is still running
                    # Send SIGTERM to the process group
                    kill_process_group(self.registry_process, signal.SIGTERM)
                    self.registry_process.wait(timeout=5)
                    print("Registry process terminated gracefully.")
                else:
                    print("Registry process already terminated.")
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                print("Registry process did not terminate gracefully or was already gone.")
                try:
                    if self.registry_process.poll() is None:
                        kill_process_group(self.registry_process, get_sigkill())
                        self.registry_process.wait()
                except (ProcessLookupError, OSError):
                    pass  # Process was already gone
            self.registry_process = None

        if self.digital_sales_mcp_process:
            try:
                if self.digital_sales_mcp_process.poll() is None:  # Process is still running
                    # Send SIGTERM to the process group
                    kill_process_group(self.digital_sales_mcp_process, signal.SIGTERM)
                    self.digital_sales_mcp_process.wait(timeout=5)
                    print("Digital sales MCP process terminated gracefully.")
                else:
                    print("Digital sales MCP process already terminated.")
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                print("Digital sales MCP process did not terminate gracefully or was already gone.")
                try:
                    if self.digital_sales_mcp_process.poll() is None:
                        kill_process_group(self.digital_sales_mcp_process, get_sigkill())
                        self.digital_sales_mcp_process.wait()
                except (ProcessLookupError, OSError):
                    pass  # Process was already gone
            self.digital_sales_mcp_process = None

        if self.memory_process:
            try:
                if self.memory_process.poll() is None:
                    kill_process_group(self.memory_process, signal.SIGTERM)
                    self.memory_process.wait(timeout=5)
                    print("Memory service process terminated gracefully.")
                else:
                    print("Memory service process already terminated.")
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                print("Memory service did not terminate gracefully or was already gone.")
                try:
                    if self.memory_process.poll() is None:
                        kill_process_group(self.memory_process, get_sigkill())
                        self.memory_process.wait()
                except (ProcessLookupError, OSError):
                    pass  # Process was already gone
            self.memory_process = None

        # Close log file handles
        if self.demo_log_handle:
            self.demo_log_handle.close()
            self.demo_log_handle = None
            print(f"Demo server log file closed: {self.demo_log_file}")

        if self.registry_log_handle:
            self.registry_log_handle.close()
            self.registry_log_handle = None
            print(f"Registry server log file closed: {self.registry_log_file}")

        if self.digital_sales_mcp_log_handle:
            self.digital_sales_mcp_log_handle.close()
            self.digital_sales_mcp_log_handle = None
            print(f"Digital sales MCP log file closed: {self.digital_sales_mcp_log_file}")
        if self.memory_log_handle:
            self.memory_log_handle.close()
            self.memory_log_handle = None
            if self.memory_log_file:
                print(f"Memory server log file closed: {self.memory_log_file}")

        # Then, kill any remaining processes by port as a backup
        print("Cleaning up any remaining processes on target ports...")
        demo_killed = self._kill_process_by_port(settings.server_ports.demo, "demo server")
        registry_killed = self._kill_process_by_port(settings.server_ports.registry, "registry")
        memory_killed = False
        if self.enable_memory_service:
            memory_killed = self._kill_process_by_port(settings.server_ports.memory, "memory service")

        saved_flows_killed = False
        if hasattr(settings.server_ports, 'saved_flows'):
            saved_flows_killed = self._kill_process_by_port(settings.server_ports.saved_flows, "saved flows")

        if not (demo_killed or registry_killed or saved_flows_killed or memory_killed):
            print("No additional processes found on target ports.")

        print("All processes stopped.")
        print("--- Test environment teardown complete ---")

    def _parse_event_data(self, data_str: str) -> Any:
        """
        Parse event data which can be:
        1. Plain string
        2. JSON string
        3. JSON object with "data" key containing the actual content
        """
        try:
            # First, try to parse as JSON
            parsed_json = json.loads(data_str)

            # If it's a dict with "data" key, extract the content
            if isinstance(parsed_json, dict) and "data" in parsed_json:
                return parsed_json["data"]

            # Otherwise, return the parsed JSON as-is
            return parsed_json

        except json.JSONDecodeError:
            # If JSON parsing fails, return as plain string
            return data_str

    def get_event_at(self, all_data: List[Dict[str, Any]], n: int) -> Tuple[str, str]:
        last_event = all_data[n]
        last_event_key = last_event['event']
        last_event_value = last_event.get('data', 'N/A')
        return last_event_key, last_event_value

    async def run_task(
        self,
        query: str,
        followup_response: Optional[ActionResponse] = None,
        stop_on_answer: bool = True,
        timeout: Optional[float] = None,
        verbose: bool = True,
        thread_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Helper function to run a streaming task and return all events.

        Args:
            query: The query string to send to the stream endpoint
            followup_response: Optional followup response for continuation
            stop_on_answer: Whether to stop streaming when "Answer" event is received
            timeout: Optional timeout for the entire operation
            verbose: Whether to print event details during streaming
            thread_id: Optional thread ID to maintain conversation context

        Returns:
            List of event dictionaries with 'event' and 'data' keys
        """
        all_events = []

        if verbose:
            print(f"\n--- Running task for query: '{query}' ---")
            if thread_id:
                print(f"Using thread ID: {thread_id}")

        try:
            if verbose:
                print(f"Sending POST request to {STREAM_ENDPOINT} with query: '{query}'")

            client_timeout = httpx.Timeout(timeout) if timeout else None

            # Build headers
            headers = {"Accept": "text/event-stream"}
            if thread_id:
                headers["X-Thread-ID"] = thread_id

            async with httpx.AsyncClient(timeout=client_timeout) as client:
                async with client.stream(
                    "POST",
                    STREAM_ENDPOINT,
                    json={"query": query} if query and query != "" else followup_response.model_dump(),
                    headers=headers,
                ) as response:
                    response.raise_for_status()

                    # Ensure content-type is correct for SSE
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" not in content_type and verbose:
                        print(f"Warning: Expected 'text/event-stream', got '{content_type}'")

                    buffer = b""
                    async for chunk in response.aiter_bytes():
                        buffer += chunk

                        # Process complete events (delimited by double newlines)
                        while b"\n\n" in buffer:
                            event_block, buffer = buffer.split(b"\n\n", 1)
                            event_lines = event_block.split(b"\n")

                            event_data = {}
                            for line in event_lines:
                                line = line.strip()
                                if not line:
                                    continue

                                if line.startswith(b"event: "):
                                    event_data["event"] = line[len(b"event: ") :].decode("utf-8").strip()
                                elif line.startswith(b"data: "):
                                    try:
                                        data_str = line[len(b"data: ") :].decode("utf-8").strip()
                                        event_data["data"] = self._parse_event_data(data_str)
                                    except UnicodeDecodeError:
                                        event_data["data"] = line[len(b"data: ") :].strip()
                                else:
                                    # Handle cases where the format might be just "<key>\n<value>"
                                    try:
                                        line_str = line.decode("utf-8").strip()
                                        if ":" not in line_str and not event_data.get("event"):
                                            # This might be an event type on its own line
                                            event_data["event"] = line_str
                                        elif ":" not in line_str and not event_data.get("data"):
                                            # This might be data on its own line
                                            event_data["data"] = self._parse_event_data(line_str)
                                    except UnicodeDecodeError:
                                        continue

                            # Only add events that have at least an event type or data
                            if event_data and (event_data.get("event") or event_data.get("data")):
                                all_events.append(event_data)

                                if verbose:
                                    print(f"Received Event: {event_data.get('event', 'N/A')}")
                                    print(f"  Data: {event_data.get('data', 'N/A')}\n")

                                # Stop early if Answer event is received and stop_on_answer is True
                                if stop_on_answer and event_data.get("event") == "Answer":
                                    if verbose:
                                        print("--- 'Answer' event received, stopping stream. ---")

                                    # # Send a stop signal to the agent if it's still running
                                    # try:
                                    #     await client.post(STOP_ENDPOINT)
                                    #     if verbose:
                                    #         print("Stop signal sent to agent.")
                                    # except httpx.HTTPStatusError as e:
                                    #     if verbose:
                                    #         print(f"Failed to send stop signal: {e}")
                                    # break

        except httpx.RequestError as exc:
            print(f"Request URL: {exc.request.url!r}")
            print(f"Request Method: {exc.request.method}")
            print(f"Exception Type: {type(exc).__name__}")
            print(f"Exception Message: {exc}")
            print("Full Traceback:")
            traceback.print_exc()
            print("--- End HTTP Request Error ---\n")
        except Exception as e:
            print("\n--- Unexpected Error Occurred ---")
            print(f"Exception Type: {type(e).__name__}")
            print(f"Exception Message: {e}")
            print("Full Traceback:")
            raise Exception(f"An unexpected error occurred during stream processing: {e}")

        if verbose:
            print(f"\n--- Task completed. Total events received: {len(all_events)} ---")

        return all_events

    def _assert_answer_event(self, all_events: List[Dict[str, Any]], expected_keywords: List[str] = None):
        """
        Common assertion logic for answer events.

        Args:
            all_events: List of events from the stream
            expected_keywords: Optional list of keywords that should be in the answer
        """
        print("\n--- Performing assertions ---")

        # Basic assertions
        self.assertGreater(len(all_events), 0, "No events were received from the stream.")

        # Find the 'Answer' event
        answer_event = next((e for e in all_events if e.get("event") == "Answer"), None)

        self.assertIsNotNone(answer_event, "The 'Answer' event was not found in the stream.")
        print("Assertion Passed: 'Answer' event found.")

        answer_data = answer_event.get("data")
        self.assertIsNotNone(answer_data, "The 'Answer' event has no data.")
        self.assertNotEqual(answer_data, "", "The 'Answer' event data is empty.")
        print("Assertion Passed: 'Answer' data is not empty.")

        # Keyword validation if provided
        if expected_keywords:
            answer_str = str(answer_data).lower()
            for keyword in expected_keywords:
                self.assertIn(keyword.lower(), answer_str, f"Answer does not contain '{keyword}'.")
            print(f"Assertion Passed: Answer contains expected keywords: {expected_keywords}")

        print("\n--- All assertions passed! ---")

        print("\n--- All assertions passed! ---")
