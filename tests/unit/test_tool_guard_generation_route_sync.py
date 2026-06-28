"""
Integration tests for the config-store sync path of the tool-guard generation route.

These tests run the real generate_tool_guards_for_policy orchestrator against a
FakeGenerationAgent and an in-memory policy storage, then assert that the route
wrote the updated policy into the correct config-store collection
(draft vs. published) depending on the X-Use-Draft header.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from cuga.backend.cuga_graph.policy.models import AlwaysTrigger, ToolGuide
from cuga.backend.server.config_store import (
    load_config,
    load_draft,
    reset_config_db,
    save_config,
    save_draft,
)
from cuga.backend.server.main import app, require_auth


# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------


class FakePoliciesManager:
    """Minimal stand-in for the CugaAgent.policies API used during generation."""

    def __init__(self, policy: ToolGuide):
        self._policy = policy

    async def generate_tool_guard_examples(self, policy_id: str, target_tool: str):
        return [f"bad {target_tool}"], [f"good {target_tool}"]

    async def update_tool_guard(self, policy_id: str, tool_guards: dict):
        # Merge the incoming tool_guards into the in-memory policy so that
        # get_policy returns the updated state for the sync step.
        if self._policy.tool_guards is None:
            self._policy.tool_guards = {}
        for tool, guard_data in tool_guards.items():
            if tool not in self._policy.tool_guards:
                self._policy.tool_guards[tool] = {}
            self._policy.tool_guards[tool].update(guard_data)

    async def generate_tool_guard_code(self, policy_id: str, target_tool: str, app_name=None):
        return f"def guard_{target_tool}():\n    return True"


class FakeGenerationAgent:
    def __init__(self, policies_manager: FakePoliciesManager):
        self.policies = policies_manager


class FakePolicyStorage:
    def __init__(self, policy: ToolGuide):
        self._policy = policy

    async def get_policy(self, policy_id: str):
        if self._policy and self._policy.id == policy_id:
            return self._policy
        return None


class FakePolicySystem:
    def __init__(self, policy: ToolGuide):
        self.storage = FakePolicyStorage(policy)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def make_tool_guide() -> ToolGuide:
    return ToolGuide(
        id="guide_1",
        name="Flight Guide",
        description="Compliance guide for flight tools",
        triggers=[AlwaysTrigger()],
        enabled=True,
        priority=1,
        target_tools=["book_flight"],
        target_apps=None,
        guide_content="Only book compliant flights.",
    )


def _config_with_policy(policy: ToolGuide) -> dict:
    policy_dict = policy.model_dump()
    return {
        "policies": {
            "enablePolicies": True,
            "policies": [
                {
                    "id": policy_dict["id"],
                    "name": policy_dict["name"],
                    "description": policy_dict["description"],
                    "policy_type": policy_dict["type"],
                    "enabled": policy_dict.get("enabled", True),
                    "triggers": policy_dict.get("triggers", []),
                    "priority": policy_dict.get("priority", 50),
                    "target_tools": policy_dict.get("target_tools", []),
                    "target_apps": policy_dict.get("target_apps"),
                    "guide_content": policy_dict.get("guide_content", ""),
                    "tool_guards": policy_dict.get("tool_guards", {}),
                    "prepend": policy_dict.get("prepend", False),
                }
            ],
        }
    }


def _patch_states_and_generation(monkeypatch, policy: ToolGuide):
    """Wire app_state with a real FakePolicySystem and stub build_tool_guard_generation_agent."""
    policies_manager = FakePoliciesManager(policy)
    fake_agent = FakeGenerationAgent(policies_manager)

    live_state = SimpleNamespace(
        policy_system=FakePolicySystem(policy),
        agent=SimpleNamespace(tool_provider=object(), llm_config=None),
    )
    draft_state = SimpleNamespace(
        policy_system=FakePolicySystem(policy),
        agent=SimpleNamespace(tool_provider=object(), llm_config=None),
    )
    monkeypatch.setattr("cuga.backend.server.main.app_state", live_state)
    monkeypatch.setattr("cuga.backend.server.main.draft_app_state", draft_state)
    monkeypatch.setattr(
        "cuga.backend.server.main.build_tool_guard_generation_agent",
        Mock(return_value=fake_agent),
    )
    return live_state, draft_state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sync_writes_to_published_store_when_no_draft_header(client, monkeypatch):
    """Without X-Use-Draft the sync must update the published config, not the draft."""
    reset_config_db()

    policy = make_tool_guide()
    config = _config_with_policy(policy)
    asyncio.run(save_config(config, "cuga-default"))

    _patch_states_and_generation(monkeypatch, policy)

    response = client.post("/api/config/policies/guide_1/tool-guards/generate")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["config_synced"] is True

    published, version = asyncio.run(load_config(None, "cuga-default"))
    draft = asyncio.run(load_draft("cuga-default"))

    # Published store must contain the generated tool_guards
    policy_entry = published["policies"]["policies"][0]
    assert policy_entry["tool_guards"] != {}
    assert "book_flight" in policy_entry["tool_guards"]
    assert "policy_code" in policy_entry["tool_guards"]["book_flight"]

    # Draft store must NOT have been touched (still None since we never seeded it)
    assert draft is None


def test_sync_writes_to_draft_store_when_draft_header_set(client, monkeypatch):
    """With X-Use-Draft: true the sync must update the draft config, not create a new published version."""
    reset_config_db()

    policy = make_tool_guide()
    config = _config_with_policy(policy)
    asyncio.run(save_draft(config, "cuga-default"))

    _patch_states_and_generation(monkeypatch, policy)

    response = client.post(
        "/api/config/policies/guide_1/tool-guards/generate",
        headers={"X-Use-Draft": "true"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["config_synced"] is True

    draft = asyncio.run(load_draft("cuga-default"))
    published, _ = asyncio.run(load_config(None, "cuga-default"))

    # Draft store must contain the generated tool_guards
    policy_entry = draft["policies"]["policies"][0]
    assert "book_flight" in policy_entry["tool_guards"]
    assert "policy_code" in policy_entry["tool_guards"]["book_flight"]

    # Published store must NOT have been touched (still None since we never seeded it)
    assert published is None


def test_sync_failure_when_policy_missing_from_config(client, monkeypatch):
    """If the policy ID is absent from config store, generation succeeds but sync is reported as failed."""
    reset_config_db()

    policy = make_tool_guide()
    config = _config_with_policy(policy)
    config["policies"]["policies"][0]["id"] = "other_policy"
    asyncio.run(save_config(config, "cuga-default"))

    _patch_states_and_generation(monkeypatch, policy)

    response = client.post("/api/config/policies/guide_1/tool-guards/generate")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["config_synced"] is False
    assert body["sync_error"] == "Policy not found in configuration store"
    assert body["tool_guards"]["book_flight"]["policy_code"]
