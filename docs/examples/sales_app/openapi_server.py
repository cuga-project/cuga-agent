"""OpenAPI surface for the managed CUGA server.

The embedded app passes LangChain tools straight into CugaAgent. The managed
server only loads MCP or OpenAPI tools, so this process exposes the same book.
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from store import STAGES, SalesStore  # noqa: E402

DB_PATH = _ROOT / "data" / "sales.sqlite"


class StageUpdate(BaseModel):
    stage: str = Field(
        description="prospecting, qualification, proposal, negotiation, closed-won, or closed-lost"
    )


def create_openapi_app(db_path: str | Path | None = None) -> FastAPI:
    book = SalesStore(db_path or DB_PATH)
    api = FastAPI(title="Harborline Sales API", version="1.0.0")

    @api.get("/opportunities", operation_id="list_opportunities")
    def list_opportunities(stage: str = "") -> list[dict]:
        """List opportunities. Filter with a stage name, or leave stage empty for every deal."""
        return book.list_opportunities(stage=stage or None)

    @api.get("/accounts", operation_id="list_accounts")
    def list_accounts() -> list[dict]:
        """List sales accounts."""
        return book.list_accounts()

    @api.get("/leads", operation_id="list_leads")
    def list_leads(status: str = "") -> list[dict]:
        """List leads. status may be new, working, or qualified."""
        return book.list_leads(status=status or None)

    @api.get("/summary", operation_id="pipeline_summary")
    def pipeline_summary() -> dict:
        """Return open pipeline value, open deal count, won value, and win rate."""
        return book.summary()

    @api.post("/opportunities/{opportunity_id}/stage", operation_id="update_opportunity_stage")
    def update_opportunity_stage(opportunity_id: int, body: StageUpdate) -> dict:
        """Move an opportunity to a new stage. Call list_opportunities first to find the id."""
        if body.stage not in STAGES:
            raise HTTPException(status_code=400, detail=f"stage must be one of: {', '.join(STAGES)}")
        try:
            return book.update_opportunity_stage(opportunity_id, body.stage)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return api


app = create_openapi_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766)
