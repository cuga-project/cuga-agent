"""Exercise container lifecycle using real child processes, without Docker."""

import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


SUPERVISOR = Path(__file__).resolve().parents[2] / "scripts/container_services.py"
spec = importlib.util.spec_from_file_location("container_services", SUPERVISOR)
services = importlib.util.module_from_spec(spec)
spec.loader.exec_module(services)


@pytest.mark.unit
def test_missing_evolve_runs_cuga_without_changing_configuration(monkeypatch, tmp_path):
    command = ["cuga", "start", "manager"]
    monkeypatch.setattr(sys, "argv", [str(SUPERVISOR), *command])
    monkeypatch.setattr(services.shutil, "which", lambda name: None)
    monkeypatch.setenv("EVOLVE_DATA_DIR", str(tmp_path / "unused"))
    monkeypatch.setenv("DYNACONF_EVOLVE__MODE", "registry")
    monkeypatch.setenv("DYNACONF_EVOLVE__ENABLED", "false")
    before = dict(os.environ)

    class ExecCalled(Exception):
        pass

    def exec_cuga(executable, args):
        assert executable == "cuga"
        assert args == command
        raise ExecCalled

    monkeypatch.setattr(os, "execvp", exec_cuga)
    with pytest.raises(ExecCalled):
        services.main()
    assert dict(os.environ) == before
    assert not (tmp_path / "unused").exists()


@pytest.mark.unit
@pytest.mark.parametrize("enabled", [None, "true", "false"])
def test_installed_evolve_is_configured_without_overriding_deployer_toggle(monkeypatch, tmp_path, enabled):
    monkeypatch.setattr(sys, "argv", [str(SUPERVISOR), "cuga", "start", "manager"])
    monkeypatch.setattr(services.shutil, "which", lambda name: "/custom/bin/evolve-mcp")
    monkeypatch.setenv("EVOLVE_DATA_DIR", str(tmp_path / "evolve"))
    monkeypatch.delenv("DYNACONF_EVOLVE__ENABLED", raising=False)
    monkeypatch.setenv("DYNACONF_EVOLVE__MODE", "auto")
    monkeypatch.setenv("DYNACONF_EVOLVE__URL", "http://unused/sse")
    if enabled is not None:
        monkeypatch.setenv("DYNACONF_EVOLVE__ENABLED", enabled)

    def supervise(cuga, evolve):
        assert cuga == ["cuga", "start", "manager"]
        assert evolve == [
            "/custom/bin/evolve-mcp",
            "--transport",
            "sse",
            "--host",
            "127.0.0.1",
            "--port",
            "8201",
        ]
        assert os.environ.get("DYNACONF_EVOLVE__ENABLED") == enabled
        assert os.environ["DYNACONF_EVOLVE__MODE"] == "direct"
        assert os.environ["DYNACONF_EVOLVE__URL"] == "http://127.0.0.1:8201/sse"
        assert (tmp_path / "evolve").is_dir()
        return 0

    monkeypatch.setattr(services, "supervise", supervise)
    assert services.main() == 0


@pytest.mark.unit
def test_cuga_is_not_started_when_evolve_never_becomes_ready(tmp_path):
    marker = tmp_path / "cuga-started"
    result = services.supervise(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
        [sys.executable, "-c", "import time; time.sleep(60)"],
        ready=lambda: False,
        startup_timeout=0.2,
        shutdown_timeout=1,
    )
    assert result == 1
    assert not marker.exists()


@pytest.mark.unit
def test_cuga_exit_stops_evolve_and_fails_container(tmp_path):
    pid_file = tmp_path / "evolve.pid"
    result = services.supervise(
        [sys.executable, "-c", "raise SystemExit(7)"],
        [
            sys.executable,
            "-c",
            f"import os,time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)",
        ],
        ready=pid_file.exists,
        shutdown_timeout=1,
    )
    assert result == 1
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.unit
def test_sigterm_stops_both_services(tmp_path):
    pid_files = [tmp_path / "cuga.pid", tmp_path / "evolve.pid"]
    commands = [
        [
            sys.executable,
            "-c",
            f"import os,time; open({str(path)!r}, 'w').write(str(os.getpid())); time.sleep(60)",
        ]
        for path in pid_files
    ]
    code = (
        "import importlib.util; "
        f"s=importlib.util.spec_from_file_location('services', {str(SUPERVISOR)!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        f"raise SystemExit(m.supervise({commands[0]!r}, {commands[1]!r}, ready=lambda: True, shutdown_timeout=1))"
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.monotonic() + 5
        while not all(path.exists() and path.read_text() for path in pid_files):
            assert proc.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=5) == 0
        for path in pid_files:
            with pytest.raises(ProcessLookupError):
                os.kill(int(path.read_text()), 0)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
