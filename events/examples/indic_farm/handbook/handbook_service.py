"""The handbook as a SERVICE — option 2 in handbook.py.

Speaks the turn-service contract, so the events layer reaches it with ``provider: http``:

    POST /v1/search   {"query": "…", "limit": 5}  →  {"context": "[Page 29]: …", "sources": [...]}

Use this when the index lives somewhere other than the events process — another box, a GPU host, or
a service their team owns and deploys on its own cycle.

    python handbook_service.py          # → http://127.0.0.1:8770
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from handbook import search_passages

app = FastAPI(title="farm handbook", docs_url=None, redoc_url=None)


DEFAULT_LIMIT = int(os.environ.get("FARM_SEARCH_LIMIT", "5"))


class SearchRequest(BaseModel):
    query: str
    limit: int = 0  # 0 = the service decides (FARM_SEARCH_LIMIT)
    model: str = ""
    params: dict = {}


@app.post("/v1/search")
async def search(req: SearchRequest):
    """How many passages to return is the SERVICE's call, not the caller's. It is also the main lever
    on answer latency: the passages travel through the supervisor and then the specialist, so both
    read them — 5 passages measured ~52 s to the text reply, 3 measured ~22 s."""
    return await search_passages(req.query, req.limit or DEFAULT_LIMIT)


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("FARM_HANDBOOK_HOST", "127.0.0.1"),
        port=int(os.environ.get("FARM_HANDBOOK_PORT", "8770")),
    )
