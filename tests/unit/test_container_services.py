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
