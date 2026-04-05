# CUGA Tool Integration Examples

This directory demonstrates **three types of tool integrations** that CUGA supports, showcasing how to connect different tool types to create powerful AI agents.

## 🎯 **Goal of This Example**

This example shows how CUGA can seamlessly integrate multiple tool types in a single workflow. The `main.py` demonstrates a complex task that uses:

1. **OpenAPI Tools** - Access external REST APIs (Digital Sales)
2. **MCP Tools** - File system operations via Model Context Protocol
3. **LangChain Tools** - Python functions for email operations

**Example Task**: *"Get top account by revenue from digital sales, send an email to the account owner, and save it to filesystem"*

## 🔄 **Two Ways to Provide Tools to CUGA**

CUGA supports two distinct approaches for tool integration, each suited for different use cases:

### 1. **Registry-Based Tools** (Separate Process)
Tools that run in the **MCP Registry**, a separate process triggered by CUGA:

- **OpenAPI Tools** - REST APIs via OpenAPI specifications
- **MCP Tools** - Model Context Protocol servers (stdio/http/sse)

**When to Use:**
- Shared tools across CUGA instances
- Persistent external services/APIs
- OpenAPI or MCP configuration in `mcp_servers.yaml`

**How it Works:**
```bash
# From docs/examples/cuga_with_runtime_tools after `uv sync` at repo root:
uv run --project ../../../ registry
# CUGA connects to registry at runtime
```

### 2. **Runtime LangChain Tools** (In-Process)
LangChain tools passed directly to CUGA at runtime:

**When to Use:**
- CUGA is a **component in another system** (embedded mode)
- Dynamic tools that change based on application state
- Custom Python functions specific to your application
- Rapid prototyping without registry configuration
- No need for the full registry process

**How it Works:**
```python
from cuga.backend.cuga_graph.utils.controller import AgentRunner as CugaAgent
from langchain_example_tool import tools as gmail_dummy_tools

# Initialize CUGA agent
cuga_agent = CugaAgent(browser_enabled=False)
await cuga_agent.initialize_appworld_env()

# Pass runtime tools directly to CUGA
tools = gmail_dummy_tools
for tool in tools:
    tool.metadata = {'server_name': "gmail"}
tracker.set_tools(tools)

# Run task with runtime tools
task_result = await cuga_agent.run_task_generic(eval_mode=False, goal=task)
```

**Key Advantage**: Ideal when CUGA is integrated into a larger system where you need to pass runtime tools without managing a separate registry process.

## 🔧 **Three Types of Tools in `mcp_servers.yaml`**

### 1. **OpenAPI Tools** (Direct API Integration)
```yaml
services:
  - digital_sales:
      url: https://digitalsales.19pc1vtv090u.us-east.codeengine.appdomain.cloud/openapi.json
      description: Digital Sales Skills API for territory accounts and client information
```
- **Purpose**: Connect to REST APIs via OpenAPI specifications
- **Use Case**: External services, existing APIs, third-party integrations
- **Example**: Digital Sales API for account management

### 2. **MCP Tools** (Model Context Protocol)

