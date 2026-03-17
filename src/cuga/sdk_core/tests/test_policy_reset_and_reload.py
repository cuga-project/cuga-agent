"""
Test for policy database reset and reload functionality.

This test suite reproduces and verifies the bug where policies DB is not properly
reloaded when CugaAgent is rerun with reset_policy_storage=True.

BUG DESCRIPTION:
When creating a new CugaAgent instance with reset_policy_storage=True, old policies
from previous agent instances should be cleared from the database. However, the bug
causes old policies to persist, leading to:
- Duplicate policies
- Policies that should have been deleted still existing
- Inconsistent state between expected and actual policies

These tests verify that:
1. reset_policy_storage=True properly clears all existing policies
2. New policies are correctly added after reset
3. Policies persist when reset_policy_storage=False (default behavior)
4. Auto-loaded policies work correctly with reset
"""

import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from loguru import logger  # noqa: E402
import uuid  # noqa: E402

from cuga.sdk import CugaAgent  # noqa: E402
from langchain_core.tools import tool  # noqa: E402


@tool
def test_tool_1() -> str:
    """A simple test tool 1."""
    return "test tool 1 executed"


@tool
def test_tool_2() -> str:
    """A simple test tool 2."""
    return "test tool 2 executed"


@tool
def test_tool_3() -> str:
    """A simple test tool 3."""
    return "test tool 3 executed"


@tool
def persistent_tool() -> str:
    """A persistent test tool."""
    return "persistent tool executed"


@pytest_asyncio.fixture
async def unique_test_db():
    """Fixture to provide a unique database file path for each test."""
    import os

    # Create unique database file path
    test_id = str(uuid.uuid4())[:8]
    db_file = f"./policy_db_test_{test_id}.db"

    # Cleanup before test (just in case)
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
            logger.info(f"Cleaned up existing database: {db_file}")
        except Exception as e:
            logger.warning(f"Could not remove database before test: {e}")

    yield db_file

    # Cleanup after test
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
            logger.info(f"Cleaned up database after test: {db_file}")
        except Exception as e:
            logger.warning(f"Could not remove database after test: {e}")


