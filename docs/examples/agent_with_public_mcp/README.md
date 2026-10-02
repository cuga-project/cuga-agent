# Agent with a public MCP server

This example connects a CUGA agent to a public MCP server, so the agent can borrow tools hosted by another service instead of defining every tool locally.

The example uses CUGA's public `cuga_knowledge` server, which exposes Wikipedia, arXiv, and Semantic Scholar tools over streamable HTTP.

## Keyless check

The public server scales to zero, so the first request after idle can take a few seconds.

From this directory:

```bash
uv run --project ../../../ python check.py
```

The check needs internet access but no AI API key. It loads the public MCP tools, calls `get_article_summary` directly for Alan Turing, and confirms that `CugaAgent` accepts the returned tools with a fake model.

The shared search tools can occasionally be rate-limited with HTTP 429. `get_article_summary` is the most reliable tool for a simple demo.

## Run the agent

Put your OpenAI-compatible API key in the repository `.env`, then run:

```bash
uv run --project ../../../ python main.py
```

For free local execution, use Ollama as described in `src/cuga/configurations/models/settings.ollama.toml`.

## Try this next

Swap the `cuga_knowledge` URL in `main.py` for another public server from `src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml`.

For example, try `cuga_geo` to borrow geography tools from the public geo server.
