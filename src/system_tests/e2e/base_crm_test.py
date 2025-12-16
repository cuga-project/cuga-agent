import asyncio
import os
import subprocess

from system_tests.e2e.base_test import BaseTestServerStream
from cuga.config import settings


class BaseCRMTestServerStream(BaseTestServerStream):
    """
    Base test class for CRM tests.
    Uses a single demo_crm command that starts all necessary services.
    """

    mode = "default"

    async def asyncSetUp(self):
        """
        Sets up the test environment for CRM tests.
        Uses 'cuga start demo_crm' with --no-email and --read-only flags.
        """
        print(f"\n--- Setting up CRM test environment for {self.__class__.__name__} ---")
        self.demo_process = None
        self.demo_log_handle = None

        self._create_log_files()

        # Clean up any existing processes on target ports
        print("Cleaning up any existing processes on target ports...")
        self._kill_process_by_port(settings.server_ports.demo, "demo server")
        self._kill_process_by_port(settings.server_ports.registry, "registry")
        self._kill_process_by_port(settings.server_ports.crm_api, "CRM API")
        self._kill_process_by_port(settings.server_ports.filesystem_mcp, "filesystem server")

        await asyncio.sleep(2)

        # Set MCP servers file for CRM configuration
        os.environ["MCP_SERVERS_FILE"] = os.path.join(
            os.path.dirname(__file__),
            "config",
            "mcp_servers_crm_hf.yaml" if self.mode == "hf" else "mcp_servers_crm.yaml",
        )
        print(f"Set MCP_SERVERS_FILE to: {os.environ['MCP_SERVERS_FILE']}")

        # Set environment variables for this test class
        print(f"Configuring environment variables: {self.test_env_vars}")
        for key, value in self.test_env_vars.items():
            if value is None:
                os.environ.pop(key, None)
                print(f"  Removed {key}")
            else:
                os.environ[key] = value
                print(f"  Set {key} = {value}")

        # Open log file for writing
        self.demo_log_handle = open(self.demo_log_file, 'w', buffering=1)

        # Start demo_crm which includes all necessary services
        demo_crm_command = ["uv", "run", "cuga", "start", "demo_crm"]
        if self.mode in ("hf"):
            demo_crm_command.extend(["--no-email", "--read-only"])
        self.demo_process = subprocess.Popen(
            demo_crm_command,
            stdout=self.demo_log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
            preexec_fn=os.setsid,
        )
        print(f"Demo CRM process started with PID: {self.demo_process.pid}")

        # Wait for servers to initialize
        print("Waiting for servers to initialize...")
        await self.wait_for_server(
            settings.server_ports.demo,
            process=self.demo_process,
            log_file=self.demo_log_file,
            process_name="Demo CRM server",
        )
        print("Server initialization wait complete.")
        print("--- CRM test environment setup complete ---")

    async def asyncTearDown(self):
        """
        Cleans up the test environment after each test method.
        """
        print(f"\n--- Tearing down CRM test environment for {self.__class__.__name__} ---")
        print("Stopping processes...")

        if self.demo_process:
            try:
                if self.demo_process.poll() is None:
                    os.killpg(os.getpgid(self.demo_process.pid), 15)
                    self.demo_process.wait(timeout=5)
                    print("Demo CRM process terminated gracefully.")
                else:
                    print("Demo CRM process already terminated.")
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                print("Demo CRM process did not terminate gracefully or was already gone.")
                try:
                    if self.demo_process.poll() is None:
                        os.killpg(os.getpgid(self.demo_process.pid), 9)
                        self.demo_process.wait()
                except (ProcessLookupError, OSError):
                    pass
            self.demo_process = None

        if self.demo_log_handle:
            self.demo_log_handle.close()
            self.demo_log_handle = None
            print(f"Demo CRM log file closed: {self.demo_log_file}")

        # Clean up any remaining processes on target ports
        print("Cleaning up any remaining processes on target ports...")
        self._kill_process_by_port(settings.server_ports.demo, "demo server")
        self._kill_process_by_port(settings.server_ports.registry, "registry")
        self._kill_process_by_port(settings.server_ports.crm_api, "CRM API")
        self._kill_process_by_port(settings.server_ports.filesystem_mcp, "filesystem server")

        print("All processes stopped.")
        print("--- CRM test environment teardown complete ---")
