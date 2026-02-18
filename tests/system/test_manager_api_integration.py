"""
System tests for CUGA Manager API.

Tests the complete workflow of:
1. Creating agent configs with tools in draft mode
2. Running tasks in draft mode with tool isolation
3. Publishing draft as new version
4. Running tasks in production mode
5. Testing draft vs production tool isolation
6. Selecting partial tools from connected apps

All tests start by cleaning up .db files in DBS_DIR, then starting `cuga start manager`.
"""

import glob
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
from loguru import logger

# Import config to get DBS_DIR
from cuga.config import DBS_DIR

# Test configuration
MANAGER_BASE_URL = "http://localhost:7860"
REGISTRY_BASE_URL = "http://localhost:8001"
MANAGE_API_URL = f"{MANAGER_BASE_URL}/api/manage"
STREAM_API_URL = f"{MANAGER_BASE_URL}/stream"
TEST_AGENT_ID = "cuga-default"
MANAGER_STARTUP_TIMEOUT = 60  # seconds
MANAGER_HEALTH_CHECK_INTERVAL = 1  # seconds


def validate_response_keywords(response_text: str, keywords: str, description: str = "response") -> bool:
    """
    Validate that response text contains expected keywords with AND/OR logic.
    
    Args:
        response_text: The text to search in (case-insensitive)
        keywords: Keywords string with |or| and |and| operators
                 Examples:
                 - "sample.txt" - single keyword
                 - "sample.txt |or| sample" - either keyword
                 - "sample.txt |and| test_workspace" - both keywords required
                 - "sample.txt |or| sample |and| workspace" - (sample.txt OR sample) AND workspace
        description: Description of what's being validated (for error messages)
    
    Returns:
        True if validation passes
        
    Raises:
        AssertionError: If validation fails with detailed message
    """
    response_lower = response_text.lower()
    
    # Split by |and| first (higher precedence)
    and_parts = keywords.split("|and|")
    
    for and_part in and_parts:
        and_part = and_part.strip()
        
        # Check if this part has |or| conditions
        if "|or|" in and_part:
            or_parts = [p.strip() for p in and_part.split("|or|")]
            # At least one OR condition must match
            if not any(keyword.lower() in response_lower for keyword in or_parts):
                raise AssertionError(
                    f"{description} should contain at least one of: {or_parts}\n"
                    f"Response preview: {response_text[:200]}..."
                )
        else:
            # Single keyword must match
            if and_part.lower() not in response_lower:
                raise AssertionError(
                    f"{description} should contain: '{and_part}'\n"
                    f"Response preview: {response_text[:200]}..."
                )
    
    return True


