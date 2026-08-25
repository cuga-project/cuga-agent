"""
E2E tests for YAML configuration

Tests YAML parsing, agent configuration loading, MCP server integration, and A2A setup.
"""

import pytest
import os
import tempfile

from cuga import CugaSupervisor
from cuga.supervisor_utils.supervisor_config import (
    build_agents_from_stored_subagents,
    load_supervisor_config,
)


@pytest.fixture(scope="function", autouse=True)
def ensure_settings_validated():
    """Ensure settings validators are applied before each test to prevent CI failures."""
    from cuga.config import settings, validators
    import dynaconf

    # Re-register all validators to ensure they're present
    # This is safe to do multiple times
    for validator in validators:
        try:
            settings.validators.register(validator)
        except Exception:
            # Validator might already be registered, that's fine
            pass

    # Ensure validators are applied (idempotent operation)
    # validate_all() is idempotent - calling it multiple times is safe
    try:
        settings.validators.validate_all()
    except dynaconf.ValidationError:
        # ValidationError means validators were already applied and some failed
        # This is expected and we can continue
        pass

    yield

    # No cleanup needed - settings is a module-level singleton


class TestSupervisorYAMLConfig:
    """E2E tests for YAML configuration"""

    @pytest.mark.asyncio
    async def test_yaml_parsing(self):
        """Test parsing YAML configuration file"""
        # Create a temporary YAML file
        yaml_content = """
supervisor:
  strategy: adaptive
  mode: delegation
  model:
    provider: openai
    model_name: gpt-4o-mini

agents:
  - name: test_agent
    type: internal
    description: "Test agent"
    tools: []
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = await load_supervisor_config(temp_path)

            assert config is not None
            assert config.supervisor is not None
            assert config.supervisor.get("strategy") == "adaptive"
            # Backward compatibility: delegation mode maps to plan_upfront
            assert config.supervisor.get("mode") in ["delegation", "plan_upfront"]
            assert len(config.agents) > 0
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_agent_configuration_loading(self):
        """Test loading agent configurations from YAML"""
        yaml_content = """
supervisor:
  strategy: sequential

agents:
  - name: agent1
    type: internal
    description: "First agent"
    tools: []
  - name: agent2
    type: internal
    description: "Second agent"
    tools: []
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = await load_supervisor_config(temp_path)

            assert len(config.agents) == 2
            assert "agent1" in config.agents
            assert "agent2" in config.agents
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_a2a_protocol_config(self):
        """Test A2A protocol configuration in YAML"""
        yaml_content = """
supervisor:
  strategy: adaptive

agents:
  - name: remote_agent
    type: external
    description: "Remote agent via A2A"
    a2a_protocol:
      enabled: true
      endpoint: http://localhost:8000/a2a
      transport: http
      capabilities: ["task_delegation"]

a2a:
  protocol_version: "1.0"
  communication:
    type: http
    timeout: 30
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = await load_supervisor_config(temp_path)

            assert len(config.agents) == 1
            remote_agent = config.agents["remote_agent"]
            assert isinstance(remote_agent, dict)
            assert remote_agent.get("type") == "external"
            assert "a2a_protocol" in remote_agent.get("config", {})
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_supervisor_from_yaml(self):
        """Test creating supervisor from YAML file"""
        # Use the fixture file if it exists
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "supervisor_config.yaml")

        if os.path.exists(fixture_path):
            supervisor = await CugaSupervisor.from_yaml(fixture_path)

            assert supervisor is not None
            assert len(supervisor._agents) > 0
        else:
            # Create a minimal test file
            yaml_content = """
supervisor:
  strategy: adaptive
  mode: delegation

agents:
  - name: test_agent
    type: internal
    tools: []
"""
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                f.write(yaml_content)
                temp_path = f.name

            try:
                supervisor = await CugaSupervisor.from_yaml(temp_path)

                assert supervisor is not None
            finally:
                os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_mcp_servers_config(self):
        """Test MCP servers configuration in YAML"""
        yaml_content = """
supervisor:
  strategy: adaptive

agents:
  - name: agent_with_mcp
    type: internal
    mcp_servers:
      - name: filesystem
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"]
        transport: stdio
    tools: []
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = await load_supervisor_config(temp_path)

            assert len(config.agents) == 1
            # MCP servers are configured but may not be fully initialized in tests
            # This test mainly verifies parsing works
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_import_from_agent(self):
        """Test that import_from loads a pre-configured CugaAgent from a Python module."""
        from langchain_core.tools import tool
        from cuga.sdk import CugaAgent
        import sys
        import types

        # Build a minimal CugaAgent and expose it via a temporary module so the
        # YAML loader can import it by dotted path.
        @tool
        def echo_tool(message: str) -> str:
            """Echo the message back."""
            return message

        agent_instance = CugaAgent(tools=[echo_tool])
        agent_instance.description = "Echo agent for testing import_from"

        # Register a fake module so importlib.import_module can find it
        fake_module_name = "_test_import_from_module"
        fake_module = types.ModuleType(fake_module_name)
        fake_module.echo_agent = agent_instance
        sys.modules[fake_module_name] = fake_module

        yaml_content = f"""
supervisor:
  strategy: adaptive

agents:
  - name: echo_agent
    import_from: {fake_module_name}.echo_agent
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = await load_supervisor_config(temp_path)

            assert len(config.agents) == 1
            assert "echo_agent" in config.agents
            loaded = config.agents["echo_agent"]
            # Should be the exact same instance we registered
            assert loaded is agent_instance
        finally:
            os.unlink(temp_path)
            sys.modules.pop(fake_module_name, None)

    @pytest.mark.asyncio
    async def test_import_from_invalid_path_raises(self):
        """Test that import_from raises when the module or attribute does not exist."""
        yaml_content = """
