#!/usr/bin/env python3
"""
End-to-End tests for OpenAPI authentication with direct server integration.
Tests the full flow: config loading -> registry server -> auth_server.py
"""

import asyncio
import os
import pytest
import pytest_asyncio
import httpx
import subprocess
import psutil
from pathlib import Path

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
        app_name: Name of the application (e.g., "auth_test_header")
        keywords: List of keywords that should be in the function name (e.g., ["items", "header"])
        method: HTTP method to filter by (e.g., "POST")

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


class TestAuthenticationE2E:
    """End-to-End tests for OpenAPI authentication"""

    @pytest_asyncio.fixture(scope="class")
    async def auth_server(self):
        """Start the auth test server"""
        server_port = 8002
        server_process = None

        try:
            kill_process_on_port(server_port)
            await asyncio.sleep(1)

            test_dir = Path(__file__).parent
            server_script = test_dir / "auth_server.py"

            server_process = subprocess.Popen(
                ['uv', 'run', 'python', str(server_script)],
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            await asyncio.sleep(2)
            try:
                await wait_for_http_ok(
                    f"http://127.0.0.1:{server_port}/health",
                    timeout_seconds=120.0,
                )
            except RuntimeError:
                server_process.terminate()
                try:
                    _, err_b = server_process.communicate(timeout=10)
                    err = (err_b or b"").decode(errors="replace")
                except Exception:
                    err = "(could not read stderr)"
                raise RuntimeError(f"Auth test server failed to start. stderr:\n{err}") from None
            print(f"✅ Auth test server started on port {server_port}")

            yield f"http://localhost:{server_port}"

        finally:
            print("Cleaning up auth test server...")
            if server_process:
                try:
                    server_process.terminate()
                    server_process.wait(timeout=5)
                except (subprocess.TimeoutExpired, ProcessLookupError):
                    if server_process.poll() is None:
                        server_process.kill()
                        server_process.wait()
                except Exception as e:
                    print(f"Error terminating server: {e}")

            kill_process_on_port(server_port)
            await asyncio.sleep(1)

    @pytest_asyncio.fixture(scope="class")
    async def registry_server(self, auth_server):
        """Start API Registry server with auth test configuration"""
        server_port = get_registry_port()
        server_process = None

        try:
            kill_process_on_port(server_port)
            await asyncio.sleep(1)

            test_dir = Path(__file__).parent
            config_path = test_dir / "mcp_servers_auth_test.yaml"

            registry_script = os.path.join(
                PACKAGE_ROOT, 'backend', 'tools_env', 'registry', 'registry', 'api_registry_server.py'
            )

            os.environ["MCP_SERVERS_FILE"] = str(config_path)

            server_process = subprocess.Popen(
                ['uv', 'run', 'python', registry_script],
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            await asyncio.sleep(2)
            try:
                await wait_for_http_ok(
                    f"http://127.0.0.1:{server_port}/",
                    timeout_seconds=120.0,
                )
            except RuntimeError:
                server_process.terminate()
                try:
                    _, err_b = server_process.communicate(timeout=10)
                    err = (err_b or b"").decode(errors="replace")
                except Exception:
                    err = "(could not read stderr)"
                raise RuntimeError(f"Registry server failed to start. stderr:\n{err}") from None
            print(f"✅ Registry server started on port {server_port}")

            yield f"http://127.0.0.1:{server_port}"

        finally:
            print("Cleaning up registry server...")
            if server_process:
                try:
                    server_process.terminate()
                    server_process.wait(timeout=5)
                except (subprocess.TimeoutExpired, ProcessLookupError):
                    if server_process.poll() is None:
                        server_process.kill()
                        server_process.wait()
                except Exception as e:
                    print(f"Error terminating registry: {e}")

            kill_process_on_port(server_port)
            await asyncio.sleep(1)

    @pytest.mark.asyncio
    async def test_registry_loads_auth_config(self, registry_server):
        """Test that registry server loads authentication configurations"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{registry_server}/applications")
            assert response.status_code == 200

            data = response.json()

            if isinstance(data, list):
                app_names = [app.get('name', 'unknown') for app in data]
            else:
                app_names = list(data.keys())

            assert "auth_test_header" in app_names
            assert "auth_test_bearer" in app_names
            assert "auth_test_api_key" in app_names
            assert "auth_test_basic" in app_names
            assert "auth_test_query" in app_names

    @pytest.mark.asyncio
    async def test_header_auth_e2e(self, registry_server):
        """Test header authentication end-to-end via registry server"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_server, "auth_test_header", ["items", "header"]
            )
            assert function_name is not None

            payload = {
                "app_name": "auth_test_header",
                "function_name": function_name,
                "args": {},
            }

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            assert response.status_code == 200

            data = response.json()
            assert "items" in data
            assert data["auth_method"] == "header"
            assert isinstance(data["items"], list)
            assert len(data["items"]) == 2

    @pytest.mark.asyncio
    async def test_bearer_auth_e2e(self, registry_server):
        """Test bearer token authentication end-to-end via registry server"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_server, "auth_test_bearer", ["items", "bearer"]
            )
            assert function_name is not None

            payload = {
                "app_name": "auth_test_bearer",
                "function_name": function_name,
                "args": {},
            }

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            assert response.status_code == 200

            data = response.json()
            assert "items" in data
            assert data["auth_method"] == "bearer"
            assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_api_key_query_auth_e2e(self, registry_server):
        """Test API key query parameter authentication end-to-end via registry server"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_server, "auth_test_api_key", ["items", "api", "key"]
            )
            assert function_name is not None

            payload = {
                "app_name": "auth_test_api_key",
                "function_name": function_name,
                "args": {},
            }

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            assert response.status_code == 200

            data = response.json()
            assert "items" in data
            assert data["auth_method"] == "api-key"
            assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_basic_auth_e2e(self, registry_server):
        """Test basic authentication end-to-end via registry server"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_server, "auth_test_basic", ["items", "basic"]
            )
            assert function_name is not None

            payload = {
                "app_name": "auth_test_basic",
                "function_name": function_name,
                "args": {},
            }

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            assert response.status_code == 200

            data = response.json()
            assert "items" in data
            assert data["auth_method"] == "basic"
            assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_custom_query_auth_e2e(self, registry_server):
        """Test custom query parameter authentication end-to-end via registry server"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_server, "auth_test_query", ["items", "custom", "query"]
            )
            assert function_name is not None

            payload = {
                "app_name": "auth_test_query",
                "function_name": function_name,
                "args": {},
            }

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            assert response.status_code == 200

            data = response.json()
            assert "items" in data
            assert data["auth_method"] == "query"
            assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_create_item_with_auth(self, registry_server):
        """Test creating an item with authentication"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try to find create function, fallback to POST method
            function_name = await find_function_name(
                client, registry_server, "auth_test_header", ["create"], method="POST"
            )
            if not function_name:
                function_name = await find_function_name(
                    client, registry_server, "auth_test_header", method="POST"
                )
            assert function_name is not None

            payload = {
                "app_name": "auth_test_header",
                "function_name": function_name,
                "args": {"id": 99, "name": "Test Item", "description": "Created via auth test"},
            }

            response = await client.post(f"{registry_server}/functions/call", json=payload)
            assert response.status_code == 200

            data = response.json()
            assert "item" in data
            assert data["item"]["id"] == 99
            assert data["item"]["name"] == "Test Item"
            assert data["auth_method"] == "header"
            assert data["message"] == "Item created successfully"


async def run_auth_e2e_tests():
    """Run E2E authentication tests standalone"""
    print("🔐 Testing OpenAPI Authentication E2E")
    print("=" * 60)

    auth_server_port = 8002
    registry_server_port = 8001
    auth_process = None
    registry_process = None

    try:
        print("🧹 Cleaning up any existing servers...")
        kill_process_on_port(auth_server_port)
        kill_process_on_port(registry_server_port)
        await asyncio.sleep(1)

        test_dir = Path(__file__).parent
        auth_script = test_dir / "auth_server.py"
        config_path = test_dir / "mcp_servers_auth_test.yaml"

        print("\n🚀 Starting auth test server...")
        auth_process = subprocess.Popen(
            ['uv', 'run', 'python', str(auth_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        await asyncio.sleep(3)

        async with httpx.AsyncClient() as client:
            for i in range(10):
                try:
                    response = await client.get(f"http://localhost:{auth_server_port}/health")
                    if response.status_code == 200:
                        print("✅ Auth test server is running!")
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            else:
                raise Exception("❌ Auth test server failed to start")

        print("\n🚀 Starting registry server...")
        from cuga.config import PACKAGE_ROOT

        registry_script = os.path.join(
            PACKAGE_ROOT, 'backend', 'tools_env', 'registry', 'registry', 'api_registry_server.py'
        )
        os.environ["MCP_SERVERS_FILE"] = str(config_path)

        registry_process = subprocess.Popen(
            ['uv', 'run', 'python', registry_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        await asyncio.sleep(3)

        registry_url = f"http://127.0.0.1:{registry_server_port}"

        async with httpx.AsyncClient() as client:
            for i in range(10):
                try:
                    response = await client.get(f"{registry_url}/")
                    if response.status_code == 200:
                        print("✅ Registry server is running!")
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            else:
                raise Exception("❌ Registry server failed to start")

        print("\n📡 Test 1: Verify auth configurations loaded")
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{registry_url}/applications")
            assert response.status_code == 200
            data = response.json()
            if isinstance(data, list):
                app_names = [app.get('name', 'unknown') for app in data]
            else:
                app_names = list(data.keys())
            print(f"✅ Found {len(app_names)} auth-enabled apps")

        print("\n📋 Test 1b: List APIs for auth_test_header")
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{registry_url}/applications/auth_test_header/apis")
            assert response.status_code == 200
            apis = response.json()
            print(f"   Found {len(apis)} APIs:")
            for api_name in list(apis.keys())[:5]:
                print(f"     - {api_name}")

        print("\n🔑 Test 2: Header Authentication")
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_url, "auth_test_header", ["items", "header"]
            )

            payload = {
                "app_name": "auth_test_header",
                "function_name": function_name,
                "args": {},
            }
            response = await client.post(f"{registry_url}/functions/call", json=payload)
            print(f"   Response status: {response.status_code}")
            print(f"   Response body: {response.text}")
            assert response.status_code == 200
            data = response.json()
            print(f"✅ Header auth successful! Got {len(data['items'])} items")

        print("\n🎫 Test 3: Bearer Token Authentication")
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_url, "auth_test_bearer", ["items", "bearer"]
            )

            payload = {
                "app_name": "auth_test_bearer",
                "function_name": function_name,
                "args": {},
            }
            response = await client.post(f"{registry_url}/functions/call", json=payload)
            assert response.status_code == 200
            data = response.json()
            print(f"✅ Bearer auth successful! Got {len(data['items'])} items")

        print("\n🔐 Test 4: API Key Query Parameter")
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_url, "auth_test_api_key", ["items", "api", "key"]
            )

            payload = {
                "app_name": "auth_test_api_key",
                "function_name": function_name,
                "args": {},
            }
            response = await client.post(f"{registry_url}/functions/call", json=payload)
            assert response.status_code == 200
            data = response.json()
            print(f"✅ API key auth successful! Got {len(data['items'])} items")

        print("\n🔒 Test 5: Basic Authentication")
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_url, "auth_test_basic", ["items", "basic"]
            )

            payload = {
                "app_name": "auth_test_basic",
                "function_name": function_name,
                "args": {},
            }
            response = await client.post(f"{registry_url}/functions/call", json=payload)
            assert response.status_code == 200
            data = response.json()
            print(f"✅ Basic auth successful! Got {len(data['items'])} items")

        print("\n🎯 Test 6: Custom Query Parameter")
        async with httpx.AsyncClient(timeout=30.0) as client:
            function_name = await find_function_name(
                client, registry_url, "auth_test_query", ["items", "custom", "query"]
            )

            payload = {
                "app_name": "auth_test_query",
                "function_name": function_name,
                "args": {},
            }
            response = await client.post(f"{registry_url}/functions/call", json=payload)
            assert response.status_code == 200
            data = response.json()
            print(f"✅ Query param auth successful! Got {len(data['items'])} items")

        print("\n🎉 All authentication E2E tests passed!")

    except Exception as e:
        print(f"❌ E2E test failed: {e}")
        raise
    finally:
        print("\n🧹 Cleaning up...")

        if auth_process:
            try:
                auth_process.terminate()
                auth_process.wait(timeout=5)
                print("✅ Auth server terminated")
            except Exception:
                if auth_process.poll() is None:
                    auth_process.kill()
                    auth_process.wait()

        if registry_process:
            try:
                registry_process.terminate()
                registry_process.wait(timeout=5)
                print("✅ Registry server terminated")
            except Exception:
                if registry_process.poll() is None:
                    registry_process.kill()
                    registry_process.wait()

        kill_process_on_port(auth_server_port)
        kill_process_on_port(registry_server_port)
        await asyncio.sleep(1)

        print("✅ Cleanup completed")


if __name__ == "__main__":
    asyncio.run(run_auth_e2e_tests())
