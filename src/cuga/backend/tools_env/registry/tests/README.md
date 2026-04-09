# Tools Environment Registry Tests

This directory contains comprehensive tests for the Tools Environment Registry system, including legacy OpenAPI integration, MCP server support, and end-to-end API registry server testing.

## Test Structure

```
tests/
├── __init__.py                     # Package initialization
├── README.md                       # This file
├── run_all_tests.py               # Main test runner
├── test_legacy_openapi.py         # Legacy OpenAPI service tests
├── test_mcp_server.py             # MCP server integration tests
├── test_mixed_configuration.py    # Mixed config support tests
└── test_e2e_api_registry.py       # End-to-end API registry server tests
```

## Test Suites

### 1. Legacy OpenAPI Integration (`test_legacy_openapi.py`)
Tests the legacy OpenAPI service integration:
- ✅ Loading and listing applications
- ✅ Listing APIs with proper OpenAPI transformation
- ✅ Calling functions with and without parameters
- ✅ Response parsing and validation

### 2. MCP Server Integration (`test_mcp_server.py`)
Tests the FastMCP server integration:
- ✅ FastMCP client initialization
- ✅ Tool loading with parameter flattening
- ✅ Proper tool prefixing (server_toolname format)
- ✅ Function calling via SSE-based MCP servers

### 3. Mixed Configuration Support (`test_mixed_configuration.py`)
Tests both legacy and MCP servers in the same configuration:
- ✅ Loading both service types simultaneously
- ✅ Proper service isolation and prefixing
- ✅ Calling functions from both service types
- ✅ Configuration format validation

### 4. E2E API Registry Server (`test_e2e_api_registry.py`)
End-to-end tests against the actual API registry server:
- ✅ HTTP endpoint testing (`/applications`, `/apis`, `/functions/call`)
- ✅ Server lifecycle management
- ✅ Function calling via REST API
- ✅ Error handling and validation

## Running Tests

### Run All Tests
```bash
cd ./src/cuga/backend/tools_env/registry/tests
uv run python run_all_tests.py
```

### Run Individual Test Suites
```bash
# Legacy OpenAPI tests
uv run python test_legacy_openapi.py

# MCP server tests  
uv run python test_mcp_server.py

# Mixed configuration tests
uv run python test_mixed_configuration.py

# E2E API registry tests
uv run python test_e2e_api_registry.py
```

### Run with Pytest
```bash
# Run all pytest tests
pytest cuga/backend/tools_env/registry/tests/

# Run specific test file
pytest cuga/backend/tools_env/registry/tests/test_legacy_openapi.py

# Run with verbose output
pytest -v cuga/backend/tools_env/registry/tests/
```

## Prerequisites

### For Legacy OpenAPI Tests
- Internet connection (accesses external Digital Sales API)
- No additional setup required

### For MCP Server Tests
- FastMCP example server running on `http://127.0.0.1:8000/sse`
- Start the server:
  ```bash
  cd ./docs/examples/cuga_with_runtime_tools
  uv run --project ../../../ python fast_mcp_example.py &
  ```

### For E2E Tests
- API registry server will be started automatically
- Port 8001 should be available
- All dependencies installed via `uv`

## Test Configuration

Tests use temporary configuration files to avoid interfering with the main system:

### Legacy Config Example
```yaml
services:
  - digital_sales:
      url: https://digitalsales.19pc1vtv090u.us-east.codeengine.appdomain.cloud/openapi.json
      description: Digital Sales API for testing
```

### MCP Config Example
```yaml
mcpServers:
  digital_sales_mcp:
    url: "http://127.0.0.1:8000/sse"
    description: FastMCP example server
    type: mcp_server
```

### Mixed Config Example
```yaml
# Legacy services
services:
  - digital_sales_legacy:
      url: https://digitalsales.19pc1vtv090u.us-east.codeengine.appdomain.cloud/openapi.json
      description: Legacy Digital Sales API

# MCP servers
mcpServers:
  digital_sales_mcp:
    url: "http://127.0.0.1:8000/sse"
    description: FastMCP example server
    type: mcp_server
```

## Expected Results

### Successful Test Run
```
🚀 Registry Test Suite
Running comprehensive tests for the Tools Environment Registry

================================================================================
🧪 RUNNING: Legacy OpenAPI Integration
================================================================================
✅ Legacy OpenAPI Integration PASSED

================================================================================
🧪 RUNNING: MCP Server Integration  
================================================================================
✅ MCP Server Integration PASSED

================================================================================
🧪 RUNNING: Mixed Configuration Support
================================================================================
✅ Mixed Configuration Support PASSED

================================================================================
🧪 RUNNING: E2E API Registry Server
================================================================================
✅ E2E API Registry Server PASSED

================================================================================
📊 TEST SUMMARY
================================================================================
Total Tests: 4
Passed: 4 ✅
Failed: 0 ❌
Total Time: 45.23s
Success Rate: 100.0%

🎉 ALL TESTS PASSED!
```

## Key Features Tested

### Parameter Flattening
- ✅ `$defs` resolution and removal
- ✅ Complex nested objects simplified to string arrays
- ✅ Proper parameter type preservation
- ✅ Descriptive messages for simplified parameters

### Tool Prefixing
- ✅ Legacy: `digital_sales_get_my_accounts`
- ✅ MCP: `digital_sales_mcp_get_my_accounts`
- ✅ No conflicts between services

### FastMCP Integration
- ✅ Proper client initialization with config
- ✅ Tool listing and registration
- ✅ Function calling with argument handling
- ✅ Error handling and fallback mechanisms

### API Registry Server
- ✅ RESTful endpoints for applications and APIs
- ✅ Function calling via HTTP POST
- ✅ Proper error responses
- ✅ Server lifecycle management

## Troubleshooting

### Common Issues

1. **MCP Server Not Running**
   ```
   Error: Failed to connect to MCP server
   Solution: Start the FastMCP server first
   ```

2. **Port Already in Use**
   ```
   Error: Server failed to start (port 8001 in use)
   Solution: Kill existing processes on port 8001
   ```

3. **Network Issues**
   ```
   Error: Failed to fetch OpenAPI spec
   Solution: Check internet connection
   ```

### Debug Mode
Add `--verbose` or set environment variable:
```bash
export DEBUG=1
uv run python run_all_tests.py
```
