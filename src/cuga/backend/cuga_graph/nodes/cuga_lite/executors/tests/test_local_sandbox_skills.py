"""End-to-end test for sandbox_mode=local with skills enabled.

The agent's `run_command` shell tool MUST land relative-path outputs under
`<cwd>/cuga_workspace/<safe_thread_id>/`, and a fresh thread's first run
must seed top-level fixtures from `<cwd>/cuga_workspace/` into the new
thread dir. Two concurrent threads must NOT see each other's files.

We exercise the real ``LocalSandboxExecutor._run_command`` over a real
subprocess so any drift in CWD wiring, mkdir, or seeding is caught.
``uv venv`` is short-circuited by pre-staging a fake ``.venv/bin/python``
so the test stays fast and doesn't require ``uv`` on PATH.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

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
    """Force `_skills_enabled()` to return True inside the executor module.

    Cheaper than fighting Dynaconf attribute-setting semantics, and scoped
    to just the helper that gates per-thread workspace selection.
    """
    monkeypatch.setattr(_lse, "_skills_enabled", lambda: True)


async def _run(executor: _lse.LocalSandboxExecutor, cmd: str, thread_id: str) -> tuple[str, str]:
    return await executor._run_command(cmd, thread_id=thread_id, timeout=30)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell idioms")
def test_local_sandbox_relative_paths_land_in_per_thread_cuga_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent emits `echo hello > out.txt` → file lands in
    `<cwd>/cuga_workspace/<thread>/out.txt`, NOT in /tmp/cuga/... or the
    cuga server's CWD."""
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    parent = tmp_path / "cuga_workspace"
    parent.mkdir(parents=True, exist_ok=True)
    # A sibling top-level file must NOT be copied into the thread dir —
    # per-thread workspaces are isolated, not inheritance buckets.
    (parent / "sibling.txt").write_text("sibling\n")

    safe = _lse._safe_thread_id("thread-A")
    thread_root = parent / safe
    _fake_venv_python(thread_root)

    executor = _lse.LocalSandboxExecutor()
    stdout, stderr = asyncio.run(_run(executor, "echo hello > out.txt", "thread-A"))

    # Workspace layout
    expected = parent / safe
    assert thread_root == expected, "thread workspace path should be <cwd>/cuga_workspace/<safe_thread>"
    local_thread_root = _lse.local_thread_workspace_root("thread-A")
    assert local_thread_root.resolve() == expected.resolve()

    # Relative shell output landed in the per-thread workspace
    out_file = expected / "out.txt"
    assert out_file.exists(), f"out.txt should be under {expected}; stdout={stdout!r} stderr={stderr!r}"
    assert out_file.read_text().strip() == "hello"

    # Parent-level file MUST NOT auto-propagate into the per-thread dir.
    assert not (expected / "sibling.txt").exists(), (
        "per-thread workspaces must stay isolated — no auto-copy from parent"
    )

    # Nothing leaked into /tmp/cuga (the legacy location)
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
    # The shared parent fixture must NOT be polluted by thread-level writes
    assert not (parent / "out.txt").exists()


def test_local_thread_workspace_root_is_shared_when_skills_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With skills disabled, all threads must collapse to the shared
    `<cwd>/cuga_workspace/` directory — matching the demo CLI UX."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_lse, "_skills_enabled", lambda: False)

    a = _lse.local_thread_workspace_root("thread-A")
    b = _lse.local_thread_workspace_root("thread-B")
    none = _lse.local_thread_workspace_root(None)
    assert a == b == none == tmp_path / "cuga_workspace"


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


# ─── In-process sandbox filesystem tools ────────────────────────────────────
#
# The `write_file` / `read_file` / `list_files` tools that LocalSandboxExecutor
# binds into the agent's namespace must honor the relative-paths contract:
# any path the agent emits — `"out.txt"`, `"./out.txt"`, `"data/x.json"` —
# lands inside the per-thread workspace.


