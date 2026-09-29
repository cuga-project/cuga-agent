"""Exercise container lifecycle using real child processes, without Docker."""

import importlib.util
import os
import json
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
    monkeypatch.setattr(
        services,
        "evolve_endpoint",
        lambda: (os.environ.get("DYNACONF_EVOLVE__MODE", "auto"), os.environ.get("DYNACONF_EVOLVE__URL", "")),
    )
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
    monkeypatch.setattr(
        services,
        "evolve_endpoint",
        lambda: (os.environ.get("DYNACONF_EVOLVE__MODE", "auto"), os.environ.get("DYNACONF_EVOLVE__URL", "")),
    )
    monkeypatch.setattr(sys, "argv", [str(SUPERVISOR), "cuga", "start", "manager"])
    monkeypatch.setattr(services.shutil, "which", lambda name: "/custom/bin/evolve-mcp")
    monkeypatch.setenv("EVOLVE_DATA_DIR", str(tmp_path / "evolve"))
    monkeypatch.delenv("DYNACONF_EVOLVE__ENABLED", raising=False)
    monkeypatch.setenv("DYNACONF_EVOLVE__MODE", "auto")
    monkeypatch.delenv("DYNACONF_EVOLVE__URL", raising=False)
    if enabled is not None:
        monkeypatch.setenv("DYNACONF_EVOLVE__ENABLED", enabled)

    def supervise(cuga, evolve):
        assert cuga == ["cuga", "start", "manager"]
        assert evolve == [sys.executable, "-m", "cuga.backend.evolve.http_worker"]
        assert len(os.environ["CUGA_EVOLVE_API_TOKEN"]) >= 32
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


@pytest.mark.unit
@pytest.mark.parametrize(
    "mode,url",
    [("registry", ""), ("direct", "https://external.example/sse"), ("auto", "https://external.example/sse")],
)
def test_external_evolve_is_preserved(monkeypatch, mode, url):
    monkeypatch.setattr(
        services,
        "evolve_endpoint",
        lambda: (os.environ.get("DYNACONF_EVOLVE__MODE", "auto"), os.environ.get("DYNACONF_EVOLVE__URL", "")),
    )
    monkeypatch.setattr(sys, "argv", [str(SUPERVISOR), "cuga", "start", "manager"])
    monkeypatch.setenv("DYNACONF_EVOLVE__MODE", mode)
    monkeypatch.setenv("DYNACONF_EVOLVE__URL", url)
    before = dict(os.environ)

    class ExecCalled(Exception):
        pass

    def exec_cuga(executable, command):
        assert executable == "cuga"
        assert command == ["cuga", "start", "manager"]
        raise ExecCalled

    monkeypatch.setattr(os, "execvp", exec_cuga)
    with pytest.raises(ExecCalled):
        services.main()
    assert dict(os.environ) == before


@pytest.mark.unit
@pytest.mark.parametrize("source", ["dotenv", "env_file", "toml", "environment"])
def test_effective_external_endpoint_prevents_bundled_startup(tmp_path, source):
    """Use the real configuration loader in a fresh process, as at startup."""
    endpoint = "https://external.example/sse"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DYNACONF_EVOLVE") and key not in {"ENV_FILE", "SETTINGS_TOML_PATH"}
    }
    env["PYTHONPATH"] = str(SUPERVISOR.parent.parent / "src")
    env["EVOLVE_DATA_DIR"] = str(tmp_path / "must-not-exist")
    if source == "environment":
        env["DYNACONF_EVOLVE__URL"] = endpoint
    elif source == "toml":
        base = (SUPERVISOR.parent.parent / "src/cuga/settings.toml").read_text()
        (tmp_path / "settings.toml").write_text(base.replace("http://127.0.0.1:8201/sse", endpoint))
    else:
        file = tmp_path / ("operator.env" if source == "env_file" else ".env")
        file.write_text(f"DYNACONF_EVOLVE__MODE=direct\nDYNACONF_EVOLVE__URL={endpoint}\n")
        if source == "env_file":
            env["ENV_FILE"] = str(file)
    code = f"""
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("services", {str(SUPERVISOR)!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sys.argv = ["container_services", "cuga", "start", "manager"]
m.shutil.which = lambda _: "/bin/evolve-mcp"
def exec_cuga(executable, command):
    from cuga.config import settings
    print(json.dumps({{"url": settings.evolve.url, "command": command}}))
    raise SystemExit(0)
def unexpected_start(*args, **kwargs):
    raise AssertionError("External configuration started bundled Evolve")
m.os.execvp = exec_cuga
m.supervise = unexpected_start
m.main()
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    assert observed == {"url": endpoint, "command": ["cuga", "start", "manager"]}
    assert not (tmp_path / "must-not-exist").exists()
