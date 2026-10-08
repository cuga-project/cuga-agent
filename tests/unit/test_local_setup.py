import base64
import os

import pytest

from cuga.local_setup import prepare_local_manager

pytestmark = pytest.mark.unit


@pytest.fixture
def manager_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CUGA_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "CUGA_SECRET_KEY",
        "CUGA_DBS_DIR",
        "CUGA_LOGGING_DIR",
        "CUGA_GUIDED_SETUP",
        "DYNACONF_STORAGE__PRESERVE_CONFIGS_ON_STARTUP",
        "DYNACONF_SECRETS__FORCE_ENV",
        "DYNACONF_KNOWLEDGE__PERSIST_DIR",
    ):
        # Record absent variables too, since the bootstrap writes os.environ.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return tmp_path / "data"


def test_repeated_bootstrap_preserves_key_and_explicit_paths(manager_environment, monkeypatch):
    root = prepare_local_manager()
    key = os.environ["CUGA_SECRET_KEY"]
    assert len(base64.urlsafe_b64decode(key)) == 32
    assert (root / "secret.key").stat().st_mode & 0o777 == 0o600
    monkeypatch.delenv("CUGA_SECRET_KEY")
    monkeypatch.setenv("CUGA_DBS_DIR", "/custom/db")
    assert prepare_local_manager() == root
    assert os.environ["CUGA_SECRET_KEY"] == key
    assert os.environ["CUGA_DBS_DIR"] == "/custom/db"
    assert os.environ["DYNACONF_SECRETS__FORCE_ENV"] == "false"
    assert os.environ["DYNACONF_KNOWLEDGE__PERSIST_DIR"] == str(root / "knowledge")


def test_rejects_public_key_file_without_replacing_it(manager_environment, monkeypatch):
    root = prepare_local_manager()
    before = (root / "secret.key").read_bytes()
    (root / "secret.key").chmod(0o644)
    monkeypatch.delenv("CUGA_SECRET_KEY")
    with pytest.raises(RuntimeError, match="must be private"):
        prepare_local_manager()
    assert (root / "secret.key").read_bytes() == before


def test_explicit_encryption_key_does_not_create_another(manager_environment, monkeypatch):
    monkeypatch.setenv("CUGA_SECRET_KEY", "external-key")
    root = prepare_local_manager()
    assert not (root / "secret.key").exists()
    assert os.environ["CUGA_SECRET_KEY"] == "external-key"


@pytest.mark.parametrize(
    "args", [["start", "manager"], ["--verbose", "start", "manager"], ["-v", "start", "manager"]]
)
@pytest.mark.parametrize("container_preset", [None, "default", "crm", "knowledge"])
def test_cli_bootstrap_keeps_ubi_presets_on_existing_flow(monkeypatch, args, container_preset):
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock
    from cuga import cli, local_setup

    prepare = Mock()
    app = Mock()
    monkeypatch.setattr(local_setup, "prepare_local_manager", prepare)
    monkeypatch.setitem(sys.modules, "cuga.cli.main", SimpleNamespace(app=app))
    monkeypatch.setattr(sys, "argv", ["cuga", *args])
    monkeypatch.delenv("CUGA_DEMO_MODE", raising=False)
    if container_preset:
        monkeypatch.setenv("CUGA_DEMO_MODE", container_preset)
    cli.run_cli()
    assert prepare.call_count == (0 if container_preset else 1)
    app.assert_called_once()