def test_sandbox_write_read_round_trip_uses_per_thread_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """write_file → read_file round-trip with a plain relative path."""
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    executor = _lse.LocalSandboxExecutor()
    write = executor.create_write_file_tool(thread_id="thread-A")
    read = executor.create_read_file_tool(thread_id="thread-A")

    msg = asyncio.run(write("notes.md", "hello world"))
    assert "File written" in msg

    on_disk = tmp_path / "cuga_workspace" / "thread-A" / "notes.md"
    assert on_disk.exists(), f"notes.md should land in per-thread dir, got {msg}"
    assert on_disk.read_text() == "hello world"

    content = asyncio.run(read("notes.md"))
    assert content == "hello world"


def test_sandbox_list_files_surfaces_relative_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh thread's `list_files` must show the agent's own writes
    inside its per-thread workspace — and NOT parent-level files (those
    belong to other threads or the shared workspace, not this one)."""
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    parent = tmp_path / "cuga_workspace"
    parent.mkdir(parents=True, exist_ok=True)
    # A parent-level file should NOT show up in this thread's listing —
    # per-thread workspaces are isolated.
    (parent / "sibling.txt").write_text("not mine\n")

    executor = _lse.LocalSandboxExecutor()
    write = executor.create_write_file_tool(thread_id="thread-A")
    list_files = executor.create_list_files_tool(thread_id="thread-A")

    asyncio.run(write("agent_output.txt", "from agent"))
    listing = asyncio.run(list_files("."))

    assert "agent_output.txt" in listing, listing
    assert "sibling.txt" not in listing, "parent-level files must not bleed into per-thread listings"


def test_sandbox_filesystem_tools_reject_paths_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense-in-depth: paths that try to escape the workspace via .. must
    be refused by `_resolve_workspace_path`, regardless of skills mode."""
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    executor = _lse.LocalSandboxExecutor()
    write = executor.create_write_file_tool(thread_id="thread-A")

    msg = asyncio.run(write("../escape.txt", "nope"))
    # Tool catches the ValueError and returns it as an "[error]" string —
    # what matters is no file was created outside the thread dir.
    assert "error" in msg.lower(), msg
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "cuga_workspace" / "escape.txt").exists()


# ─── MCP filesystem tool wrappers ───────────────────────────────────────────
#
# The chat-agent and cuga-lite paths both inject filesystem MCP tools into
# the agent's namespace; when skills are enabled they go through a wrapper
# that prefixes relative paths with `<safe_thread_id>/`. We mock the
# underlying tool so the test never needs a real MCP server.


def _make_recorder():
    """Return (record_dict, async_inner_callable) — inner records whatever
    call shape it receives (positional-dict OR kwargs) so a single fixture
    works against both runtime invocation patterns."""
    captured: dict = {}

    async def inner(*args, **kwargs):
        if args and isinstance(args[0], dict):
            captured.update(args[0])
        if kwargs:
            captured.update(kwargs)
        return "ok"

    return captured, inner


