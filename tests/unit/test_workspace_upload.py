"""Unit tests for thread-scoped workspace uploads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuga.backend.server import workspace_upload as wu


def test_sanitize_upload_filename_accepts_json() -> None:
    assert wu.sanitize_upload_filename("instana_events.json") == "instana_events.json"


def test_sanitize_upload_filename_rejects_hidden() -> None:
    with pytest.raises(ValueError, match="Invalid filename"):
        wu.sanitize_upload_filename(".secret.json")


def test_sanitize_upload_filename_rejects_bad_extension() -> None:
    with pytest.raises(ValueError, match="Unsupported file type"):
        wu.sanitize_upload_filename("data.csv")


def test_sanitize_upload_filename_strips_path() -> None:
    assert wu.sanitize_upload_filename("/tmp/evil/../ok.json") == "ok.json"


def test_sanitize_upload_filename_normalizes_browser_download_name() -> None:
    assert (
        wu.sanitize_upload_filename("servicenow_incidents_2entries (1).json")
        == "servicenow_incidents_2entries_1.json"
    )


def test_merge_manifest_replaces_same_name() -> None:
    manifest = {
        "thread_id": "t1",
        "files": [{"name": "a.json", "path": "./uploads/a.json", "size_bytes": 1}],
    }
    merged = wu._merge_manifest_entry(manifest, thread_id="t1", filename="a.json", size_bytes=99)
    assert len(merged["files"]) == 1
    assert merged["files"][0]["size_bytes"] == 99
    assert merged["files"][0]["path"] == "./uploads/a.json"
    assert "shell_path" not in merged["files"][0]


def test_validate_upload_content_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="Invalid JSON"):
        wu.validate_upload_content(b"{not json", "bad.json")


def test_validate_upload_content_accepts_jsonl() -> None:
    wu.validate_upload_content(b'{"a": 1}\n{"b": 2}\n', "events.jsonl")


def test_unique_upload_name_appends_suffix_on_collision() -> None:
    manifest = {"files": [{"name": "foo.json"}]}
    unique = wu._unique_upload_name("foo.json", manifest)
    assert unique.startswith("foo_")
    assert unique.endswith(".json")
    assert unique != "foo.json"


@pytest.mark.asyncio
async def test_upload_workspace_bytes_host_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wu, "workspace_tree_is_sandbox_backed", lambda: False)

    result = await wu.upload_workspace_bytes("thread-1", "instana.json", b'{"events": []}')
    assert result["path"] == "workspace/uploads/instana.json"
    assert result["sandbox_path"] == "/workspace/uploads/instana.json"

    on_disk = tmp_path / "cuga_workspace" / "thread-1" / "uploads" / "instana.json"
    assert on_disk.read_bytes() == b'{"events": []}'

    manifest_path = tmp_path / "cuga_workspace" / "thread-1" / "uploads" / ".manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["thread_id"] == "thread-1"
    assert manifest["files"][0]["name"] == "instana.json"
    assert manifest["files"][0]["path"] == "./uploads/instana.json"
    assert "shell_path" not in manifest["files"][0]


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wu, "workspace_tree_is_sandbox_backed", lambda: False)
    big = b"x" * (wu.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        await wu.upload_workspace_bytes("t", "big.json", big)


def test_format_upload_context_lists_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    uploads = tmp_path / "cuga_workspace" / "t1" / "uploads"
    uploads.mkdir(parents=True)
    manifest = {
        "thread_id": "t1",
        "files": [{"name": "a.json", "path": "./uploads/a.json", "size_bytes": 2 * 1024 * 1024}],
    }
    (uploads / ".manifest.json").write_text(json.dumps(manifest))

    ctx = wu.format_upload_context("t1")
    assert ctx is not None
    assert "./uploads/a.json" in ctx
    assert "shell_path" not in ctx
    assert "as-is" in ctx


def test_format_upload_context_normalizes_legacy_virtual_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    uploads = tmp_path / "cuga_workspace" / "t1" / "uploads"
    uploads.mkdir(parents=True)
    manifest = {
        "thread_id": "t1",
        "files": [{"name": "a.json", "path": "/workspace/uploads/a.json", "size_bytes": 100}],
    }
    (uploads / ".manifest.json").write_text(json.dumps(manifest))

    ctx = wu.format_upload_context("t1")
    assert ctx is not None
    assert "./uploads/a.json" in ctx


@pytest.mark.asyncio
async def test_delete_thread_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wu, "workspace_tree_is_sandbox_backed", lambda: False)
    uploads = tmp_path / "cuga_workspace" / "t1" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "x.json").write_text("{}")

    await wu.delete_thread_uploads("t1")
    assert not uploads.exists()


def test_resolve_host_workspace_path_thread_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    resolved = wu.resolve_host_workspace_path("workspace/uploads/foo.json", "thread-A")
    assert resolved == (tmp_path / "cuga_workspace" / "thread-A" / "uploads" / "foo.json").resolve()


def test_fetch_host_workspace_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    uploads = tmp_path / "cuga_workspace" / "t1" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "data.json").write_text("{}")

    tree = wu.fetch_host_workspace_tree("t1")
    names = {node["name"] for node in tree}
    assert "uploads" in names
