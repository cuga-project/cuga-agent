"""
Test script for agent tools API.
Run this after starting the server to verify the implementation.
"""

import requests
import json

BASE_URL = "http://localhost:8000/api/manage"
AGENT_ID = "test-agent"


def test_save_single_tool():
    """Test saving a single tool."""
    print("\n1. Testing save single tool...")
    tool = {
        "name": "filesystem",
        "type": "mcp",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"],
        "transport": "stdio",
        "description": "File system operations",
    }

    response = requests.post(f"{BASE_URL}/agents/{AGENT_ID}/tools", json=tool)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    print("✅ Save single tool passed")


def test_get_all_tools():
    """Test getting all tools for an agent."""
    print("\n2. Testing get all tools...")
    response = requests.get(f"{BASE_URL}/agents/{AGENT_ID}/tools")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Response: {json.dumps(data, indent=2)}")
    assert response.status_code == 200
    assert data["agent_id"] == AGENT_ID
    assert len(data["tools"]) > 0
    print("✅ Get all tools passed")


def test_save_batch_tools():
    """Test saving multiple tools at once."""
    print("\n3. Testing save batch tools...")
    tools = {
        "tools": [
            {
                "name": "github",
                "type": "mcp",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "transport": "stdio",
                "description": "GitHub operations",
            },
            {
                "name": "slack",
                "type": "mcp",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-slack"],
                "transport": "stdio",
                "description": "Slack operations",
            },
        ]
    }

    response = requests.post(f"{BASE_URL}/agents/{AGENT_ID}/tools/batch", json=tools)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    assert response.status_code == 200
    assert response.json()["count"] == 2
    print("✅ Save batch tools passed")


def test_list_agents():
    """Test listing all agents with tools."""
    print("\n4. Testing list agents...")
    response = requests.get(f"{BASE_URL}/agents")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Response: {json.dumps(data, indent=2)}")
    assert response.status_code == 200
    assert len(data["agents"]) > 0
    print("✅ List agents passed")


def test_get_registry_yaml():
    """Test getting registry YAML format."""
    print("\n5. Testing get registry YAML...")
    response = requests.get(f"{BASE_URL}/agents/{AGENT_ID}/tools/registry-yaml")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Response: {json.dumps(data, indent=2)}")
    assert response.status_code == 200
    assert "registry_yaml" in data
    assert "mcpServers" in data["registry_yaml"]
    print("✅ Get registry YAML passed")


def test_export_to_yaml():
    """Test exporting tools to YAML file."""
    print("\n6. Testing export to YAML...")
    response = requests.post(f"{BASE_URL}/agents/{AGENT_ID}/tools/export-yaml")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Response: {json.dumps(data, indent=2)}")
    assert response.status_code == 200
    assert data["status"] == "success"
    print("✅ Export to YAML passed")


def test_delete_single_tool():
    """Test deleting a single tool."""
    print("\n7. Testing delete single tool...")
    response = requests.delete(f"{BASE_URL}/agents/{AGENT_ID}/tools/slack")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    print("✅ Delete single tool passed")


def test_delete_all_tools():
    """Test deleting all tools for an agent."""
    print("\n8. Testing delete all tools...")
    response = requests.delete(f"{BASE_URL}/agents/{AGENT_ID}/tools")
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Response: {json.dumps(data, indent=2)}")
    assert response.status_code == 200
    assert data["status"] == "success"
    print("✅ Delete all tools passed")


def run_all_tests():
    """Run all tests in sequence."""
    print("=" * 60)
    print("Starting Agent Tools API Tests")
    print("=" * 60)

    try:
        test_save_single_tool()
        test_get_all_tools()
        test_save_batch_tools()
        test_list_agents()
        test_get_registry_yaml()
        test_export_to_yaml()
        test_delete_single_tool()
        test_delete_all_tools()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        raise
    except requests.exceptions.ConnectionError:
        print("\n❌ Could not connect to server. Make sure it's running on http://localhost:8000")
        raise
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        raise


if __name__ == "__main__":
    run_all_tests()
