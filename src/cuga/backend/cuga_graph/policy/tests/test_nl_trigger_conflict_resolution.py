"""E2E test: NL trigger conflict resolution between Playbook and Intent Guard."""

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
    NaturalLanguageTrigger,
)


@pytest.mark.asyncio
async def test_nl_trigger_conflict_playbook_vs_intent_guard():
    """
    Test that when both Playbook and Intent Guard have NL triggers with same priority
    and threshold, the LLM conflict resolution correctly selects the Playbook for
    "What is CUGA" query.

    Scenario:
    - Playbook: NL trigger "What is CUGA" (threshold 0.7, priority 50)
    - Intent Guard: NL trigger "What is ALTK" (threshold 0.7, priority 50)
    - User query: "what is CUGA..."
    - Expected: Playbook should match (not Intent Guard)
    - Validation: Check that playbook content with special word is present
    """
    print("\n" + "=" * 80)
    print("E2E TEST: NL Trigger Conflict Resolution - Playbook vs Intent Guard")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup
        print("\n📋 Step 1: Setting up policy system")
        print("-" * 80)
        storage = await setup_policy_storage("test_nl_conflict")
        langfuse_handler = setup_langfuse_tracing()
        policy_system = await setup_policy_system(storage)
        await policy_system.initialize()

        # Step 2: Add Playbook with NL trigger "What is CUGA"
        print("\n📋 Step 2: Adding Playbook with NL trigger 'What is CUGA'")
        print("-" * 80)

        playbook = Playbook(
            id=f"playbook_cuga_{uuid.uuid4().hex[:8]}",
            name="CUGA Information Playbook",
            description="Provides information about CUGA system",
            triggers=[
                NaturalLanguageTrigger(
                    value=["What is CUGA"],
                    target="intent",
                    threshold=0.7,
                ),
            ],
            markdown_content="""# CUGA Information Guide

## CUGA Information Guide

Respond to the user with the following information:
- CUGA is a powerful agentic system that helps with various tasks.
- Then thank him for asking about CUGA with the following message exactly:
  "Thank you for asking about CUGA. I'm happy to help!"
""",
            priority=50,
            enabled=True,
        )

        await storage.add_policy(playbook)
        print(f"  ✅ Added Playbook: '{playbook.name}'")
        print("     NL Trigger: 'What is CUGA'")
        print("     Threshold: 0.7")
        print(f"     Priority: {playbook.priority}")

        # Step 3: Add Intent Guard with NL trigger "What is ALTK"
        print("\n📋 Step 3: Adding Intent Guard with NL trigger 'What is ALTK'")
        print("-" * 80)

        intent_guard = IntentGuard(
            id=f"guard_altk_{uuid.uuid4().hex[:8]}",
            name="ALTK Information Blocker",
            description="Blocks queries about ALTK system",
            triggers=[
                NaturalLanguageTrigger(
                    value=["What is ALTK"],
                    target="intent",
                    threshold=0.7,
                ),
            ],
            response=IntentGuardResponse(
                response_type="natural_language",
                content="⛔ Information about ALTK is not available through this interface.",
            ),
            allow_override=False,
            priority=50,  # Same priority as playbook
            enabled=True,
        )

        await storage.add_policy(intent_guard)
        print(f"  ✅ Added Intent Guard: '{intent_guard.name}'")
        print("     NL Trigger: 'What is ALTK'")
        print("     Threshold: 0.7")
        print(f"     Priority: {intent_guard.priority}")

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

        # Step 5: Test with query that should match Playbook (not Intent Guard)
        print("\n📋 Step 5: Testing query 'what is CUGA...'")
        print("-" * 80)

        user_query = "what is CUGA and how does it work?"
        print(f"  User query: '{user_query}'")
        print("  - Should match Playbook with NL trigger 'What is CUGA'")
        print("  - Should NOT match Intent Guard with NL trigger 'What is ALTK'")
        print("  - Expected: Playbook should be selected via LLM conflict resolution")

        initial_state = create_initial_state(
            user_query=user_query,
            thread_id=f"test_nl_conflict_{uuid.uuid4().hex[:8]}",
            sub_task_app="test_app",
        )

        config = create_graph_config("test_nl_conflict", policy_system, ["test_app"], langfuse_handler)

        print("\n  🚀 Running graph execution...")
        result = await run_graph_execution(compiled_graph, initial_state, config, langfuse_handler)

        # Step 6: Verify Playbook matched (not Intent Guard)
        print("\n📋 Step 6: Verify Playbook matched and Intent Guard did not")
        print("-" * 80)

        assert result is not None, "Should have result"
        metadata = result.get('cuga_lite_metadata', {})

        print(f"  Policy matched: {metadata.get('policy_matched', False)}")
        print(f"  Policy blocked: {metadata.get('policy_blocked', False)}")
        print(f"  Policy type: {metadata.get('policy_type', 'N/A')}")
        print(f"  Policy name: {metadata.get('policy_name', 'N/A')}")
        print(f"  Action type: {metadata.get('action_type', 'N/A')}")

        # Verify Playbook matched (not Intent Guard)
        assert metadata.get('policy_matched'), f"Policy should be matched. Got: {metadata}"
        assert not metadata.get('policy_blocked'), "Should NOT be blocked (Intent Guard would block)"
        assert metadata.get('policy_name') == 'CUGA Information Playbook', (
            f"Should match the Playbook. Got: {metadata.get('policy_name')}"
        )
        assert metadata.get('policy_type') == 'playbook', (
            f"Should be playbook type. Got: {metadata.get('policy_type')}"
        )

        # Verify playbook content with special validation word is present
        final_answer = result.get('final_answer', '')
        print(f"\n  Final answer preview: {final_answer[:200]}...")

        assert 'happy to help' in final_answer.lower(), (
            f"Should contain playbook validation word 'happy to help'. Got answer: {final_answer[:500]}"
        )

        # Verify it mentions CUGA (from playbook, not ALTK)
        assert 'CUGA' in final_answer.upper(), "Should mention CUGA from the playbook"

        # Verify it does NOT contain the Intent Guard block message
        assert 'not available' not in final_answer.lower(), (
            "Should NOT contain Intent Guard block message 'not available'"
        )
        assert 'ALTK' not in final_answer.upper(), "Should NOT mention ALTK (Intent Guard trigger)"

        print("  ✅ Playbook correctly matched via NL trigger")
        print("  ✅ Intent Guard did NOT match (correct conflict resolution)")
        print("  ✅ Playbook content with validation word is present")
        print("  ✅ LLM conflict resolution working correctly!")

        print("\n✅ NL Trigger Conflict Resolution Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()
