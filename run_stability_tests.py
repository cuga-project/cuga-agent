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
import json
import glob
from pathlib import Path
import platform

# Set UTF-8 encoding for stdout/stderr on Windows to handle Unicode characters
if platform.system() == "Windows":
    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            # Python < 3.7 or reconfigure not available, use environment variable
            os.environ["PYTHONIOENCODING"] = "utf-8"
    if sys.stderr.encoding != "utf-8":
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

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
    """Kill any process listening on the given port (cross-platform)."""
    import platform

    if platform.system() == "Windows":
        # Use psutil on Windows (more reliable than netstat)
        try:
            import psutil

            killed_any = False
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
                                    f"  Killing process {proc.info['pid']} ({proc.info['name']}) on port {port}"
                                )
                                proc.terminate()
                                try:
                                    proc.wait(timeout=2)
                                except psutil.TimeoutExpired:
                                    proc.kill()
                                    proc.wait()
                                killed_any = True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            if not killed_any:
                # Port might not be in use, which is fine
                pass
        except ImportError:
            # Fallback to netstat if psutil not available
            try:
                result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
                for line in result.stdout.split('\n'):
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        if len(parts) > 4:
                            pid = parts[-1]
                            subprocess.run(["taskkill", "/F", "/PID", pid], check=False, capture_output=True)
            except Exception as e:
                print(f"  Warning: Could not kill process on port {port}: {e}")
        except Exception as e:
            print(f"  Warning: Could not kill process on port {port}: {e}")
    else:
        # Unix/Linux: use lsof
        try:
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


