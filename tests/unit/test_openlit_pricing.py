"""OpenLit airgapped pricing resolution (issue #475)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.observability import openlit_init


@pytest.mark.unit
def test_bundled_pricing_json_exists_and_is_valid():
    path = openlit_init.bundled_openlit_pricing_json()
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "chat" in data


@pytest.mark.unit
def test_resolve_pricing_json_prefers_env(tmp_path, monkeypatch):
    custom = tmp_path / "custom-pricing.json"
    custom.write_text('{"chat": {}}', encoding="utf-8")
    monkeypatch.setenv("OPENLIT_PRICING_JSON", str(custom))

    resolved = openlit_init.resolve_openlit_pricing_json()
    assert resolved == str(custom)


@pytest.mark.unit
def test_resolve_pricing_json_falls_back_to_bundled(monkeypatch):
    monkeypatch.delenv("OPENLIT_PRICING_JSON", raising=False)
    resolved = openlit_init.resolve_openlit_pricing_json(settings_pricing_json="")
    assert Path(resolved) == openlit_init.bundled_openlit_pricing_json()


@pytest.mark.unit
def test_init_openlit_passes_local_pricing_json(monkeypatch):
    monkeypatch.delenv("OPENLIT_PRICING_JSON", raising=False)
    monkeypatch.delenv("LITELLM_LOCAL_MODEL_COST_MAP", raising=False)

    fake_openlit = MagicMock()
    settings = SimpleNamespace(
        observability=SimpleNamespace(openlit=True, pricing_json=""),
        service=SimpleNamespace(tenant_id="", instance_id=""),
    )

    monkeypatch.setattr(openlit_init, "_initialized", False)
    monkeypatch.setattr(openlit_init, "openlit", fake_openlit)

    with patch("cuga.config.settings", settings):
        openlit_init.init_openlit()

    fake_openlit.init.assert_called_once()
    kwargs = fake_openlit.init.call_args.kwargs
    assert kwargs["pricing_json"] == str(openlit_init.bundled_openlit_pricing_json())
    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP", "").lower() in {"1", "true", "yes"}
    assert os.environ.get("OPENLIT_PRICING_JSON") == str(openlit_init.bundled_openlit_pricing_json())
