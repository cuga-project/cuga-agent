"""Unknown / unpublished agents must not silently chat as cuga-default."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from cuga.backend.server import main as main_mod

pytestmark = pytest.mark.unit


def _request():
    request = MagicMock()
    request.app.state.draft_app_state = None
    return request


def test_unpublished_agent_does_not_fall_back_to_default():
    request = _request()
    with patch.object(main_mod, "app_state") as mock_state:
        mock_state.agent = object()
        mock_state.agent_graphs_cache = {}
        with patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(main_mod._resolve_stream_agent(request, "trip-supervisor", False))
    assert exc.value.status_code == 404
    assert "trip-supervisor" in exc.value.detail


def test_graph_build_error_does_not_fall_back_to_default():
    request = _request()
    with patch.object(main_mod, "app_state") as mock_state:
        mock_state.agent = object()
        mock_state.agent_graphs_cache = {}
        with patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=({"agent": {"kind": "single"}}, None),
        ):
            with patch(
                "cuga.backend.cuga_graph.graph.DynamicAgentGraph",
                side_effect=RuntimeError("boom"),
            ):
                with pytest.raises(HTTPException) as exc:
                    asyncio.run(main_mod._resolve_stream_agent(request, "trip-supervisor", False))
    assert exc.value.status_code == 500
    assert "trip-supervisor" in exc.value.detail