@pytest.mark.asyncio
async def test_policy_reset_and_reload_on_agent_rerun(unique_test_db):
    """
    Test that policies DB is correctly reloaded when CugaAgent is rerun
    with reset_policy_storage=True and reload_from_system=True.

    This test reproduces the bug where:
    1. Create agent with reset_policy_storage=True
    2. Add policies
    3. Create new agent instance with reset_policy_storage=True
    4. Verify old policies are cleared
    5. Load new policies
    6. Verify only new policies exist
    """
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable

    logger.info("\n" + "=" * 80)
    logger.info("Test: Policy Reset and Reload on Agent Rerun")
    logger.info("=" * 80)

    # Use unique .cuga folder to avoid conflicts with workspace .cuga
    test_id = str(uuid.uuid4())[:8]
    cuga_folder = f".cuga_test_{test_id}"

    # Create policy system with unique database
    policy_system1 = PolicyConfigurable()
    await policy_system1.initialize(policy_db_path=unique_test_db)

    # Step 1: Create first agent and add policies
    logger.info("\n📝 Step 1: Create first agent and add initial policies")
    agent1 = CugaAgent(
        tools=[test_tool_1, test_tool_2],
        cuga_folder=cuga_folder,
        policy_system=policy_system1,
        reset_policy_storage=True,
    )
    await agent1.initialize()

    # Add some initial policies (using ToolApproval which doesn't require embeddings)
    await agent1.policies.add_tool_approval(
        name="Initial Policy 1",
        description="First test policy",
        required_tools=["test_tool_1"],
    )

    await agent1.policies.add_tool_approval(
        name="Initial Policy 2",
        description="Second test policy",
        required_tools=["test_tool_2"],
    )

    # Verify policies were added
    policies1 = await agent1.policies.list()
    assert len(policies1) == 2, f"Expected 2 policies, got {len(policies1)}"
    policy_names_1 = {p['name'] for p in policies1}
    assert "Initial Policy 1" in policy_names_1
    assert "Initial Policy 2" in policy_names_1
    logger.info(f"✅ Step 1 complete: Added {len(policies1)} initial policies")

    # Cleanup agent1
    if hasattr(agent1, '_policy_system') and agent1._policy_system:
        if hasattr(agent1._policy_system, 'storage') and agent1._policy_system.storage:
            await agent1._policy_system.storage.disconnect()

    # Create policy system with same database for agent2
    policy_system2 = PolicyConfigurable()
    await policy_system2.initialize(policy_db_path=unique_test_db)

    # Step 2: Create second agent with reset_policy_storage=True
    logger.info("\n🔄 Step 2: Create second agent with reset_policy_storage=True")
    agent2 = CugaAgent(
        tools=[test_tool_3], cuga_folder=cuga_folder, policy_system=policy_system2, reset_policy_storage=True
    )
    await agent2.initialize()

    # Verify policies were cleared
    policies2_after_reset = await agent2.policies.list()
    logger.info(f"Policies after reset: {len(policies2_after_reset)}")
    if policies2_after_reset:
        logger.info(f"Found policies: {[p['name'] for p in policies2_after_reset]}")

    assert len(policies2_after_reset) == 0, (
        f"Expected 0 policies after reset, got {len(policies2_after_reset)}. "
        f"Policies: {[p['name'] for p in policies2_after_reset]}"
    )
    logger.info("✅ Step 2 complete: Policies were correctly cleared")

    # Step 3: Add new policies to agent2
    logger.info("\n📝 Step 3: Add new policies to agent2")
    await agent2.policies.add_tool_approval(
        name="New Policy 1",
        description="New test policy 1",
        required_tools=["test_tool_3"],
    )

    await agent2.policies.add_tool_approval(
        name="New Policy 2",
        description="New test policy 2",
        required_tools=["test_tool_3"],
    )

    # Verify new policies exist
    policies2_final = await agent2.policies.list()
    assert len(policies2_final) == 2, f"Expected 2 policies, got {len(policies2_final)}"
    policy_names_2 = {p['name'] for p in policies2_final}
    assert "New Policy 1" in policy_names_2
    assert "New Policy 2" in policy_names_2

    # Verify old policies DO NOT exist
    assert "Initial Policy 1" not in policy_names_2, "Old policy 'Initial Policy 1' should not exist"
    assert "Initial Policy 2" not in policy_names_2, "Old policy 'Initial Policy 2' should not exist"

    logger.info(f"✅ Step 3 complete: Added {len(policies2_final)} new policies")
    logger.info(f"   New policies: {list(policy_names_2)}")

    # Cleanup agent2
    if hasattr(agent2, '_policy_system') and agent2._policy_system:
        if hasattr(agent2._policy_system, 'storage') and agent2._policy_system.storage:
            await agent2._policy_system.storage.disconnect()

    logger.info("\n" + "=" * 80)
    logger.info("✅ Test passed: Policy reset and reload works correctly")
    logger.info("=" * 80)


