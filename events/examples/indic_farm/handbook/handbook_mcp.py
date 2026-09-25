"""The handbook as an MCP TOOL — option 3 in handbook.py, and NOT what the example uses.

A tool hands the decision to the model: it may search, or not, and with whatever wording it chooses.
For a single handbook where every question is a handbook question, that is a reasoning hop and ~1.3 s
bought for nothing — the example retrieves deterministically instead (see `retrieve` in the pipeline).

Worth switching to when the agent has a real choice to make — handbook vs weather vs market prices —
at which point add this server to mcp_servers.yaml and name it under the agent in the roster.

    python handbook_mcp.py              # → http://127.0.0.1:8765/mcp
"""

from __future__ import annotations

import logging
import os

from fastmcp import FastMCP

from handbook import search_passages

mcp = FastMCP("farm_handbook")


@mcp.tool()
async def search_handbook(question: str) -> dict:
    """Search the farmer's handbook (Farmer's Handbook on Basic Agriculture, MANAGE/GIZ) for passages
    relevant to a farming question. Pass the farmer's question as-is, in ANY language — the index is
    multilingual. Returns the top passages as "[Page N]: text" in `context`, plus `sources` (page +
    relative relevance). Answer ONLY from this context and cite pages."""
    out = await search_passages(question)
    if not out["context"]:
        out["context"] = "(no passages found in the handbook)"
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run(
        transport="http",
        host=os.environ.get("FARM_HANDBOOK_HOST", "127.0.0.1"),
        port=int(os.environ.get("FARM_HANDBOOK_PORT", "8765")),
        path="/mcp",
    )
