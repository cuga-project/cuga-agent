"""The M3 calibration harness parses REST servers without importing them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "tool_search_calibration.py"

SERVER = '''
from fastapi import APIRouter, Query
app = APIRouter()

# Endpoint to get coaches who were players
@app.get("/v1/hockey/coaches_players", operation_id="get_coaches_who_were_players", summary="Coaches who previously played.")
async def get_coaches_who_were_players():
    return {}

@app.get("/v1/hockey/games", operation_id="list_games", summary="Games in a season.")
async def list_games(season: int = Query(...)):
    return {}
'''


def _module():
    spec = importlib.util.spec_from_file_location("tool_search_calibration", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def test_parse_server_builds_tools_and_comment_cases(tmp_path):
    calib = _module()
    (tmp_path / "hockey.py").write_text(SERVER)
    catalogue, cases = calib.load(tmp_path)

    names = [t.name for t in catalogue["hockey"]]
    assert names == ["hockey_get_coaches_who_were_players", "hockey_list_games"]
    assert catalogue["hockey"][0]._search_hint == "Coaches who previously played."
    # Only the commented endpoint yields a case; the "Endpoint to" prefix is stripped.
    assert [(c.query, c.gold) for c in cases] == [
        ("get coaches who were players", "hockey_get_coaches_who_were_players")
    ]

    runs = calib.evaluate(catalogue, cases, k=5)
    assert runs[0][2] == 1, "the gold tool ranks first"
    rows = calib.gate_grid(runs, 5, [10], [0.2])
    assert rows[0]["confident_precision"] in (0.0, 1.0)
