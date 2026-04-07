#!/usr/bin/env python3
"""
End-to-End tests for API Registry Server
Tests the full API Registry server including HTTP endpoints
"""

import asyncio
import os
import pytest
import pytest_asyncio
import httpx
import tempfile
import subprocess
import psutil

from cuga.backend.tools_env.registry.tests.e2e_helpers import (
    REPO_ROOT,
    get_registry_port,
    wait_for_http_ok,
)
from cuga.config import PACKAGE_ROOT


def kill_process_on_port(port):
    """Kill any process running on the specified port"""
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                # Get network connections for this process
                connections = proc.net_connections()
                for conn in connections:
                    if conn.laddr.port == port:
                        print(f"Killing process {proc.info['pid']} ({proc.info['name']}) on port {port}")
                        proc.kill()
                        proc.wait(timeout=5)
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception as e:
        print(f"Error killing process on port {port}: {e}")
    return False


async def find_function_name(client, registry_server, app_name, keywords=None, method=None):
    """
    Helper function to find the correct function name for an application.

    Args:
        client: httpx.AsyncClient instance
        registry_server: Base URL of the registry server
        app_name: Name of the application (e.g., "digital_sales")
        keywords: List of keywords that should be in the function name (e.g., ["accounts", "my"])
        method: HTTP method to filter by (e.g., "GET")

    Returns:
        str: The function name, or None if not found
    """
    apis_response = await client.get(f"{registry_server}/applications/{app_name}/apis")
    if apis_response.status_code != 200:
        return None

    apis = apis_response.json()
    if not apis:
        return None

    app_prefix = f"{app_name}_"
    function_name = None

    # First pass: look for exact match with all keywords
    if keywords:
        for api_name in apis.keys():
            if api_name.startswith(app_prefix):
                api_lower = api_name.lower()
                if all(keyword.lower() in api_lower for keyword in keywords):
                    function_name = api_name
                    break

    # Second pass: if method specified, look for matching method
    if not function_name and method:
        for api_name, api_info in apis.items():
            if api_name.startswith(app_prefix):
                if isinstance(api_info, dict) and api_info.get('method', '').upper() == method.upper():
                    function_name = api_name
                    break

    # Third pass: any function with prefix and at least one keyword
    if not function_name and keywords:
        for api_name in apis.keys():
            if api_name.startswith(app_prefix):
                api_lower = api_name.lower()
                if any(keyword.lower() in api_lower for keyword in keywords):
                    function_name = api_name
                    break

    # Fallback: any function with the prefix
    if not function_name:
        for api_name in apis.keys():
            if api_name.startswith(app_prefix):
                function_name = api_name
                break

    # Last resort: first API
    if not function_name:
        function_name = list(apis.keys())[0]

    return function_name


# Test configurations
LEGACY_CONFIG = """# Legacy OpenAPI services
services:
  - digital_sales:
      url: https://digitalsales.19pc1vtv090u.us-east.codeengine.appdomain.cloud/openapi.json
      description: Digital Sales API for testing
"""

MCP_CONFIG = """# MCP server configuration
mcpServers:
  digital_sales_mcp:
    url: "http://127.0.0.1:8000/sse"
    description: FastMCP example server for Digital Sales API integration (SSE-based)
    type: mcp_server
"""


@pytest_asyncio.fixture(scope="module")
async def registry_server():
    """Start API Registry server for testing"""
    server_port = get_registry_port()
    server_process = None
    config_path = None

    try:
        # Kill any existing process on the port
        kill_process_on_port(server_port)
        await asyncio.sleep(1)

        # Create temporary config file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(LEGACY_CONFIG)
            config_path = f.name

        # Set environment variable
        os.environ["MCP_SERVERS_FILE"] = config_path

        registry_script = os.path.join(
            PACKAGE_ROOT, 'backend', 'tools_env', 'registry', 'registry', 'api_registry_server.py'
        )

        # Start server in background (cwd=repo root so `uv run` resolves the project)
        server_process = subprocess.Popen(
            ['uv', 'run', 'python', registry_script],
            cwd=str(REPO_ROOT),
        )

        await asyncio.sleep(2)
        await wait_for_http_ok(f"http://127.0.0.1:{server_port}/", timeout_seconds=120.0)

        yield f"http://127.0.0.1:{server_port}"

    finally:
        # Cleanup - try multiple methods to ensure server is stopped
        print("Cleaning up registry server...")

        # Method 1: Terminate the subprocess if it exists
        if server_process:
            try:
                server_process.terminate()
                server_process.wait(timeout=5)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                if server_process.poll() is None:  # Process still running
                    server_process.kill()
                    server_process.wait()
            except Exception as e:
                print(f"Error terminating server process: {e}")

        # Method 2: Kill any remaining process on the port
        kill_process_on_port(server_port)

        # Method 3: Wait a moment to ensure cleanup
        await asyncio.sleep(1)

        # Remove config file
        if config_path and os.path.exists(config_path):
            try:
                os.remove(config_path)
            except Exception as e:
                print(f"Error removing config file: {e}")