def test_mcp_func_wrapper_prefixes_relative_paths_when_skills_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wrap_filesystem_tool_func` is the integration point used by
    cuga_lite_graph for raw-function MCP tools. Relative path args MUST
    be prefixed with `<safe_thread_id>/` before forwarding — across both
    positional-dict and kwargs invocation shapes."""
    from cuga.backend.cuga_graph.nodes.chat.chat_agent import mcp_filesystem_wrapper

    monkeypatch.setattr(mcp_filesystem_wrapper, "_skills_enabled", lambda: True)

    captured, inner = _make_recorder()
    wrapped = mcp_filesystem_wrapper.wrap_filesystem_tool_func(
        "filesystem_write_file", inner, thread_id="thread-A"
    )

    # Positional-dict call shape (the form `tool.ainvoke({...})` ultimately resolves to)
    asyncio.run(wrapped({"path": "contacts.txt", "content": "x"}))
    assert captured == {"path": "thread-A/contacts.txt", "content": "x"}

    captured.clear()
    # Kwargs call shape (the form the agent's Python code uses)
    asyncio.run(wrapped(path="./data/raw.json", content="y"))
    assert captured == {"path": "thread-A/data/raw.json", "content": "y"}


def test_mcp_func_wrapper_passes_absolute_paths_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absolute paths must NOT be rewritten — they go straight to MCP, which
    will allow-check them. This preserves the loud-failure behavior we want
    if the LLM ignores the relative-paths instruction."""
    from cuga.backend.cuga_graph.nodes.chat.chat_agent import mcp_filesystem_wrapper

    monkeypatch.setattr(mcp_filesystem_wrapper, "_skills_enabled", lambda: True)

    captured: dict = {}

    async def inner(**kwargs):
        captured.update(kwargs)
        return "ok"

    wrapped = mcp_filesystem_wrapper.wrap_filesystem_tool_func(
        "filesystem_read_text_file", inner, thread_id="thread-A"
    )
    asyncio.run(wrapped(path="/workspace/contacts.txt"))
    assert captured == {"path": "/workspace/contacts.txt"}, "absolute path must pass through unchanged"


def test_mcp_func_wrapper_is_noop_when_skills_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With skills disabled the workspace is shared; no prefix should be added."""
    from cuga.backend.cuga_graph.nodes.chat.chat_agent import mcp_filesystem_wrapper

    monkeypatch.setattr(mcp_filesystem_wrapper, "_skills_enabled", lambda: False)

    captured: dict = {}

    async def inner(args: dict) -> str:
        captured.update(args)
        return "ok"

    wrapped = mcp_filesystem_wrapper.wrap_filesystem_tool_func(
        "filesystem_write_file", inner, thread_id="thread-A"
    )
    # When skills are off, the wrapper should be the identity function.
    assert wrapped is inner
    asyncio.run(wrapped({"path": "contacts.txt"}))
    assert captured == {"path": "contacts.txt"}


def test_mcp_func_wrapper_only_wraps_filesystem_named_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-filesystem tools (crm_*, email_*, etc.) must not be wrapped even
    when skills are on — they don't accept filesystem path args."""
    from cuga.backend.cuga_graph.nodes.chat.chat_agent import mcp_filesystem_wrapper

    monkeypatch.setattr(mcp_filesystem_wrapper, "_skills_enabled", lambda: True)

    async def inner(**kwargs):
        return "ok"

    wrapped = mcp_filesystem_wrapper.wrap_filesystem_tool_func(
        "crm_get_contacts", inner, thread_id="thread-A"
    )
    assert wrapped is inner, "non-filesystem tools must be passed through unchanged"


def test_mcp_basetool_wrapper_rewrites_paths_via_structured_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wrap_mcp_filesystem_tools` is the chat-agent integration point that
    operates on LangChain BaseTool instances. The wrapped tool keeps the
    same name/description while rewriting path args on invoke."""
    from cuga.backend.cuga_graph.nodes.chat.chat_agent import mcp_filesystem_wrapper
    from langchain_core.tools import StructuredTool

    monkeypatch.setattr(mcp_filesystem_wrapper, "_skills_enabled", lambda: True)

    captured: dict = {}

    # Real explicit signature so langchain can introspect args_schema cleanly.
    async def fs_inner(path: str, content: str = "") -> str:
        captured["path"] = path
        captured["content"] = content
        return "ok"

    async def crm_inner(email: str) -> str:
        captured["email"] = email
        return "ok"

    fs_tool = StructuredTool.from_function(
        coroutine=fs_inner,
        name="filesystem_write_file",
        description="Mock filesystem write_file",
    )
    crm_tool = StructuredTool.from_function(
        coroutine=crm_inner,
        name="crm_get_contacts",
        description="Mock CRM tool — should not be wrapped",
    )

    wrapped = mcp_filesystem_wrapper.wrap_mcp_filesystem_tools([fs_tool, crm_tool], thread_id="thread-A")
    # CRM tool passes through unchanged (no path translation needed)
    assert wrapped[1] is crm_tool
    # Filesystem tool keeps its public identity but the instance is a fresh wrapper
    assert wrapped[0] is not fs_tool
    assert wrapped[0].name == "filesystem_write_file"
    assert wrapped[0].description == "Mock filesystem write_file"

    asyncio.run(wrapped[0].ainvoke({"path": "out.txt", "content": "hi"}))
    assert captured == {"path": "thread-A/out.txt", "content": "hi"}
