"""Unit tests for scripts/deploy-image.sh."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_deploy_image_fails_when_docker_push_fails(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, code in (("ibmcloud", 0), ("docker", 1)):
        stub = bin_dir / name
        stub.write_text(f"#!/bin/sh\nexit {code}\n")
        stub.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts/deploy-image.sh"),
            "url",
            "region",
            "group",
            "reg",
            "ns",
            "img",
            "tag",
        ],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Done pushing image to registry" not in result.stdout
