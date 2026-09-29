# Tools Environment Registry

The Tools Environment Registry is a comprehensive system for managing and integrating various types of API services and tools. It provides a unified interface for OpenAPI services, TRM (Tool Runtime Manager) tools, and MCP (Model Context Protocol) servers.

## 🚀 Quick Start

### Starting the Registry Server

```bash
# Start the registry server
uv run python -m cuga.backend.tools_env.registry.registry.api_registry_server

# Or with custom configuration
MCP_SERVERS_FILE=/path/to/your/config.yaml uv run python -m cuga.backend.tools_env.registry.registry.api_registry_server
```

The server will start on `http://127.0.0.1:8001` by default.

### API Endpoints

- `GET /applications` - List all registered applications
- `GET /applications/{app_name}/apis` - List APIs for a specific application
- `GET /apis` - List all APIs across all applications
- `POST /functions/call` - Call a specific function/tool
- `POST /functions/onboard` - Onboard new tools dynamically

## 📋 Configuration

The registry supports multiple service types through YAML configuration files. By default, it uses `config/mcp_servers.yaml`.

### Configuration Structure

```yaml
# Legacy OpenAPI services
services:
  - service_name:
      url: "https://api.example.com/openapi.json"
      description: "Description of the service"
      type: "openapi" # Optional, inferred from URL
      include: # Optional: filter specific operations
        - "operation_id_1"
        - "operation_id_2"
      api_overrides: # Optional: customize operations
        - operation_id: "get_users"
          description: "Custom description"
          drop_request_body_parameters: ["internal_param"]
          drop_query_parameters: ["debug_mode"]

# MCP (Model Context Protocol) servers
mcpServers:
  server_name:
    url: "http://localhost:8000/sse"
    command: "python" # Optional: for local MCP servers
    args: ["-m", "my_mcp_server"] # Optional: command arguments

# TRM (Tool Runtime Manager) services
trmServices:
  trm_service:
    url: "http://localhost:9000"
    tools: ["tool1", "tool2"]
    auth:
      type: "Authorization"
      value: "Bearer your-token"
```

## 🛠️ Adding Different Types of Services

### 1. OpenAPI Services

OpenAPI services are automatically discovered and integrated from their OpenAPI specification.

**Example Configuration:**

```yaml
services:
  - digital_sales:
      url: "https://digitalsales.example.com/openapi.json"
      description: "Digital Sales API for customer management"
      include: # Only include specific operations
        - "getMyAccounts"
        - "getAccountsTpp"
        - "getJobRoles"
      api_overrides:
        - operation_id: "getAccountsTpp"
          description: "Retrieve accounts from TPP with enhanced filtering"
          drop_query_parameters: ["debug", "internal_flag"]
```

**Features:**

- Automatic parameter extraction and validation
- Response schema parsing
- Security scheme detection
- Parameter filtering and customization

#### AppWorld login modes

AppWorld apps need a bearer token. The service's `auth.type` sets who logs in:

| `auth.type` | Who logs in |
|---|---|
| `oauth2` | The registry logs in to each app before the agent starts, using the supervisor's passwords (cuga-eval's default). |
| `oauth2_agent` | The agent logs in itself. The registry attaches a token only after the agent's own `/auth/token` call for that app; before that, the app returns its own 401. |

```yaml
services:
  - spotify:
      url: "http://appworld:9000/spotify/openapi.json"
      description: "Music streaming app"
      auth:
        type: oauth2_agent
```

When AppWorld runs in another container or host, point the registry at it with the full
URL, which replaces `http://localhost:{apis_url}`:

```bash
export DYNACONF_SERVER_PORTS__APIS_HOST=http://appworld:9000
```

To run cuga-eval's `mcp_servers_appworld.yaml` this way, set each app's `auth.type` to
`oauth2_agent` and its `url` to the remote host; the rest of the file stays the same.

### 2. MCP (Model Context Protocol) Servers

