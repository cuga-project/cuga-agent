"""Path mapping and transport framing for the execd filesystem backend.

These are the parts that must hold without a sandbox to talk to: how an
agent-facing ``/workspace`` path becomes a path inside the thread's directory,
what refuses to resolve at all, and how a result is carried back out of a shell
command's stdout.
"""

from __future__ import annotations

import base64
import json

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.filesystem_backend import (
    _INVOKE,
    _MAX_WRITE_CHUNK,
    ExecdFilesystemBackend,
)

pytestmark = pytest.mark.unit


class _FakeExecd:
    """Stands in for ExecdExecutor, recording what the backend sends."""

    def __init__(self, result=None, error=None, has_kernel=True):
        self.calls: list[tuple[str, dict]] = []
        self.has_kernel = has_kernel
        self._payload = {"ok": True, "result": result if result is not None else {}}
        if error is not None:
            self._payload = {"ok": False, "error": error}

    def has_context(self, thread_id):
        return self.has_kernel

    def _workspace_path(self, thread_id):
        return f"/workspace/{thread_id or '_default'}"

    async def _run_shell_command(self, command, thread_id, extra_env=None):
        self.calls.append((command, extra_env or {}))
        framed = base64.b64encode(json.dumps(self._payload).encode()).decode()
        return f"noise from the command\nCUGA_FS_RESULT:{framed}", "", False


def _backend(**kwargs) -> tuple[ExecdFilesystemBackend, _FakeExecd]:
    fake = _FakeExecd(**kwargs)
    return ExecdFilesystemBackend(fake, "thread-a"), fake


@pytest.mark.parametrize(
    "given,expected",
    [
        ("/workspace", ""),
        ("/workspace/", ""),
        ("/workspace/notes/a.txt", "notes/a.txt"),
        ("notes/a.txt", "notes/a.txt"),
        ("/tmp/legacy.txt", "legacy.txt"),
        ("/workspace/./a/../b.txt", "b.txt"),
    ],
)
def test_agent_paths_map_into_the_thread_workspace(given, expected):
    backend, _ = _backend()
    assert backend._rel(given) == expected


@pytest.mark.parametrize("given", ["/etc/passwd", "/workspace/../../etc/passwd", "../outside", ""])
def test_paths_leaving_the_workspace_are_refused(given):
    backend, _ = _backend()
    with pytest.raises(ValueError):
        backend._rel(given)


@pytest.mark.asyncio
async def test_the_thread_directory_is_the_root_not_the_container_workspace():
    """One container holds every thread, so /workspace must not be shared."""
    backend, fake = _backend(result={"text": "hi"})

    await backend.read_text("/workspace/a.txt", operation="read_file")

    _command, env = fake.calls[0]
    spec = json.loads(base64.b64decode(env["CUGA_FS_OP"]).decode())
    assert spec["root"] == "/workspace/thread-a"
    assert spec["args"]["path"] == "a.txt"


@pytest.mark.asyncio
async def test_operands_travel_in_the_environment_not_the_command():
    """Nothing user-supplied is interpolated, so nothing needs shell quoting."""
    backend, fake = _backend(result={"path": "/workspace/x"})
    nasty = "it's \"quoted\"; rm -rf /\n$(whoami)"

    await backend.write_text("/workspace/x", nasty, operation="write_file")

    command, env = fake.calls[0]
    # The command is a fixed one-liner: same string for every operation.
    assert command == _INVOKE
    spec = json.loads(base64.b64decode(env["CUGA_FS_OP"]).decode())
    assert base64.b64decode(spec["args"]["data"]).decode("utf-8") == nasty


@pytest.mark.asyncio
async def test_large_writes_are_split_so_each_env_var_fits_one_execve_string():
    """Linux caps one exported string at 128 KiB; a 100 MiB upload must not be one."""
    backend, fake = _backend(result={"path": "/workspace/big.bin"})
    text = "x" * (3 * _MAX_WRITE_CHUNK + 1)

    await backend.write_text("/workspace/big.bin", text, operation="write_file")

    specs = [json.loads(base64.b64decode(env["CUGA_FS_OP"]).decode()) for _c, env in fake.calls]
    assert len(specs) == 4
    assert [s["args"]["append"] for s in specs] == [False, True, True, True]
    assert all(len(env["CUGA_FS_OP"]) < 128 * 1024 for _c, env in fake.calls)
    assert b"".join(base64.b64decode(s["args"]["data"]) for s in specs) == text.encode()


@pytest.mark.asyncio
async def test_an_empty_write_still_truncates_the_file():
    backend, fake = _backend(result={"path": "/workspace/x"})

    await backend.write_text("/workspace/x", "", operation="write_file")

    spec = json.loads(base64.b64decode(fake.calls[0][1]["CUGA_FS_OP"]).decode())
    assert len(fake.calls) == 1 and spec["args"] == {"path": "x", "data": "", "append": False}


@pytest.mark.asyncio
async def test_result_is_read_from_the_framed_line_not_the_whole_stdout():
    backend, _ = _backend(result={"text": "file body"})
    assert await backend.read_text("/workspace/a.txt", operation="read_file") == "file body"


@pytest.mark.asyncio
async def test_sandbox_errors_surface_as_the_type_the_tools_expect():
    backend, _ = _backend(error="FileNotFoundError: File not found in workspace: a.txt")
    with pytest.raises(FileNotFoundError):
        await backend.read_text("/workspace/a.txt", operation="read_file")


@pytest.mark.asyncio
async def test_a_missing_result_line_is_an_error_not_an_empty_read():
    backend, fake = _backend()

    async def _no_result(command, thread_id, extra_env=None):
        return "", "python3: command not found", True

    fake._run_shell_command = _no_result
    with pytest.raises(RuntimeError, match="no result"):
        await backend.read_text("/workspace/a.txt", operation="read_file")


@pytest.mark.asyncio
async def test_tree_of_a_thread_without_a_kernel_is_empty_and_creates_none():
    """The UI polls the tree; a poll must not bring a kernel into existence."""
    backend, fake = _backend(result={"dirs": ["/workspace/a"], "files": []}, has_kernel=False)

    assert await backend.walk() == ([], [])
    assert fake.calls == []


@pytest.mark.asyncio
async def test_tree_of_a_thread_with_a_kernel_is_read_from_the_sandbox():
    backend, fake = _backend(result={"dirs": ["/workspace/a"], "files": ["/workspace/a/b.txt"]})

    assert await backend.walk() == (["/workspace/a"], ["/workspace/a/b.txt"])
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_removing_from_a_thread_without_a_kernel_sends_nothing():
    """Deleting a conversation clears its uploads; that must not create a kernel to clear."""
    backend, fake = _backend(has_kernel=False)

    await backend.remove_tree("/workspace/uploads")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_writing_to_a_thread_without_a_kernel_still_reaches_the_sandbox():
    """Only reads of an empty workspace are skipped; a write is what creates it."""
    backend, fake = _backend(result={"path": "/workspace/x"}, has_kernel=False)

    await backend.write_text("/workspace/x", "data", operation="write_file")
    assert len(fake.calls) == 1
