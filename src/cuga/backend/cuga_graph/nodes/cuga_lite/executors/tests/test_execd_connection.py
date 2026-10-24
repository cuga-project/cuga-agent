import os

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import connection
from cuga.config import settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    adv = settings.advanced_features
    for name in ("execd_url", "execd_api_key", "sandbox_secret_dir"):
        monkeypatch.setattr(adv, name, "", raising=False)
    monkeypatch.setattr(adv, "sandbox_mode", "local", raising=False)
    connection.set_runtime_config(None)
    yield
    connection.set_runtime_config(None)


def _mount_secret(monkeypatch, tmp_path, url="http://execd.ns.svc:44772", key="k1"):
    secret = tmp_path / "cuga-sandbox"
    secret.mkdir()
    if url is not None:
        (secret / "url").write_text(url + "\n")
    if key is not None:
        (secret / "SANDBOX_API_KEY").write_text(key)
    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", str(secret), raising=False)
    return secret


def test_off_by_default():
    assert not connection.is_enabled()
    assert not connection.execd_active()
    assert connection.resolve() is None


def test_sandbox_mode_execd_turns_it_on_from_env(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "sandbox_mode", "execd", raising=False)
    monkeypatch.setattr(settings.advanced_features, "execd_url", "http://execd:44772", raising=False)
    monkeypatch.setattr(settings.advanced_features, "execd_api_key", "PLAINKEY", raising=False)

    assert connection.execd_active()
    expected_key = "PLAINKEY"  # pragma: allowlist secret
    assert connection.resolve() == connection.SandboxConnection(
        url="http://execd:44772", api_key=expected_key
    )


def test_ui_cannot_switch_off_a_sandbox_the_config_requires(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "sandbox_mode", "execd", raising=False)
    connection.set_runtime_config({"enabled": False})

    assert connection.is_enabled()
    assert connection.status()["required_by_config"] is True


def test_ui_switch_and_url_override_env(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "execd_url", "http://env:1", raising=False)
    connection.set_runtime_config({"enabled": True})
    assert connection.resolve().url == "http://env:1"

    connection.set_runtime_config({"enabled": True, "url": "http://ui:2", "api_key_ref": ""})
    assert connection.resolve().url == "http://ui:2"
    assert connection.effective_sandbox_mode("local") == "execd"

    connection.set_runtime_config({"enabled": False, "url": "http://ui:2"})
    assert connection.resolve() is None
    assert connection.effective_sandbox_mode("local") == "local"


def test_rows_saved_with_the_old_source_field_still_load():
    connection.set_runtime_config({"source": "manual", "url": "http://ui:2"})
    assert connection.is_enabled()
    connection.set_runtime_config({"source": "off"})
    assert not connection.is_enabled()


def test_mounted_secret_wins_over_url_and_picks_up_rotation(monkeypatch, tmp_path):
    secret = _mount_secret(monkeypatch, tmp_path)
    connection.set_runtime_config({"enabled": True, "url": "http://ui:2", "api_key_ref": "ignored"})

    assert connection.status()["connection"] == "secret"
    assert connection.resolve() == connection.SandboxConnection("http://execd.ns.svc:44772", "k1")

    key = secret / "SANDBOX_API_KEY"
    key.write_text("k2")
    st = key.stat()
    os.utime(key, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert connection.resolve().api_key == "k2"


def test_incomplete_secret_falls_back_to_url_and_key(monkeypatch, tmp_path):
    _mount_secret(monkeypatch, tmp_path, key=None)
    connection.set_runtime_config({"enabled": True, "url": "http://ui:2", "api_key_ref": "K"})

    status = connection.status()
    assert status["connection"] == "manual"
    assert status["secret"]["has_url"] and not status["secret"]["available"]
    assert connection.resolve() == connection.SandboxConnection("http://ui:2", "K")


def test_secret_layout_of_the_sandbox_operator(monkeypatch, tmp_path):
    secret = tmp_path / "cuga-sandbox"
    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", str(secret), raising=False)
    assert connection.status()["secret"]["available"] is False

    secret.mkdir()
    (secret / "SANDBOX_URL").write_text("https://sandbox-api.example")
    assert connection.status()["secret"]["available"] is False
    (secret / "SANDBOX_API_KEY").write_text("k")
    assert connection.status()["secret"]["available"] is True

    connection.set_runtime_config({"enabled": True})
    assert connection.resolve() == connection.SandboxConnection("https://sandbox-api.example", "k")


def test_directory_stored_in_the_db_is_ignored(monkeypatch, tmp_path):
    # Where the secret is mounted is deployment configuration; the UI only switches the sandbox.
    secret = _mount_secret(monkeypatch, tmp_path, url="http://env:1")
    connection.set_runtime_config({"enabled": True, "binding": "other"})

    assert connection.resolve().url == "http://env:1"
    assert connection.status()["secret"]["dir"] == str(secret)


def test_default_secret_dir_is_the_binding_mount(monkeypatch):
    # The chart mounts the binding here; no env var is needed to find it.
    from cuga.config import validators

    default = next(v for v in validators if "advanced_features.sandbox_secret_dir" in v.names).default
    assert default == "/bindings/cuga-sandbox"

    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", default, raising=False)
    assert str(connection.secret_dir()) == "/bindings/cuga-sandbox"
    # Outside a pod the directory does not exist: URL and key are asked for instead.
    assert connection.status()["connection"] == "manual"


def test_empty_secret_dir_disables_the_lookup(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", "", raising=False)
    assert connection.secret_dir() is None
