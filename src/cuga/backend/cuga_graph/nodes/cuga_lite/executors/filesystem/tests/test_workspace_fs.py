"""Tests for the consolidated runtime filesystem tools (host backend).

Covers the behavior the old per-executor tools + the MCP wrapper used to
provide: per-thread vs shared workspace layout, the ``/workspace`` virtual
root, ``..`` traversal rejection, the write/read/edit/list/move/search/info
surface, and that download/upload are callable but NOT LLM tools.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import (
    HostWorkspaceBackend,
    WorkspaceFilesystem,
    create_filesystem_tools,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import paths as _paths


def _fs(thread_id: str | None) -> WorkspaceFilesystem:
    return WorkspaceFilesystem(backend=HostWorkspaceBackend(thread_id), thread_id=thread_id)


def _enable_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_paths, "skills_enabled", lambda: True)


def _disable_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_paths, "skills_enabled", lambda: False)


# ─── workspace layout ───────────────────────────────────────────────────────


def test_per_thread_layout_when_skills_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    msg = asyncio.run(_fs("thread-A").write_file("notes.md", "hello"))
    assert "File written" in msg
    on_disk = tmp_path / "cuga_workspace" / "thread-A" / "notes.md"
    assert on_disk.read_text() == "hello"


def test_shared_layout_when_skills_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _disable_skills(monkeypatch)

    asyncio.run(_fs("thread-A").write_file("a.txt", "x"))
    asyncio.run(_fs("thread-B").write_file("b.txt", "y"))
    shared = tmp_path / "cuga_workspace"
    assert (shared / "a.txt").read_text() == "x"
    assert (shared / "b.txt").read_text() == "y"


def test_two_threads_are_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    asyncio.run(_fs("thread-A").write_file("out.txt", "from-A"))
    asyncio.run(_fs("thread-B").write_file("out.txt", "from-B"))
    base = tmp_path / "cuga_workspace"
    assert (base / "thread-A" / "out.txt").read_text() == "from-A"
    assert (base / "thread-B" / "out.txt").read_text() == "from-B"
    assert not (base / "out.txt").exists()


def test_workspace_virtual_root_maps_to_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    asyncio.run(_fs("thread-A").write_file("/workspace/sub/x.txt", "v"))
    assert (tmp_path / "cuga_workspace" / "thread-A" / "sub" / "x.txt").read_text() == "v"
    assert asyncio.run(_fs("thread-A").read_file("/workspace/sub/x.txt")) == "v"


# ─── traversal rejection ────────────────────────────────────────────────────


@pytest.mark.parametrize("evil", ["../escape.txt", "sub/../../thread-B/x.txt", "..\\thread-B\\x.txt"])
def test_traversal_is_rejected(evil: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)

    msg = asyncio.run(_fs("thread-A").write_file(evil, "nope"))
    assert "error" in msg.lower()
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "cuga_workspace" / "escape.txt").exists()


# ─── read / write / list ────────────────────────────────────────────────────


def test_write_read_round_trip_and_slicing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)
    fs = _fs("t")

    asyncio.run(fs.write_file("data.txt", "l1\nl2\nl3\nl4"))
    assert asyncio.run(fs.read_file("data.txt")) == "l1\nl2\nl3\nl4"
    assert asyncio.run(fs.read_file("data.txt", start_line=2, end_line=3)) == "l2\nl3"
    grep = asyncio.run(fs.read_file("data.txt", grep_pattern="l3"))
    assert grep == "3|l3"


def test_list_files_surfaces_writes_not_siblings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)
    (tmp_path / "cuga_workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cuga_workspace" / "sibling.txt").write_text("not mine")

    fs = _fs("thread-A")
    asyncio.run(fs.write_file("agent_output.txt", "from agent"))
    listing = asyncio.run(fs.list_files("."))
    parsed = json.loads(listing)
    names = {e["name"] for e in parsed["entries"]}
    assert "agent_output.txt" in names
    assert "sibling.txt" not in names


# ─── edit / move / search / info ────────────────────────────────────────────


def test_edit_file_unique_match_diff_and_dryrun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)
    fs = _fs("t")
    asyncio.run(fs.write_file("c.py", "x = 1\ny = 2\n"))

    dry = asyncio.run(fs.edit_file("c.py", [{"oldText": "x = 1", "newText": "x = 9"}], dryRun=True))
    assert "Dry run" in dry
    assert asyncio.run(fs.read_file("c.py")) == "x = 1\ny = 2\n"

    applied = asyncio.run(fs.edit_file("c.py", [{"oldText": "x = 1", "newText": "x = 9"}]))
    assert "Changes applied" in applied
    assert "x = 9" in asyncio.run(fs.read_file("c.py"))

    dup = asyncio.run(fs.write_file("d.txt", "a\na\n"))
    assert "File written" in dup
    err = asyncio.run(fs.edit_file("d.txt", [{"oldText": "a", "newText": "b"}]))
    assert "must be unique" in err


def test_move_file_fails_when_destination_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)
    fs = _fs("t")
    asyncio.run(fs.write_file("a.txt", "A"))
    asyncio.run(fs.write_file("b.txt", "B"))

    err = asyncio.run(fs.move_file("a.txt", "b.txt"))
    assert "already exists" in err

    ok = asyncio.run(fs.move_file("a.txt", "c.txt"))
    assert "Moved" in ok
    assert asyncio.run(fs.read_file("c.txt")) == "A"


def test_search_files_and_get_file_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _enable_skills(monkeypatch)
    fs = _fs("t")
    asyncio.run(fs.write_file("pkg/mod.py", "print(1)"))
    asyncio.run(fs.write_file("pkg/data.txt", "x"))

    found = asyncio.run(fs.search_files(".", "**/*.py", None))
    assert "mod.py" in found and "data.txt" not in found

    info = asyncio.run(fs.get_file_info("pkg/mod.py"))
    assert "isFile: True" in info and "size:" in info


# ─── transfer is callable but NOT an LLM tool ───────────────────────────────


def test_download_upload_callable_but_not_structured_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _disable_skills(monkeypatch)
    fs = _fs(None)

    tool_names = {t.name for t in fs.as_structured_tools()}
    assert "download_file" not in tool_names
    assert "upload_file" not in tool_names
    assert tool_names == {
        "read_file",
        "write_file",
        "edit_file",
        "list_files",
        "make_directory",
        "move_file",
        "search_files",
        "get_file_info",
    }

    asyncio.run(fs.write_file("report.txt", "hello"))
    msg = asyncio.run(fs.download_file("report.txt"))
    assert "downloaded successfully" in msg
    assert (tmp_path / "cuga_workspace" / "report.txt").exists()

    external = tmp_path / "incoming.csv"
    external.write_text("a,b")
    up = asyncio.run(fs.upload_file(str(external), "uploaded/incoming.csv"))
    assert "uploaded successfully" in up
    assert asyncio.run(fs.read_file("uploaded/incoming.csv")) == "a,b"


def test_factory_returns_eight_named_tools() -> None:
    names = [t.name for t in create_filesystem_tools("t")]
    assert names == [
        "read_file",
        "write_file",
        "edit_file",
        "list_files",
        "make_directory",
        "move_file",
        "search_files",
        "get_file_info",
    ]
