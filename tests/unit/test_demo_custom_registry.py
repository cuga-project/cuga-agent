"""The reported demo + custom registry command must not seed an unrelated sales agent."""

from pathlib import Path

import pytest

from cuga.backend.server import config_store
from cuga.backend.server.demo_manage_setup import setup_demo_manage_config
from cuga.backend.server.demo_setup_utils.registry_tools import custom_registry_tools

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_store():
    config_store.reset_config_db()
    yield
    config_store.reset_config_db()


def test_custom_cuga_apps_registry_replaces_sales_demo_seed(monkeypatch):
    from cuga.config import settings

    path = (
        Path(__file__).resolve().parents[2]
        / "src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml"
    )
    monkeypatch.setenv("MCP_SERVERS_FILE", str(path))
    monkeypatch.setattr(settings.storage, "preserve_configs_on_startup", "prod")
    monkeypatch.setattr(settings.storage, "mode", "local")
    setup_demo_manage_config("demo", tools=[{"name": "digital_sales"}], filesystem=False)

    config, _ = config_store.run_sync(config_store.load_config(None, "cuga-default"))
    assert config["agent"]["name"] != "Digital Sales Agent"
    assert {tool["name"] for tool in config["tools"]} == {
        "cuga_web",
        "cuga_knowledge",
        "cuga_geo",
        "cuga_finance",
        "cuga_code",
        "cuga_local",
        "cuga_text",
    }
    assert config_store.run_sync(config_store.load_draft("cuga-default")) == config


@pytest.mark.parametrize("value", ["", "none", "None"])
def test_managed_database_mode_keeps_demo_defaults(monkeypatch, value):
    monkeypatch.delenv("CUGA_SUPERVISOR_ROSTER", raising=False)
    monkeypatch.setenv("MCP_SERVERS_FILE", value)
    assert custom_registry_tools() is None


@pytest.mark.parametrize("registry_file", ["", "none"])
def test_roster_without_custom_registry_does_not_add_sales_agent(monkeypatch, registry_file):
    from cuga.config import settings

    monkeypatch.setenv("MCP_SERVERS_FILE", registry_file)
    monkeypatch.setenv("CUGA_SUPERVISOR_ROSTER", "/rosters/default.yaml")
    monkeypatch.setattr(settings.storage, "preserve_configs_on_startup", "prod")
    monkeypatch.setattr(settings.storage, "mode", "local")

    setup_demo_manage_config("demo", tools=[{"name": "digital_sales"}], filesystem=False)

    config, _ = config_store.run_sync(config_store.load_config(None, "cuga-default"))
    assert config["agent"]["name"] != "Digital Sales Agent"
    assert config["tools"] == []


def test_invalid_custom_file_does_not_reset_existing_configs(monkeypatch, tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text("mcpServers: [invalid")
    monkeypatch.setenv("MCP_SERVERS_FILE", str(path))
    existing = {"agent": {"name": "Keep me"}, "tools": []}
    config_store.run_sync(config_store.save_config(existing, "cuga-default"))

    with pytest.raises(ValueError):
        setup_demo_manage_config("demo", filesystem=False)

    assert config_store.run_sync(config_store.load_config(None, "cuga-default"))[0] == existing


def test_valid_custom_registry_preserves_human_edited_published_and_draft(monkeypatch):
    from cuga.config import settings

    path = (
        Path(__file__).resolve().parents[2]
        / "src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml"
    )
    monkeypatch.setenv("MCP_SERVERS_FILE", str(path))
    monkeypatch.setattr(settings.storage, "preserve_configs_on_startup", "any")
    monkeypatch.setattr(settings.storage, "mode", "local")
    published = {"agent": {"name": "Saved custom agent"}, "tools": [{"name": "custom-app"}]}
    draft = {"agent": {"name": "Unsaved custom agent"}, "tools": []}
    config_store.run_sync(config_store.save_config(published, "cuga-default"))
    config_store.run_sync(config_store.save_draft(draft, "cuga-default"))

    setup_demo_manage_config("demo", tools=[{"name": "digital_sales"}], filesystem=False)

    assert config_store.run_sync(config_store.load_config(None, "cuga-default"))[0] == published
    assert config_store.run_sync(config_store.load_draft("cuga-default")) == draft
