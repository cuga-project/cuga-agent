# CUGA as MCP Server

This example demonstrates how to run CUGA (Computer Using Generalist Agent) as a Model Context Protocol (MCP) server, enabling it to be used as a tool by other applications.

## What is CUGA?

CUGA is an autonomous AI agent that can:
- 🤖 **Perform web actions** with intelligent planning
- 🔗 **Connect to APIs** seamlessly  
- 📱 **Automate repetitive tasks** on websites
- 🧠 **Decompose complex tasks** into manageable steps
- 🎯 **Execute multi-step workflows** autonomously

## What is MCP?

Model Context Protocol (MCP) is a standard that allows AI applications to expose their capabilities as tools that other applications can use. By running CUGA as an MCP server, you can integrate its powerful automation capabilities into other systems.

## How This Example Works

This example creates an MCP server that exposes CUGA's task execution capabilities with multiple execution modes:

1. **Initializes CUGA Agent** - Sets up the core CUGA system
2. **Configures Environment Variables** - Sets up MCP servers file and execution modes
3. **Exposes Multiple Tools** - Provides three different execution modes:
   - `run_api_task` - API-only mode (headless, no GUI)
   - `run_web_task` - Web mode (browser with GUI interaction)
   - `run_hybrid_task` - Hybrid mode (combination of API and web)
4. **Handles Task Execution** - Processes tasks based on selected mode and returns results

## Quick Start

From the repository root, install dependencies once (this repo has a single root `uv.lock`; see **Dependency lockfile** in [CONTRIBUTING.md](../../../CONTRIBUTING.md)):

```bash
cd /path/to/cuga-agent
uv sync
cd docs/examples/cuga_as_mcp
```

1. **Configure LLM Access:**
   - Follow the [main README LLM configuration section](../../README.md#llm-configuration---advanced-options) for setup instructions
   - Copy the environment file: `cp .env.example .env`
   - Add your API key to the `.env` file

2. **Start the API Registry (in a separate terminal):**
   ```bash
   export MCP_SERVERS_FILE=./mcp_servers.yaml
   uv run --project ../../../ registry
   ```
3. **Start the MCP Server:**
   ```bash
   uv run --project ../../../ main.py
   ```

4. **The server will:**
   - Start on `http://localhost:8000/sse`
   - Configure MCP servers from `mcp_servers.yaml`
   - Expose three execution modes:
     - `run_api_task` - Headless automation (API mode)
     - `run_web_task` - Browser GUI automation (Web mode)  
     - `run_hybrid_task` - Combined API and web automation (Hybrid mode)

5. **Use from another application:**
   ```python
   # API Mode - Headless execution
   result = await mcp_client.call_tool("run_api_task", {
       "task": "get my top account by revenue"
   })
   
   # Web Mode - Browser GUI execution
   result = await mcp_client.call_tool("run_web_task", {
       "task": "navigate to dashboard and get revenue data",
       "start_url": "https://example.com"
   })
   
   # Hybrid Mode - Combined execution
   result = await mcp_client.call_tool("run_hybrid_task", {
       "task": "analyze data from API and web interface",
       "start_url": "https://example.com"
   })
   ```

## Example Tasks by Mode

### API Mode (`run_api_task`)
Perfect for data retrieval and API interactions:
- "Get my top account by revenue from digital sales"
- "List all accounts with revenue above $100k"
- "Find the account with the highest growth rate"
- "Retrieve customer data from CRM API"

### Web Mode (`run_web_task`)
Ideal for browser-based interactions:
- "Navigate to dashboard and download report"
- "Fill out contact form on website"
- "Extract data from web tables"
- "Automate login and data entry workflows"

### Hybrid Mode (`run_hybrid_task`)
Combines API and web capabilities:
- "Get API data and cross-reference with web dashboard"
- "Validate API results against web interface"
- "Sync data between web form and API endpoint"
- "Perform comprehensive data analysis across platforms"

## Integration

This MCP server can be integrated with:
- Claude Desktop
- Other AI applications that support MCP
- Custom applications using MCP clients

## Environment Variables

The server automatically configures the following environment variables:

- `MCP_SERVERS_FILE` - Path to MCP servers configuration file
- `DYNA_CONF_ADVANCED_FEATURES__MODE` - Execution mode (`api`, `web`, or `hybrid`)

## Requirements

- CUGA backend running
- Digital Sales API accessible (if using API features)
- Python 3.12+
- Required dependencies installed
- Browser environment (for web and hybrid modes)

## Files

- `main.py` - The MCP server implementation with three execution modes
- `mcp_servers.yaml` - MCP servers configuration file
- `README.md` - This documentation

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   MCP Client    │───▶│   CUGA MCP       │───▶│   CUGA Agent    │
│                 │    │   Server         │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │                          │
                              ▼                          ▼
                       ┌──────────────┐         ┌─────────────────┐
                       │ Environment  │         │ Browser/API     │
                       │ Variables    │         │ Execution       │
                       └──────────────┘         └─────────────────┘
```
