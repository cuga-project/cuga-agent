"""
Test for agent-specific registry loading from database.
This test reproduces the issue where a service is loaded from the database
but then cannot be found when querying for its APIs.
"""

import pytest
from unittest.mock import patch, AsyncMock
from cuga.backend.tools_env.registry.config.config_loader import load_service_configs_from_db
from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.registry.api_registry import ApiRegistry
from cuga.backend.utils.consts import ServiceType


@pytest.fixture
def mock_db_tools():
    """Mock tools returned from database"""
    return [
        {
            "name": "filesystem",
            "type": "mcp",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "description": "Filesystem MCP server",
        }
    ]


@pytest.mark.asyncio
async def test_agent_registry_loading_from_db(mock_db_tools):
    """
    Test that services loaded from database are properly accessible.

    This reproduces the bug where:
    1. Service is loaded from DB: "Loaded 1 services from database for agent 'cuga-draft'"
    2. Service appears in available apps: "Available apps: ['filesystem']"
    3. But querying for the service fails: "Application 'filesystem' not found in registry"
    """
    agent_id = "cuga-draft"

    # Mock the database call (get_tools_from_agent_config lives in managed_mcp, now async)
    with patch(
        'cuga.backend.server.managed_mcp.get_tools_from_agent_config',
        new_callable=AsyncMock,
        return_value=mock_db_tools,
    ):
        # Load services from database
        services = await load_service_configs_from_db(agent_id)

        # Verify service was loaded
        assert len(services) == 1
        assert "filesystem" in services
        assert services["filesystem"].type == ServiceType.MCP_SERVER

        # Create MCPManager with loaded services
        manager = MCPManager(config=services)

        # Verify the service is in schema_urls (the source of truth)
        assert "filesystem" in manager.schema_urls

        # Create ApiRegistry
        registry = ApiRegistry(client=manager)

        # Mock the MCP server initialization to avoid actual connection
        with patch.object(manager, '_initialize_fastmcp_client', new_callable=AsyncMock) as mock_init:
            # Simulate successful initialization by populating the required data structures
            async def mock_initialize(mcp_servers):
                for name, config in mcp_servers:
                    # Simulate what _initialize_fastmcp_client does
                    manager.tools_by_server[name] = [
                        {
                            "type": "function",
                            "function": {
                                "name": f"{name}_read_file",
                                "description": "Read a file",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ]
                    manager.mcp_clients[name] = config.command
                    manager.auth_config[name] = config.auth

            mock_init.side_effect = mock_initialize

            # Start servers (this should initialize MCP servers)
            await registry.start_servers()

        # Now test that we can get the list of applications
        apps = await registry.show_applications()
        app_names = [app.name for app in apps]

        print(f"Available apps: {app_names}")
        assert "filesystem" in app_names, f"filesystem not in {app_names}"

        # Test that we can get app names using the new method
        manager_app_names = manager.get_app_names()
        print(f"Manager app names: {manager_app_names}")
        assert "filesystem" in manager_app_names

        # This is where the bug occurs - trying to get APIs for the app
        try:
            apis = await registry.show_apis_for_app("filesystem")
            print(f"Successfully retrieved {len(apis)} APIs for filesystem")
            assert isinstance(apis, dict), "APIs should be returned as a dict"
        except Exception as e:
            pytest.fail(f"Failed to get APIs for filesystem: {e}")


@pytest.mark.asyncio
async def test_mcp_server_detection_in_get_apis():
    """
    Test that get_apis_for_application correctly detects MCP servers.

    The bug is in the detection logic at line 506-509 of mcp_manager.py:
    is_mcp_server = app_name in self.mcp_clients or (
        app_name in self.schema_urls and
        self.schema_urls[app_name].type == ServiceType.MCP_SERVER
    )

    This should work even if mcp_clients is not yet populated (during initialization).
    """
    from cuga.backend.tools_env.registry.config.config_loader import ServiceConfig

    # Create a service config for an MCP server
    config = ServiceConfig(
        name="filesystem",
        type=ServiceType.MCP_SERVER,
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        description="Filesystem MCP server",
    )

    services = {"filesystem": config}
    manager = MCPManager(config=services)

    # At this point, mcp_clients is empty but schema_urls should have the service
    assert "filesystem" in manager.schema_urls
    assert manager.schema_urls["filesystem"].type == ServiceType.MCP_SERVER

    # The detection logic should work
    app_name = "filesystem"
    is_mcp_server = app_name in manager.mcp_clients or (
        app_name in manager.schema_urls and manager.schema_urls[app_name].type == ServiceType.MCP_SERVER
    )

    assert is_mcp_server, "MCP server should be detected even before initialization"

    # Now test get_apis_for_application
    # It should return empty dict if no tools loaded yet, not raise KeyError
    result = manager.get_apis_for_application(app_name)
    assert isinstance(result, dict), "Should return dict, not raise exception"
    assert len(result) == 0, "Should return empty dict when no tools loaded yet"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
