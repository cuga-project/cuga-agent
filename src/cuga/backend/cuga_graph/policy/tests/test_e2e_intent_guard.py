"""E2E test: Intent guard blocks execution in CugaLite graph."""

import pytest

from cuga.backend.cuga_graph.policy.models import (
    IntentGuard,
    IntentGuardResponse,
    KeywordTrigger,
    NaturalLanguageTrigger,
)

from .helpers import (
    setup_policy_storage,
    setup_llm_manager,
    setup_langfuse_tracing,
    setup_policy_system,
    setup_cuga_lite_graph,
    create_initial_state,
    create_graph_config,
    run_graph_execution,
    MinimalToolProvider,
)


@pytest.mark.asyncio
async def test_e2e_intent_guard_blocks_in_cuga_lite():
    """
    E2E Test: Intent guard blocks execution in CugaLite graph.

    No mocking - full integration test from policy storage to graph execution.
    """
    print("\n" + "=" * 80)
    print("E2E TEST 1: Intent Guard Blocking")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage
        print("\n📋 Step 1: Setting up policy storage")
        print("-" * 80)
        storage = await setup_policy_storage("e2e_test_intent_guard")
        print("  ✅ Created policy storage")

        # Step 2: Create intent guard policy
        print("\n📋 Step 2: Creating intent guard policy")
        print("-" * 80)
        intent_guard = IntentGuard(
            id="e2e_guard_delete",
            name="E2E Account Deletion Guard",
            description="Blocks account deletion requests",
            triggers=[
                KeywordTrigger(
                    value=["delete", "account"],
                    target="intent",
                    case_sensitive=False,
                ),
                NaturalLanguageTrigger(
                    value=["I want to delete my account"],
                    target="intent",
                    threshold=0.7,
                ),
            ],
            response=IntentGuardResponse(
                response_type="natural_language",
                content="❌ Account deletion is not allowed through this interface. Please contact support.",
            ),
            allow_override=False,
            priority=100,
            enabled=True,
        )
        print(f"  ✅ Created intent guard policy: {intent_guard.name}")

        # Step 3: Setup LLM and Langfuse
        print("\n📋 Step 3: Setting up LLM and tracing")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        langfuse_handler = setup_langfuse_tracing()

        if langfuse_handler:
            print("  ✅ Langfuse tracing enabled")
        else:
            print("  ℹ️  Langfuse not available (optional)")

        # Step 4: Initialize policy system
        print("\n📋 Step 4: Initializing policy system")
        print("-" * 80)
        policy_system = await setup_policy_system(storage, llm, [intent_guard])
        print("  ✅ Initialized policy system")

        # Step 5: Create tool provider and CugaLite graph
        print("\n📋 Step 5: Creating CugaLite graph")
        print("-" * 80)
        tool_provider = MinimalToolProvider()
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, [])
        print("  ✅ Created and compiled CugaLite graph")

        # Step 6: Create initial state and config
        print("\n📋 Step 6: Setting up execution")
        print("-" * 80)
        initial_state = create_initial_state(
            user_query="I want to delete my account",
            thread_id="e2e_test_intent",
        )
        config = create_graph_config("e2e_test_intent", policy_system, [], langfuse_handler)

        print(f"  User query: {initial_state.chat_messages[0].content}")
        print(f"  Thread ID: {initial_state.thread_id}")
        print("  ✅ Created initial state and config")

        # Step 7: Run graph execution
        print("\n📋 Step 7: Running graph execution")
        print("-" * 80)
        print("\n🚀 Running CugaLite graph...")
        result = await run_graph_execution(compiled_graph, initial_state, config, langfuse_handler)

        # Step 8: Verify results
        print("\n📋 Step 8: Verifying results")
        print("-" * 80)
        print(f"  Execution complete: {result['execution_complete']}")
        print(f"  Final answer: {result.get('final_answer', 'N/A')[:200]}...")

        # Check if policy metadata exists
        if result.get('cuga_lite_metadata'):
            print(f"  Policy blocked: {result['cuga_lite_metadata'].get('policy_blocked', False)}")
            print(f"  Policy name: {result['cuga_lite_metadata'].get('policy_name', 'N/A')}")

        # Assertions
        assert result["execution_complete"], "Execution should be complete"
        assert result.get("cuga_lite_metadata") is not None, "cuga_lite_metadata should be set"
        assert result["cuga_lite_metadata"].get("policy_blocked"), "Intent should be blocked"
        assert result["cuga_lite_metadata"].get("policy_id") == "e2e_guard_delete"
        assert "support" in result["final_answer"].lower()
        assert result.get("script") is None, "No code should be generated when blocked"

        print("\n✅ E2E Intent Guard Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()
