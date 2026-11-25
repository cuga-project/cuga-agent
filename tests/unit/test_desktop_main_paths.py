"""Tests verifying personal home directory paths were removed from loader, docs, and fixtures."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TARGET_FILES = [
    _REPO_ROOT / "src/frontend_workspaces/frontend/electron_loader/desktop_main.cjs",
    _REPO_ROOT / "src/frontend_workspaces/frontend/electron_loader/uv_paths.cjs",
    _REPO_ROOT / "src/cuga/sdk_core/tests/conversation_messages.json",
    _REPO_ROOT / "src/cuga/backend/tools_env/registry/README.md",
    _REPO_ROOT / "src/cuga/backend/tools_env/registry/QUICKSTART.md",
]


def test_no_hardcoded_users_paths_in_target_files() -> None:
    for path in _TARGET_FILES:
        content = path.read_text(encoding="utf-8")
        assert "/Users/" not in content, f"Hardcoded /Users/ path remains in {path}"


def test_conversation_messages_json_is_valid() -> None:
    json_path = _REPO_ROOT / "src/cuga/sdk_core/tests/conversation_messages.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) > 0


def test_electron_loader_uv_paths_unit_tests() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node executable not available on PATH")
    test_file = _REPO_ROOT / "src/frontend_workspaces/frontend/electron_loader/uv_paths.test.cjs"
    result = subprocess.run([node, "--test", str(test_file)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"node --test failed:\n{result.stdout}\n{result.stderr}"
