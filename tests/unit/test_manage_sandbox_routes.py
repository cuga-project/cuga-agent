"""Manage → Code sandbox: on/off only; the connection follows from what is mounted."""

import pytest
from fastapi import HTTPException

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import connection
from cuga.backend.server.manage_routes import sandbox_routes
from cuga.backend.server.manage_routes.sandbox_routes import SandboxSettingsBody, put_sandbox_settings
from cuga.config import settings

pytestmark = pytest.mark.unit

_real_probe = sandbox_routes._probe


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    adv = settings.advanced_features
    for name in ("execd_url", "execd_api_key", "sandbox_secret_dir"):
        monkeypatch.setattr(adv, name, "", raising=False)
    monkeypatch.setattr(adv, "sandbox_mode", "local", raising=False)
    saved: list[dict] = []

    async def save(config):
        saved.append(config)

    async def probe(url):
        return {"reachable": True, "status_code": 200, "probed": url}

    # The routes import these names directly; patch them where they are used.
    monkeypatch.setattr(sandbox_routes, "save_sandbox_settings", save)
    monkeypatch.setattr(sandbox_routes, "_probe", probe)
    connection.set_runtime_config(None)
    yield saved
    connection.set_runtime_config(None)


@pytest.mark.asyncio
async def test_enabling_with_a_mounted_secret_needs_nothing_and_tests_at_once(
    monkeypatch, tmp_path, _isolated
):
    (tmp_path / "url").write_text("https://sandbox-api.example")
    (tmp_path / "api_key").write_text("k")
    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", str(tmp_path), raising=False)

    result = await put_sandbox_settings(SandboxSettingsBody(enabled=True))

    assert result["enabled"] and result["connection"] == "secret"
    assert result["probed"] == "https://sandbox-api.example"
    assert _isolated == [{"enabled": True}]


@pytest.mark.asyncio
async def test_enabling_without_a_secret_asks_for_the_url():
    with pytest.raises(HTTPException) as exc:
        await put_sandbox_settings(SandboxSettingsBody(enabled=True))
    assert exc.value.status_code == 422

    result = await put_sandbox_settings(SandboxSettingsBody(enabled=True, url=" https://execd.example "))
    assert result["connection"] == "manual"
    assert result["probed"] == "https://execd.example"


@pytest.mark.asyncio
async def test_switching_off_keeps_the_url_for_next_time():
    await put_sandbox_settings(SandboxSettingsBody(enabled=True, url="https://execd.example"))
    result = await put_sandbox_settings(SandboxSettingsBody(enabled=False))

    assert not result["enabled"]
    assert result["manual"]["url"] == "https://execd.example"


@pytest.mark.asyncio
async def test_cannot_switch_off_a_sandbox_the_deployment_requires(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "sandbox_mode", "execd", raising=False)
    with pytest.raises(HTTPException) as exc:
        await put_sandbox_settings(SandboxSettingsBody(enabled=False))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_key_entered_in_the_ui_is_sent_without_a_sync_db_read(monkeypatch):
    # get_secret_sync runs on a fresh event loop, which breaks against the
    # server's asyncpg pool; the key must come from the cache instead.
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor
    from cuga.backend.storage import secrets_store

    stored: dict[str, str] = {}

    async def set_secret(secret_id, value, **kwargs):
        stored[secret_id] = value

    async def get_secret(secret_id, **kwargs):
        return stored.get(secret_id)

    def no_sync_read(*args, **kwargs):
        raise AssertionError("sync secrets read from async code")

    monkeypatch.setattr(secrets_store, "set_secret", set_secret)
    monkeypatch.setattr(secrets_store, "get_secret", get_secret)
    monkeypatch.setattr(secrets_store, "get_secret_sync", no_sync_read)
    monkeypatch.setattr("cuga.backend.secrets.backends.db_backend.get_secret_sync", no_sync_read)

    api_key = "s3cret"  # pragma: allowlist secret
    await put_sandbox_settings(
        SandboxSettingsBody(enabled=True, url="https://sandbox.example", api_key=api_key)
    )
    assert ExecdExecutor._auth_headers()["X-API-Key"] == "s3cret"

    # After a restart only the db:// reference is known; startup loads the key.
    runtime = connection.get_runtime_config()
    connection.set_runtime_config(None)

    async def load():
        return runtime

    monkeypatch.setattr(sandbox_routes, "load_sandbox_settings", load)
    await sandbox_routes.load_sandbox_settings_into_runtime()
    assert ExecdExecutor._auth_headers()["X-API-Key"] == "s3cret"

    # Saving again without re-entering the key keeps sending it.
    await put_sandbox_settings(SandboxSettingsBody(enabled=True, url="https://sandbox.example"))
    assert ExecdExecutor._auth_headers()["X-API-Key"] == "s3cret"


@pytest.mark.asyncio
async def test_probe_reports_a_rejected_key(monkeypatch):
    import httpx

    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor

    connection.set_runtime_config({"enabled": True, "url": "https://sandbox.example", "api_key_ref": "plain"})
    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    monkeypatch.setattr(
        ExecdExecutor,
        "_client",
        classmethod(lambda cls, timeout, thread=None: httpx.AsyncClient(transport=transport)),
    )

    result = await _real_probe("https://sandbox.example")
    assert result["status_code"] == 401
    assert result["auth_error"] == "API key rejected by the sandbox"


@pytest.mark.asyncio
async def test_startup_fails_loudly_when_stored_settings_cannot_be_read(monkeypatch):
    """A transient DB error must not start the server with the sandbox silently off."""
    attempts = []

    async def broken():
        attempts.append(1)
        raise ConnectionError("db down")

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(sandbox_routes, "load_sandbox_settings", broken)
    monkeypatch.setattr(sandbox_routes.asyncio, "sleep", no_sleep)
    with pytest.raises(RuntimeError, match="code-sandbox settings"):
        await sandbox_routes.load_sandbox_settings_into_runtime()
    assert len(attempts) == sandbox_routes._LOAD_ATTEMPTS


@pytest.mark.asyncio
async def test_startup_retries_a_transient_settings_read(monkeypatch):
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("db blip")
        return {"enabled": True, "url": "https://sandbox.example"}

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(sandbox_routes, "load_sandbox_settings", flaky)
    monkeypatch.setattr(sandbox_routes.asyncio, "sleep", no_sleep)
    await sandbox_routes.load_sandbox_settings_into_runtime()
    assert connection.is_enabled() is True
