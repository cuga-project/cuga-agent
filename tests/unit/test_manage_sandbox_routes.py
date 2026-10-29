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
    monkeypatch.setattr(adv, "sandbox_callback_url", "http://cuga.ns.svc:7860", raising=False)
    saved: list[dict] = []

    async def save(config):
        saved.append(config)

    async def probe(conn):
        return {"reachable": True, "probed": conn.url if conn else None}

    # The routes import these names directly; patch them where they are used.
    monkeypatch.setattr(sandbox_routes, "save_sandbox_settings", save)
    monkeypatch.setattr(sandbox_routes, "_probe", probe)
    connection.set_runtime_config(None)
    yield saved
    connection.set_runtime_config(None)


def _mount_secret(monkeypatch, tmp_path):
    (tmp_path / "url").write_text("https://sandbox-api.example")
    (tmp_path / "api_key").write_text("k")
    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", str(tmp_path), raising=False)


@pytest.mark.asyncio
async def test_enabling_with_a_mounted_secret_needs_nothing_and_tests_at_once(
    monkeypatch, tmp_path, _isolated
):
    _mount_secret(monkeypatch, tmp_path)

    result = await put_sandbox_settings(SandboxSettingsBody(enabled=True))

    assert result["enabled"] and result["connection"] == "secret"
    assert result["probed"] == "https://sandbox-api.example"
    assert _isolated == [{"enabled": True}]


@pytest.mark.asyncio
async def test_without_a_secret_the_sandbox_is_not_offered_and_cannot_be_enabled(_isolated):
    assert connection.status()["available"] is False

    with pytest.raises(HTTPException) as exc:
        await put_sandbox_settings(SandboxSettingsBody(enabled=True))

    assert exc.value.status_code == 422
    assert _isolated == []


@pytest.mark.asyncio
async def test_the_ui_takes_no_url_or_key(monkeypatch, tmp_path):
    # Fields an older UI sent are ignored; the mounted secret is the connection.
    _mount_secret(monkeypatch, tmp_path)

    body = SandboxSettingsBody.model_validate(
        {"enabled": True, "url": "https://evil.example", "api_key": "x"}
    )
    result = await put_sandbox_settings(body)

    assert result["probed"] == "https://sandbox-api.example"
    assert connection.get_runtime_config() == {"enabled": True}


@pytest.mark.asyncio
async def test_a_sandbox_on_can_always_be_switched_off(monkeypatch, tmp_path, _isolated):
    _mount_secret(monkeypatch, tmp_path)
    await put_sandbox_settings(SandboxSettingsBody(enabled=True))
    # The binding went away while the sandbox was on: the switch stays visible.
    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", "", raising=False)
    assert connection.status()["available"] is True

    result = await put_sandbox_settings(SandboxSettingsBody(enabled=False))

    assert not result["enabled"]
    assert _isolated[-1] == {"enabled": False}


@pytest.mark.asyncio
async def test_settings_url_makes_the_sandbox_available_for_local_development(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "execd_url", "http://localhost:8090", raising=False)

    assert connection.status()["available"] is True
    result = await put_sandbox_settings(SandboxSettingsBody(enabled=True))
    assert result["connection"] == "settings" and result["probed"] == "http://localhost:8090"


@pytest.mark.asyncio
async def test_cannot_switch_off_a_sandbox_the_deployment_requires(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "sandbox_mode", "execd", raising=False)
    with pytest.raises(HTTPException) as exc:
        await put_sandbox_settings(SandboxSettingsBody(enabled=False))
    assert exc.value.status_code == 422


def _probe_against(monkeypatch, status: int | Exception):
    import httpx

    def handler(request):
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, key, reason",
    [
        (401, "plain", "API key rejected by the sandbox"),
        (403, "", "The sandbox requires an API key"),
        (500, "plain", "The sandbox returned an error"),
        (ConnectionError("refused at 10.0.0.7"), "plain", "The sandbox does not answer"),
    ],
)
async def test_probe_gives_a_reason_not_the_details(monkeypatch, status, key, reason):
    # Status codes and exception text (hosts, addresses) stay in the server log (item 16).
    _probe_against(monkeypatch, status)

    result = await _real_probe(connection.SandboxConnection("https://sandbox.example", key))

    assert result["probe_error"] == reason
    assert set(result) == {"reachable", "probe_error"}


