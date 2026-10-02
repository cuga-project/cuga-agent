"""Keyless smoke check for the public MCP example."""

import asyncio
import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from cuga import CugaAgent
from main import load_tools


async def check() -> None:
    """Smoke-check the public MCP payload and construct a CugaAgent offline."""
    tools = await load_tools()
    by_name = {tool.name: tool for tool in tools}
    assert "get_article_summary" in by_name, list(by_name)
    result = await by_name["get_article_summary"].ainvoke({"title": "Alan Turing"})
    payload = result[0]["text"] if isinstance(result, list) else str(result)
    parsed = json.loads(payload)
    assert parsed.get("ok") is True, payload
    print(payload[:100])
    CugaAgent(tools=tools, model=FakeListChatModel(responses=["unused"]))
    print("OK:", len(tools), "tools")


if __name__ == "__main__":
    asyncio.run(check())
