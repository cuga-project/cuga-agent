"""Verify the shipped hook configuration in a fresh process (Evolve has global hook state)."""

import importlib.util
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
    # Match the CPU-only Linux image on Apple hosts too. READI otherwise picks
    # MPS, whose spaCy tensors cannot cross the hook dispatcher's worker threads.
    bootstrap = "import runpy, sys; "
    if sys.platform == "darwin" and importlib.util.find_spec("spacy") is not None:
        bootstrap += "import spacy; spacy.prefer_gpu = lambda *a, **k: False; spacy.require_cpu(); "
    bootstrap += "runpy.run_path(sys.argv[1], run_name='__main__')"
    return subprocess.run(
        [sys.executable, "-c", bootstrap, str(ROOT / "src/scripts/verify_evolve_hooks.py")],
        env={
            **os.environ,
            "EVOLVE_HOOKS_CONFIG": str(config),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PIP_NO_INDEX": "1",
        },
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.slow
def test_bundled_hooks_enforce_protections():
    pytest.importorskip("risk_assessment")
    pytest.importorskip("en_core_web_trf")
    result = run_verification(ROOT / "src/cuga/configurations/evolve/hooks.yaml")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verified offline" in result.stdout


def test_empty_operator_config_is_not_silently_replaced_by_defaults(tmp_path):
    config = tmp_path / "hooks.yaml"
    config.write_text("plugins: []\n")
    result = run_verification(config)
    assert result.returncode != 0
    assert "Required bundled hook is inactive" in result.stderr