MCP tools support **three transport types** following [FastMCP patterns](https://gofastmcp.com/clients/transports):

#### **STDIO Transport** (Default for Local Commands)
```yaml
mcpServers:
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "./cuga_workspace"]
    transport: stdio  # Optional: auto-detected
    env:
      LOG_LEVEL: INFO
    description: Standard file system operations
```
- **Best For**: Local development, file operations, subprocess tools
- **Features**: Client manages server lifecycle, environment isolation

#### **HTTP Transport** (Recommended for Production)
```yaml
mcpServers:
  production_api:
    url: https://api.example.com/mcp
    transport: http
    description: Production MCP server
```
- **Best For**: Remote services, production deployments, scalability
- **Features**: Efficient bidirectional streaming, already-running servers

#### **SSE Transport** (Legacy)
```yaml
mcpServers:
  legacy_api:
    url: https://api.example.com/sse
    transport: sse  # Auto-detected from /sse in URL
    description: Legacy SSE server
```
- **Best For**: Backward compatibility
- **Features**: Server-Sent Events, maintained for legacy systems

**Transport Auto-Detection**: When `transport` is not specified:
- Has `command` → STDIO
- URL contains `/sse` → SSE
- URL without `/sse` → HTTP

### 3. **LangChain Tools** (Python Functions)
Defined in `langchain_example_tool.py`:
```python
# Gmail tools loaded at runtime
read_tool = StructuredTool.from_function(read_emails)
send_tool = StructuredTool.from_function(send_email)
tools = [read_tool, send_tool]
```
- **Purpose**: Python functions as tools, runtime tools, rapid prototyping
- **Use Case**: Custom logic, data processing.
- **Example**: Gmail email operations with dummy data

## 🚀 **Working Example in `main.py`**

The main application demonstrates all three tool types working together:

```python
# Task combines all three tool types
task = "Get top account by revenue from my accounts in digital sales, then send an email to the account owner, and save it to to file in my filesystem under cuga_workspace/email_sent.md"

# 1. Uses Digital Sales API (OpenAPI) to get account data
# 2. Uses Gmail tools (LangChain) to send email
# 3. Uses filesystem (MCP) to save the email content
```

## 📋 **Prerequisites**

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js (for MCP filesystem server)

## 🛠️ **Setup Instructions**

### 1. **Install Dependencies**

This repository uses one lockfile at the root. From the repo root:

```bash
cd /path/to/cuga-agent
uv sync
cd docs/examples/cuga_with_runtime_tools
```

See **Dependency lockfile** in [CONTRIBUTING.md](../../../CONTRIBUTING.md) for why there is no separate `uv.lock` in this folder.

Create a local `.env` or copy the main `.env` file:

```bash
cp ../../../.env .env
```

### 2. **Start MCP Registry** (for OpenAPI and MCP tools)
```bash
export MCP_SERVERS_FILE=./mcp_servers.yaml
uv run --project ../../../ registry
```

### 3. **Run the Complete Example**
In a second terminal, from this same directory:

```bash
uv run --project ../../../ main.py
```

### 4. **Kill Registry Process** (if needed)
- **macOS/Linux:**
  ```bash
  lsof -ti:8001 | xargs kill -9
  ```
- **Windows:**
  ```bash
  netstat -ano | findstr :8001
  taskkill /PID <PID> /F
  ```

## 📁 **File Structure**

```
docs/examples/cuga_with_runtime_tools/
├── main.py                    # Main example showing all tool types
├── langchain_example_tool.py  # LangChain Gmail tools (dummy data)
├── fast_mcp_example.py       # MCP server example
├── mcp_servers.yaml          # Configuration for OpenAPI & MCP tools
├── cuga_workspace/           # Workspace for file operations
│   └── email_sent.md         # Example output file
└── README.md                 # This file
```

## 🔍 **What Happens When You Run `main.py`**

1. **Initialize CUGA Agent** with all three tool types
2. **Load OpenAPI tools** from Digital Sales API via MCP registry
3. **Load MCP tools** for filesystem operations
4. **Load LangChain tools** for Gmail operations (runtime)
5. **Execute complex task** that orchestrates all tool types:
   - Query Digital Sales API for top revenue account
   - Generate and send email using Gmail tools
   - Save email content to filesystem using MCP tools

## 🎯 **Key Benefits Demonstrated**

- **Flexibility**: Mix different tool types in one workflow
- **Scalability**: Add new tools without code changes
- **Reusability**: Tools can be used across different tasks
- **Integration**: Seamless communication between tool types

This example showcases CUGA's powerful ability to create unified AI workflows that span multiple systems and protocols.

## 🚇 **MCP Transport Types Guide**

### When to Use Each Transport

| Transport | Use Case | Example |
|-----------|----------|---------|
| **STDIO** | Local development, file operations | File system, local scripts |
| **HTTP** | Production, remote services | Cloud APIs, microservices |
| **SSE** | Legacy compatibility | Existing SSE infrastructure |

### Transport Configuration Examples

**Local Development Setup (STDIO)**
```yaml
mcpServers:
  local_tools:
    command: python
    args: ["./my_tools.py", "--verbose"]
    env:
      DEBUG: "true"
      API_KEY: ${YOUR_API_KEY}
```

**Production Setup (HTTP)**
```yaml
mcpServers:
  prod_api:
    url: https://api.example.com/mcp
    transport: http
```

**Legacy System (SSE)**
```yaml
mcpServers:
  legacy:
    url: https://legacy.example.com/sse
    transport: sse
```

### Environment Variables (STDIO Only)

STDIO transports run in isolated environments. Pass environment variables explicitly:

```yaml
mcpServers:
  secure_server:
    command: python
    args: ["server.py"]
    env:
      API_KEY: your_secret_key
      DATABASE_URL: postgresql://localhost/db
      LOG_LEVEL: INFO
```

**Note**: HTTP and SSE transports connect to already-running servers that manage their own environment.

## 📚 **Additional Resources**

- [FastMCP Transport Documentation](https://gofastmcp.com/clients/transports)
- [MCP Transport Types Guide](../../../src/cuga/backend/tools_env/registry/docs/MCP_TRANSPORTS.md)
- [Registry Configuration](../../../src/cuga/backend/tools_env/registry/README.md)
