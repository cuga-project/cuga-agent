"""Stability classes must not inherit the preceding stack's agent config."""

import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from system_tests import conftest as stack_fixtures
from system_tests.e2e import server_stack

pytestmark = pytest.mark.unit


def test_crm_then_digital_sales_use_separate_configs(monkeypatch, tmp_path_factory, tmp_path):
    """Exercise real config storage in fresh subprocesses, without servers or an LLM."""
    from cuga.config import settings
    from system_tests.e2e import base_test

    real_run = subprocess.run
    real_popen = subprocess.Popen
    original_dir = str(tmp_path / "original")
    monkeypatch.setenv("CUGA_DBS_DIR", original_dir)
    monkeypatch.setenv("MCP_SERVERS_FILE", "original.yaml")
    external_db = tmp_path / "external.db"
    with sqlite3.connect(external_db) as conn:
        conn.execute("CREATE TABLE keep_me (value TEXT)")
        conn.execute("INSERT INTO keep_me VALUES ('external database untouched')")
    external_contents = external_db.read_bytes()
    monkeypatch.setenv("DYNACONF_STORAGE__MODE", "prod")
    monkeypatch.setenv("DYNACONF_STORAGE__LOCAL_DB_PATH", str(external_db))
    monkeypatch.setenv("DYNACONF_KNOWLEDGE__ENABLED", "false")
    monkeypatch.setattr(stack_fixtures, "allocate_stability_env", lambda **kw: {})
    monkeypatch.setattr(stack_fixtures, "cleanup_ports", lambda ports: None)
    monkeypatch.setattr(stack_fixtures, "_snapshot_server_urls", lambda: [])
    monkeypatch.setattr(stack_fixtures, "_sync_server_urls", lambda ports: None)
    monkeypatch.setattr(stack_fixtures, "_sync_settings_ports", lambda ports: None)
    monkeypatch.setattr(settings, "reload", lambda: None)
    monkeypatch.setattr(base_test, "_apply_base_test_env_defaults", lambda: None)
    monkeypatch.setattr(server_stack, "_class_log_dir", lambda name: tmp_path / name)
    monkeypatch.setattr(server_stack, "get_preexec_fn", lambda: None)
    monkeypatch.setattr(server_stack, "wait_for_http_ready", lambda *a, **kw: None)
    monkeypatch.setattr(server_stack, "assert_available_tools", lambda *a: None)
    monkeypatch.setattr(server_stack.subprocess, "Popen", MagicMock())
    monkeypatch.setattr(stack_fixtures, "stop_stack", lambda handles: None)

    # Real subprocess.run needs its original Popen; mock only the stack's server
    # launches by swapping Popen back while a seed subprocess runs.
    def run_seed(command, **kwargs):
        if command[:4] == ["uv", "run", "python", "-c"]:
            command = [sys.executable, "-c", command[4]]
        with monkeypatch.context() as seed_patch:
            seed_patch.setattr(subprocess, "Popen", real_popen)
            return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run_seed)
    directories = []

    def seed_crm(name, **kwargs):
        directories.append(Path(os.environ["CUGA_DBS_DIR"]))
        assert os.environ["DYNACONF_STORAGE__MODE"] == "local"
        assert os.environ["DYNACONF_STORAGE__LOCAL_DB_PATH"] == str(directories[-1] / "cuga.db")
        assert not (directories[-1] / "cuga.db").exists()
        run_seed(
            [
                sys.executable,
                "-c",
                "from cuga.backend.server.config_store import run_sync, save_config, save_draft; "
                "config = {'agent': {'name': 'CRM Agent'}, 'tools': [{'name': 'crm'}]}; "
                "run_sync(save_config(config)); run_sync(save_draft(config))",
            ],
            check=True,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def launch_sales(name):
        directories.append(Path(os.environ["CUGA_DBS_DIR"]))
        assert os.environ["DYNACONF_STORAGE__MODE"] == "local"
        assert os.environ["DYNACONF_STORAGE__LOCAL_DB_PATH"] == str(directories[-1] / "cuga.db")
        assert not (directories[-1] / "cuga.db").exists()
        handles = server_stack.start_digital_sales_stack(name)
        for handle in handles.log_handles:
            handle.close()
        return handles

    monkeypatch.setattr(stack_fixtures, "start_crm_stack", seed_crm)
    monkeypatch.setattr(stack_fixtures, "start_digital_sales_stack", launch_sales)
    registry = str(Path(base_test.MCP_SERVERS_FILE_PATH).resolve())
    for class_name, kind in [("CRMFirst", "crm"), ("SalesSecond", "digital_sales")]:
        cls = type(
            class_name, (), {"_stability_stack": kind, "test_env_vars": {"MCP_SERVERS_FILE": registry}}
        )
        fixture = stack_fixtures.e2e_class_stack.__wrapped__(SimpleNamespace(cls=cls), tmp_path_factory)
        next(fixture)
        with pytest.raises(StopIteration):
            next(fixture)
        assert os.environ["CUGA_DBS_DIR"] == original_dir
        assert os.environ["DYNACONF_STORAGE__MODE"] == "prod"
        assert os.environ["DYNACONF_STORAGE__LOCAL_DB_PATH"] == str(external_db)

    assert directories[0] != directories[1]
    assert external_db.read_bytes() == external_contents
    for directory, expected in zip(directories, ["crm", "digital_sales"]):
        with sqlite3.connect(directory / "cuga.db") as conn:
            rows = conn.execute(
                "SELECT version, config_json FROM agent_configs WHERE agent_id='cuga-default'"
            ).fetchall()
        assert {version for version, _ in rows} == {"draft", "1"}
        assert all([tool["name"] for tool in json.loads(raw)["tools"]] == [expected] for _, raw in rows)


@pytest.mark.parametrize("startup_error", [False, True])
def test_storage_env_restored_after_stack_teardown(monkeypatch, tmp_path_factory, startup_error):
    from cuga.config import settings

    monkeypatch.setenv("CUGA_DBS_DIR", "original-dbs")
    monkeypatch.setenv("DYNACONF_STORAGE__MODE", "prod")
    monkeypatch.setenv("DYNACONF_STORAGE__LOCAL_DB_PATH", "original.db")
    monkeypatch.setattr(settings, "reload", lambda: None)
    monkeypatch.setattr(stack_fixtures, "allocate_stability_env", lambda **kw: {})
    monkeypatch.setattr(stack_fixtures, "cleanup_ports", lambda ports: None)
    monkeypatch.setattr(stack_fixtures, "_sync_settings_ports", lambda ports: None)
    monkeypatch.setattr(stack_fixtures, "_snapshot_server_urls", lambda: [])
    monkeypatch.setattr(stack_fixtures, "_sync_server_urls", lambda ports: None)
    monkeypatch.setattr(stack_fixtures, "stop_stack", lambda handles: None)
    # Track these variables before the fixture changes them directly.
    for key in ("MCP_SERVERS_FILE", "CUGA_TEST_ENV"):
        monkeypatch.setenv(key, os.environ.get(key, ""))

    def launch(*args, **kwargs):
        assert os.environ["CUGA_DBS_DIR"] != "original-dbs"
        if startup_error:
            raise RuntimeError("startup failed")

    monkeypatch.setattr(stack_fixtures, "start_crm_stack", launch)
    cls = type("CRM", (), {"_stability_stack": "crm"})
    fixture = stack_fixtures.e2e_class_stack.__wrapped__(SimpleNamespace(cls=cls), tmp_path_factory)
    if startup_error:
        with pytest.raises(RuntimeError, match="startup failed"):
            next(fixture)
    else:
        next(fixture)
        with pytest.raises(StopIteration):
            next(fixture)
    assert os.environ["CUGA_DBS_DIR"] == "original-dbs"
    assert os.environ["DYNACONF_STORAGE__MODE"] == "prod"
    assert os.environ["DYNACONF_STORAGE__LOCAL_DB_PATH"] == "original.db"
