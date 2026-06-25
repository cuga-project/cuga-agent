"""SDK policy tests for CugaSupervisor.

Mirrors ``test_sdk_policies.py`` for CRUD parity and adds invoke-level e2e tests
that verify each policy type is enforced during supervisor execution (intent guard,
playbook sub-agent orchestration, tool guide, tool approval, output formatter CRUD).
"""

import pytest
import pytest_asyncio
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from cuga import CugaAgent, CugaSupervisor
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable


@pytest_asyncio.fixture(autouse=True, scope="function")
async def clean_policy_storage():
    """Clean up policy storage before each test to ensure isolation."""
    supervisor = CugaSupervisor(
        agents={},
        auto_load_policies=False,
        filesystem_sync=False,
    )

    policies = await supervisor.policies.list()
    for policy in policies:
        await supervisor.policies.delete(policy["id"])

    yield

    policies = await supervisor.policies.list()
    for policy in policies:
        await supervisor.policies.delete(policy["id"])


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient"""
    return f"Email sent to {to} with subject '{subject}'"


@tool
def delete_record(record_id: str) -> str:
    """Delete a record from the database"""
    return f"Deleted record {record_id}"


@tool
def calculate_sum(a: int, b: int) -> int:
    """Calculate the sum of two numbers"""
    return a + b


@tool
def get_user_id(name: str) -> str:
    """Get the internal user ID for a given name."""
    if name.lower() == "alice":
        return "user_alice_99"
    return "unknown_user"


@tool
def get_user_account_value(user_id: str) -> int:
    """Get the account value for a specific user ID."""
    if user_id == "user_alice_99":
        return 1500
    return 0


def _isolated_supervisor(**kwargs) -> CugaSupervisor:
    """Supervisor with an isolated policy store (no auto-load from .cuga)."""
    return CugaSupervisor(
        auto_load_policies=False,
        reset_policy_storage=True,
        filesystem_sync=False,
        cuga_lite_max_steps=120,
        **kwargs,
    )


def _graph_values(supervisor: CugaSupervisor, thread_id: str) -> dict:
    state = supervisor.graph.get_state({"configurable": {"thread_id": thread_id}})
    return state.values if state else {}


def _onboarding_agents() -> dict[str, CugaAgent]:
    user_finder = CugaAgent(
        tools=[get_user_id],
        auto_load_policies=False,
        reset_policy_storage=True,
        filesystem_sync=False,
    )
    user_finder.description = "Agent that finds user IDs"

    account_manager = CugaAgent(
        tools=[get_user_account_value],
        auto_load_policies=False,
        reset_policy_storage=True,
        filesystem_sync=False,
    )
    account_manager.description = "Agent that finds account values"

    return {"user_finder": user_finder, "account_manager": account_manager}


class TestSupervisorToolApprovalPolicy:
    @pytest.mark.asyncio
    async def test_tool_approval_policy_basic(self):
        supervisor = _isolated_supervisor(agents={})

        policy_id = await supervisor.policies.add_tool_approval(
            name="Approve Delete Operations",
            required_tools=["delete_record"],
            approval_message="This will delete data. Please confirm.",
        )

        assert policy_id is not None
        assert policy_id.startswith("tool_approval_")

        policies = await supervisor.policies.list()
        assert len(policies) == 1
        assert policies[0]["name"] == "Approve Delete Operations"
        assert policies[0]["type"] == "tool_approval"


class TestSupervisorPlaybookPolicy:
    @pytest.mark.asyncio
    async def test_playbook_policy_with_keywords(self):
        supervisor = _isolated_supervisor(agents={})

        policy_id = await supervisor.policies.add_playbook(
            name="Customer Onboarding",
            content="# Customer Onboarding Guide\n\n1. Verify email",
            keywords=["onboard", "signup"],
            description="Guide for onboarding new customers",
        )

        assert policy_id is not None
        assert policy_id.startswith("playbook_")

        policies = await supervisor.policies.list()
        assert len(policies) == 1
        assert policies[0]["type"] == "playbook"


class TestSupervisorIntentGuardPolicy:
    @pytest.mark.asyncio
    async def test_intent_guard_with_keywords(self):
        supervisor = _isolated_supervisor(agents={})

        policy_id = await supervisor.policies.add_intent_guard(
            name="Block Delete Operations",
            keywords=["delete", "remove"],
            response="Deletion operations are not permitted in this system.",
        )

        assert policy_id is not None
        assert policy_id.startswith("intent_guard_")

        policies = await supervisor.policies.list()
        assert len(policies) == 1
        assert policies[0]["type"] == "intent_guard"


class TestSupervisorToolGuidePolicy:
    @pytest.mark.asyncio
    async def test_tool_guide_basic(self):
        supervisor = _isolated_supervisor(agents={})

        policy_id = await supervisor.policies.add_tool_guide(
            name="Email Security Guidelines",
            content="## Security Guidelines\n- Verify recipient email",
            target_tools=["send_email"],
        )

        assert policy_id is not None
        assert policy_id.startswith("tool_guide_")

        policies = await supervisor.policies.list()
        assert len(policies) == 1
        assert policies[0]["type"] == "tool_guide"


class TestSupervisorOutputFormatterPolicy:
    @pytest.mark.asyncio
    async def test_output_formatter_basic(self):
        supervisor = _isolated_supervisor(agents={})

        policy_id = await supervisor.policies.add_output_formatter(
            name="Summary Formatter",
            format_config="# Summary",
            format_type="markdown",
            keywords=["summary"],
        )

        assert policy_id is not None
        assert policy_id.startswith("output_formatter_")


class TestSupervisorPolicyManagement:
    @pytest.mark.asyncio
    async def test_list_multiple_policy_types(self):
        supervisor = _isolated_supervisor(agents={})

        await supervisor.policies.add_intent_guard(
            name="Guard 1",
            keywords=["delete"],
            response="Blocked",
        )
        await supervisor.policies.add_playbook(
            name="Playbook 1",
            content="# Content",
            keywords=["onboard"],
        )
        await supervisor.policies.add_tool_approval(
            name="Approval 1",
            required_tools=["delete_record"],
        )
        await supervisor.policies.add_tool_guide(
            name="Guide 1",
            content="# Guidelines",
            target_tools=["send_email"],
        )
        await supervisor.policies.add_output_formatter(
            name="Formatter 1",
            format_config="# Formatting",
            format_type="markdown",
            keywords=["format"],
        )

        policies = await supervisor.policies.list()
        assert len(policies) == 5

        policy_types = {p["type"] for p in policies}
        assert policy_types == {
            "intent_guard",
            "playbook",
            "tool_approval",
            "tool_guide",
            "output_formatter",
        }

    @pytest.mark.asyncio
    async def test_delete_policy(self):
        supervisor = _isolated_supervisor(agents={})

        policy_id = await supervisor.policies.add_intent_guard(
            name="Temporary Guard",
            keywords=["test"],
            response="Blocked",
        )

        assert len(await supervisor.policies.list()) == 1
        assert await supervisor.policies.delete(policy_id) is True
        assert len(await supervisor.policies.list()) == 0

    @pytest.mark.asyncio
    async def test_get_nonexistent_policy(self):
        supervisor = _isolated_supervisor(agents={})
        assert await supervisor.policies.get("nonexistent_policy_id") is None

    @pytest.mark.asyncio
    async def test_policies_property_returns_same_manager(self):
        supervisor = _isolated_supervisor(agents={})
        assert supervisor.policies is supervisor.policies


class TestSupervisorPolicyInitialization:
    @pytest.mark.asyncio
    async def test_initialize_honors_reset_policy_storage_without_auto_load(self, monkeypatch):
        """reset_policy_storage must trigger policy-system init even when auto-load is off.

        Parity with CugaAgent.initialize(); previously the guard only checked auto-load.
        """
        supervisor = CugaSupervisor(agents={}, auto_load_policies=False, reset_policy_storage=True)
        called = {"value": False}

        async def fake_ensure():
            called["value"] = True

        monkeypatch.setattr(supervisor.policies, "_ensure_policy_system", fake_ensure)
        await supervisor.initialize()
        assert called["value"] is True

    @pytest.mark.asyncio
    async def test_initialize_skips_when_no_auto_load_and_no_reset(self, monkeypatch):
        supervisor = CugaSupervisor(agents={}, auto_load_policies=False, reset_policy_storage=False)
        called = {"value": False}

        async def fake_ensure():
            called["value"] = True

        monkeypatch.setattr(supervisor.policies, "_ensure_policy_system", fake_ensure)
        await supervisor.initialize()
        assert called["value"] is False


class TestSupervisorPolicyContext:
    def test_create_context_from_supervisor_state(self):
        """Supervisor state uses input + supervisor_chat_messages for policy matching."""
        state = CugaSupervisorState(
            supervisor_chat_messages=[HumanMessage(content="delete all records")],
            input="delete all records",
            thread_id="ctx_test",
            url="",
        )
        context = PolicyConfigurable.create_context_from_state(state, {"configurable": {}})
        assert context.user_input == "delete all records"
        assert context.chat_messages == ["delete all records"]


class TestSupervisorPolicyE2E:
    """Invoke-level e2e tests: policies must affect supervisor runtime, not just CRUD."""

    @pytest.mark.asyncio
    async def test_e2e_intent_guard_blocks_invoke(self):
        worker = CugaAgent(
            tools=[delete_record],
            auto_load_policies=False,
            reset_policy_storage=True,
            filesystem_sync=False,
        )
        supervisor = _isolated_supervisor(agents={"worker": worker})

        await supervisor.policies.add_intent_guard(
            name="Block Delete Operations",
            keywords=["delete", "remove"],
            response="POLICY_BLOCK: Deletion operations are not permitted.",
        )

        result = await supervisor.invoke("delete all customer records")
        values = _graph_values(supervisor, result.thread_id)
        metadata = values.get("supervisor_metadata") or {}

        assert "POLICY_BLOCK" in result.answer
        assert metadata.get("policy_blocked") is True
        assert metadata.get("policy_type") == "intent_guard"
        assert values.get("selected_agents") == []

    @pytest.mark.asyncio
    async def test_e2e_playbook_orchestrates_sub_agents(self):
        supervisor = _isolated_supervisor(
            agents=_onboarding_agents(),
            special_instructions=(
                "When onboarding, delegate to user_finder first, then account_manager. "
                "Do not ask clarifying questions."
            ),
        )

        await supervisor.policies.add_playbook(
            name="Onboarding Playbook",
            content="""# Customer Onboarding
