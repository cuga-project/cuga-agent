"""Harborline sales desk: dashboard plus an embedded CugaAgent."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from store import SalesStore  # noqa: E402
from tools import build_tools  # noqa: E402

SALES_INSTRUCTIONS = """
You are the Harborline sales assistant. The sales book is available only through tools.
Your first response must be a python code block that calls a tool. Do not answer in prose before a tool result is printed.
Use pipeline_summary for totals. Use list_opportunities, list_accounts, list_leads, list_contacts, or get_account for details.
When asked to move a deal, call update_opportunity_stage with the opportunity id from list_opportunities.
Do not invent accounts, people, amounts, or stages. State amounts in dollars.
""".strip()

SALES_PLAYBOOK = """
# Use the sales book

1. Call a tool before answering any question about deals, accounts, leads, contacts, or totals.
2. For a stage such as negotiation, call list_opportunities with that stage.
3. Answer only after the tool result is printed. Do not stop after restating the question.
""".strip()

STATIC_DIR = _ROOT / "static"
DEFAULT_DB = _ROOT / "data" / "sales.sqlite"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str | None = None


def create_app(db_path: str | Path | None = None) -> FastAPI:
    store = SalesStore(db_path or DEFAULT_DB)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        agent = getattr(app.state, "agent", None)
        if agent is not None:
            await agent.aclose()

    app = FastAPI(title="Harborline Sales", lifespan=lifespan)
    app.state.store = store
    app.state.agent = None

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/summary")
    def summary():
        return store.summary()

    @app.get("/api/accounts")
    def accounts():
        return store.list_accounts()

    @app.get("/api/accounts/{name}")
    def account(name: str):
        found = store.get_account(name)
        if found is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return found

    @app.get("/api/leads")
    def leads():
        return store.list_leads()

    @app.get("/api/contacts")
    def contacts():
        return store.list_contacts()

    @app.get("/api/opportunities")
    def opportunities():
        return store.list_opportunities()

    @app.post("/api/chat")
    async def chat(body: ChatRequest):
        agent = await _agent_for(app, store)
        result = await agent.invoke(body.message, thread_id=body.thread_id, track_tool_calls=True)
        return {
            "answer": result.answer,
            "error": result.error,
            "thread_id": result.thread_id,
            "tool_calls": [_public_tool_call(call) for call in result.tool_calls],
        }

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


async def _agent_for(app: FastAPI, store: SalesStore):
    if app.state.agent is None:
        from cuga import CugaAgent

        app.state.agent = CugaAgent(
            tools=build_tools(store),
            enable_knowledge=False,
            auto_load_policies=False,
            filesystem_sync=False,
            special_instructions=SALES_INSTRUCTIONS,
        )
        await app.state.agent.policies.add_playbook(
            name="Use the sales book",
            keywords=[
                "deal",
                "deals",
                "pipeline",
                "account",
                "lead",
                "contact",
                "opportunity",
                "negotiation",
            ],
            content=SALES_PLAYBOOK,
        )
    return app.state.agent


def _public_tool_call(call: dict) -> dict:
    return {
        "name": call.get("name") or call.get("tool_name") or "tool",
        "arguments": call.get("arguments") or call.get("args") or {},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8765)
