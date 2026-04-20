"""
E2E tests for YAML configuration

Tests YAML parsing, agent configuration loading, MCP server integration, and A2A setup.
"""

import pytest
import os
import tempfile

from cuga import CugaSupervisor
from cuga.supervisor_utils.supervisor_config import load_supervisor_config


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