def generate_summary_report(results_dir: str = "test-results"):
    """Generate a summary report from collected test results."""
    results_path = Path(results_dir)
    all_results = {}
    total_passed = 0
    total_failed = 0
    total_tests = 0

    # Collect results from all Python versions
    if results_path.exists():
        result_files = glob.glob(str(results_path / "test_results_python_*.json"))
        if not result_files:
            result_files = glob.glob(str(results_path / "**/test_results_python_*.json"), recursive=True)

        for result_file in result_files:
            # Extract Python version from filename: test_results_python_3.11.json -> 3.11
            filename = Path(result_file).name
            python_version = filename.replace("test_results_python_", "").replace(".json", "")
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    all_results[python_version] = data
                    total_passed += data["passed"]
                    total_failed += data["failed"]
                    total_tests += data["total"]
            except Exception as e:
                print(f"Error reading {result_file}: {e}")
                all_results[python_version] = {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0}

    # Calculate overall pass rate
    overall_pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0

    # Generate report
    if not all_results:
        report = []
        report.append("## 📊 Stability Test Results")
        report.append("")
        report.append("⚠️ No test results found. Tests may have failed before results could be saved.")
        report.append("Please check individual job logs for details.")
        summary_text = "\n".join(report)
        summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_file:
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(summary_text)
        print(summary_text)
        return

    report = []
    report.append("## 📊 Stability Test Results")
    report.append("")
    report.append(
        f"**Overall Pass Rate: {overall_pass_rate:.1f}%** ({total_passed}/{total_tests} tests passed)"
    )
    report.append("")

    # Per-version breakdown
    report.append("### Results by Python Version")
    report.append("")
    report.append("| Python Version | Passed | Failed | Total | Pass Rate |")
    report.append("|---------------|--------|--------|-------|-----------|")

    for version in sorted(all_results.keys()):
        data = all_results[version]
        status_emoji = "✅" if data["pass_rate"] >= 88 else "⚠️"
        report.append(
            f"| {status_emoji} {version} | {data['passed']} | {data['failed']} | {data['total']} | {data['pass_rate']:.1f}% |"
        )

    report.append("")

    # Aggregate test results across all Python versions
    test_status_by_name = {}  # test_name -> {versions: [list], all_passed: bool, any_failed: bool}

    for version in sorted(all_results.keys()):
        data = all_results[version]
        if data.get("tests"):
            for test in data["tests"]:
                test_name = test["name"]
                if test_name not in test_status_by_name:
                    test_status_by_name[test_name] = {
                        "versions": [],
                        "all_passed": True,
                        "any_failed": False,
                    }
                test_status_by_name[test_name]["versions"].append(
                    {
                        "version": version,
                        "status": test["status"],
                    }
                )
                if test["status"] == "FAIL":
                    test_status_by_name[test_name]["all_passed"] = False
                    test_status_by_name[test_name]["any_failed"] = True

    # Overall test results summary
    report.append("### Overall Test Results")
    report.append("")

    # Tests that passed in all versions
    all_passed_tests = [name for name, info in test_status_by_name.items() if info["all_passed"]]
    # Tests that failed in at least one version
    any_failed_tests = [name for name, info in test_status_by_name.items() if info["any_failed"]]

    if all_passed_tests:
        report.append("**✅ Tests Passed in All Versions:**")
        for test_name in sorted(all_passed_tests):
            report.append(f"- ✅ {test_name}")
        report.append("")

    if any_failed_tests:
        report.append("**❌ Tests Failed in At Least One Version:**")
        for test_name in sorted(any_failed_tests):
            # Show which versions passed/failed for this test
            info = test_status_by_name[test_name]
            version_statuses = []
            for v in info["versions"]:
                status_emoji = "✅" if v["status"] == "PASS" else "❌"
                version_statuses.append(f"{status_emoji} {v['version']}")
            versions_str = ", ".join(version_statuses)
            report.append(f"- ❌ {test_name} ({versions_str})")
        report.append("")

    if not all_passed_tests and not any_failed_tests:
        report.append("No test results available.")
        report.append("")

    # Detailed breakdown if pass rate >= 88%
    if overall_pass_rate >= 88:
        report.append("### 📋 Detailed Test Results by Python Version")
        report.append("")

        for version in sorted(all_results.keys()):
            data = all_results[version]
            report.append(f"#### Python {version}")
            report.append("")

            if data.get("tests"):
                passed_tests = [t for t in data["tests"] if t["status"] == "PASS"]
                failed_tests = [t for t in data["tests"] if t["status"] == "FAIL"]

                if passed_tests:
                    report.append("**✅ Passed Tests:**")
                    for test in passed_tests:
                        report.append(f"- ✅ {test['name']}")
                    report.append("")

                if failed_tests:
                    report.append("**❌ Failed Tests:**")
                    for test in failed_tests:
                        report.append(f"- ❌ {test['name']}")
                    report.append("")
    else:
        report.append("### ⚠️ Low Pass Rate")
        report.append("")
        report.append(f"Overall pass rate ({overall_pass_rate:.1f}%) is below the 88% threshold.")
        report.append("Please review the individual job results above for details.")

    report.append("---")
    report.append("")
    report.append("⚠️  Note: Tests are configured to report warnings instead of failures.")
    report.append("Check individual job results above for detailed test outcomes.")

    # Write to GitHub step summary
    summary_text = "\n".join(report)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(summary_text)

    print(summary_text)


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
    parser.add_argument(
        "--generate-summary",
        action="store_true",
        help="Generate summary report from collected test results.",
    )
    parser.add_argument(
        "--results-dir",
        default="test-results",
        help="Directory containing test result JSON files (for --generate-summary).",
    )
    args = parser.parse_args()

    if args.generate_summary:
        generate_summary_report(args.results_dir)
        return

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
        # Limit concurrent workers on Windows due to slower subprocess spawning
        # and resource constraints. Windows subprocess creation is significantly
        # slower than Unix, so fewer concurrent tests reduces contention.
        if platform.system() == "Windows":
            # Windows subprocess spawning is much slower than Unix, and concurrent
            # execution causes significant resource contention. Even with limited
            # workers, the overhead of managing multiple subprocesses simultaneously
            # can make parallel execution slower than sequential on Windows.
            # Use 2 workers as a compromise - allows some parallelism while
            # minimizing contention. For best performance, consider running
            # sequentially on Windows (remove --parallel flag).
            max_workers = min(2, len(test_targets))
            print(
                f"Running {len(test_targets)} tests in parallel (max {max_workers} concurrent on Windows)..."
            )
        else:
            # On Unix/Linux, we can use more workers as subprocess spawning is faster
            max_workers = min(8, len(test_targets))
            print(f"Running {len(test_targets)} tests in parallel (max {max_workers} concurrent)...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
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

    # Calculate pass rate
    pass_rate = (passed_count / len(results) * 100) if len(results) > 0 else 0
    print(f"Pass rate: {pass_rate:.1f}%")

    # Output JSON results for workflow consumption
    results_json = {
        "total": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate": round(pass_rate, 2),
        "tests": [{"name": name, "status": "PASS" if success else "FAIL"} for name, success in results],
    }

    results_file = os.environ.get("TEST_RESULTS_FILE", "test_results.json")
    try:
        # Use UTF-8 encoding explicitly for cross-platform compatibility
        # Windows defaults to cp1252 which can't encode all Unicode characters
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results_json, f, indent=2, ensure_ascii=False)
        try:
            print(f"\nResults saved to {results_file}")
        except UnicodeEncodeError:
            print(f"\nResults saved to {results_file}")
    except Exception as e:
        # Safe error printing that handles encoding issues
        error_msg = str(e)
        try:
            print(f"\nWarning: Could not save results to {results_file}: {error_msg}")
        except UnicodeEncodeError:
            # Fallback to ASCII-safe message
            print(f"\nWarning: Could not save results to {results_file}: {repr(e)}")

    if not all_passed:
        # Use ASCII-safe characters for Windows compatibility
        warning_msg = (
            f"\nWARNING: {failed_count} test(s) failed. This is reported as a warning, not a failure."
        )
        try:
            print(
                f"\n⚠️  WARNING: {failed_count} test(s) failed. This is reported as a warning, not a failure."
            )
        except UnicodeEncodeError:
            print(warning_msg)
        print("The workflow will continue to allow other Python versions to run.")
        sys.exit(0)  # Exit with 0 to allow workflow to continue
    else:
        try:
            print("\n✅ All tests passed!")
        except UnicodeEncodeError:
            print("\nAll tests passed!")


if __name__ == "__main__":
    main()
