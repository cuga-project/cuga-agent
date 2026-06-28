from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from cuga.backend.cuga_graph.policy.models import AlwaysTrigger, IntentGuard, IntentGuardResponse, ToolGuide
from cuga.backend.server.main import app, require_auth


class FakeStorage:
    def __init__(self, policy):
        self.policy = policy

    async def get_policy(self, policy_id: str):
        if self.policy and self.policy.id == policy_id:
            return self.policy
        return None


class FakePolicySystem:
    def __init__(self, policy):
        self.storage = FakeStorage(policy)


def make_tool_guide(target_tools=None):
    return ToolGuide(
        id="tool_guide_1",
        name="Guide",
        description="Guide description",
        triggers=[AlwaysTrigger()],
        enabled=True,
        priority=1,
        target_tools=target_tools if target_tools is not None else ["book_flight"],
        target_apps=None,
        guide_content="Only book compliant flights.",
    )


def make_intent_guard():
    return IntentGuard(
        id="intent_guard_1",
        name="Intent Guard",
        description="Blocks unsafe intent",
        triggers=[AlwaysTrigger()],
        response=IntentGuardResponse(
            response_type="natural_language",
            content="This action is not allowed.",
            status_code=None,
        ),
        allow_override=False,
        priority=1,
        enabled=True,
    )


@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def patch_states(monkeypatch, live_policy, draft_policy=None):
    live_state = SimpleNamespace(
        policy_system=FakePolicySystem(live_policy),
        agent=SimpleNamespace(tool_provider=object(), _model=object()),
    )
    draft_state = SimpleNamespace(
        policy_system=FakePolicySystem(draft_policy if draft_policy is not None else live_policy),
        agent=SimpleNamespace(tool_provider=object(), _model=object()),
    )
    monkeypatch.setattr("cuga.backend.server.main.app_state", live_state)
    monkeypatch.setattr("cuga.backend.server.main.draft_app_state", draft_state)


def patch_generation(monkeypatch):
    fake_agent = object()
    build_agent = Mock(return_value=fake_agent)
    generate = AsyncMock(
        return_value={
            "status": "ok",
            "policy_id": "tool_guide_1",
            "results": [{"tool": "book_flight", "status": "ok"}],
            "tool_guards": {"book_flight": {"policy_code": "def guard(): pass"}},
            "config_synced": True,
        }
    )
    monkeypatch.setattr("cuga.backend.server.main.build_tool_guard_generation_agent", build_agent)
    monkeypatch.setattr("cuga.backend.server.main.generate_tool_guards_for_policy", generate)
    monkeypatch.setattr(
        "cuga.backend.server.main._sync_policy_to_config_store",
        AsyncMock(return_value=(True, None)),
    )
    return build_agent, generate


def test_generate_tool_guard_route_happy_path(client, monkeypatch):
    patch_states(monkeypatch, make_tool_guide())
    build_agent, generate = patch_generation(monkeypatch)

    response = client.post("/api/config/policies/tool_guide_1/tool-guards/generate")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["policy_id"] == "tool_guide_1"
    assert body["results"] == [{"tool": "book_flight", "status": "ok"}]
    assert body["config_synced"] is True
    build_agent.assert_called_once()
    generate.assert_called_once()


def test_generate_tool_guard_route_uses_draft_header(client, monkeypatch):
    live_policy = make_tool_guide(target_tools=["live_tool"])
    draft_policy = make_tool_guide(target_tools=["draft_tool"])
    patch_states(monkeypatch, live_policy, draft_policy)
    build_agent, generate = patch_generation(monkeypatch)

    response = client.post(
        "/api/config/policies/tool_guide_1/tool-guards/generate",
        headers={"X-Use-Draft": "true"},
    )

    assert response.status_code == 200
    assert build_agent.call_args.kwargs["policy_system"].storage.policy.target_tools == ["draft_tool"]
    assert generate.call_args.kwargs["policy_system"].storage.policy.target_tools == ["draft_tool"]


def test_generate_tool_guard_route_rejects_missing_policy(client, monkeypatch):
    patch_states(monkeypatch, live_policy=None)
    patch_generation(monkeypatch)

    response = client.post("/api/config/policies/missing/tool-guards/generate")

    assert response.status_code == 404
    assert response.json()["status"] == "error"


def test_generate_tool_guard_route_rejects_wrong_policy_type(client, monkeypatch):
    patch_states(monkeypatch, make_intent_guard())
    patch_generation(monkeypatch)

    response = client.post("/api/config/policies/intent_guard_1/tool-guards/generate")

    assert response.status_code == 400
    assert response.json()["status"] == "error"


@pytest.mark.parametrize("target_tools", [[], ["*"]])
def test_generate_tool_guard_route_rejects_no_concrete_tools(client, monkeypatch, target_tools):
    patch_states(monkeypatch, make_tool_guide(target_tools=target_tools))
    patch_generation(monkeypatch)

    response = client.post("/api/config/policies/tool_guide_1/tool-guards/generate")

    assert response.status_code == 400
    assert response.json() == {
        "status": "error",
        "message": "Select specific target tools to generate a guard",
    }


def test_generate_tool_guard_route_rejects_disabled_policy(client, monkeypatch):
    policy = make_tool_guide()
    policy.enabled = False
    patch_states(monkeypatch, policy)
    patch_generation(monkeypatch)

    response = client.post("/api/config/policies/tool_guide_1/tool-guards/generate")

    assert response.status_code == 400
    assert response.json() == {
        "status": "error",
        "message": "Policy 'tool_guide_1' is disabled",
    }
