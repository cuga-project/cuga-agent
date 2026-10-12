# 🚀 Quick Start Guide

Get up and running with the Tools Environment Registry in minutes!

## 1. Basic Setup

### Start with Default Configuration
```bash
# Navigate to the project root
cd <repo>

# Start the registry server with default config
uv run python -m cuga.backend.tools_env.registry.registry.api_registry_server
```

The server will start at `http://127.0.0.1:8001`

### Verify Installation
```bash
# Check server health
curl http://localhost:8001/

# List available applications
curl http://localhost:8001/applications
```

## 2. Add Your First MCP Server

### File System Server (Easiest Start)
```yaml
# config/my_first_config.yaml
mcpServers:
  my_files:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/your-username/Documents"]
    description: "My document file manager"
```

### Start with Custom Config
```bash
MCP_SERVERS_FILE=config/my_first_config.yaml uv run python -m cuga.backend.tools_env.registry.registry.api_registry_server
```

## 3. Test Your Setup

### List Your Tools
```bash
curl http://localhost:8001/applications/my_files/apis
```

### Call a File Operation
```bash
curl -X POST http://localhost:8001/functions/call \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "my_files",
    "function_name": "my_files_list_files",
    "args": {"path": "."}
  }'
```

## 4. Add More Services

### OpenAPI Service
```yaml
services:
  - my_api:
      url: "https://jsonplaceholder.typicode.com/openapi.json"
      description: "JSONPlaceholder test API"
```

### Remote MCP Server
```yaml
mcpServers:
  remote_service:
    url: "http://your-mcp-server:8000/sse"
    description: "My remote MCP server"
```

## 5. Common Commands

```bash
# Start server
uv run python -m cuga.backend.tools_env.registry.registry.api_registry_server

# Run tests
uv run pytest src/cuga/backend/tools_env/registry/tests/ -v

# Start with custom config
MCP_SERVERS_FILE=/path/to/config.yaml uv run python -m cuga.backend.tools_env.registry.registry.api_registry_server

# Check logs
tail -f logs/registry_server.log
```

## 6. Next Steps

- Read the full [README.md](README.md) for detailed configuration options
- Check [config/mcp_servers.yaml](config/mcp_servers.yaml) for the default configuration
- See [config/mcp_servers_crm.yaml](config/mcp_servers_crm.yaml) for a multi-server example
- Review [../../../../../docs/examples/cuga_with_runtime_tools/mcp_servers.yaml](../../../../../docs/examples/cuga_with_runtime_tools/mcp_servers.yaml) for an example mixing different tool types

## 🆘 Troubleshooting

### Server Won't Start
- Check if port 8001 is available: `lsof -i :8001`
- Verify configuration file exists and is valid YAML
- Check logs for detailed error messages

### MCP Server Connection Failed
- Ensure the MCP server is running and accessible
- Test connectivity: `curl http://your-mcp-server:8000/health`
- Check firewall settings

### Tool Not Found
- Verify service is listed in `/applications`
- Check exact tool name format: `service_name_tool_name`
- Ensure service loaded successfully (check logs)

## 📝 Example Workflow

1. **Create config file**:
   ```yaml
   mcpServers:
     workspace:
       command: "npx"
       args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/workspace"]
       description: "My workspace files"
   ```

2. **Start server**:
   ```bash
   MCP_SERVERS_FILE=my_config.yaml uv run python -m cuga.backend.tools_env.registry.registry.api_registry_server
   ```

3. **Test connection**:
   ```bash
   curl http://localhost:8001/applications
   ```

4. **Use your tools**:
   ```bash
   curl -X POST http://localhost:8001/functions/call \
     -H "Content-Type: application/json" \
     -d '{"app_name": "workspace", "function_name": "workspace_read_file", "args": {"path": "README.md"}}'
   ```