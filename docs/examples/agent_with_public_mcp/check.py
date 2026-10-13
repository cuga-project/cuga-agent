"""Keyless check: the public MCP server answers, and CugaAgent accepts its tools (no LLM call)."""

import asyncio

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from cuga import CugaAgent
from main import load_tools

tools = asyncio.run(load_tools())
by_name = {t.name: t for t in tools}
assert "get_article_summary" in by_name, list(by_name)
out = asyncio.run(by_name["get_article_summary"].ainvoke({"title": "Alan Turing"}))
text = out[0]["text"] if isinstance(out, list) else str(out)  # MCP tools return a list of content blocks
assert '"ok": true' in text, text
print(text[:100])
CugaAgent(tools=tools, model=FakeListChatModel(responses=["unused"]))
print("OK:", len(tools), "tools")
