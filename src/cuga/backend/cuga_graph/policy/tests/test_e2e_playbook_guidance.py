"""E2E test: Playbook guides execution in CugaLite graph."""

import pytest

from cuga.backend.cuga_graph.policy.models import Playbook, PlaybookStep, KeywordTrigger
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface

from .helpers import (
    setup_policy_storage,
    setup_llm_manager,
    setup_langfuse_tracing,
    setup_policy_system,
    setup_cuga_lite_graph,
    create_initial_state,
    create_graph_config,
    run_graph_execution,
)


@pytest.mark.asyncio
async def test_e2e_playbook_guides_execution_in_cuga_lite():
    """
    E2E Test: Playbook guides execution in CugaLite graph.

    No mocking - full integration test from policy storage to graph execution.
    """
    print("\n" + "=" * 80)
    print("E2E TEST 2: Playbook Guidance")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage
        print("\n📋 Step 1: Setting up policy storage")
        print("-" * 80)
        storage = await setup_policy_storage("e2e_test_playbook")
        print("  ✅ Created policy storage")

        # Step 2: Create playbook policy
        print("\n📋 Step 2: Creating playbook policy")
        print("-" * 80)
        playbook = Playbook(
            id="e2e_playbook_checkout",
            name="E2E Checkout Playbook",
            description="Guides user through checkout",
            triggers=[
                KeywordTrigger(
                    value=["checkout", "buy"],
                    target="intent",
                    case_sensitive=False,
                ),
            ],
            markdown_content="""# Checkout Process

## Steps:

1. **Add items to cart**
   - Navigate to products
   - Click "Add to Cart"

2. **Review cart**
   - Verify items
   - Apply discounts

3. **Complete purchase**
   - Enter payment
   - Confirm order
""",
            steps=[
                PlaybookStep(
                    step_number=1,
                    instruction="Add items to cart",
                    expected_outcome="Items in cart",
                    tools_allowed=["click", "navigate"],
                ),
                PlaybookStep(
                    step_number=2,
                    instruction="Review cart",
                    expected_outcome="Cart reviewed",
                    tools_allowed=["read"],
                ),
                PlaybookStep(
                    step_number=3,
                    instruction="Complete purchase",
                    expected_outcome="Order placed",
                    tools_allowed=["fill", "click"],
                ),
            ],
            priority=10,
            enabled=True,
        )
        print(f"  ✅ Created playbook policy: {playbook.name}")

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
        policy_system = await setup_policy_system(storage, llm, [playbook])
        print("  ✅ Initialized policy system")

        # Step 5: Create tool provider and CugaLite graph
        print("\n📋 Step 5: Creating CugaLite graph")
        print("-" * 80)

        class MinimalToolProvider(ToolProviderInterface):
            async def initialize(self):
                pass

            async def get_apps(self):
                from pydantic import BaseModel

                class App(BaseModel):
                    name: str
                    type: str = "api"

                return [App(name="shopping")]

            async def get_all_tools(self):
                return []

            async def get_tools(self, app_name):
                return []

        tool_provider = MinimalToolProvider()
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, ["shopping"])
        print("  ✅ Created and compiled CugaLite graph")

        # Step 6: Create initial state and config
        print("\n📋 Step 6: Setting up execution")
        print("-" * 80)
        initial_state = create_initial_state(
            user_query="Help me checkout and buy items",
            thread_id="e2e_test_playbook",
            sub_task_app="shopping",
        )
        config = create_graph_config("e2e_test_playbook", policy_system, ["shopping"], langfuse_handler)

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
        print(f"  Result keys: {list(result.keys())}")
        print(f"  Execution complete: {result.get('execution_complete', False)}")

        metadata = result.get('cuga_lite_metadata', {})
        print(f"  Policy matched: {metadata.get('policy_matched', False)}")
        print(f"  Policy type: {metadata.get('policy_type', 'N/A')}")
        print(f"  Policy name: {metadata.get('policy_name', 'N/A')}")
        print(f"  Playbook steps: {len(metadata.get('playbook_steps', []))}")
        if result.get('final_answer'):
            print(f"  Final answer: {result['final_answer'][:100]}...")

        # Check if playbook guidance was added to messages
        # Note: The guidance is injected into the model's input but not persisted in chat_messages
        playbook_guidance_added = metadata.get('playbook_guidance_added', False)
        print(f"  Playbook guidance added to model input: {playbook_guidance_added}")

        # Assertions
        assert result.get("execution_complete"), "Execution should be complete"
        assert metadata.get("policy_matched"), "Policy should match"
        assert metadata.get("policy_type") == "playbook"
        assert metadata.get("playbook_guidance") is not None
        assert len(metadata.get("playbook_steps", [])) == 3
        assert result["prepared_prompt"] is not None  # System prompt should exist
        assert metadata.get("playbook_guidance_added"), "Playbook guidance should be added to model input"

        print("\n✅ E2E Playbook Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()