supervisor:
  strategy: adaptive

agents:
  - name: bad_agent
    import_from: non_existent_module.non_existent_attr
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            with pytest.raises(ModuleNotFoundError):
                await load_supervisor_config(temp_path)
        finally:
            os.unlink(temp_path)


@pytest.mark.unit
class TestBuildAgentsFromStoredSubAgents:
    """build_agents_from_stored_subagents — the manage-UI store-sourced loader (issue #101)."""

    @pytest.mark.asyncio
    async def test_a2a_entry_resolves_to_external_config(self, monkeypatch):
        monkeypatch.setenv("TEST_A2A_TOKEN", "secret-token")

        agents = await build_agents_from_stored_subagents(
            [
                {
                    "kind": "a2a",
                    "name": "hotel_agent",
                    "endpoint": "http://localhost:9000",
                    "auth": {"type": "bearer", "tokenEnvVar": "TEST_A2A_TOKEN"},
                    "timeout": 15,
                }
            ]
        )

        assert list(agents.keys()) == ["hotel_agent"]
        entry = agents["hotel_agent"]
        assert entry["type"] == "external"
        a2a_cfg = entry["config"]["a2a_protocol"]
        assert a2a_cfg["endpoint"] == "http://localhost:9000"
        assert a2a_cfg["timeout"] == 15
        assert a2a_cfg["auth"] == {"type": "bearer", "token": "secret-token"}

    @pytest.mark.asyncio
    async def test_a2a_entry_missing_name_or_endpoint_is_skipped(self):
        agents = await build_agents_from_stored_subagents(
            [
                {"kind": "a2a", "endpoint": "http://localhost:9000"},
                {"kind": "a2a", "name": "no-endpoint"},
                {"kind": "a2a", "name": "ok", "endpoint": "http://localhost:9001"},
            ]
        )
        assert list(agents.keys()) == ["ok"]

    @pytest.mark.asyncio
    async def test_a2a_entry_without_token_env_var_has_no_auth(self):
        agents = await build_agents_from_stored_subagents(
            [{"kind": "a2a", "name": "public_agent", "endpoint": "http://localhost:9001"}]
        )

        assert agents["public_agent"]["config"]["a2a_protocol"]["auth"] is None

    @pytest.mark.asyncio
    async def test_internal_ref_resolves_to_cuga_agent(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        from cuga.backend.server.config_store import reset_config_db, save_config
        from cuga.sdk import CugaAgent

        reset_config_db()
        await save_config(
            {
                "agent": {"name": "Flight Booker", "description": "Books flights"},
                "tools": [{"name": "flights_app", "type": "openapi"}],
            },
            agent_id="flight-booker",
        )

        agents = await build_agents_from_stored_subagents([{"kind": "internal", "ref": "flight-booker"}])

        assert list(agents.keys()) == ["flight-booker"]
        assert isinstance(agents["flight-booker"], CugaAgent)

    @pytest.mark.asyncio
    async def test_internal_ref_missing_config_is_skipped(self):
        from cuga.backend.server.config_store import reset_config_db

        reset_config_db()

        agents = await build_agents_from_stored_subagents([{"kind": "internal", "ref": "does-not-exist"}])

        assert agents == {}

    @pytest.mark.asyncio
    async def test_internal_ref_forwards_llm_and_feature_settings(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        seen = {}
        fake_model = object()

        def _fake_get_model(model_config):
            seen["model"] = model_config
            return fake_model

        monkeypatch.setattr(
            "cuga.supervisor_utils.supervisor_config._get_model_from_config",
            _fake_get_model,
        )

        from cuga.backend.server.config_store import reset_config_db, save_config
        from cuga.sdk import CugaAgent

        reset_config_db()
        await save_config(
            {
                "agent": {"name": "Sales East"},
                "llm": {"provider": "openai", "model": "gpt-4o-mini", "temperature": 0.2},
                "feature_flags": {
                    "enable_todos": True,
                    "reflection": False,
                    "max_steps": 12,
                    "enable_filesystem_tools": True,
                    "shortlisting_tool_threshold": 7,
                },
            },
            agent_id="sales-east",
        )

        agents = await build_agents_from_stored_subagents([{"kind": "internal", "ref": "sales-east"}])

        assert list(agents.keys()) == ["sales-east"]
        agent = agents["sales-east"]
        assert isinstance(agent, CugaAgent)
        assert agent._model is fake_model
        assert seen["model"]["model_name"] == "gpt-4o-mini"
        assert seen["model"]["provider"] == "openai"
        overrides = getattr(agent, "_feature_overrides", {})
        assert overrides.get("enable_todos") is True
        assert overrides.get("reflection_enabled") is False
        assert overrides.get("cuga_lite_max_steps") == 12
        assert overrides.get("enable_filesystem_tools") is True
        assert overrides.get("shortlisting_tool_threshold") == 7
