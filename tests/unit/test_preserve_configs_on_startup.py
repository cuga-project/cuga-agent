from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cuga.backend.server import config_store
from cuga.backend.server.config_store import (
    has_any_config,
    load_draft,
    reset_config_db,
    save_draft,
    should_preserve_existing_configs,
)
from cuga.backend.server.demo_manage_setup import setup_demo_manage_config
from cuga.config import settings as cuga_settings


@pytest.mark.unit
def test_should_preserve_defaults_to_prod_only(monkeypatch):
    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="prod", preserve_configs_on_startup="prod"),
    )
    assert should_preserve_existing_configs() is True

    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="local", preserve_configs_on_startup="prod"),
    )
    assert should_preserve_existing_configs() is False


@pytest.mark.unit
def test_should_preserve_any_and_local(monkeypatch):
    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="local", preserve_configs_on_startup="any"),
    )
    assert should_preserve_existing_configs() is True

    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="local", preserve_configs_on_startup="local"),
    )
    assert should_preserve_existing_configs() is True

    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="prod", preserve_configs_on_startup="local"),
    )
    assert should_preserve_existing_configs() is False


@pytest.mark.unit
def test_should_preserve_invalid_value_falls_back_to_prod(monkeypatch):
    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="prod", preserve_configs_on_startup="nope"),
    )
    assert should_preserve_existing_configs() is True

    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="local", preserve_configs_on_startup="nope"),
    )
    assert should_preserve_existing_configs() is False


@pytest.mark.unit
def test_has_any_config_respects_tenant_and_instance(monkeypatch):
    reset_config_db()
    monkeypatch.setattr(config_store, "_tenant_id", lambda: "tenant-a")
    monkeypatch.setattr(config_store, "_instance_id", lambda: "inst-a")

    assert asyncio.run(has_any_config("cuga-default")) is False
    asyncio.run(save_draft({"agent": {"name": "A"}}, "cuga-default"))
    assert asyncio.run(has_any_config("cuga-default")) is True

    monkeypatch.setattr(config_store, "_tenant_id", lambda: "tenant-b")
    monkeypatch.setattr(config_store, "_instance_id", lambda: "inst-b")
    assert asyncio.run(has_any_config("cuga-default")) is False


@pytest.mark.unit
def test_setup_skips_seed_when_preserving_existing(monkeypatch):
    reset_config_db()
    monkeypatch.setattr(config_store, "_tenant_id", lambda: "")
    monkeypatch.setattr(config_store, "_instance_id", lambda: "")
    asyncio.run(save_draft({"agent": {"name": "Existing"}, "tools": []}, "cuga-default"))

    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="local", preserve_configs_on_startup="any"),
    )

    setup_demo_manage_config("manager", agent_id="cuga-default", filesystem=False)

    draft = asyncio.run(load_draft("cuga-default"))
    assert draft is not None
    assert draft["agent"]["name"] == "Existing"


@pytest.mark.unit
def test_setup_seeds_when_preserve_on_but_empty(monkeypatch):
    reset_config_db()
    monkeypatch.setattr(config_store, "_tenant_id", lambda: "")
    monkeypatch.setattr(config_store, "_instance_id", lambda: "")
    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="local", preserve_configs_on_startup="any"),
    )

    assert asyncio.run(has_any_config("cuga-default")) is False
    setup_demo_manage_config("manager", agent_id="cuga-default", filesystem=False)
    assert asyncio.run(has_any_config("cuga-default")) is True
    draft = asyncio.run(load_draft("cuga-default"))
    assert draft is not None


@pytest.mark.unit
def test_setup_reseeds_when_preserve_gate_off(monkeypatch):
    reset_config_db()
    monkeypatch.setattr(config_store, "_tenant_id", lambda: "")
    monkeypatch.setattr(config_store, "_instance_id", lambda: "")
    asyncio.run(save_draft({"agent": {"name": "Existing"}, "tools": []}, "cuga-default"))

    monkeypatch.setattr(
        cuga_settings,
        "storage",
        SimpleNamespace(mode="local", preserve_configs_on_startup="prod"),
    )

    setup_demo_manage_config("manager", agent_id="cuga-default", filesystem=False)
    draft = asyncio.run(load_draft("cuga-default"))
    assert draft is not None
    assert draft["agent"]["name"] != "Existing"
