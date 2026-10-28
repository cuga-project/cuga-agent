# agent_with_public_mcp

An MCP server is a web service that offers a bundle of ready-made tools over a standard protocol. This example points a `CugaAgent` at the public CUGA "knowledge" MCP server (Wikipedia, arXiv and Semantic Scholar tools), gives the agent those tools, and asks it a question. About 30 lines, no tools written by hand.

## Check it works (no API key, no LLM)

```bash
cd docs/examples/agent_with_public_mcp && uv run --project ../../../ python check.py
# {"ok": true, "data": {"title": "Alan Turing", "summary": "Alan Mathison Turing was an English math...
# OK: 9 tools
```

`check.py` loads the tools, calls `get_article_summary` directly, and confirms `CugaAgent` accepts the tools using a fake model. It needs internet access.

## Run the agent

With an API key: put `OPENAI_API_KEY=...` in the repo's `.env`, then:

```bash
cd docs/examples/agent_with_public_mcp && uv run --project ../../../ python main.py
```

Or use free, local Ollama, as described in [`settings.ollama.toml`](../../../src/cuga/configurations/models/settings.ollama.toml).

## Heads-up

- The server sleeps when idle, so the first call can take 5-10 seconds.
- The search tools on the shared server are sometimes rate-limited (`429`). `get_article_summary` is the most reliable one to demo.

## Try this next

Swap in a different server URL from [`mcp_servers_cuga_apps.yaml`](../../../src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml), for example `cuga_geo`.
