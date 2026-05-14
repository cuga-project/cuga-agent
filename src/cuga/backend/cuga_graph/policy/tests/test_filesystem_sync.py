"""
Tests for Policy Filesystem Sync Feature

Tests validate:
- Auto-loading policies from .cuga folder on initialization
- Auto-saving policies to .cuga folder when created via SDK
- Bidirectional sync (FS -> DB, DB -> FS)
- Removal sync (delete from DB if removed from FS)
- Global settings integration (settings.toml)
- Per-agent override of global settings
"""

import os
import shutil
import tempfile
import uuid
import pytest
import pytest_asyncio
from langchain_core.tools import tool

from cuga import CugaAgent
from cuga.backend.cuga_graph.policy.filesystem_sync import PolicyFilesystemSync
from cuga.backend.cuga_graph.policy.models import (
    IntentGuard,
    Playbook,
    ToolGuide,
    ToolGuard,
    ToolApproval,
    OutputFormatter,
    KeywordTrigger,
    AlwaysTrigger,
    IntentGuardResponse,
)


@tool
def test_tool(input_data: str) -> str:
    """A simple test tool"""
    return f"Processed: {input_data}"


@pytest.fixture
def temp_cuga_folder():
    """Create a temporary .cuga folder for testing"""
    temp_dir = tempfile.mkdtemp()
    cuga_path = os.path.join(temp_dir, ".cuga")
    os.makedirs(cuga_path, exist_ok=True)

    yield cuga_path

    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture(autouse=True, scope="function")
async def clean_policy_storage():
    """Clean up policy storage before and after each test"""
    agent = CugaAgent(tools=[test_tool], filesystem_sync=False)

    policies = await agent.policies.list()
    for policy in policies:
        await agent.policies.delete(policy["id"])

    yield

    policies = await agent.policies.list()
    for policy in policies:
        await agent.policies.delete(policy["id"])


