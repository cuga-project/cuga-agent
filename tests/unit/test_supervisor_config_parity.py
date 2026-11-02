"""Startup, Manage and runtime rebuilds preserve supervisor config ownership."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.server.manage_routes import apply, config_routes
from cuga.config import settings

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("llm", [{}, {"model": "editor-model", "temperature": 0.9}])
@pytest.mark.parametrize("published", [False, True])
async def test_environment_managed_model_survives_config_apply(monkeypatch, llm, published):
    monkeypatch.setattr(settings.secrets, "force_env", True)
    monkeypatch.setenv("MODEL_NAME", "deployment-model")
    monkeypatch.setenv("MODEL_TEMPERATURE", "0.2")
    monkeypatch.delenv("CUGA_MANAGER_MODE", raising=False)
    state = SimpleNamespace(agent=None, policy_system=None)

    if published:
        await apply.apply_published_config(state, {"llm": llm})
    else:
        apply.apply_llm_to_state(state, llm)

    assert os.environ["MODEL_NAME"] == "deployment-model"
    assert os.environ["MODEL_TEMPERATURE"] == "0.2"
    assert state.current_llm is None


@pytest.mark.asyncio
@pytest.mark.parametrize("draft", [None, "1"])
async def test_manage_exposes_legacy_nested_instructions_without_changing_store(monkeypatch, draft):
    config = {
        "agent": {"kind": "supervisor"},
        "supervisor": {"special_instructions": "full\nmultiline instructions"},
    }
    monkeypatch.setattr(config_routes, "resolve_registered_agent_id", AsyncMock(return_value="cuga"))
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_config",
        AsyncMock(side_effect=lambda *_: (json.loads(json.dumps(config)), 1)),
    )
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_draft",
        AsyncMock(side_effect=lambda *_: json.loads(json.dumps(config))),
    )
    monkeypatch.setattr(config_routes, "merge_mcp_yaml_into_config", lambda _: None)

    response = await config_routes.get_manage_config(SimpleNamespace(), draft=draft, agent_id="cuga")

    assert json.loads(response.body)["config"]["special_instructions"] == "full\nmultiline instructions"
    assert "special_instructions" not in config