class TestAPIRegistryE2E:
    """End-to-End tests for API Registry Server"""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, registry_server):
        """Test root endpoint"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{registry_server}/")
            assert response.status_code == 200
            data = response.json()
            assert "message" in data

    @pytest.mark.asyncio
    async def test_list_applications(self, registry_server):
        """Test /applications endpoint"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{registry_server}/applications")
            assert response.status_code == 200

            data = response.json()

            # API registry returns list of AppDefinition objects
            if isinstance(data, list):
                assert len(data) > 0
                app_names = [app.get('name', 'unknown') for app in data]
                assert "digital_sales" in app_names
            else:
                assert isinstance(data, dict)
                assert len(data) > 0
                assert "digital_sales" in data

    @pytest.mark.asyncio
    async def test_list_apis_for_application(self, registry_server):
        """Test /applications/{app_name}/apis endpoint"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{registry_server}/applications/digital_sales/apis")
            assert response.status_code == 200

            data = response.json()

            # API returns a dictionary of API definitions
            assert isinstance(data, dict)
            assert len(data) == 4

            # Verify API structure - data is a dict with API names as keys
            for api_name, api_info in data.items():
                assert isinstance(api_info, dict)
                assert "api_name" in api_info
                assert "description" in api_info
                assert "app_name" in api_info
                assert api_info["app_name"] == "digital_sales"

    @pytest.mark.asyncio
    async def test_list_all_apis(self, registry_server):
        """Test /apis endpoint"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{registry_server}/apis")
            assert response.status_code == 200

            data = response.json()

            # API returns a nested dictionary: {app_name: {api_name: api_info}}
            assert isinstance(data, dict)
            assert "digital_sales" in data

            digital_sales_apis = data["digital_sales"]
            assert isinstance(digital_sales_apis, dict)
            assert len(digital_sales_apis) >= 4  # At least 4 APIs from digital_sales

            # Verify structure of APIs
            for api_name, api_info in digital_sales_apis.items():
                assert isinstance(api_info, dict)
                assert "api_name" in api_info
                assert "description" in api_info
                assert "app_name" in api_info

    @pytest.mark.asyncio
    async def test_call_function(self, registry_server):
        """Test /functions/call endpoint"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Find function name dynamically - look for accounts-related function
            function_name = await find_function_name(
                client, registry_server, "digital_sales", ["accounts", "my"], method="GET"
            )
            if not function_name:
                # Fallback: any GET function with accounts
                function_name = await find_function_name(
                    client, registry_server, "digital_sales", ["accounts"], method="GET"
                )
            assert function_name is not None, "Could not find accounts function"

            payload = {
                "app_name": "digital_sales",
                "function_name": function_name,
                "args": {},
            }

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            assert response.status_code == 200

            data = response.json()
            assert isinstance(data, dict)
            assert "accounts" in data
            assert "coverage_id" in data
            assert "client_status" in data

            # Verify accounts structure
            accounts = data["accounts"]
            assert isinstance(accounts, list)
            assert len(accounts) > 0

            # Check first account structure
            account = accounts[0]
            assert "name" in account
            assert "state" in account
            assert "revenue" in account

    @pytest.mark.asyncio
    async def test_call_function_with_params(self, registry_server):
        """Test calling function with parameters"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Find function name dynamically - look for third_party_accounts function
            function_name = await find_function_name(
                client, registry_server, "digital_sales", ["third", "party", "accounts"], method="GET"
            )
            if not function_name:
                # Fallback: any GET function with accounts that might accept parameters
                function_name = await find_function_name(
                    client, registry_server, "digital_sales", ["accounts"], method="GET"
                )
            assert function_name is not None, "Could not find third party accounts function"

            payload = {
                "app_name": "digital_sales",
                "function_name": function_name,
                "args": {
                    "campaign_name": "Tech Transformation",
                },
            }

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            assert response.status_code == 200

            data = response.json()
            assert isinstance(data, dict)
            assert "accounts" in data

    @pytest.mark.asyncio
    async def test_invalid_function_call(self, registry_server):
        """Test calling non-existent function"""
        async with httpx.AsyncClient() as client:
            payload = {"app_name": "digital_sales", "function_name": "non_existent_function", "args": {}}

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            # Should return error (404 or 500)
            assert response.status_code in [404, 500]

    @pytest.mark.asyncio
    async def test_invalid_app_name(self, registry_server):
        """Test calling function on non-existent app"""
        async with httpx.AsyncClient() as client:
            payload = {"app_name": "non_existent_app", "function_name": "some_function", "args": {}}

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            # Should return error (404 or 500)
            assert response.status_code in [404, 500]