1. Delegate to user_finder to get Alice's user ID
2. Delegate to account_manager to get her account value using that user ID
""",
            keywords=["onboard"],
        )

        result = await supervisor.invoke(
            "Onboard Alice: find the user ID for Alice, then get her account value."
        )
        values = _graph_values(supervisor, result.thread_id)
        metadata = values.get("supervisor_metadata") or {}
        selected = values.get("selected_agents") or []
        delegation_count = (values.get("metrics") or {}).get("delegation_count", 0)

        assert metadata.get("policy_type") == "playbook"
        assert metadata.get("playbook_guidance") or metadata.get("playbook_guidance_added")
        assert len(selected) >= 1 or delegation_count >= 1
        assert result.error is None

    @pytest.mark.asyncio
    async def test_e2e_tool_guide_applies_on_invoke(self):
        user_finder = CugaAgent(
            tools=[get_user_id],
            auto_load_policies=False,
            reset_policy_storage=True,
            filesystem_sync=False,
        )
        user_finder.description = "Finds user IDs"
        supervisor = _isolated_supervisor(agents={"user_finder": user_finder})

        await supervisor.policies.add_tool_guide(
            name="Delegation Guide",
            content="Always include the full customer name in the delegation task.",
            target_tools=["delegate_to_user_finder"],
            keywords=["alice"],
        )

        result = await supervisor.invoke("Get Alice's user id")
        metadata = (_graph_values(supervisor, result.thread_id).get("supervisor_metadata")) or {}
        guides = metadata.get("guides") or []

        assert guides
        assert any(g.get("policy_name") == "Delegation Guide" for g in guides)

    @pytest.mark.asyncio
    async def test_e2e_tool_approval_requests_approval(self):
        from datetime import datetime

        from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
            ActionResponse,
            ActionType,
        )

        user_finder = CugaAgent(
            tools=[get_user_id],
            auto_load_policies=False,
            reset_policy_storage=True,
            filesystem_sync=False,
        )
        user_finder.description = "Finds user IDs"
        supervisor = _isolated_supervisor(agents={"user_finder": user_finder})

        await supervisor.policies.add_tool_approval(
            name="Approve Delegation",
            required_tools=["delegate_to_user_finder"],
            approval_message="Delegation requires approval.",
        )

        thread_id = "supervisor_tool_approval_e2e"
        config = {"configurable": {"thread_id": thread_id}}
        result = await supervisor.invoke(
            "Find Alice's user id using the user_finder agent",
            thread_id=thread_id,
        )
        values = _graph_values(supervisor, thread_id)
        metadata = values.get("supervisor_metadata") or {}
        steps_before = values.get("step_count", 0)

        assert metadata.get("policy_type") == "tool_approval"
        answer_lower = result.answer.lower()
        assert "approve" in answer_lower or "approval" in answer_lower or "paused" in answer_lower
        state_before = supervisor.graph.get_state(config)
        assert "WaitForResponse" in (state_before.next or ())

        hitl = values.get("hitl_action")
        assert hitl is not None
        if hasattr(hitl, "action_id"):
            hitl = hitl.model_dump()
        assert hitl["action_id"] == "tool_approval"
        assert hitl["return_to"] == "CugaSupervisor"
        tool_data = hitl["additional_data"]["tool"]
        assert "delegate_to_user_finder" in tool_data["required_tools"]
        assert tool_data.get("policy_name") == "Approve Delegation"
        assert tool_data.get("full_code")

        approval = ActionResponse(
            action_id="tool_approval",
            response_type=ActionType.CONFIRMATION,
            confirmed=True,
            timestamp=datetime.now().isoformat(),
            user_id=thread_id,
            session_id=thread_id,
        )
        await supervisor.invoke(None, thread_id=thread_id, action_response=approval)
        values_after = _graph_values(supervisor, thread_id)
        assert values_after.get("step_count", 0) > steps_before

    @pytest.mark.asyncio
    async def test_e2e_tool_approval_denial_cancels_execution(self):
        from datetime import datetime

        from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
            ActionResponse,
            ActionType,
        )

        user_finder = CugaAgent(
            tools=[get_user_id],
            auto_load_policies=False,
            reset_policy_storage=True,
            filesystem_sync=False,
        )
        user_finder.description = "Finds user IDs"
        supervisor = _isolated_supervisor(agents={"user_finder": user_finder})

        await supervisor.policies.add_tool_approval(
            name="Approve Delegation",
            required_tools=["delegate_to_user_finder"],
            approval_message="Delegation requires approval.",
        )

        thread_id = "supervisor_tool_approval_denial_e2e"
        config = {"configurable": {"thread_id": thread_id}}
        await supervisor.invoke(
            "Find Alice's user id using the user_finder agent",
            thread_id=thread_id,
        )
        values = _graph_values(supervisor, thread_id)
        steps_before = values.get("step_count", 0)
        delegation_before = (values.get("metrics") or {}).get("delegation_count", 0)

        denial = ActionResponse(
            action_id="tool_approval",
            response_type=ActionType.CONFIRMATION,
            confirmed=False,
            timestamp=datetime.now().isoformat(),
            user_id=thread_id,
            session_id=thread_id,
        )
        result = await supervisor.invoke(None, thread_id=thread_id, action_response=denial)
        values_after = _graph_values(supervisor, thread_id)

        assert "cancel" in result.answer.lower()
        assert values_after.get("execution_complete") is True
        assert values_after.get("hitl_action") is None
        assert values_after.get("step_count", 0) == steps_before
        assert (values_after.get("metrics") or {}).get("delegation_count", 0) == delegation_before
        state_after = supervisor.graph.get_state(config)
        assert not state_after.next or state_after.next == ()

    @pytest.mark.asyncio
    async def test_e2e_output_formatter_applies_on_invoke(self):
        supervisor = _isolated_supervisor(agents={})

        await supervisor.policies.add_output_formatter(
            name="Hello Formatter",
            format_config="FORMATTED_HELLO_RESPONSE",
            format_type="direct",
            keywords=["hello"],
        )

        result = await supervisor.invoke("hello", thread_id="supervisor_formatter_e2e")
        assert result.answer == "FORMATTED_HELLO_RESPONSE"

        metadata = (_graph_values(supervisor, "supervisor_formatter_e2e").get("supervisor_metadata")) or {}
        assert metadata.get("output_formatter_applied") is True
