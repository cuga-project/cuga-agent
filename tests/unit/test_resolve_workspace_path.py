"""Tests for virtual /workspace path resolution (incl. Windows-safe POSIX mapping)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    resolve_workspace_path,
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
