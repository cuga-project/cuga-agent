import os
import subprocess
from datetime import datetime
from dynaconf import Dynaconf
import sys
import argparse
import concurrent.futures
import socket
import threading
import time
import uuid

# Configuration
IMAGE_NAME = "cuga-e2e-tests"
DOCKERFILE = "Dockerfile.test"
LOGS_CONTAINER_PATH = "/app/src/system_tests/e2e/logs"
LOCAL_LOGS_BASE = "test-logs"

# List of environment variables that require dynamic port allocation
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
        self._reserved_ports = set()

    def get_free_port(self):
        """Finds a free port on localhost."""
        with self._lock:
            while True:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', 0))
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    port = s.getsockname()[1]
                    if port not in self._reserved_ports:
                        self._reserved_ports.add(port)
                        return port
                # If we get here, the port was technically free but in our reserved list (unlikely with OS allocation but possible)
                time.sleep(0.01)

    def allocate_ports(self):
        """Allocates a unique port for each defined dynamic variable."""
        allocations = {}
        for var_name in DYNAMIC_PORT_VARS:
            allocations[var_name] = str(self.get_free_port())
        return allocations


port_manager = PortManager()


def run_command(cmd, cwd=None, capture_output=False, check=True, env=None):
    """Run a shell command."""
    # In parallel mode, prints might interleave
    print(f"Running: {' '.join(cmd)} (env vars modified: {bool(env)})")

    # Merge current env with provided env
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    result = subprocess.run(cmd, cwd=cwd, capture_output=capture_output, text=True, check=False, env=full_env)
    if check and result.returncode != 0:
        print(f"Error executing command: {cmd}")
        if result.stderr:
            print(result.stderr)
        # Don't exit system-wide in thread
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

    return result


def check_image_exists(image_name):
    """Check if docker image exists."""
    cmd = ["docker", "images", "-q", image_name]
    try:
        result = run_command(cmd, capture_output=True, check=False)
        return bool(result.stdout.strip())
    except Exception:
        return False


def build_image():
    """Build the docker image."""
    print(f"Building Docker image {IMAGE_NAME} from {DOCKERFILE}...")
    cmd = ["docker", "build", "-f", DOCKERFILE, "-t", IMAGE_NAME, "."]
    run_command(cmd)
    print("Build complete.")


def run_docker_test(test_full_path, run_timestamp):
    """Run a single test case in a docker container."""
    # test_full_path example: src/system_tests/e2e/fast_test.py::TestClass::test_method

    safe_test_name = test_full_path.replace("/", "_").replace(":", "_").replace(".", "_")
    container_name = f"cuga_test_{safe_test_name}_{run_timestamp}"

    print(f"\n--- Running Docker Test: {test_full_path} ---")

    # Create unique CRM DB path for this test
    test_uuid = str(uuid.uuid4())
    workdir = os.getcwd()
    crm_db_path = os.path.join(workdir, "crm_tmp", f"crm_db_{test_uuid}")
    print(f"Allocated CRM DB path for {safe_test_name}: {crm_db_path}")

    # Run the container
    docker_cmd = [
        "docker",
        "run",
        "--name",
        container_name,
        "-v",
        f"{os.getcwd()}/.env:/app/.env",
        "-e",
        f"DYNACONF_CRM_DB_PATH={crm_db_path}",
        IMAGE_NAME,
        test_full_path,
    ]

    try:
        test_result = run_command(docker_cmd, check=False)
        success = test_result.returncode == 0
    except Exception as e:
        print(f"Docker run exception: {e}")
        success = False

    local_log_dir = os.path.join(LOCAL_LOGS_BASE, run_timestamp, safe_test_name)
    os.makedirs(local_log_dir, exist_ok=True)

    print(f"Copying logs for {test_full_path} from container to {local_log_dir}...")

    # Copy logs from container
    cp_cmd = ["docker", "cp", f"{container_name}:{LOGS_CONTAINER_PATH}", local_log_dir]
    run_command(cp_cmd, check=False)

    # Cleanup container
    print(f"Removing container {container_name}...")
    run_command(["docker", "rm", container_name], check=False)

    return success


def kill_process_on_port(port):
    """Kill any process listening on the given port."""
    try:
        # Find process using the port
        result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, check=False)

        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid:
                    print(f"  Killing process {pid} on port {port}")
                    subprocess.run(["kill", "-9", pid], check=False)
    except Exception as e:
        print(f"  Warning: Could not kill process on port {port}: {e}")


def cleanup_ports(env_ports):
    """Clean up any processes still running on allocated ports."""
    print("\n--- Cleaning up allocated ports ---")
    for var_name, port in env_ports.items():
        try:
            port_int = int(port)
            print(f"Checking port {port_int} ({var_name})...")
            kill_process_on_port(port_int)
        except (ValueError, TypeError):
            print(f"Skipping {var_name} (not a port): {port}")
        except Exception as e:
            print(f"Warning: Error cleaning up {var_name}: {e}")
    print("--- Port cleanup complete ---")


