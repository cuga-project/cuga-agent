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
