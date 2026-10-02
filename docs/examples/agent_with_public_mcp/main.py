"""A CugaAgent whose tools come from one public MCP server over streamable HTTP."""

import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient

from cuga import CugaAgent

# The cuga-apps hosted "knowledge" server (Wikipedia / arXiv / Semantic Scholar tools).
# It scales to zero, so the first request after idle can take a few seconds.
MCP_SERVERS = {
    "cuga_knowledge": {
        "url": "https://cuga-apps-mcp-knowledge.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud/mcp",
        "transport": "streamable_http",
    }
}


async def load_tools():
    return await MultiServerMCPClient(MCP_SERVERS).get_tools()


async def main() -> None:
    tools = await load_tools()
    print("MCP tools:", [t.name for t in tools])
    agent = CugaAgent(tools=tools)
    result = await agent.invoke(
        "Using Wikipedia, who was Alan Turing? Answer in two sentences."
    )
    print(result.answer)


if __name__ == "__main__":
    asyncio.run(main())
