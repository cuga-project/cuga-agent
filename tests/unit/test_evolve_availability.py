"""Optional memory deployment discovery must not follow runtime preferences."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.evolve import integration

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,url,installed,registry,registered,expected",
    [
        ("auto", "http://127.0.0.1:8201/sse", False, False, False, False),
        ("auto", "http://127.0.0.1:8201/sse", True, False, False, True),
        ("direct", "http://127.0.0.1:8201/sse", False, False, False, True),
        ("auto", "https://memory.internal/sse", False, False, False, True),
        ("direct", "", True, False, False, False),
        ("registry", "http://127.0.0.1:8201/sse", True, True, False, False),
        ("registry", "", False, True, True, True),
        ("registry", "", True, False, False, False),
    ],
)
async def test_configuration_independent_of_enabled(
    monkeypatch, mode, url, installed, registry, registered, expected
):
    import importlib.util

    config = SimpleNamespace(
        evolve=SimpleNamespace(mode=mode, url=url, app_name="evolve", enabled=False),
        advanced_features=SimpleNamespace(registry=registry),
    )
    monkeypatch.setattr(integration, "settings", config)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if installed else None)
    monkeypatch.setattr(
        integration.EvolveIntegration, "_registry_has_app", AsyncMock(return_value=registered)
    )
    for enabled in (False, True):
        config.evolve.enabled = enabled
        assert await integration.EvolveIntegration.is_configured() is expected


@pytest.mark.asyncio
async def test_registry_discovery_failure_does_not_break_configuration(monkeypatch):
    monkeypatch.setattr(
        integration,
        "settings",
        SimpleNamespace(
            evolve=SimpleNamespace(mode="registry", app_name="evolve"),
            advanced_features=SimpleNamespace(registry=True),
        ),
    )
    monkeypatch.setattr(
        integration.EvolveIntegration, "_registry_has_app", AsyncMock(side_effect=ConnectionError)
    )
    assert await integration.EvolveIntegration.is_configured() is False
