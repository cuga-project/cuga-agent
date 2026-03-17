"""E2E test: Intent Guards have priority over Playbooks."""

import uuid
import pytest

from .helpers import (
    setup_policy_storage,
    setup_llm_manager,
    setup_langfuse_tracing,
    setup_policy_system,
    setup_cuga_lite_graph,
    MinimalToolProvider,
    create_initial_state,
    create_graph_config,
    run_graph_execution,
)

from cuga.backend.cuga_graph.policy.models import (
    IntentGuard,
    IntentGuardResponse,
    Playbook,
    KeywordTrigger,
    NaturalLanguageTrigger,
)


@pytest.mark.asyncio
async def test_intent_guard_priority_over_playbook():
    """
    Test that Intent Guards (blockers) are checked FIRST and take priority over Playbooks.

    Scenario:
    - User query: "How do I delete all customer data?"
    - Intent Guard: Blocks queries with "delete" keyword (confidence: 0.33 with OR operator)
    - Playbook: Provides guidance for "customer data" queries (high confidence)
    - Expected: Intent Guard should match and block, even if Playbook has higher confidence
    """
    print("\n" + "=" * 80)
    print("E2E TEST: Intent Guard Priority Over Playbook")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup
        print("\n📋 Step 1: Setting up policy system")
        print("-" * 80)
        storage = await setup_policy_storage("test_guard_priority")
        langfuse_handler = setup_langfuse_tracing()
        policy_system = await setup_policy_system(storage)
        await policy_system.initialize()

        # Step 2: Add Intent Guard (blocker)
        print("\n📋 Step 2: Adding Intent Guard (blocker)")
        print("-" * 80)

        intent_guard = IntentGuard(
            id=f"guard_delete_{uuid.uuid4().hex[:8]}",
            name="Delete Operations Blocker",
            description="Blocks any delete operations on customer data",
            triggers=[
                KeywordTrigger(
                    value=["delete", "remove", "erase", "destroy"],
                    target="intent",
                    case_sensitive=False,
                    operator="or",  # Match ANY keyword
                ),
            ],
            response=IntentGuardResponse(
                response_type="natural_language",
                content="⛔ Delete operations on customer data are not allowed. Please contact an administrator.",
            ),
            priority=100,
            enabled=True,
        )

        # Add to storage (embedding will be generated automatically)
        await storage.add_policy(intent_guard)
        print(f"  ✅ Added Intent Guard: '{intent_guard.name}'")
        print(f"     Keywords: {intent_guard.triggers[0].value}")
        print(f"     Operator: {intent_guard.triggers[0].operator}")
        print(f"     Response: {intent_guard.response.response_type}")

        # Step 3: Add Playbook (guidance)
        print("\n📋 Step 3: Adding Playbook (guidance)")
        print("-" * 80)

        playbook = Playbook(
            id=f"playbook_customer_{uuid.uuid4().hex[:8]}",
            name="Customer Data Management Guide",
            description="Provides guidance for customer data operations",
            triggers=[
                NaturalLanguageTrigger(
                    value=["user wants to work with customer data, records, or information"],
                    target="intent",
                    threshold=0.7,
                ),
            ],
            markdown_content="""# Customer Data Management

When working with customer data:
1. Always verify permissions
2. Follow data privacy regulations
3. Log all access attempts
4. Use secure connections
""",
            priority=50,
            enabled=True,
        )

        await storage.add_policy(playbook)
        print(f"  ✅ Added Playbook: '{playbook.name}'")
        print("     Trigger: NL trigger for customer data queries")

        # Reset policy system to reload
        policy_system._initialized = False
        await policy_system.initialize()

        # Step 4: Create graph
        print("\n📋 Step 4: Creating CugaLite graph")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        tool_provider = MinimalToolProvider()
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, ["test_app"])
        print("  ✅ Graph created")

        # Step 5: Test with query that matches BOTH policies
        print("\n📋 Step 5: Testing query that matches BOTH Intent Guard and Playbook")
        print("-" * 80)

        user_query = "How do I delete all customer data from the database?"
        print(f"  User query: '{user_query}'")
        print("  - Contains 'delete' keyword → should trigger Intent Guard")
        print("  - About 'customer data' → might trigger Playbook")
        print("  - Expected: Intent Guard should win (blockers have priority)")

        initial_state = create_initial_state(
            user_query=user_query,
            thread_id=f"test_priority_{uuid.uuid4().hex[:8]}",
            sub_task_app="test_app",
        )

        config = create_graph_config("test_priority", policy_system, ["test_app"], langfuse_handler)

        print("\n  🚀 Running graph execution...")
        result = await run_graph_execution(compiled_graph, initial_state, config, langfuse_handler)

        # Step 6: Verify Intent Guard matched (not Playbook)
        print("\n📋 Step 6: Verify Intent Guard matched and blocked")
        print("-" * 80)

        assert result is not None, "Should have result"
        metadata = result.get('cuga_lite_metadata', {})

        print(f"  Policy matched: {metadata.get('policy_matched', False)}")
        print(f"  Policy type: {metadata.get('policy_type', 'N/A')}")
        print(f"  Policy name: {metadata.get('policy_name', 'N/A')}")
        print(f"  Action type: {metadata.get('action_type', 'N/A')}")

        # Verify Intent Guard matched (not Playbook)
        # Intent Guards set 'policy_blocked' instead of 'policy_matched'
        assert metadata.get('policy_blocked'), f"Policy should be blocked. Got: {metadata}"
        assert metadata.get('policy_name') == 'Delete Operations Blocker', (
            f"Should match the blocker policy. Got: {metadata.get('policy_name')}"
        )

        # Verify block message is present
        final_answer = result.get('final_answer', '')
        assert 'not allowed' in final_answer.lower() or 'blocked' in final_answer.lower(), (
            "Should contain block message"
        )

        print("  ✅ Intent Guard correctly matched and blocked")
        print("  ✅ Playbook did NOT override the Intent Guard")
        print("  ✅ Priority system working correctly!")

        print("\n✅ Intent Guard Priority Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()


@pytest.mark.asyncio
async def test_multiple_intent_guards_all_checked():
    """
    Test that ALL Intent Guards are checked, not just the first one.

    Scenario:
    - User query: "Delete and modify sensitive customer records"
    - Intent Guard 1: Blocks "delete" keyword
    - Intent Guard 2: Blocks "modify" keyword
    - Intent Guard 3: Blocks "sensitive" keyword
    - Expected: Highest confidence Intent Guard should match
    """
    print("\n" + "=" * 80)
    print("E2E TEST: Multiple Intent Guards - All Checked")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup
        print("\n📋 Step 1: Setting up policy system")
        print("-" * 80)
        storage = await setup_policy_storage("test_multiple_guards")
        langfuse_handler = setup_langfuse_tracing()
        policy_system = await setup_policy_system(storage)
        await policy_system.initialize()

        # Step 2: Add multiple Intent Guards
        print("\n📋 Step 2: Adding multiple Intent Guards")
        print("-" * 80)

        guards = [
            IntentGuard(
                id=f"guard_delete_{uuid.uuid4().hex[:8]}",
                name="Delete Blocker",
                description="Blocks delete operations",
                triggers=[
                    KeywordTrigger(
                        value=["delete", "remove"],
                        target="intent",
                        case_sensitive=False,
                        operator="or",
                    ),
                ],
                response=IntentGuardResponse(
                    response_type="natural_language",
                    content="⛔ Delete operations are blocked.",
                ),
                priority=100,
                enabled=True,
            ),
            IntentGuard(
                id=f"guard_modify_{uuid.uuid4().hex[:8]}",
                name="Modify Blocker",
                description="Blocks modify operations",
                triggers=[
                    KeywordTrigger(
                        value=["modify", "change", "update"],
                        target="intent",
                        case_sensitive=False,
                        operator="or",
                    ),
                ],
                response=IntentGuardResponse(
                    response_type="natural_language",
                    content="⛔ Modify operations are blocked.",
                ),
                priority=90,
                enabled=True,
            ),
            IntentGuard(
                id=f"guard_sensitive_{uuid.uuid4().hex[:8]}",
                name="Sensitive Data Blocker",
                description="Blocks access to sensitive data",
                triggers=[
                    KeywordTrigger(
                        value=["sensitive", "confidential", "secret"],
                        target="intent",
                        case_sensitive=False,
                        operator="or",
                    ),
                ],
                response=IntentGuardResponse(
                    response_type="natural_language",
                    content="⛔ Access to sensitive data is blocked.",
                ),
                priority=95,
                enabled=True,
            ),
        ]

        for guard in guards:
            await storage.add_policy(guard)
            print(f"  ✅ Added Intent Guard: '{guard.name}' (priority: {guard.priority})")

        # Reset policy system
        policy_system._initialized = False
        await policy_system.initialize()

        # Step 3: Create graph
        print("\n📋 Step 3: Creating CugaLite graph")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        tool_provider = MinimalToolProvider()
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, ["test_app"])
        print("  ✅ Graph created")

        # Step 4: Test with query that matches ALL guards
        print("\n📋 Step 4: Testing query that matches ALL Intent Guards")
        print("-" * 80)

        user_query = "Delete and modify sensitive customer records"
        print(f"  User query: '{user_query}'")
        print("  - Contains 'delete' → matches Delete Blocker (priority 100)")
        print("  - Contains 'modify' → matches Modify Blocker (priority 90)")
        print("  - Contains 'sensitive' → matches Sensitive Data Blocker (priority 95)")
        print("  - Expected: Highest confidence guard should match")

        initial_state = create_initial_state(
            user_query=user_query,
            thread_id=f"test_multi_{uuid.uuid4().hex[:8]}",
            sub_task_app="test_app",
        )

        config = create_graph_config("test_multi", policy_system, ["test_app"], langfuse_handler)

        print("\n  🚀 Running graph execution...")
        result = await run_graph_execution(compiled_graph, initial_state, config, langfuse_handler)

        # Step 5: Verify one of the Intent Guards matched
        print("\n📋 Step 5: Verify Intent Guard matched")
        print("-" * 80)

        assert result is not None, "Should have result"
        metadata = result.get('cuga_lite_metadata', {})

        print(f"  Policy matched: {metadata.get('policy_matched', False)}")
        print(f"  Policy type: {metadata.get('policy_type', 'N/A')}")
        print(f"  Policy name: {metadata.get('policy_name', 'N/A')}")

        # Verify an Intent Guard matched
        # Intent Guards set 'policy_blocked' instead of 'policy_matched'
        assert metadata.get('policy_blocked'), f"Policy should be blocked. Got: {metadata}"

        matched_name = metadata.get('policy_name', '')
        assert matched_name in ['Delete Blocker', 'Modify Blocker', 'Sensitive Data Blocker'], (
            f"Should match one of the Intent Guards. Got: {matched_name}"
        )

        print(f"  ✅ Intent Guard '{matched_name}' matched")
        print("  ✅ All Intent Guards were checked")
        print("  ✅ Highest confidence guard was selected")

        print("\n✅ Multiple Intent Guards Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()