class TestFilesystemSyncBasics:
    """Test basic filesystem sync operations"""

    @pytest.mark.asyncio
    async def test_policy_filesystem_sync_initialization(self, temp_cuga_folder):
        """Test PolicyFilesystemSync initialization"""
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)

        assert fs_sync.cuga_folder == temp_cuga_folder
        assert os.path.exists(temp_cuga_folder)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "policy_factory,expected_folder,expected_content",
        [
            (
                lambda: IntentGuard(
                    id="test_guard_123",
                    name="Test Guard",
                    description="Test intent guard",
                    triggers=[KeywordTrigger(value=["delete", "remove"])],
                    response=IntentGuardResponse(
                        content="Operation blocked", response_type="natural_language"
                    ),
                ),
                "intent_guards",
                ["test_guard_123", "Test Guard", "intent_guard"],
            ),
            (
                lambda: Playbook(
                    id="test_playbook_123",
                    name="Test Playbook",
                    description="Test playbook",
                    triggers=[KeywordTrigger(value=["onboard"])],
                    markdown_content="# Onboarding Steps\n1. Step 1\n2. Step 2",
                ),
                "playbooks",
                ["test_playbook_123", "Test Playbook", "Onboarding Steps"],
            ),
            (
                lambda: ToolGuide(
                    id="test_guide_123",
                    name="Test Guide",
                    description="Test tool guide",
                    triggers=[AlwaysTrigger()],
                    target_tools=["test_tool"],
                    guide_content="## Guidelines\n- Be careful",
                    prepend=False,
                ),
                "tool_guides",
                None,
            ),
            (
                lambda: ToolApproval(
                    id="test_approval_123",
                    name="Test Approval",
                    description="Test tool approval",
                    approval_message="Approve this?",
                    required_tools=["test_tool"],
                    required_apps=[],
                ),
                "tool_approvals",
                None,
            ),
            (
                lambda: OutputFormatter(
                    id="test_formatter_123",
                    name="Test Formatter",
                    description="Test output formatter",
                    triggers=[KeywordTrigger(value=["format"])],
                    format_config="# Formatted output",
                    format_type="markdown",
                ),
                "output_formatters",
                None,
            ),
        ],
    )
    async def test_save_policy_to_filesystem(
        self, temp_cuga_folder, policy_factory, expected_folder, expected_content
    ):
        """Test saving different policy types to filesystem"""
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)
        policy = policy_factory()

        file_path = fs_sync.save_policy_to_file(policy)

        assert os.path.exists(file_path)
        assert expected_folder in file_path
        assert file_path.endswith(".md")

        if expected_content:
            with open(file_path, "r") as f:
                content = f.read()
                for expected in expected_content:
                    assert expected in content

    @pytest.mark.asyncio
    async def test_save_tool_guide_with_tool_guards_to_filesystem(self, temp_cuga_folder):
        """Test saving tool_guards in ToolGuide frontmatter."""
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)
        policy = ToolGuide(
            id="test_guide_with_guards",
            name="Guide With Guards",
            description="Test tool guide with per-tool guard configuration",
            triggers=[AlwaysTrigger()],
            target_tools=["test_tool"],
            target_apps=None,
            guide_content="## Guidelines\n- Be careful",
            tool_guards={
                "test_tool": ToolGuard(
                    violating_examples=["Deleting all records without confirmation"],
                    compliance_examples=["Delete one record after explicit confirmation"],
                    policy_code="def validate(call):\n    return True",
                )
            },
            prepend=False,
            priority=0,
            enabled=True,
        )

        file_path = fs_sync.save_policy_to_file(policy)

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "tool_guards:" in content
        assert "test_tool:" in content
        assert "Only allow safe usage" in content

    @pytest.mark.asyncio
    async def test_delete_policy_file(self, temp_cuga_folder):
        """Test deleting a policy file"""
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)

        policy = IntentGuard(
            id="test_guard_delete",
            name="Delete Me",
            description="Test deletion",
            triggers=[KeywordTrigger(value=["test"])],
            response=IntentGuardResponse(content="Blocked", response_type="natural_language"),
        )

        file_path = fs_sync.save_policy_to_file(policy)
        assert os.path.exists(file_path)

        success = fs_sync.delete_policy_file("test_guard_delete")
        assert success is True
        assert not os.path.exists(file_path)

    @pytest.mark.asyncio
    async def test_get_filesystem_policy_ids(self, temp_cuga_folder):
        """Test getting all policy IDs from filesystem"""
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)

        # Create multiple policies
        policy1 = IntentGuard(
            id="test_guard_1",
            name="Guard 1",
            description="Test 1",
            triggers=[KeywordTrigger(value=["test"])],
            response=IntentGuardResponse(content="Blocked", response_type="natural_language"),
        )

        policy2 = Playbook(
            id="test_playbook_1",
            name="Playbook 1",
            description="Test playbook",
            triggers=[KeywordTrigger(value=["test"])],
            markdown_content="# Test",
        )

        fs_sync.save_policy_to_file(policy1)
        fs_sync.save_policy_to_file(policy2)

        policy_ids = fs_sync.get_filesystem_policy_ids()

        assert len(policy_ids) == 2
        assert "test_guard_1" in policy_ids
        assert "test_playbook_1" in policy_ids