@pytest.mark.asyncio
async def test_probe_sends_the_identity_headers_and_the_key_under_test(monkeypatch):
    import httpx

    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        def handler(request):
            seen.append(request)
            return httpx.Response(200)

        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    await _real_probe(connection.SandboxConnection("https://sandbox.example", "k-under-test"))

    assert seen[0].headers["X-API-Key"] == "k-under-test"
    assert seen[0].headers["X-Tenant-Id"] and seen[0].headers["X-Agent-Id"]


@pytest.mark.asyncio
async def test_probe_of_a_healthy_sandbox_is_just_reachable(monkeypatch):
    _probe_against(monkeypatch, 200)
    assert await _real_probe(connection.SandboxConnection("https://sandbox.example", "k")) == {
        "reachable": True
    }


@pytest.mark.asyncio
async def test_a_sandbox_that_does_not_answer_is_not_switched_on(monkeypatch, tmp_path, _isolated):
    # Probed before saving: a failed check leaves the sandbox off and nothing stored (item 24).
    _mount_secret(monkeypatch, tmp_path)

    async def failing(conn):
        return {"reachable": False, "probe_error": "The sandbox does not answer"}

    monkeypatch.setattr(sandbox_routes, "_probe", failing)
    with pytest.raises(HTTPException) as exc:
        await put_sandbox_settings(SandboxSettingsBody(enabled=True))

    assert exc.value.status_code == 422 and "does not answer" in exc.value.detail
    assert _isolated == []
    assert connection.is_enabled() is False


@pytest.mark.asyncio
async def test_without_a_callback_url_the_sandbox_is_not_switched_on(monkeypatch, tmp_path, _isolated):
    # Every tool call from the sandbox comes back to this address (item 25).
    _mount_secret(monkeypatch, tmp_path)
    monkeypatch.setattr(settings.advanced_features, "sandbox_callback_url", "", raising=False)

    with pytest.raises(HTTPException) as exc:
        await put_sandbox_settings(SandboxSettingsBody(enabled=True))

    assert exc.value.status_code == 422 and "sandbox_callback_url" in exc.value.detail
    assert _isolated == []


def test_status_names_what_is_missing(monkeypatch):
    # sandbox_mode = "execd" with nothing configured says so (item 32).
    monkeypatch.setattr(settings.advanced_features, "sandbox_mode", "execd", raising=False)
    assert connection.status()["error"] == "Sandbox URL is not configured"

    monkeypatch.setattr(settings.advanced_features, "execd_url", "http://localhost:8090", raising=False)
    monkeypatch.setattr(settings.advanced_features, "sandbox_callback_url", "", raising=False)
    assert "sandbox_callback_url" in connection.status()["error"]


@pytest.mark.asyncio
async def test_switching_off_releases_the_kernels_this_process_holds(monkeypatch, tmp_path):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor

    _mount_secret(monkeypatch, tmp_path)
    await put_sandbox_settings(SandboxSettingsBody(enabled=True))
    released: list[str] = []

    async def release(thread_id, agent_id=None, timeout=None):
        released.append(thread_id)

    monkeypatch.setattr(ExecdExecutor, "release_thread", staticmethod(release))
    monkeypatch.setattr(ExecdExecutor, "_contexts", {"t1": "ctx-1", "t2": "ctx-2"})

    await put_sandbox_settings(SandboxSettingsBody(enabled=False))

    assert sorted(released) == ["t1", "t2"]


