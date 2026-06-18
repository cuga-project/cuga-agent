"""SDK policy tests for CugaSupervisor.

Mirrors ``test_sdk_policies.py`` to verify the supervisor exposes the same
policy management API as CugaAgent.
"""

import pytest
import pytest_asyncio
from langchain_core.tools import tool

from cuga import CugaSupervisor


@pytest_asyncio.fixture(autouse=True, scope="function")
async def clean_policy_storage():
    """Clean up policy storage before each test to ensure isolation."""
    supervisor = CugaSupervisor(agents={})

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


class TestSupervisorToolApprovalPolicy:
    @pytest.mark.asyncio
    async def test_tool_approval_policy_basic(self):
        supervisor = CugaSupervisor(agents={})

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
        supervisor = CugaSupervisor(agents={})

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
        supervisor = CugaSupervisor(agents={})

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
        supervisor = CugaSupervisor(agents={})

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
        supervisor = CugaSupervisor(agents={})

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
        supervisor = CugaSupervisor(agents={})

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
        supervisor = CugaSupervisor(agents={})

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
        supervisor = CugaSupervisor(agents={})
        assert await supervisor.policies.get("nonexistent_policy_id") is None

    @pytest.mark.asyncio
    async def test_policies_property_returns_same_manager(self):
        supervisor = CugaSupervisor(agents={})
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
