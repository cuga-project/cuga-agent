"""`cuga evaluate` returns once the evaluation finishes, instead of waiting
forever on the tool registry (a server that never exits on its own), and
stops the registry on the way out."""

import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from cuga.cli import main as cli

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_services(monkeypatch):
    """Replace the registry with a never-ending process and the evaluation with
    a short one whose exit code the test controls."""
    spawned = {}
    exit_code = {"value": 0}

    def fake_run_direct_service(service_name, command, cwd=None, log_file=None, env_vars=None):
        if service_name == "registry":
            argv = [sys.executable, "-c", "import time; time.sleep(300)"]
        else:
            argv = [sys.executable, "-c", f"import sys; sys.exit({exit_code['value']})"]
        process = subprocess.Popen(argv)
        cli.direct_processes[service_name] = process
        spawned[service_name] = process
        return process

    monkeypatch.setattr(cli, "run_direct_service", fake_run_direct_service)
    monkeypatch.setattr(cli, "wait_for_registry_server", lambda port: None)
    cli.direct_processes.clear()
    yield spawned, exit_code
    for process in spawned.values():
        if process.poll() is None:
            process.kill()
    cli.direct_processes.clear()


def _evaluate():
    start = time.monotonic()
    result = CliRunner().invoke(cli.app, ["evaluate", "tests.json", "results.json"])
    return result, time.monotonic() - start


def test_returns_when_evaluation_finishes_and_stops_registry(fake_services):
    spawned, _ = fake_services
    result, elapsed = _evaluate()
    assert result.exit_code == 0, result.output
    assert elapsed < 30
    registry = spawned["registry"]
    registry.wait(timeout=10)
    assert registry.returncode is not None


def test_propagates_evaluation_exit_code(fake_services):
    spawned, exit_code = fake_services
    exit_code["value"] = 3
    result, _ = _evaluate()
    assert result.exit_code == 3
    spawned["registry"].wait(timeout=10)