@pytest.mark.asyncio
async def test_switching_off_is_saved_first_and_does_not_wait_on_a_broken_sandbox(
    monkeypatch, tmp_path, _isolated
):
    """Kernels are released after "off" is saved, in parallel and with a short timeout."""
    import asyncio
    import time

    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor

    _mount_secret(monkeypatch, tmp_path)
    await put_sandbox_settings(SandboxSettingsBody(enabled=True))
    seen: list[tuple] = []

    async def hung_release(thread_id, agent_id=None, timeout=None):
        # What the route has done by the time a kernel is released.
        seen.append((thread_id, timeout, connection.is_enabled(), list(_isolated)))
        await asyncio.sleep(0.3)

    monkeypatch.setattr(ExecdExecutor, "release_thread", staticmethod(hung_release))
    monkeypatch.setattr(ExecdExecutor, "_contexts", {f"t{i}": f"ctx-{i}" for i in range(5)})

    started = time.monotonic()
    await put_sandbox_settings(SandboxSettingsBody(enabled=False))

    # Five kernels at 0.3 s each, released one at a time, would take 1.5 s.
    assert time.monotonic() - started < 1.0
    assert len(seen) == 5
    for _thread, timeout, enabled, saved in seen:
        assert timeout == sandbox_routes._RELEASE_TIMEOUT_S
        assert enabled is False
        assert saved[-1] == {"enabled": False}


@pytest.mark.asyncio
async def test_resetting_to_the_defaults_releases_the_kernels_like_switching_off(monkeypatch, tmp_path):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor

    _mount_secret(monkeypatch, tmp_path)
    await put_sandbox_settings(SandboxSettingsBody(enabled=True))
    released: list[str] = []

    async def release(thread_id, agent_id=None, timeout=None):
        released.append(thread_id)

    async def delete():
        pass

    monkeypatch.setattr(sandbox_routes, "delete_sandbox_settings", delete)
    monkeypatch.setattr(ExecdExecutor, "release_thread", staticmethod(release))
    monkeypatch.setattr(ExecdExecutor, "_contexts", {"t1": "ctx-1"})

    status = await sandbox_routes.reset_sandbox_settings()

    assert status["enabled"] is False
    assert released == ["t1"]


@pytest.mark.asyncio
async def test_resetting_keeps_the_kernels_of_a_sandbox_the_deployment_requires(monkeypatch, tmp_path):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor

    _mount_secret(monkeypatch, tmp_path)
    monkeypatch.setattr(settings.advanced_features, "sandbox_mode", "execd", raising=False)
    released: list[str] = []

    async def release(thread_id, agent_id=None, timeout=None):
        released.append(thread_id)  # pragma: no cover

    async def delete():
        pass

    monkeypatch.setattr(sandbox_routes, "delete_sandbox_settings", delete)
    monkeypatch.setattr(ExecdExecutor, "release_thread", staticmethod(release))
    monkeypatch.setattr(ExecdExecutor, "_contexts", {"t1": "ctx-1"})

    await sandbox_routes.reset_sandbox_settings()

    assert released == []


@pytest.mark.asyncio
async def test_startup_continues_with_the_sandbox_off_when_settings_cannot_be_read(monkeypatch):
    """A DB error reading the sandbox row must not stop the server from starting."""
    attempts = []

    async def broken():
        attempts.append(1)
        raise ConnectionError("db down")

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(sandbox_routes, "load_sandbox_settings", broken)
    monkeypatch.setattr(sandbox_routes.asyncio, "sleep", no_sleep)

    await sandbox_routes.load_sandbox_settings_into_runtime()

    assert len(attempts) == sandbox_routes._LOAD_ATTEMPTS
    assert connection.is_enabled() is False


@pytest.mark.asyncio
async def test_startup_keeps_a_sandbox_the_deployment_requires_when_settings_cannot_be_read(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "sandbox_mode", "execd", raising=False)

    async def broken():
        raise ConnectionError("db down")

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(sandbox_routes, "load_sandbox_settings", broken)
    monkeypatch.setattr(sandbox_routes.asyncio, "sleep", no_sleep)

    await sandbox_routes.load_sandbox_settings_into_runtime()

    assert connection.is_enabled() is True


@pytest.mark.asyncio
async def test_startup_retries_a_transient_settings_read(monkeypatch):
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("db blip")
        return {"enabled": True}

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(sandbox_routes, "load_sandbox_settings", flaky)
    monkeypatch.setattr(sandbox_routes.asyncio, "sleep", no_sleep)
    await sandbox_routes.load_sandbox_settings_into_runtime()
    assert connection.is_enabled() is True
