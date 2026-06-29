import pytest

from cuga.backend.cuga_graph.policy.models import AlwaysTrigger, ToolGuide
from cuga.backend.server.tool_guard_generation import generate_tool_guards_for_policy


class FakePolicyStorage:
    def __init__(self, policy):
        self.policy = policy

    async def get_policy(self, policy_id: str):
        if self.policy and self.policy.id == policy_id:
            return self.policy
        return None


class FakePolicySystem:
    def __init__(self, policy):
        self.storage = FakePolicyStorage(policy)


class FakePoliciesManager:
    def __init__(self, *, fail_tool: str | None = None):
        self.fail_tool = fail_tool
        self.calls = []

    async def generate_tool_guard_examples(self, policy_id: str, target_tool: str):
        self.calls.append(("examples", policy_id, target_tool))
        if target_tool == self.fail_tool:
            raise RuntimeError("example generation failed")
        return [f"bad {target_tool}"], [f"good {target_tool}"]

    async def update_tool_guard(self, policy_id: str, tool_guards: dict):
        self.calls.append(("update", policy_id, tool_guards))
        return policy_id

    async def generate_tool_guard_code(self, policy_id: str, target_tool: str, app_name=None):
        self.calls.append(("code", policy_id, target_tool, app_name))
        return f"def guard_{target_tool}():\n    return True"


class FakeGenerationAgent:
    def __init__(self, policies_manager: FakePoliciesManager):
        self.policies = policies_manager


def make_tool_guide(target_tools=None):
    return ToolGuide(
        id="tool_guide_1",
        name="Guide",
        description="Guide description",
        triggers=[AlwaysTrigger()],
        enabled=True,
        priority=1,
        target_tools=target_tools if target_tools is not None else ["book_flight", "cancel_flight"],
        target_apps=None,
        guide_content="Only book compliant flights.",
    )


@pytest.mark.asyncio
async def test_generate_tool_guards_for_policy_calls_sdk_methods_in_order():
    policy = make_tool_guide()
    policies_manager = FakePoliciesManager()

    response = await generate_tool_guards_for_policy(
        policy_system=FakePolicySystem(policy),
        policy_id="tool_guide_1",
        generation_agent=FakeGenerationAgent(policies_manager),
    )

    assert response == {
        "status": "ok",
        "policy_id": "tool_guide_1",
        "results": [
            {"tool": "book_flight", "status": "ok"},
            {"tool": "cancel_flight", "status": "ok"},
        ],
    }
    assert policies_manager.calls == [
        ("examples", "tool_guide_1", "book_flight"),
        (
            "update",
            "tool_guide_1",
            {
                "book_flight": {
                    "violating_examples": ["bad book_flight"],
                    "compliance_examples": ["good book_flight"],
                }
            },
        ),
        ("code", "tool_guide_1", "book_flight", None),
        (
            "update",
            "tool_guide_1",
            {"book_flight": {"policy_code": "def guard_book_flight():\n    return True"}},
        ),
        ("examples", "tool_guide_1", "cancel_flight"),
        (
            "update",
            "tool_guide_1",
            {
                "cancel_flight": {
                    "violating_examples": ["bad cancel_flight"],
                    "compliance_examples": ["good cancel_flight"],
                }
            },
        ),
        ("code", "tool_guide_1", "cancel_flight", None),
        (
            "update",
            "tool_guide_1",
            {"cancel_flight": {"policy_code": "def guard_cancel_flight():\n    return True"}},
        ),
    ]


@pytest.mark.asyncio
async def test_generate_tool_guards_for_policy_returns_partial_failure_and_continues():
    policy = make_tool_guide()
    policies_manager = FakePoliciesManager(fail_tool="book_flight")

    response = await generate_tool_guards_for_policy(
        policy_system=FakePolicySystem(policy),
        policy_id="tool_guide_1",
        generation_agent=FakeGenerationAgent(policies_manager),
    )

    assert response["status"] == "ok"
    assert response["results"] == [
        {"tool": "book_flight", "status": "error", "message": "ToolGuard generation failed for this tool"},
        {"tool": "cancel_flight", "status": "ok"},
    ]
    assert ("examples", "tool_guide_1", "cancel_flight") in policies_manager.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("target_tools", [[], ["*"]])
async def test_generate_tool_guards_for_policy_rejects_no_concrete_tools(target_tools):
    policy = make_tool_guide(target_tools=target_tools)

    with pytest.raises(ValueError, match="specific target tools"):
        await generate_tool_guards_for_policy(
            policy_system=FakePolicySystem(policy),
            policy_id="tool_guide_1",
            generation_agent=FakeGenerationAgent(FakePoliciesManager()),
        )


@pytest.mark.asyncio
async def test_generate_tool_guards_for_policy_rejects_disabled_policy():
    policy = make_tool_guide()
    policy.enabled = False

    with pytest.raises(ValueError, match="disabled"):
        await generate_tool_guards_for_policy(
            policy_system=FakePolicySystem(policy),
            policy_id="tool_guide_1",
            generation_agent=FakeGenerationAgent(FakePoliciesManager()),
        )
