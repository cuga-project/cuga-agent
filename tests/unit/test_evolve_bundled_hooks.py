"""Verify the shipped hook configuration in a fresh process (Evolve has global hook state)."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.unit


def run_verification(config):
    pytest.importorskip("altk_evolve")
    pytest.importorskip("cpex")
    pytest.importorskip("cpex_pii_filter")
    return subprocess.run(
        [sys.executable, str(ROOT / "src/scripts/verify_evolve_hooks.py")],
        env={**os.environ, "EVOLVE_HOOKS_CONFIG": str(config)},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_bundled_hooks_enforce_protections():
    result = run_verification(ROOT / "src/cuga/configurations/evolve/hooks.yaml")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verified offline" in result.stdout


def test_empty_operator_config_is_not_silently_replaced_by_defaults(tmp_path):
    config = tmp_path / "hooks.yaml"
    config.write_text("plugins: []\n")
    result = run_verification(config)
    assert result.returncode != 0
    assert "Required bundled hook is inactive" in result.stderr
