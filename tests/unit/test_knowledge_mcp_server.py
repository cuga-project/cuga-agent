"""Security and path-resolution tests for the knowledge MCP server."""

from __future__ import annotations

from pathlib import Path

import pytest

from cuga.backend.knowledge.mcp_server import _resolve_ingest_file_path


def test_resolve_ingest_file_path_rejects_host_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute paths outside the workspace"):
        _resolve_ingest_file_path("/etc/passwd", thread_id="")


def test_resolve_ingest_file_path_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="traversal"):
        _resolve_ingest_file_path("../../etc/passwd", thread_id="")


def test_resolve_ingest_file_path_accepts_workspace_relative_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "cuga_workspace" / "notes.txt"
    doc.parent.mkdir(parents=True)
    doc.write_text("hello", encoding="utf-8")

    resolved = _resolve_ingest_file_path("notes.txt", thread_id="")
    assert resolved == doc.resolve()


def test_resolve_ingest_file_path_accepts_virtual_workspace_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "cuga_workspace" / "thread-1" / "uploads" / "report.pdf"
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"%PDF-1.4")

    resolved = _resolve_ingest_file_path(
        "/workspace/uploads/report.pdf",
        thread_id="thread-1",
    )
    assert resolved == doc.resolve()


def test_resolve_ingest_file_path_rejects_missing_workspace_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cuga_workspace").mkdir()
    with pytest.raises(FileNotFoundError, match="File not found"):
        _resolve_ingest_file_path("/workspace/missing.txt", thread_id="")
