"""Unit tests for the optional embedded Evolve process."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _load_supervisor():
    path = Path(__file__).resolve().parents[2] / "scripts/embedded-evolve-supervisor.py"
    spec = importlib.util.spec_from_file_location("embedded_evolve_supervisor", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evolve_namespace_is_the_service_instance_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNACONF_SERVICE__INSTANCE_ID", "service-instance-1")
    monkeypatch.setenv("EVOLVE_NAMESPACE_ID", "stale-namespace")

    environment = _load_supervisor().evolve_environment()

    assert environment["EVOLVE_NAMESPACE_ID"] == "service-instance-1"


def test_embedded_evolve_rejects_missing_service_instance_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import cuga.config

    monkeypatch.setattr(cuga.config, "get_service_instance_id", lambda: "")

    with pytest.raises(RuntimeError, match="DYNACONF_SERVICE__INSTANCE_ID is required"):
        _load_supervisor().evolve_environment()