def run_local_test(test_full_path, run_timestamp):
    """Run a single test case locally with dynamic port allocation."""

    safe_test_name = test_full_path.replace("/", "_").replace(":", "_").replace(".", "_")
    print(f"\n--- Running Local Test: {test_full_path} ---")

    # Allocate ports
    env_ports = port_manager.allocate_ports()
    print(f"Allocated ports for {safe_test_name}: {env_ports}")

    # Create unique CRM DB path for this test
    test_uuid = str(uuid.uuid4())
    workdir = os.getcwd()
    crm_db_path = os.path.join(workdir, "crm_tmp", f"crm_db_{test_uuid}")
    env_ports["DYNACONF_CRM_DB_PATH"] = crm_db_path
    print(f"Allocated CRM DB path for {safe_test_name}: {crm_db_path}")

    # Prepare command
    cmd = ["pytest", test_full_path]

    # Run pytest with modified environment
    try:
        run_command(cmd, check=True, env=env_ports)
        success = True
    except subprocess.CalledProcessError:
        success = False
    except Exception as e:
        print(f"Local run exception: {e}")
        success = False
    finally:
        # Always clean up ports after test completes
        cleanup_ports(env_ports)

    # Note: Local logs are handled by the test configuration itself (usually writing to files or stdout).
    # If the test framework writes to a specific directory based on env vars, that would be handled here.
    # Assuming standard pytest output or that tests are configured to write logs relative to CWD or /tmp.

    return success


def run_test_wrapper(method, test_full_path, run_timestamp):
    if method == "docker":
        return run_docker_test(test_full_path, run_timestamp)
    else:
        return run_local_test(test_full_path, run_timestamp)


def main():
    parser = argparse.ArgumentParser(description="Run stability tests.")
    parser.add_argument("--parallel", action="store_true", help="Run tests in parallel.")
    parser.add_argument(
        "--rebuild", action="store_true", help="Force rebuild of the docker image (only for docker method)."
    )
    parser.add_argument("--clean", action="store_true", help="Stop and remove all running test containers.")
    parser.add_argument(
        "--method",
        choices=["local", "docker"],
        default="docker",
        help="Execution method: 'local' or 'docker'.",
    )
    args = parser.parse_args()

    if args.clean and args.method == "docker":
        print("Stopping and removing all test containers (filtered by 'cuga_test_')...")
        ps_cmd = ["docker", "ps", "-a", "-q", "--filter", "name=cuga_test_"]
        ps_result = run_command(ps_cmd, capture_output=True, check=False)
        container_ids = ps_result.stdout.strip().split()

        if container_ids:
            print(f"Found {len(container_ids)} containers to remove.")
            rm_cmd = ["docker", "rm", "-f"] + container_ids
            run_command(rm_cmd, check=False)
            print("Cleanup complete.")
        else:
            print("No test containers found.")
        sys.exit(0)

    # Load configuration
    print("Loading configuration from stability_test_config.toml...")
    settings = Dynaconf(settings_files=["src/system_tests/e2e/stability_test_config.toml"])

    if args.method == "docker":
        if args.rebuild or not check_image_exists(IMAGE_NAME):
            if args.rebuild:
                print(f"Rebuild requested for {IMAGE_NAME}...")
            else:
                print(f"Image {IMAGE_NAME} not found.")
            build_image()
        else:
            print(f"Image {IMAGE_NAME} found. Skipping build.")

    # Get stability tests
    try:
        stability_config = settings.stability
        tests = stability_config.tests
    except AttributeError:
        print("Error: [stability] section or tests not found in stability_test_config.toml")
        sys.exit(1)

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"Run Timestamp (ID): {run_timestamp}")
    print(f"Method: {args.method}")

    results = []
    test_targets = []

    # Gather all test targets
    for test_entry in tests:
        file_path = test_entry.file
        cases = test_entry.cases

        for case in cases:
            # Construct the full pytest target
            full_target = f"{file_path}::{case}"
            test_targets.append(full_target)

    if args.parallel:
        print(f"Running {len(test_targets)} tests in parallel...")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Submit all tests
            future_to_test = {
                executor.submit(run_test_wrapper, args.method, target, run_timestamp): target
                for target in test_targets
            }

            for future in concurrent.futures.as_completed(future_to_test):
                target = future_to_test[future]
                try:
                    success = future.result()
                    results.append((target, success))
                except Exception as exc:
                    print(f"\n{target} generated an exception: {exc}")
                    results.append((target, False))
    else:
        print(f"Running {len(test_targets)} tests sequentially...")
        for target in test_targets:
            success = run_test_wrapper(args.method, target, run_timestamp)
            results.append((target, success))

    print("\n\n=== Test Summary ===")
    all_passed = True
    # Sort results for cleaner display since parallel execution might mix them up
    results.sort(key=lambda x: x[0])

    passed_count = 0
    failed_count = 0

    for name, success in results:
        status = "PASS" if success else "FAIL"
        print(f"[{status}] {name}")
        if success:
            passed_count += 1
        else:
            failed_count += 1
            all_passed = False

    print("\n=== Final Results ===")
    print(f"Total tests: {len(results)}")
    print(f"Passed: {passed_count}")
    print(f"Failed: {failed_count}")

    if not all_passed:
        print(f"\n⚠️  WARNING: {failed_count} test(s) failed. This is reported as a warning, not a failure.")
        print("The workflow will continue to allow other Python versions to run.")
        sys.exit(0)  # Exit with 0 to allow workflow to continue
    else:
        print("\n✅ All tests passed!")


if __name__ == "__main__":
    main()