MCP servers provide tools through the Model Context Protocol standard.

#### Remote MCP Server

```yaml
mcpServers:
  sales_mcp:
    url: "http://127.0.0.1:8000/sse"
    description: "Sales MCP server with customer tools"
```

#### Local MCP Server

```yaml
mcpServers:
  local_tools:
    command: "python"
    args: ["-m", "my_local_mcp_server"]
    description: "Local MCP server with file system tools"
```

#### File System MCP Server Example

Here's how to add a file system MCP server:

```yaml
mcpServers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/directory"]
    description: "File system operations MCP server"
```

> **💡 Best Practice**: When developing custom MCP servers, include response schema information in tool descriptions to work around MCP protocol limitations. See the [MCP Response Schema Representation](#mcp-response-schema-representation) section for implementation details.

**Sample File System MCP Server Configuration:**

```yaml
# config/mcp_servers_filesystem.yaml
mcpServers:
  # File system server for document management
  document_fs:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/documents"]
    description: "Document management file system server"

  # File system server for project files
  project_fs:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/projects", "--readonly"]
    description: "Read-only access to project files"

  # Custom Python file system server
  custom_fs:
    command: "python"
    args: ["-m", "my_fs_server", "--root", "/safe/directory", "--max-file-size", "10MB"]
    description: "Custom file system server with safety limits"
```

### 3. TRM (Tool Runtime Manager) Services

TRM services provide tools through a runtime management system.

```yaml
trmServices:
  data_tools:
    url: "http://localhost:9000"
    tools: ["query_database", "export_data", "generate_report"]
    auth:
      type: "Authorization"
      value: "Bearer your-api-token"
    description: "Data processing and reporting tools"
```

## 📝 Sample Configurations

### Complete Sample Configuration

```yaml
# config/sample_complete.yaml

# OpenAPI Services
services:
  - sales_api:
      url: "https://sales.example.com/openapi.json"
      description: "Sales management API"
      include: ["getAccounts", "createLead", "updateContact"]

  - inventory_api:
      url: "https://inventory.example.com/api/docs/openapi.json"
      description: "Inventory management system"
      api_overrides:
        - operation_id: "getProducts"
          description: "Get products with enhanced filtering"
          drop_query_parameters: ["internal_debug"]

# MCP Servers
mcpServers:
  # Remote MCP server
  customer_tools:
    url: "http://customer-tools.example.com:8000/sse"
    description: "Customer relationship management tools"

  # Local file system server
  documents:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/shared/documents"]
    description: "Document management system"

  # Local Python MCP server
  data_processor:
    command: "python"
    args: ["-m", "data_mcp_server", "--config", "config.json"]
    description: "Data processing and analysis tools"

  # Git repository server
  code_repo:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-git", "/path/to/repo"]
    description: "Git repository operations"

# TRM Services
trmServices:
  analytics:
    url: "http://analytics.internal:9000"
    tools: ["run_analysis", "generate_insights", "export_dashboard"]
    auth:
      type: "X-API-Key"
      value: "your-analytics-api-key"
    description: "Analytics and reporting platform"
```

### File System MCP Server Samples

```yaml
# config/filesystem_samples.yaml
mcpServers:
  # Basic file operations
  file_manager:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/workspace"]
    description: "File management operations"

  # Read-only document access
  docs_readonly:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/documents", "--readonly"]
    description: "Read-only access to documents"

  # Multiple directory access
  multi_dir:
    command: "python"
    args: ["-m", "filesystem_mcp", "--dirs", "/Users/projects,/Users/temp", "--max-depth", "3"]
    description: "Multi-directory file system access"

  # Secure file server with restrictions
  secure_fs:
    command: "python"
    args:
      [
        "-m",
        "secure_fs_mcp",
        "--root",
        "/safe/sandbox",
        "--allowed-extensions",
        ".txt,.md,.json,.yaml",
        "--max-file-size",
        "5MB",
        "--no-hidden-files",
      ]
    description: "Secure file system with safety restrictions"
```

## 🔧 How It Works

### Service Discovery and Integration

1. **Configuration Loading**: The registry reads YAML configuration files to discover services
2. **Service Initialization**: Each service type is initialized with its specific adapter
3. **Tool Registration**: Tools/APIs are registered with consistent naming (`service_name_tool_name`)
4. **Unified Interface**: All services expose the same standardized API format

### API Format Standardization

All services return APIs in a consistent format:

```json
{
  "service_name_api_name": {
    "app_name": "service_name",
    "secure": false,
    "api_name": "service_name_api_name",
    "path": "/api/endpoint",
    "method": "POST",
    "description": "API description",
    "parameters": [
      {
        "name": "param_name",
        "type": "string",
        "required": true,
        "description": "Parameter description",
        "constraints": ["must be one of: [value1, value2]"]
      }
    ],
    "response_schemas": {
      "success": { "result": "object" },
      "failure": { "error": "string" }
    }
  }
}
```

### Tool Calling

```bash
# Call a tool via HTTP API
curl -X POST http://localhost:8001/functions/call \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "sales_api",
    "function_name": "sales_api_get_accounts",
    "args": {
      "limit": 10,
      "status": "active"
    }
  }'
```

## 🧪 Testing

### Running Tests

```bash
# Run all registry tests
uv run pytest cuga/backend/tools_env/registry/tests/ -v

# Run specific test categories
uv run pytest cuga/backend/tools_env/registry/tests/test_legacy_openapi.py -v
uv run pytest cuga/backend/tools_env/registry/tests/test_mcp_server.py -v
uv run pytest cuga/backend/tools_env/registry/tests/test_mixed_configuration.py -v
```

### Test Configurations

The test suite includes sample configurations for different scenarios:

- **Legacy OpenAPI**: Tests OpenAPI service integration
- **MCP Servers**: Tests MCP server tool registration and calling
- **Mixed Configuration**: Tests multiple service types together
- **E2E Testing**: Tests the full HTTP API server

## ⚠️ Important Limitations and Workarounds

### MCP Response Schema Representation

Due to limitations in the MCP (Model Context Protocol) specification, response schemas are not natively supported in tool definitions. This means that tools created from MCP servers may not have complete response schema information available to consumers.

Recommended way as of today: https://gofastmcp.com/servers/tools#output-schemas

Or: continue reading for workarounds

**Recommended Workaround:**

When creating MCP servers, it's recommended to include response schema information directly in the tool descriptions. This can be done by customizing the MCP component during server creation.

**Example Implementation:**

````python
from fastmcp import FastMCP
from fastmcp.server.openapi import OpenAPITool

def customize_components(route, component):
    """Add response schema to tool descriptions"""
    if isinstance(component, OpenAPITool):
        # Add response schema to description
        component.description = f"{component.description}\nresponse schema: ```\n{component.output_schema}```"

# Create MCP server with customization
mcp = FastMCP.from_openapi(
    openapi_spec=spec,
    client=client,
    mcp_component_fn=customize_components,
)
````

**Alternative Approaches:**

1. **Schema as Dictionary**: Include the response schema as a structured dictionary in the description:

   ```python
   component.description = f"{component.description}\n\nExpected Response Format:\n{json.dumps(response_schema, indent=2)}"
   ```

2. **Example Response**: Include a sample response without actual values:
   ```python
   example_response = {
       "accounts": [{"name": "string", "state": "string", "revenue": "integer"}],
       "coverage_id": "string",
       "client_status": "string"
   }
   component.description = f"{component.description}\n\nExample Response:\n{json.dumps(example_response, indent=2)}"
   ```

**Registry Implementation:**

The Tools Environment Registry addresses this limitation by:

- Extracting response schemas from OpenAPI specifications when available
- Providing consistent response schema information across all service types
- Including response schemas in the standardized API format

**Custom Output Schema in MCP Tools: ( Recommended )**

When developing your own MCP servers, you can define custom output schemas using the `@mcp.tool()` decorator:

```python
@mcp.tool(output_schema={
    "type": "object",
    "properties": {
        "data": {"type": "string"},
        "metadata": {"type": "object"}
    }
})
def custom_schema_tool() -> dict:
    """Tool with custom output schema."""
    return {"data": "Hello", "metadata": {"version": "1.0"}}
```

This approach provides structured output schema information that can be used by the registry system.

**Reference Example:**

See `docs/examples/client_package_usage/fast_mcp_example.py` for a complete implementation of this workaround.

## 🔍 Debugging and Troubleshooting

### Common Issues

1. **MCP Server Connection Failed**

   ```
   Error: Failed to connect to MCP server at http://localhost:8000/sse
   ```

   - Ensure the MCP server is running and accessible
   - Check the URL and port configuration
   - Verify network connectivity

2. **OpenAPI Schema Loading Failed**

   ```
   Error: Failed to fetch OpenAPI schema from https://api.example.com/openapi.json
   ```

   - Verify the URL is accessible
   - Check authentication requirements
   - Ensure the schema is valid OpenAPI format

3. **Tool Not Found**
   ```
   Error: Tool 'service_name_tool_name' not found in any server
   ```
   - Verify the service is properly configured and loaded
   - Check the exact tool name (should include service prefix)
   - Ensure the service initialization completed successfully

### Logging

Enable debug logging to troubleshoot issues:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Health Checks

```bash
# Check server health
curl http://localhost:8001/

# List all applications
curl http://localhost:8001/applications

# Check specific application
curl http://localhost:8001/applications/your_service_name/apis
```

## 🚀 Advanced Usage

### Dynamic Tool Onboarding

```python
# Onboard tools dynamically via API
import requests

payload = {
    "app_name": "dynamic_service",
    "schemas": [
        {
            "name": "custom_tool",
            "description": "A dynamically added tool",
            "input_schema": {
                "type": "object",
                "properties": {
                    "input": {"type": "string"}
                },
                "required": ["input"]
            }
        }
    ]
}

response = requests.post("http://localhost:8001/functions/onboard", json=payload)
```

### Custom MCP Server Development

```python
# Example custom MCP server with output schemas
from mcp import FastMCP

app = FastMCP()

@app.tool("custom_analysis", output_schema={
    "type": "object",
    "properties": {
        "result": {"type": "string"},
        "confidence": {"type": "number"},
        "metadata": {
            "type": "object",
            "properties": {
                "method": {"type": "string"},
                "timestamp": {"type": "string"}
            }
        }
    },
    "required": ["result", "confidence"]
})
def analyze_data(data: str, method: str = "default") -> dict:
    """Analyze data using specified method with structured output"""
    import datetime

    # Your analysis logic here
    result = f"Analysis result for {data} using {method}"

    return {
        "result": result,
        "confidence": 0.95,
        "metadata": {
            "method": method,
            "timestamp": datetime.datetime.now().isoformat()
        }
    }

@app.tool("simple_tool", output_schema={
    "type": "object",
    "properties": {
        "data": {"type": "string"},
        "status": {"type": "string"}
    }
})
def simple_tool(input_text: str) -> dict:
    """Simple tool with basic output schema"""
    return {
        "data": f"Processed: {input_text}",
        "status": "success"
    }

if __name__ == "__main__":
    app.run()
```

## 📚 Additional Resources

- [MCP Protocol Documentation](https://modelcontextprotocol.io/)
- [OpenAPI Specification](https://swagger.io/specification/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)

## 🤝 Contributing

1. Add new service adapters in `mcp_manager/`
2. Update tests in `tests/`
3. Follow the standardized API format
4. Add configuration samples
5. Update this README with new features

---

For more examples and advanced configurations, check the `tests/` directory and `config/` samples.
