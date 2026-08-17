import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_profiling_extra_skips_memray_on_windows() -> None:
    parsed = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    extras = parsed["project"]["optional-dependencies"]["profiling"]
    memray_reqs = [dep for dep in extras if dep.startswith("memray")]
    assert memray_reqs, "profiling extra must depend on memray"
    for dep in memray_reqs:
        assert "sys_platform" in dep and "win32" in dep and "!=" in dep, (
            f"memray must be skipped on Windows (no wheels); got {dep!r}"
        )