@pytest.mark.asyncio
async def test_policy_reset_with_auto_load(unique_test_db):
    """
    Test that policies DB is correctly reset when auto_load_policies=True
    and reset_policy_storage=True.

    This test covers the scenario where:
    1. Create agent with policies folder and auto_load_policies=True
    2. Create new agent with reset_policy_storage=True and auto_load_policies=True
    3. Verify that old policies are cleared before new ones are loaded
    """
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
    import os

    logger.info("\n" + "=" * 80)
    logger.info("Test: Policy Reset with Auto Load")
    logger.info("=" * 80)

    # Use unique .cuga folder inside workspace to avoid sandbox issues with /var/folders
    test_id = str(uuid.uuid4())[:8]
    tmpdir = os.path.join(os.getcwd(), f".policy_test_tmp_{test_id}")
    os.makedirs(tmpdir, exist_ok=True)
    try:
        cuga_folder = os.path.join(tmpdir, f".cuga_test_{test_id}")
        os.makedirs(cuga_folder)

        # Create tool_approvals subfolder (required structure)
        tool_approvals_folder = os.path.join(cuga_folder, "tool_approvals")
        os.makedirs(tool_approvals_folder)

        # Create first policy file
        policy1_file = os.path.join(tool_approvals_folder, "policy1.md")
        policy1_content = (
            "---\n"
            "name: Auto-loaded Policy 1\n"
            "type: tool_approval\n"
            "enabled: true\n"
            "required_tools:\n"
            "- test_tool_1\n"
            "---\n\n"
            "## Description\n"
            "First auto-loaded policy\n"
        )
        with open(policy1_file, "w") as f:
            f.write(policy1_content)

        # Create policy system with unique database
        policy_system1 = PolicyConfigurable()
        await policy_system1.initialize(policy_db_path=unique_test_db)

        # Step 1: Create first agent with auto_load_policies=True
        logger.info("\n📝 Step 1: Create first agent with auto_load_policies=True")
        agent1 = CugaAgent(
            tools=[test_tool_1],
            cuga_folder=cuga_folder,
            policy_system=policy_system1,
            auto_load_policies=True,
            reset_policy_storage=False,  # Don't reset on first run
        )
        await agent1.initialize()

        policies1 = await agent1.policies.list()
        assert len(policies1) == 1, f"Expected 1 policy, got {len(policies1)}"
        assert policies1[0]['name'] == "Auto-loaded Policy 1"
        logger.info("✅ Step 1 complete: Auto-loaded 1 policy")

        # Manually add another policy (simulating previous state)
        await agent1.policies.add_tool_approval(
            name="Manual Policy",
            description="Manually added policy",
            required_tools=["test_tool_1"],
        )

        policies1_with_manual = await agent1.policies.list()
        assert len(policies1_with_manual) == 2, f"Expected 2 policies, got {len(policies1_with_manual)}"
        logger.info("✅ Added manual policy, total: 2 policies")

        # Cleanup agent1
        if hasattr(agent1, '_policy_system') and agent1._policy_system:
            if hasattr(agent1._policy_system, 'storage') and agent1._policy_system.storage:
                await agent1._policy_system.storage.disconnect()

        # Step 2: Update policy file
        policy1_updated = (
            "---\n"
            "name: Auto-loaded Policy 1 Updated\n"
            "type: tool_approval\n"
            "enabled: true\n"
            "required_tools:\n"
            "  - test_tool_2\n"
            "---\n\n"
            "## Description\n"
            "Updated auto-loaded policy\n"
        )
        with open(policy1_file, "w") as f:
            f.write(policy1_updated)

        # Create policy system with same database for agent2
        policy_system2 = PolicyConfigurable()
        await policy_system2.initialize(policy_db_path=unique_test_db)

        # Step 3: Create second agent with reset_policy_storage=True
        logger.info("\n🔄 Step 2: Create second agent with reset_policy_storage=True")
        agent2 = CugaAgent(
            tools=[test_tool_2],
            cuga_folder=cuga_folder,
            policy_system=policy_system2,
            auto_load_policies=True,
            reset_policy_storage=True,  # Reset before loading
        )
        await agent2.initialize()

        # Verify old policies are gone and new ones are loaded
        policies2 = await agent2.policies.list()
        policy_names_2 = {p['name'] for p in policies2}

        logger.info(f"Policies after reset and reload: {len(policies2)}")
        logger.info(f"Policy names: {list(policy_names_2)}")

        # Should only have the updated policy from file
        assert len(policies2) == 1, f"Expected 1 policy, got {len(policies2)}"
        assert "Auto-loaded Policy 1 Updated" in policy_names_2
        assert "Manual Policy" not in policy_names_2, "Manual policy should be cleared"
        assert "Auto-loaded Policy 1" not in policy_names_2, "Old version should not exist"

        logger.info("✅ Step 2 complete: Reset and reload successful")

        # Cleanup agent2
        if hasattr(agent2, '_policy_system') and agent2._policy_system:
            if hasattr(agent2._policy_system, 'storage') and agent2._policy_system.storage:
                await agent2._policy_system.storage.disconnect()

        logger.info("\n" + "=" * 80)
        logger.info("✅ Test passed: Policy reset with auto load works correctly")
        logger.info("=" * 80)
    finally:
        import shutil

        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_policy_reload_without_reset(unique_test_db):
    """
    Test that policies persist when reset_policy_storage=False (default).

    This verifies the opposite case - policies should NOT be cleared
    when creating a new agent instance with reset_policy_storage=False.
    """
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable

    logger.info("\n" + "=" * 80)
    logger.info("Test: Policy Reload Without Reset")
    logger.info("=" * 80)

    # Use unique .cuga folder to avoid conflicts with workspace .cuga
    test_id = str(uuid.uuid4())[:8]
    cuga_folder = f".cuga_test_{test_id}"

    # Create policy system with unique database
    policy_system1 = PolicyConfigurable()
    await policy_system1.initialize(policy_db_path=unique_test_db)

    # Step 1: Create first agent and add policies
    logger.info("\n📝 Step 1: Create first agent and add policies")
    agent1 = CugaAgent(
        tools=[persistent_tool],
        cuga_folder=cuga_folder,
        policy_system=policy_system1,
        reset_policy_storage=False,
    )
    await agent1.initialize()

    await agent1.policies.add_tool_approval(
        name="Persistent Policy",
        description="Should persist across agent instances",
        required_tools=["persistent_tool"],
    )

    policies1 = await agent1.policies.list()
    assert len(policies1) == 1, f"Expected 1 policy, got {len(policies1)}"
    logger.info("✅ Step 1 complete: Added 1 policy")

    # Cleanup agent1
    if hasattr(agent1, '_policy_system') and agent1._policy_system:
        if hasattr(agent1._policy_system, 'storage') and agent1._policy_system.storage:
            await agent1._policy_system.storage.disconnect()

    # Create policy system with same database for agent2
    policy_system2 = PolicyConfigurable()
    await policy_system2.initialize(policy_db_path=unique_test_db)

    # Step 2: Create second agent WITHOUT reset
    logger.info("\n🔄 Step 2: Create second agent without reset_policy_storage")
    agent2 = CugaAgent(
        tools=[persistent_tool],
        cuga_folder=cuga_folder,
        policy_system=policy_system2,
        reset_policy_storage=False,
    )
    await agent2.initialize()

    # Verify policy persisted
    policies2 = await agent2.policies.list()
    assert len(policies2) == 1, f"Expected 1 policy to persist, got {len(policies2)}"
    assert policies2[0]['name'] == "Persistent Policy"
    logger.info("✅ Step 2 complete: Policy correctly persisted")

    # Cleanup agent2
    if hasattr(agent2, '_policy_system') and agent2._policy_system:
        if hasattr(agent2._policy_system, 'storage') and agent2._policy_system.storage:
            await agent2._policy_system.storage.disconnect()

    # Create policy system with same database for agent3
    policy_system3 = PolicyConfigurable()
    await policy_system3.initialize(policy_db_path=unique_test_db)

    # Step 3: Create third agent WITH reset
    logger.info("\n🔄 Step 3: Create third agent WITH reset_policy_storage=True")
    agent3 = CugaAgent(
        tools=[persistent_tool],
        cuga_folder=cuga_folder,
        policy_system=policy_system3,
        reset_policy_storage=True,
    )
    await agent3.initialize()

    # Verify policy was cleared
    policies3 = await agent3.policies.list()
    assert len(policies3) == 0, f"Expected 0 policies after reset, got {len(policies3)}"
    logger.info("✅ Step 3 complete: Policy correctly cleared with reset")

    # Cleanup agent3
    if hasattr(agent3, '_policy_system') and agent3._policy_system:
        if hasattr(agent3._policy_system, 'storage') and agent3._policy_system.storage:
            await agent3._policy_system.storage.disconnect()

    logger.info("\n" + "=" * 80)
    logger.info("✅ Test passed: Policy persistence and reset behavior works correctly")
    logger.info("=" * 80)
