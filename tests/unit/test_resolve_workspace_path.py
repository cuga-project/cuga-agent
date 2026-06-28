"""Tests for virtual /workspace path resolution (incl. Windows-safe POSIX mapping)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    assert_resolved_path_under,
    child_path_under,
    ensure_thread_workspace_seeded,
    local_base_dir,
    read_bytes_under,
    remove_file_under,
    resolve_workspace_path,
    write_bytes_under,
)
from cuga.backend.server import workspace_upload as wu


def test_resolve_manifest_virtual_path_under_thread_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    resolved = resolve_workspace_path(
        "/workspace/uploads/.manifest.json",
        thread_id="thread-1",
        operation="read_manifest",
    )
    expected = tmp_path / "cuga_workspace" / "thread-1" / "uploads" / ".manifest.json"
    assert resolved == expected.resolve()


def test_resolve_virtual_path_survives_windows_normpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.path.normpath turns /workspace/... into D:\\workspace\\... on Windows."""
    monkeypatch.chdir(tmp_path)
    real_normpath = os.path.normpath

    def broken_normpath(path: str) -> str:
        if path.replace("\\", "/").startswith("/workspace"):
            return "D:" + path.replace("/", "\\")
        return real_normpath(path)

    monkeypatch.setattr(os.path, "normpath", broken_normpath)
    resolved = resolve_workspace_path(
        "/workspace/uploads/.manifest.json",
        thread_id="abc-123",
        operation="read_manifest",
    )
    expected = tmp_path / "cuga_workspace" / "abc-123" / "uploads" / ".manifest.json"
    assert resolved == expected.resolve()


def test_format_upload_context_empty_manifest_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    real_normpath = os.path.normpath

    def broken_normpath(path: str) -> str:
        if path.replace("\\", "/").startswith("/workspace"):
            return "D:" + path.replace("/", "\\")
        return real_normpath(path)

    monkeypatch.setattr(os.path, "normpath", broken_normpath)
    assert wu.format_upload_context("thread-1") is None


def test_child_path_under_rejects_traversal(tmp_path: Path) -> None:
    base = tmp_path / "root"
    base.mkdir()
    with pytest.raises(ValueError, match="Invalid path segment"):
        child_path_under(base, "..")


def test_assert_resolved_path_under_rejects_escape(tmp_path: Path) -> None:
    base = tmp_path / "root"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Path must stay under"):
        assert_resolved_path_under(outside, base)


def test_write_and_read_bytes_under_thread_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    base = local_base_dir()
    base.mkdir()
    written = write_bytes_under(base, b'{"ok": true}', "thread-1", "uploads", "data.json")
    assert written.is_file()
    assert read_bytes_under(written, base) == b'{"ok": true}'
    remove_file_under(written, base)
    assert not written.exists()


def test_ensure_thread_workspace_seeded_copies_crm_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUGA_THREAD_WORKSPACE_SEED", "crm")
    shared = local_base_dir()
    shared.mkdir(parents=True)
    (shared / "contacts.txt").write_text("a@example.com", encoding="utf-8")
    (shared / "cuga_knowledge.md").write_text("# CUGA\n", encoding="utf-8")

    ensure_thread_workspace_seeded("thread-1")
    seeded = shared / "thread-1" / "contacts.txt"
    assert seeded.is_file()
    assert seeded.read_text(encoding="utf-8") == "a@example.com"
    knowledge = shared / "thread-1" / "cuga_knowledge.md"
    assert knowledge.is_file()
    assert "CUGA" in knowledge.read_text(encoding="utf-8")