class ManagerProcess:
    """Context manager for starting and stopping the CUGA manager process."""

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None

    def __enter__(self):
        """Start the manager process."""
        logger.info("Starting CUGA manager...")
        
        # Set environment variable for manager mode
        env = os.environ.copy()
        env["CUGA_MANAGER_MODE"] = "true"
        
        self.process = subprocess.Popen(
            ["cuga", "start", "manager"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        
        # Wait for manager to be ready
        self._wait_for_manager()
        
        logger.info("✅ CUGA manager started successfully")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop the manager process."""
        if self.process:
            logger.info("Stopping CUGA manager...")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Manager didn't stop gracefully, killing...")
                self.process.kill()
                self.process.wait()
            logger.info("✅ CUGA manager stopped")

    def _wait_for_manager(self):
        """Wait for the manager to be ready by checking health endpoint."""
        start_time = time.time()
        while time.time() - start_time < MANAGER_STARTUP_TIMEOUT:
            try:
                response = httpx.get(f"{MANAGER_BASE_URL}/", timeout=2.0)
                if response.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            time.sleep(MANAGER_HEALTH_CHECK_INTERVAL)
        
        raise TimeoutError(
            f"Manager did not start within {MANAGER_STARTUP_TIMEOUT} seconds"
        )


@pytest.fixture(scope="module", autouse=True)
def cleanup_and_start_manager():
    """
    Fixture that runs before all tests in this module.
    Cleans up database files, creates test workspace, and starts the manager.
    
    NOTE: Currently requires manager restart after saving first config because:
    - Agents are built once at startup with empty config
    - MCP servers only start if config exists at startup
    - Saving config later triggers registry reload but doesn't rebuild agents
    - This is a known limitation that should be fixed in manage_routes.py
    """
    # Clean up all .db files in DBS_DIR
    logger.info(f"Cleaning up database files in {DBS_DIR}...")
    db_files = glob.glob(os.path.join(DBS_DIR, "*.db"))
    for db_file in db_files:
        try:
            os.remove(db_file)
            logger.info(f"Removed {db_file}")
        except Exception as e:
            logger.warning(f"Failed to remove {db_file}: {e}")
    
    # Create test workspace directory for filesystem tool
    test_workspace = Path("./test_workspace")
    logger.info(f"Creating test workspace directory: {test_workspace.absolute()}")
    test_workspace.mkdir(exist_ok=True)
    
    # Create a sample file in the workspace for testing
    sample_file = test_workspace / "sample.txt"
    sample_file.write_text("This is a test file for CUGA system tests.")
    logger.info(f"Created sample file: {sample_file}")
    
    # Start manager
    with ManagerProcess():
        yield
    
    # Cleanup after all tests
    logger.info("All tests completed, cleaning up...")
    
    # Clean up test workspace
    try:
        import shutil
        if test_workspace.exists():
            shutil.rmtree(test_workspace)
            logger.info(f"Removed test workspace: {test_workspace}")
    except Exception as e:
        logger.warning(f"Failed to remove test workspace: {e}")


@pytest.fixture
def http_client():
    """Provide an HTTP client for tests with extended timeout."""
    # Use longer timeout for manager operations that may trigger registry reloads
    with httpx.Client(timeout=120.0) as client:
        yield client


@pytest.fixture
def test_agent_config():
    """Provide a test agent configuration with filesystem tool."""
    return {
        "tools": [
            {
                "name": "filesystem",
                "type": "mcp",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "./test_workspace"],
                "transport": "stdio",
                "description": "File system operations for testing",
            }
        ],
        "llm": {
            "model": "gpt-4",
            "temperature": 0.7,
        },
    }


@pytest.fixture
def test_agent_config_with_partial_tools():
    """Provide a test agent configuration with partial tool selection."""
    return {
        "tools": [
            {
                "name": "filesystem",
                "type": "mcp",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "./test_workspace"],
                "transport": "stdio",
                "description": "File system operations",
                "include": ["read_file", "write_file"],  # Only include specific tools
            },
            {
                "name": "github",
                "type": "mcp",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "transport": "stdio",
                "description": "GitHub operations",
                "include": ["list_repos"],  # Only include specific tools
            }
        ],
        "llm": {
            "model": "gpt-4",
            "temperature": 0.7,
        },
    }


class TestManagerAPIWorkflow:
    """Test the complete manager API workflow."""

    def test_01_save_draft_config(self, http_client: httpx.Client, test_agent_config: Dict[str, Any]):
        """Test saving a draft configuration with filesystem tool."""
        logger.info("Test 1: Saving draft configuration...")
        logger.info(f"Sending POST to {MANAGE_API_URL}/config/draft")
        logger.info(f"Agent ID: {TEST_AGENT_ID}")
        logger.info(f"Config: {json.dumps(test_agent_config, indent=2)}")
        
        try:
            response = http_client.post(
                f"{MANAGE_API_URL}/config/draft",
                params={"agent_id": TEST_AGENT_ID},
                json={"config": test_agent_config},
            )
            
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response body: {response.text}")
            
            assert response.status_code == 200, f"Failed to save draft: {response.text}"
            data = response.json()
            assert data["status"] == "success"
            assert data["version"] == "draft"
            assert data["agent_id"] == TEST_AGENT_ID
            
            logger.info("✅ Draft configuration saved successfully")
            
            # Draft agent graph is rebuilt after config save (see manage_routes.py)
            # This starts MCP servers, but they need a moment to initialize
            logger.info("Waiting 5 seconds for MCP servers to fully initialize...")
            time.sleep(5)
            logger.info("✅ Draft configuration saved and MCP servers should be ready")
            
        except httpx.ReadTimeout as e:
            logger.error(f"Request timed out after 120 seconds: {e}")
            logger.error("This may indicate the manager is not responding or the registry reload is taking too long")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {type(e).__name__}: {e}")
            raise

    def test_02_get_draft_config(self, http_client: httpx.Client):
        """Test retrieving the draft configuration."""
        logger.info("Test 2: Retrieving draft configuration...")
        logger.info(f"Sending GET to {MANAGE_API_URL}/config?agent_id={TEST_AGENT_ID}&draft=1")
        
        try:
            response = http_client.get(
                f"{MANAGE_API_URL}/config",
                params={"agent_id": TEST_AGENT_ID, "draft": "1"},
            )
            
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response body: {response.text[:500]}...")  # Log first 500 chars
            
            assert response.status_code == 200, f"Failed to get draft: {response.text}"
            data = response.json()
            assert data["version"] == "draft"
            assert data["agent_id"] == TEST_AGENT_ID
            assert "tools" in data["config"]
            assert len(data["config"]["tools"]) > 0
            assert data["config"]["tools"][0]["name"] == "filesystem"
            
            logger.info("✅ Draft configuration retrieved successfully")
        except Exception as e:
            logger.error(f"Error retrieving draft config: {type(e).__name__}: {e}")
            raise

    def test_03_run_task_in_draft_mode(self, http_client: httpx.Client):
        """Test running a task in draft mode."""
        logger.info("Test 3: Running task in draft mode...")
        # Create a simple task that uses the filesystem tool
        task_request = {
            "query": "List the files in the test_workspace directory"
        }
        
        # Add header to use draft mode
        headers = {"X-Use-Draft": "true"}
        
        response = http_client.post(
            STREAM_API_URL,
            json=task_request,
            headers=headers,
        )
        
        assert response.status_code == 200, f"Failed to run task in draft: {response.text}"
        
        # For streaming response, collect the stream and validate content
        response_text = response.text
        logger.info(f"Response preview: {response_text[:500]}...")
        
        # Validate that the response contains expected keywords
        # The agent should mention the sample file we created
        validate_response_keywords(
            response_text,
            "sample.txt |or| sample",
            "Draft mode response"
        )
        
        logger.info("✅ Task executed in draft mode successfully with expected content")

    def test_04_publish_draft_as_version(self, http_client: httpx.Client, test_agent_config: Dict[str, Any]):
        """Test publishing draft as a new version."""
        logger.info("Test 4: Publishing draft as new version...")
        
        response = http_client.post(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": TEST_AGENT_ID},
            json={"config": test_agent_config},
        )
        
        assert response.status_code == 200, f"Failed to publish: {response.text}"
        data = response.json()
        assert data["status"] == "success"
        assert data["version"] == "1"  # First published version
        assert data["agent_id"] == TEST_AGENT_ID
        
        logger.info(f"✅ Draft published as version {data['version']}")

    def test_05_get_published_config(self, http_client: httpx.Client):
        """Test retrieving the published configuration."""
        logger.info("Test 5: Retrieving published configuration...")
        
        response = http_client.get(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": TEST_AGENT_ID, "version": "1"},
        )
        
        assert response.status_code == 200, f"Failed to get published config: {response.text}"
        data = response.json()
        assert data["version"] == "1"
        assert data["agent_id"] == TEST_AGENT_ID
        assert "tools" in data["config"]
        
        logger.info("✅ Published configuration retrieved successfully")

    def test_06_run_task_in_production_mode(self, http_client: httpx.Client):
        """Test running a task in production mode (published version)."""
        logger.info("Test 6: Running task in production mode...")
        
        task_request = {
            "query": "List the files in the test_workspace directory"
        }
        
        # No X-Use-Draft header means production mode
        response = http_client.post(
            STREAM_API_URL,
            json=task_request,
        )
        
        assert response.status_code == 200, f"Failed to run task in production: {response.text}"
        
        # Validate response contains expected content
        response_text = response.text
        logger.info(f"Response preview: {response_text[:500]}...")
        
        # The agent should mention the sample file
        validate_response_keywords(
            response_text,
            "sample.txt |or| sample",
            "Production mode response"
        )
        
        logger.info("✅ Task executed in production mode successfully with expected content")

    def test_07_draft_vs_production_isolation(
        self, http_client: httpx.Client, test_agent_config: Dict[str, Any]
    ):
        """Test that draft and production modes are properly isolated."""
        logger.info("Test 7: Testing draft vs production isolation...")
        
        # Modify draft config with a different tool
        modified_config = test_agent_config.copy()
        modified_config["tools"].append({
            "name": "github",
            "type": "mcp",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "transport": "stdio",
            "description": "GitHub operations (draft only)",
        })
        
        # Save modified draft
        response = http_client.post(
            f"{MANAGE_API_URL}/config/draft",
            params={"agent_id": TEST_AGENT_ID},
            json={"config": modified_config},
        )
        assert response.status_code == 200
        
        # Get draft config - should have 2 tools
        draft_response = http_client.get(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": TEST_AGENT_ID, "draft": "1"},
        )
        assert draft_response.status_code == 200
        draft_data = draft_response.json()
        assert len(draft_data["config"]["tools"]) == 2
        
        # Get production config - should still have 1 tool
        prod_response = http_client.get(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": TEST_AGENT_ID, "version": "1"},
        )
        assert prod_response.status_code == 200
        prod_data = prod_response.json()
        assert len(prod_data["config"]["tools"]) == 1
        
        logger.info("✅ Draft and production modes are properly isolated")

    def test_08_partial_tool_selection(
        self, http_client: httpx.Client, test_agent_config_with_partial_tools: Dict[str, Any]
    ):
        """Test selecting partial tools from connected apps and verify tool isolation."""
        logger.info("Test 8: Testing partial tool selection...")
        
        # Save config with partial tool selection in draft mode
        response = http_client.post(
            f"{MANAGE_API_URL}/config/draft",
            params={"agent_id": f"{TEST_AGENT_ID}-partial"},
            json={"config": test_agent_config_with_partial_tools},
        )
        assert response.status_code == 200
        logger.info("Saved partial tool config to draft")
        
        # Retrieve and verify the config
        get_response = http_client.get(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": f"{TEST_AGENT_ID}-partial", "draft": "1"},
        )
        assert get_response.status_code == 200
        data = get_response.json()
        
        # Verify tools have include lists
        tools = data["config"]["tools"]
        assert len(tools) == 2
        
        filesystem_tool = next(t for t in tools if t["name"] == "filesystem")
        assert "include" in filesystem_tool
        assert set(filesystem_tool["include"]) == {"read_file", "write_file"}
        
        github_tool = next(t for t in tools if t["name"] == "github")
        assert "include" in github_tool
        assert github_tool["include"] == ["list_repos"]
        
        logger.info("✅ Config verification passed")
        
        # Test tool isolation in DRAFT mode - ask agent to list available tools
        logger.info("Testing tool availability in DRAFT mode...")
        task_request = {"query": "Show me all tool names you have available"}
        headers = {"X-Use-Draft": "true"}
        
        draft_response = http_client.post(
            STREAM_API_URL,
            json=task_request,
            headers=headers,
        )
        assert draft_response.status_code == 200
        draft_text = draft_response.text
        logger.info(f"Draft mode tools response preview: {draft_text[:500]}...")
        
        # In draft mode, should have access to the partial tools
        validate_response_keywords(
            draft_text,
            "read_file |or| write_file |or| list_repos",
            "Draft mode should mention included tools"
        )
        
        logger.info("✅ Draft mode has access to partial tools")
        
        # Publish the partial tool config
        logger.info("Publishing partial tool config...")
        publish_response = http_client.post(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": f"{TEST_AGENT_ID}-partial"},
            json={"config": test_agent_config_with_partial_tools},
        )
        assert publish_response.status_code == 200
        logger.info("✅ Published partial tool config")
        
        # Test tool isolation in PRODUCTION mode - ask agent to list available tools
        logger.info("Testing tool availability in PRODUCTION mode...")
        prod_response = http_client.post(
            STREAM_API_URL,
            json=task_request,
            # No X-Use-Draft header = production mode
        )
        assert prod_response.status_code == 200
        prod_text = prod_response.text
        logger.info(f"Production mode tools response preview: {prod_text[:500]}...")
        
        # In production mode, should also have access to the published partial tools
        validate_response_keywords(
            prod_text,
            "read_file |or| write_file |or| list_repos",
            "Production mode should mention included tools"
        )
        
        logger.info("✅ Production mode has access to published partial tools")
        logger.info("✅ Partial tool selection and isolation working correctly")

    def test_09_config_history(self, http_client: httpx.Client):
        """Test retrieving configuration history."""
        logger.info("Test 9: Testing configuration history...")
        
        response = http_client.get(f"{MANAGE_API_URL}/config/history")
        
        assert response.status_code == 200, f"Failed to get history: {response.text}"
        data = response.json()
        assert "versions" in data
        assert len(data["versions"]) > 0
        
        # Verify version 1 exists
        versions = [v["version"] for v in data["versions"]]
        assert "1" in versions
        
        logger.info(f"✅ Configuration history retrieved: {len(data['versions'])} versions")

    def test_10_multiple_versions(self, http_client: httpx.Client, test_agent_config: Dict[str, Any]):
        """Test creating multiple versions."""
        logger.info("Test 10: Testing multiple versions...")
        
        # Publish version 2
        modified_config = test_agent_config.copy()
        modified_config["llm"]["temperature"] = 0.5
        
        response = http_client.post(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": TEST_AGENT_ID},
            json={"config": modified_config},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "2"
        
        # Verify we can get both versions
        v1_response = http_client.get(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": TEST_AGENT_ID, "version": "1"},
        )
        assert v1_response.status_code == 200
        v1_data = v1_response.json()
        assert v1_data["config"]["llm"]["temperature"] == 0.7
        
        v2_response = http_client.get(
            f"{MANAGE_API_URL}/config",
            params={"agent_id": TEST_AGENT_ID, "version": "2"},
        )
        assert v2_response.status_code == 200
        v2_data = v2_response.json()
        assert v2_data["config"]["llm"]["temperature"] == 0.5
        
        logger.info("✅ Multiple versions working correctly")


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])