class TestSDKFilesystemSync:
    """Test filesystem sync integration with SDK"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "add_method,method_kwargs",
        [
            (
                "add_intent_guard",
                {"name": "Auto Save Guard", "keywords": ["delete"], "response": "Blocked"},
            ),
            (
                "add_playbook",
                {"name": "Auto Save Playbook", "content": "# Test Content", "keywords": ["test"]},
            ),
            (
                "add_tool_guide",
                {"name": "Auto Save Guide", "content": "## Guidelines", "target_tools": ["test_tool"]},
            ),
            (
                "add_tool_approval",
                {"name": "Auto Save Approval", "required_tools": ["test_tool"]},
            ),
            (
                "add_output_formatter",
                {
                    "name": "Auto Save Formatter",
                    "format_config": "# Formatted",
                    "format_type": "markdown",
                    "keywords": ["format"],
                },
            ),
        ],
    )
    async def test_auto_save_on_add_policy(self, temp_cuga_folder, add_method, method_kwargs):
        """Test that adding any policy type auto-saves to filesystem"""
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            filesystem_sync=True,
        )

        add_func = getattr(agent.policies, add_method)
        policy_id = await add_func(**method_kwargs)

        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)
        policy_ids = fs_sync.get_filesystem_policy_ids()

        assert policy_id in policy_ids

    @pytest.mark.asyncio
    async def test_auto_delete_from_filesystem(self, temp_cuga_folder):
        """Test that deleting a policy also removes it from filesystem"""
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            filesystem_sync=True,
        )

        policy_id = await agent.policies.add_intent_guard(
            name="Delete Me",
            keywords=["test"],
            response="Blocked",
        )

        # Verify it exists in filesystem
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)
        policy_ids_before = fs_sync.get_filesystem_policy_ids()
        assert policy_id in policy_ids_before

        # Delete via SDK
        await agent.policies.delete(policy_id)

        # Verify it's removed from filesystem
        policy_ids_after = fs_sync.get_filesystem_policy_ids()
        assert policy_id not in policy_ids_after

    @pytest.mark.asyncio
    async def test_filesystem_sync_disabled(self, temp_cuga_folder):
        """Test that filesystem sync can be disabled"""
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            filesystem_sync=False,
        )

        policy_id = await agent.policies.add_intent_guard(
            name="No Save",
            keywords=["test"],
            response="Blocked",
        )

        # Verify it's NOT in filesystem
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)
        policy_ids = fs_sync.get_filesystem_policy_ids()

        assert policy_id not in policy_ids


class TestAutoLoadPolicies:
    """Test auto-loading policies from .cuga folder"""

    @pytest.mark.asyncio
    async def test_auto_load_from_folder(self, temp_cuga_folder):
        """Test auto-loading policies when .cuga folder exists"""
        # Create policy files manually
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)

        policy = IntentGuard(
            id="preexisting_guard",
            name="Pre-existing Guard",
            description="Should be auto-loaded",
            triggers=[KeywordTrigger(value=["delete"])],
            response=IntentGuardResponse(content="Blocked", response_type="natural_language"),
        )

        fs_sync.save_policy_to_file(policy)

        # Create agent with auto_load enabled
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        # Verify policy was loaded
        policies = await agent.policies.list()
        policy_ids = [p["id"] for p in policies]

        assert "preexisting_guard" in policy_ids

    @pytest.mark.asyncio
    async def test_auto_load_multiple_policy_types(self, temp_cuga_folder):
        """Test auto-loading multiple policy types from folder"""
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)

        # Create different policy types
        guard = IntentGuard(
            id="guard_1",
            name="Guard",
            description="Test",
            triggers=[KeywordTrigger(value=["test"])],
            response=IntentGuardResponse(content="Blocked", response_type="natural_language"),
        )

        playbook = Playbook(
            id="playbook_1",
            name="Playbook",
            description="Test",
            triggers=[KeywordTrigger(value=["test"])],
            markdown_content="# Test",
        )

        guide = ToolGuide(
            id="guide_1",
            name="Guide",
            description="Test",
            triggers=[AlwaysTrigger()],
            target_tools=["*"],
            target_apps=None,
            guide_content="## Test",
            tool_guards={
                "test_tool": ToolGuard(
                    violating_examples=["bad_example()"],
                    compliance_examples=["good_example()"],
                    policy_code="def validate(call):\n    return True",
                )
            },
            prepend=False,
            priority=0,
            enabled=True,
        )

        fs_sync.save_policy_to_file(guard)
        fs_sync.save_policy_to_file(playbook)
        fs_sync.save_policy_to_file(guide)

        # Load them
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        policies = await agent.policies.list()

        assert len(policies) == 3
        policy_types = {p["type"] for p in policies}
        assert "intent_guard" in policy_types
        assert "playbook" in policy_types
        assert "tool_guide" in policy_types

        loaded_guide = await agent.policies.get("guide_1")
        assert loaded_guide is not None
        assert loaded_guide["policy"].tool_guards is not None
        assert "test_tool" in loaded_guide["policy"].tool_guards
        # ToolGuard no longer has description field - it's derived from ToolGuide

    @pytest.mark.asyncio
    async def test_auto_load_disabled(self, temp_cuga_folder):
        """Test that auto-load can be disabled"""
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)

        policy = IntentGuard(
            id="not_loaded",
            name="Not Loaded",
            description="Should not be loaded",
            triggers=[KeywordTrigger(value=["test"])],
            response=IntentGuardResponse(content="Blocked", response_type="natural_language"),
        )

        fs_sync.save_policy_to_file(policy)

        # Create agent with auto_load disabled
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=False,
            filesystem_sync=True,
        )

        policies = await agent.policies.list()
        policy_ids = [p["id"] for p in policies]

        assert "not_loaded" not in policy_ids


class TestBidirectionalSync:
    """Test bidirectional sync between filesystem and database"""

    @pytest.mark.asyncio
    async def test_validation_adds_fs_policies_to_db(self, temp_cuga_folder):
        """Test that validation sync adds filesystem-only policies to DB"""
        # Create policy in filesystem only
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)

        policy = IntentGuard(
            id="fs_only_policy",
            name="FS Only",
            description="Only in filesystem",
            triggers=[KeywordTrigger(value=["test"])],
            response=IntentGuardResponse(content="Blocked", response_type="natural_language"),
        )

        fs_sync.save_policy_to_file(policy)

        # Create agent with auto-load (triggers validation)
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        # Verify policy is now in DB
        policies = await agent.policies.list()
        policy_ids = [p["id"] for p in policies]

        assert "fs_only_policy" in policy_ids

    @pytest.mark.asyncio
    async def test_validation_saves_db_policies_to_fs(self, temp_cuga_folder):
        """Test that validation sync saves DB-only policies to filesystem"""
        # Create agent and add policy (without filesystem sync initially)
        agent_no_sync = CugaAgent(
            tools=[test_tool],
            filesystem_sync=False,
        )

        await agent_no_sync.policies.add_intent_guard(
            name="DB Only",
            keywords=["test"],
            response="Blocked",
            policy_id="db_only_policy",
        )

        # Now create new agent with filesystem sync enabled and trigger sync
        agent_with_sync = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        # Manually trigger sync to save DB-only policies to filesystem
        await agent_with_sync.policies.sync_from_filesystem()

        # Verify policy was saved to filesystem
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)
        policy_ids = fs_sync.get_filesystem_policy_ids()

        assert "db_only_policy" in policy_ids

    @pytest.mark.asyncio
    async def test_validation_removes_deleted_fs_policies_from_db(self, temp_cuga_folder):
        """Test that validation sync removes DB policies if deleted from filesystem"""
        # Create policy with filesystem sync
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        await agent.policies.add_intent_guard(
            name="To Be Deleted",
            keywords=["test"],
            response="Blocked",
            policy_id="to_be_deleted",
        )

        # Verify it's in both DB and filesystem
        policies = await agent.policies.list()
        assert any(p["id"] == "to_be_deleted" for p in policies)

        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)
        fs_ids = fs_sync.get_filesystem_policy_ids()
        assert "to_be_deleted" in fs_ids

        # Manually delete from filesystem
        fs_sync.delete_policy_file("to_be_deleted")

        # Create new agent (triggers validation sync)
        agent2 = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        # Verify policy was removed from DB
        policies = await agent2.policies.list()
        policy_ids = [p["id"] for p in policies]

        assert "to_be_deleted" not in policy_ids


class TestLoadFromFolder:
    """Test load_from_folder functionality"""

    @pytest.mark.asyncio
    async def test_load_from_folder_manual(self, temp_cuga_folder):
        """Test manually loading policies from folder"""
        # Create policy files
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)

        policy = Playbook(
            id="manual_load_playbook",
            name="Manual Load",
            description="Manually loaded",
            triggers=[KeywordTrigger(value=["test"])],
            markdown_content="# Manual Load Test",
        )

        fs_sync.save_policy_to_file(policy)

        # Create agent without auto-load
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=False,
            filesystem_sync=True,
        )

        # Manually load
        await agent.policies.load_from_folder(temp_cuga_folder)

        # Verify loaded
        policies = await agent.policies.list()
        policy_ids = [p["id"] for p in policies]

        assert "manual_load_playbook" in policy_ids

    @pytest.mark.asyncio
    async def test_load_from_folder_clear_existing(self, temp_cuga_folder):
        """Test loading from folder with clear_existing=True"""
        # Add policy without filesystem sync (DB only)
        agent_no_sync = CugaAgent(
            tools=[test_tool],
            filesystem_sync=False,
        )

        await agent_no_sync.policies.add_intent_guard(
            name="Existing Policy",
            keywords=["old"],
            response="Old",
            policy_id="existing_policy",
        )

        # Create new policy in filesystem
        fs_sync = PolicyFilesystemSync(cuga_folder=temp_cuga_folder)
        new_policy = Playbook(
            id="new_policy",
            name="New Policy",
            description="New",
            triggers=[KeywordTrigger(value=["new"])],
            markdown_content="# New",
        )
        fs_sync.save_policy_to_file(new_policy)

        # Now create agent with filesystem sync but disable auto_load
        # to prevent validation from saving existing_policy to filesystem
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            filesystem_sync=True,
            auto_load_policies=False,
        )

        # Load with clear_existing
        await agent.policies.load_from_folder(temp_cuga_folder, clear_existing=True)

        # Verify old policy is gone (was only in DB), new policy exists (from filesystem)
        policies = await agent.policies.list()
        policy_ids = [p["id"] for p in policies]

        assert "existing_policy" not in policy_ids
        assert "new_policy" in policy_ids


class TestGlobalSettings:
    """Test global settings integration"""

    @pytest.mark.asyncio
    async def test_agent_uses_default_settings(self):
        """Test that agent uses default settings from settings.toml"""
        # This test verifies the agent respects global defaults
        agent = CugaAgent(tools=[test_tool])

        # The agent should have used settings from config
        # We can't easily test the exact values without mocking,
        # but we can verify it initializes without errors
        assert agent is not None

    @pytest.mark.asyncio
    async def test_agent_overrides_settings(self, temp_cuga_folder):
        """Test that per-agent parameters override global settings"""
        # Create agent with explicit overrides
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            filesystem_sync=False,  # Override global setting
            auto_load_policies=False,  # Override global setting
        )

        assert agent.cuga_folder == temp_cuga_folder
        assert agent._filesystem_sync is False
        assert agent._auto_load_policies is False


class TestEdgeCases:
    """Test edge cases and error handling"""

    @pytest.mark.asyncio
    async def test_missing_cuga_folder(self):
        """Test behavior when .cuga folder doesn't exist"""
        non_existent = "/tmp/non_existent_cuga_folder_" + uuid.uuid4().hex

        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=non_existent,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        # Should not crash, just skip auto-load
        policies = await agent.policies.list()
        assert policies is not None

    @pytest.mark.asyncio
    async def test_empty_cuga_folder(self, temp_cuga_folder):
        """Test behavior with empty .cuga folder"""
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        policies = await agent.policies.list()
        assert len(policies) == 0

    @pytest.mark.asyncio
    async def test_multiple_agents_same_folder(self, temp_cuga_folder):
        """Test multiple agents using the same .cuga folder"""
        # Agent 1 creates a policy
        agent1 = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            filesystem_sync=True,
        )

        policy_id = await agent1.policies.add_intent_guard(
            name="Shared Policy",
            keywords=["test"],
            response="Blocked",
        )

        # Agent 2 should see the policy
        agent2 = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        policies = await agent2.policies.list()
        policy_ids = [p["id"] for p in policies]

        assert policy_id in policy_ids

    @pytest.mark.asyncio
    async def test_invalid_policy_file_ignored(self, temp_cuga_folder):
        """Test that invalid policy files are ignored during load"""
        # Create invalid file
        invalid_file = os.path.join(temp_cuga_folder, "intent_guards", "invalid.md")
        os.makedirs(os.path.dirname(invalid_file), exist_ok=True)

        with open(invalid_file, "w") as f:
            f.write("This is not a valid policy file")

        # Should not crash during load
        agent = CugaAgent(
            tools=[test_tool],
            cuga_folder=temp_cuga_folder,
            auto_load_policies=True,
            filesystem_sync=True,
        )

        # Should load successfully (ignoring invalid file)
        policies = await agent.policies.list()
        assert policies is not None
