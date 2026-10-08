"""Harborline sales desk store, tools, and HTTP routes. No live LLM."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_APP_DIR = Path(__file__).resolve().parents[2] / "docs" / "examples" / "sales_app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from app import create_app  # noqa: E402
from store import SalesStore  # noqa: E402
from tools import build_tools  # noqa: E402

pytestmark = pytest.mark.unit


def test_seed_and_open_pipeline(tmp_path: Path) -> None:
    store = SalesStore(tmp_path / "sales.sqlite")
    summary = store.summary()
    assert summary["account_count"] == 6
    assert summary["lead_count"] == 6
    assert summary["open_deal_count"] == 7
    assert summary["open_pipeline"] == 180000 + 128000 + 240000 + 205000 + 96000 + 310000 + 72000
    assert summary["won_value"] == 54000


def test_update_stage_rejects_unknown_and_missing(tmp_path: Path) -> None:
    store = SalesStore(tmp_path / "sales.sqlite")
    deal = store.list_opportunities(stage="negotiation")[0]
    moved = store.update_opportunity_stage(deal["id"], "closed-won")
    assert moved["stage"] == "closed-won"
    with pytest.raises(ValueError, match="stage must be one of"):
        store.update_opportunity_stage(deal["id"], "signed")
    with pytest.raises(ValueError, match="was not found"):
        store.update_opportunity_stage(9999, "proposal")


def test_tools_read_account_and_update_stage(tmp_path: Path) -> None:
    store = SalesStore(tmp_path / "sales.sqlite")
    tools = {tool.name: tool for tool in build_tools(store)}
    account = json.loads(tools["get_account"].invoke({"name": "Paper & Pine"}))
    assert account["owner"] == "Andre Walsh"
    assert any(deal["name"] == "Loyalty rebuild" for deal in account["opportunities"])
    deal_id = next(deal["id"] for deal in account["opportunities"] if deal["name"] == "Loyalty rebuild")
    updated = json.loads(
        tools["update_opportunity_stage"].invoke({"opportunity_id": deal_id, "stage": "closed-won"})
    )
    assert updated["stage"] == "closed-won"
    assert updated["account_name"] == "Paper & Pine"


def test_http_board_and_index(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "sales.sqlite"))
    summary = client.get("/api/summary")
    assert summary.status_code == 200
    assert summary.json()["open_pipeline"] > 0
    page = client.get("/")
    assert page.status_code == 200
    assert "Harborline" in page.text
    assert client.get("/static/app.js").status_code == 200
    missing = client.get("/api/accounts/Not%20A%20Company")
    assert missing.status_code == 404
