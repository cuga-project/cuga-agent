# Interactive form filling with MCP Apps

A tool can ask the user to fill in a form in the CUGA chat before it returns its result.
This uses the [MCP Apps](https://mcpui.dev/guide/mcp-apps) standard (SEP-1865; it grew out of MCP-UI):

- the tool declares `_meta.ui.resourceUri` pointing at a `ui://` resource,
- the resource is an HTML app (`text/html;profile=mcp-app`) that CUGA renders in a sandboxed iframe,
- the app calls its server's tools (those with `visibility` including `"app"`) through CUGA,
- when the user is done, the app sends `ui/update-model-context` and `ui/message`; CUGA gives the
  outcome to the agent as the result of the original tool call, and the agent continues.

## The demo: table designer

`src/cuga/demo_tools/table_designer_mcp/` is a deterministic MCP server (same input, same output):

| Tool | Visible to | What it does |
|---|---|---|
| `propose_table(table_name, purpose, fields)` | model, app | Proposes columns and opens the form (`ui://table-designer/form`) |
| `submit_table_design(session_id, table_name, columns)` | app | Validates the edits and shows the review form |
| `edit_table_design(session_id)` | app | Goes back from the review to the edit form |
| `confirm_table_design(session_id)` | app | Accepts the design; the form returns it to the agent |
| `create_table(table_name, columns)` | model | "Creates" the table and returns the DDL |

Flow: the agent calls `propose_table`, and the turn pauses. The edit form lets the user rename,
retype, add or remove columns. **Continue to review** calls `submit_table_design`, and the tool
answers with a second form showing the changes and the SQL. **Accept design** calls
`confirm_table_design`. The agent resumes with the approved columns and calls `create_table`.

## Run it

```bash
# 1. the MCP server (streamable HTTP on 127.0.0.1:8210)
uv run python -m cuga.demo_tools.table_designer_mcp.server

# 2. CUGA with that server
MCP_SERVERS_FILE=$PWD/src/cuga/demo_tools/table_designer_mcp/mcp_servers.yaml uv run cuga start demo
```

Open http://localhost:7860/chat and ask: *"Create a customers table to store name, email and signup date."*

## How CUGA hosts MCP Apps

| Piece | Where |
|---|---|
| Reads `_meta.ui`, hides app-only tools from the agent, wraps results of UI tools (for app clients) | `backend/tools_env/registry/mcp_manager/mcp_manager.py` |
| `/mcp_apps/resources/read`, `/mcp_apps/tools/call` (visibility enforced) | `backend/tools_env/registry/registry/api_registry_server.py` |
| Pause signal, envelope, CSP, the client capability flag | `backend/mcp_apps/protocol.py` |
| Tool wrapper raises the pause | `backend/cuga_graph/nodes/cuga_lite/providers/registry.py` |
| Pause → human-in-the-loop action; resume → tool result | `adapter/sandbox_node.py`, `cuga_lite_node.py`, `backend/mcp_apps/interaction.py` |
| Server-side record of each interaction (the browser only names its id) | `backend/mcp_apps/store.py` |
| Chat UI API and the sandbox proxy page | `backend/mcp_apps/routes.py`, `backend/mcp_apps/sandbox_proxy.html` |
| Renders the app with `@mcp-ui/client` `AppRenderer` | `frontend/src/carbon-chat/McpAppWidget.tsx`, `mcpApps.ts` |

Only a client that renders MCP Apps gets the form. The CUGA web chat says so with the header
`X-CUGA-MCP-Apps: 1` on `/stream`. Every other caller (the SDK, API clients, channels, other agents)
gets the tool's ordinary result, as before MCP Apps support. Tools whose visibility is `["app"]`
are never given to the agent.

Security notes:

- The app runs in a double-iframe sandbox on a **different origin** than the chat. Locally CUGA swaps
  `localhost` ↔ `127.0.0.1`; elsewhere set `CUGA_MCP_APPS_SANDBOX_ORIGIN` to an origin that serves
  `/mcp-apps/sandbox_proxy.html` (and `CUGA_MCP_APPS_HOST_ORIGINS` to the chat's origin).
- The proxy page's CSP comes from the resource's `_meta.ui.csp`; with none, the app has no network access.
- The browser never chooses the server, resource or tools: they come from the server-side interaction
  record. App calls to tools without `"app"` visibility are rejected.

Limitations: interactions are held in memory (a pending form does not survive a server restart), and
only registry (MCP server) tools are supported, in CugaLite (CodeAct and function calling).