async def run_e2e_tests():
    """Run E2E tests standalone"""
    print("🧪 Testing API Registry Server E2E")
    print("=" * 60)

    server_port = 8001
    server_process = None
    config_path = None

    try:
        # Kill any existing process on the port
        print("🧹 Cleaning up any existing server on port 8001...")
        kill_process_on_port(server_port)
        await asyncio.sleep(1)

        # Create temporary config file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(LEGACY_CONFIG)
            config_path = f.name

        # Set environment variable
        os.environ["MCP_SERVERS_FILE"] = config_path

        print(f"✅ Created temporary config: {config_path}")

        # Start server
        print("\n🚀 Starting API Registry Server...")
        server_process = subprocess.Popen(
            [
                'uv',
                'run',
                'python',
                os.path.join(
                    PACKAGE_ROOT, 'backend', 'tools_env', 'registry', 'registry', 'api_registry_server.py'
                ),
            ],
            cwd=None,
        )
        # Wait for server to start
        print("⏳ Waiting for server to start...")
        await asyncio.sleep(3)

        base_url = "http://127.0.0.1:8001"

        # Check if server is running
        async with httpx.AsyncClient() as client:
            for i in range(10):  # Try for 10 seconds
                try:
                    response = await client.get(f"{base_url}/")
                    if response.status_code == 200:
                        print("✅ Server is running!")
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            else:
                raise Exception("❌ Server failed to start")

        # Test 1: Root endpoint
        print("\n📡 Test 1: Root Endpoint")
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/")
            assert response.status_code == 200
            data = response.json()
            print(f"✅ Root response: {data['message']}")

        # Test 2: List applications
        print("\n📱 Test 2: List Applications")
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/applications")
            assert response.status_code == 200
            data = response.json()

            # API registry returns list of AppDefinition objects
            if isinstance(data, list):
                app_names = [app.get('name', 'unknown') for app in data]
                print(f"✅ Found applications: {app_names}")
            else:
                print(f"✅ Found applications: {list(data.keys())}")

        # Test 3: List APIs
        print("\n🔍 Test 3: List APIs for digital_sales")
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/applications/digital_sales/apis")
            assert response.status_code == 200
            data = response.json()
            print(f"✅ Found {len(data)} APIs")

            # Show first few APIs
            if isinstance(data, list):
                api_list = data[:3]
            else:
                # If it's a dict, get the values
                api_list = list(data.values())[:3] if isinstance(data, dict) else [data]

            for i, api in enumerate(api_list, 1):
                if isinstance(api, dict) and 'function' in api:
                    func = api['function']
                    print(f"   {i}. {func['name']}: {func['description'][:60]}...")
                else:
                    print(f"   {i}. {api}")

        # Test 4: Call function
        print("\n📞 Test 4: Call Function")
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Find function name dynamically
            function_name = await find_function_name(
                client, base_url, "digital_sales", ["accounts", "my"], method="GET"
            )
            if not function_name:
                function_name = await find_function_name(
                    client, base_url, "digital_sales", ["accounts"], method="GET"
                )

            assert function_name is not None, "Could not find accounts function"

            payload = {
                "app_name": "digital_sales",
                "function_name": function_name,
                "args": {},
            }

            response = await client.post(f"{base_url}/functions/call", json=payload)
            assert response.status_code == 200
            data = response.json()

            print("✅ Function call successful!")
            print(f"   Response keys: {list(data.keys())}")
            if 'accounts' in data:
                print(f"   Found {len(data['accounts'])} accounts")

        print("\n🎉 E2E tests completed successfully!")

    except Exception as e:
        print(f"❌ E2E test failed: {e}")
        raise
    finally:
        # Cleanup - try multiple methods to ensure server is stopped
        print("\n🧹 Cleaning up...")

        # Method 1: Terminate the subprocess if it exists
        if server_process:
            try:
                server_process.terminate()
                server_process.wait(timeout=5)
                print("✅ Server process terminated")
            except (subprocess.TimeoutExpired, ProcessLookupError):
                if server_process.poll() is None:  # Process still running
                    server_process.kill()
                    server_process.wait()
                    print("✅ Server process killed")
            except Exception as e:
                print(f"Error terminating server process: {e}")

        # Method 2: Kill any remaining process on the port
        if kill_process_on_port(server_port):
            print("✅ Killed remaining process on port")

        # Method 3: Wait a moment to ensure cleanup
        await asyncio.sleep(1)

        # Remove config file
        if config_path and os.path.exists(config_path):
            try:
                os.remove(config_path)
                print("✅ Removed temporary config file")
            except Exception as e:
                print(f"Error removing config file: {e}")

        print("✅ Cleanup completed")


if __name__ == "__main__":
    asyncio.run(run_e2e_tests())
