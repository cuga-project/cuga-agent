"""End-to-end test for sandbox_mode=local with skills enabled.

The agent's `run_command` shell tool MUST land relative-path outputs under
`<cwd>/cuga_workspace/<safe_thread_id>/`. Two concurrent threads must NOT
see each other's files. Filesystem tools themselves are now provided by the
consolidated ``filesystem`` package — covered by ``filesystem/tests``.

We exercise the real ``LocalSandboxExecutor._run_command`` over a real
subprocess so any drift in CWD wiring, mkdir, or workspace selection is
caught. ``uv venv`` is short-circuited by pre-staging a fake
``.venv/bin/python`` so the test stays fast and doesn't require ``uv``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import paths as _paths
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local import (
    local_sandbox_executor as _lse,
)


def _fake_venv_python(workspace_root: Path) -> Path:
    """Pre-stage a stub `.venv` so `_ensure_workspace_venv` skips `uv venv`."""
    if sys.platform == "win32":
        py = workspace_root / ".venv" / "Scripts" / "python.exe"
    else:
        py = workspace_root / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    return py


def _enable_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the canonical workspace resolver into per-thread mode."""
    monkeypatch.setattr(_paths, "skills_enabled", lambda: True)


async def _run(executor: _lse.LocalSandboxExecutor, cmd: str, thread_id: str) -> tuple[str, str, int]:
    return await executor._run_command(cmd, thread_id=thread_id, timeout=30)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell idioms")
def test_local_sandbox_relative_paths_land_in_per_thread_cuga_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent emits `echo hello > out.txt` → file lands in
    `<cwd>/cuga_workspace/<thread>/out.txt`."""
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    parent = tmp_path / "cuga_workspace"
    parent.mkdir(parents=True, exist_ok=True)
    (parent / "sibling.txt").write_text("sibling\n")

    safe = _lse._safe_thread_id("thread-A")
    thread_root = parent / safe
    _fake_venv_python(thread_root)

    executor = _lse.LocalSandboxExecutor()
    stdout, stderr, _rc = asyncio.run(_run(executor, "echo hello > out.txt", "thread-A"))

    expected = parent / safe
    assert _lse.local_thread_workspace_root("thread-A").resolve() == expected.resolve()

    out_file = expected / "out.txt"
    assert out_file.exists(), f"out.txt should be under {expected}; stdout={stdout!r} stderr={stderr!r}"
    assert out_file.read_text().strip() == "hello"

    assert not (expected / "sibling.txt").exists(), (
        "per-thread workspaces must stay isolated — no auto-copy from parent"
    )
    assert not (Path("/tmp") / "cuga").exists() or not any(Path("/tmp/cuga").iterdir()), (
        "no files should be created under the legacy /tmp/cuga path"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell idioms")
def test_local_sandbox_two_threads_are_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two concurrent threads write the same relative filename but must NOT
    overwrite each other — they live in separate per-thread subdirs."""
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    parent = tmp_path / "cuga_workspace"
    parent.mkdir(parents=True, exist_ok=True)

    for tid in ("thread-A", "thread-B"):
        _fake_venv_python(parent / _lse._safe_thread_id(tid))

    executor = _lse.LocalSandboxExecutor()
    asyncio.run(_run(executor, "echo from-A > out.txt", "thread-A"))
    asyncio.run(_run(executor, "echo from-B > out.txt", "thread-B"))

    a_dir = parent / _lse._safe_thread_id("thread-A")
    b_dir = parent / _lse._safe_thread_id("thread-B")

    assert (a_dir / "out.txt").read_text().strip() == "from-A"
    assert (b_dir / "out.txt").read_text().strip() == "from-B"
    assert not (parent / "out.txt").exists()


def test_local_thread_workspace_root_is_per_thread_even_when_skills_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a thread_id, workspaces stay isolated even when skills are disabled."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_paths, "skills_enabled", lambda: False)

    a = _lse.local_thread_workspace_root("thread-A")
    b = _lse.local_thread_workspace_root("thread-B")
    none = _lse.local_thread_workspace_root(None)
    assert a == tmp_path / "cuga_workspace" / "thread-A"
    assert b == tmp_path / "cuga_workspace" / "thread-B"
    assert none == tmp_path / "cuga_workspace"
    assert a != b != none


def test_local_thread_workspace_root_is_per_thread_when_skills_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With skills enabled, threads diverge into safe-id subdirs."""
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    a = _lse.local_thread_workspace_root("thread-A")
    b = _lse.local_thread_workspace_root("thread/B")  # slash should be sanitized
    assert a == tmp_path / "cuga_workspace" / "thread-A"
    assert b == tmp_path / "cuga_workspace" / "thread_B"
    assert a != b


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell idioms")
def test_local_sandbox_run_command_rewrites_workspace_upload_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``head /workspace/uploads/foo.json`` works after rewrite to ``./uploads/...``."""
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    parent = tmp_path / "cuga_workspace"
    thread_root = parent / _lse._safe_thread_id("thread-A")
    uploads = thread_root / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "data.json").write_text('{"ok": true}\n')
    _fake_venv_python(thread_root)

    executor = _lse.LocalSandboxExecutor()
    stdout, stderr, rc = asyncio.run(_run(executor, "head -n 1 /workspace/uploads/data.json", "thread-A"))
    assert rc == 0
    assert '{"ok": true}' in stdout
    assert "No such file" not in stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell idioms")
def test_run_command_omits_stderr_on_success_with_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeprecationWarning on stderr must not break json.loads on stdout JSON."""
    import json

    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)
    thread_root = tmp_path / "cuga_workspace" / _lse._safe_thread_id("thread-A")
    thread_root.mkdir(parents=True)
    py = _fake_venv_python(thread_root)
    py.unlink()
    py.symlink_to(sys.executable)

    executor = _lse.LocalSandboxExecutor()
    run = executor.create_run_command_tool("thread-A")
    out = asyncio.run(
        run(
            "python -c \"import datetime, json; "
            "datetime.datetime.utcfromtimestamp(1); "
            "print(json.dumps({'ok': True}))\""
        )
    )
    assert "[stderr]" not in out
    assert json.loads(out.split("\n[stderr]\n", 1)[0].strip()) == {"ok": True}
