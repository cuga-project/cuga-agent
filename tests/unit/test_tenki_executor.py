from __future__ import annotations

import asyncio

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.tenki.tenki_executor import (
    REMOTE_WORKSPACE_ROOT,
    TenkiSandboxExecutor,
)


class _FS:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def mkdir(self, path, recursive=True):
        self.paths.append(path)


class _Sandbox:
    def __init__(self, result=None, ready_error: BaseException | None = None) -> None:
        self.id = "sandbox-id"
        self.fs = _FS()
        self.result = result
        self.ready_error = ready_error
        self.ready_timeouts: list[int] = []
        self.closed = 0

    async def wait_ready(self, timeout):
        self.ready_timeouts.append(timeout)
        if self.ready_error:
            raise self.ready_error

    async def exec(self, *args, **kwargs):
        return self.result

    async def close_if_open(self):
        self.closed += 1


class _Result:
    def __init__(self, exit_code=0, stdout="", stderr="", reason="exit") -> None:
        self.exit_code = exit_code
        self.stdout_text = stdout
        self.stderr_text = stderr
        self.reason = reason
        self.ok = exit_code == 0


class _Client:
    def __init__(self, sandbox: _Sandbox) -> None:
        self.sandbox = sandbox
        self.create_kwargs: dict = {}
        self.closed = 0

    async def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.sandbox

    async def close(self):
        self.closed += 1


@pytest.mark.unit
def test_lifecycle_state_is_instance_scoped():
    first = TenkiSandboxExecutor()
    second = TenkiSandboxExecutor()
    first._sandboxes["thread"] = _Sandbox(_Result())
    first._released.add("thread")
    assert not second._sandboxes
    assert not second._released


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_get_creates_one_sandbox(monkeypatch):
    executor = TenkiSandboxExecutor()
    sandbox = _Sandbox(_Result())
    creates = 0

    async def create(key, cuga_folder=None):
        nonlocal creates
        creates += 1
        await asyncio.sleep(0)
        return sandbox

    monkeypatch.setattr(executor, "_create_sandbox", create)
    first, second = await asyncio.gather(
        executor._get_or_create_sandbox("thread"), executor._get_or_create_sandbox("thread")
    )
    assert first is second is sandbox
    assert creates == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_setup_cancellation_terminates_allocated_sandbox(monkeypatch):
    executor = TenkiSandboxExecutor()
    sandbox = _Sandbox(_Result())
    client = _Client(sandbox)

    async def project_id(_client):
        return "project"

    async def cancel_upload(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(executor, "_new_client", lambda: client)
    monkeypatch.setattr(executor, "_project_id", project_id)
    monkeypatch.setattr(executor, "_upload_skills", cancel_upload)

    with pytest.raises(asyncio.CancelledError):
        await executor._create_sandbox("thread")
    assert sandbox.closed == 1
    assert client.closed == 1
    assert "thread" not in executor._sandboxes


@pytest.mark.unit
@pytest.mark.asyncio
async def test_readiness_failure_terminates_allocated_sandbox(monkeypatch):
    executor = TenkiSandboxExecutor()
    sandbox = _Sandbox(ready_error=RuntimeError("not ready"))
    client = _Client(sandbox)

    monkeypatch.setattr(executor, "_new_client", lambda: client)
    monkeypatch.setattr(executor, "_project_id", lambda _client: asyncio.sleep(0, result="project"))

    with pytest.raises(RuntimeError, match="not ready"):
        await executor._create_sandbox("thread")

    assert sandbox.closed == 1
    assert client.closed == 1
    assert client.create_kwargs["wait"] is False
    assert client.create_kwargs["max_duration"] > 0
    assert sandbox.ready_timeouts == [180]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dead_cached_sandbox_is_closed_before_replacement(monkeypatch):
    executor = TenkiSandboxExecutor()
    stale = _Sandbox(_Result())
    stale_client = _Client(stale)
    replacement = _Sandbox(_Result())

    async def stale_exec(*args, **kwargs):
        raise RuntimeError("gone")

    async def create(key, cuga_folder=None):
        executor._clients[key] = _Client(replacement)
        return replacement

    stale.exec = stale_exec
    executor._sandboxes["thread"] = stale
    executor._clients["thread"] = stale_client
    monkeypatch.setattr(executor, "_create_sandbox", create)

    assert await executor._get_or_create_sandbox("thread") is replacement
    assert stale.closed == 1
    assert stale_client.closed == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_command_surfaces_failure_and_release_terminates(monkeypatch):
    executor = TenkiSandboxExecutor()
    sandbox = _Sandbox(_Result(exit_code=17, stderr="failed", reason="exit"))
    client = _Client(sandbox)

    async def create(key, cuga_folder=None):
        executor._clients[key] = client
        return sandbox

    monkeypatch.setattr(executor, "_create_sandbox", create)
    output = await executor.create_run_command_tool("thread")("false")
    assert "failed" in output
    assert "exit code 17" in output
    await executor.release_sandbox("thread")
    assert sandbox.closed == 1
    assert client.closed == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_all_terminates_every_cached_sandbox():
    executor = TenkiSandboxExecutor()
    sandboxes = {key: _Sandbox(_Result()) for key in ("a", "b")}
    clients = {key: _Client(sandbox) for key, sandbox in sandboxes.items()}
    executor._sandboxes.update(sandboxes)
    executor._clients.update(clients)

    await executor.release_all()

    assert not executor._sandboxes
    assert not executor._clients
    assert all(sandbox.closed == 1 for sandbox in sandboxes.values())
    assert all(client.closed == 1 for client in clients.values())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_sandbox_times_out_on_stalled_teardown(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.tenki import tenki_executor

    executor = TenkiSandboxExecutor()
    sandbox = _Sandbox(_Result())

    async def stalled_close():
        await asyncio.sleep(3600)

    sandbox.close_if_open = stalled_close
    executor._sandboxes["thread"] = sandbox
    monkeypatch.setattr(tenki_executor, "CLEANUP_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await executor.release_sandbox("thread")
    assert "thread" not in executor._sandboxes


@pytest.mark.unit
@pytest.mark.asyncio
async def test_released_conversation_cannot_recreate_sandbox(monkeypatch):
    executor = TenkiSandboxExecutor()
    creates = 0

    async def create(key, cuga_folder=None):
        nonlocal creates
        creates += 1
        return _Sandbox(_Result())

    monkeypatch.setattr(executor, "_create_sandbox", create)
    await executor.release_sandbox("thread")

    with pytest.raises(RuntimeError, match="released"):
        await executor._get_or_create_sandbox("thread")
    assert creates == 0


@pytest.mark.unit
def test_remote_workspace_stays_inside_tenki_workdir():
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.tenki.filesystem_backend import (
        TenkiRemoteSandboxBackend,
    )

    backend = TenkiRemoteSandboxBackend(TenkiSandboxExecutor(), "thread")
    assert backend._remote("/workspace/a.txt") == f"{REMOTE_WORKSPACE_ROOT}/a.txt"
    with pytest.raises(ValueError, match="stay under"):
        backend._remote("/workspace/../secret")
