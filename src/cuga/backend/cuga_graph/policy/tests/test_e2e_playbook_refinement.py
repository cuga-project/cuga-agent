"""E2E test: Playbook refinement based on user progress."""

import pytest

from cuga.backend.cuga_graph.policy.models import (
    Playbook,
    NaturalLanguageTrigger,
)
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
from cuga.config import settings


@pytest.mark.asyncio
async def test_e2e_playbook_refinement_with_progress():
    """
    E2E Test: Playbook refinement when user has already completed part of the playbook.

    Tests that when playbook_refine is enabled, the playbook is refined based on
    user's current progress in the conversation.
    """
    print("\n" + "=" * 80)
    print("E2E TEST: Playbook Refinement with User Progress")
    print("=" * 80)

    # Save original setting
    original_refine_setting = getattr(settings.policy, 'playbook_refine', False)

    storage = None
    try:
        # Enable playbook refinement for this test
        settings.policy.playbook_refine = True
        print(f"\n📋 Playbook refinement enabled: {settings.policy.playbook_refine}")

        # Step 1: Setup policy storage
        print("\n📋 Step 1: Setting up policy storage")
        print("-" * 80)
        storage = await setup_policy_storage("e2e_test_playbook_refine")
        print("  ✅ Created policy storage")

        # Step 2: Create playbook policy with multiple steps
        print("\n📋 Step 2: Creating playbook policy")
        print("-" * 80)
        playbook = Playbook(
            id="e2e_playbook_data_analysis",
            name="E2E Data Analysis Playbook",
            description="Guides user through data analysis workflow",
            triggers=[
                NaturalLanguageTrigger(
                    value=["User wants to analyze data"],
                    target="intent",
                    threshold=0.7,
                ),
            ],
            markdown_content="""# Data Analysis Workflow

## Steps:

1. **Load Data**
   - Import the dataset
   - Verify data structure
   - Check for missing values

2. **Clean Data**
   - Remove duplicates
   - Handle missing values
   - Standardize formats

3. **Analyze Data**
   - Calculate statistics (Avg spending, total spending, most spent category)
   - Identify patterns (spending trends, spending by category, spending by day of week)

4. **Generate Report**
   - Summarize findings
""",
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

                return [App(name="analytics")]

            async def get_all_tools(self):
                return []

            async def get_tools(self, app_name):
                return []

        tool_provider = MinimalToolProvider()
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, ["analytics"])
        print("  ✅ Created and compiled CugaLite graph")

        # Step 6: Create initial state with conversation history showing progress
        print("\n📋 Step 6: Setting up execution with conversation history")
        print("-" * 80)

        # Simulate user who has already completed steps 1 and 2
        from langchain_core.messages import HumanMessage

        # add sample data for spendings of last 3 days
        sample_data = {
            "spendings": [100, 200, 300],
            "date": ["2025-01-01", "2025-01-02", "2025-01-03"],
            "category": ["food", "transport", "entertainment"],
        }
        msg = f"I want to analyze my dataset i have already loaded and cleaned it, here is the sample data: {sample_data}"
        chat_messages = [
            HumanMessage(content=msg),
        ]

        initial_state = create_initial_state(
            user_query=msg,
            thread_id="e2e_test_playbook_refine",
            sub_task_app="analytics",
        )
        # Add conversation history to show progress
        initial_state.chat_messages = chat_messages

        config = create_graph_config(
            "e2e_test_playbook_refine", policy_system, ["analytics"], langfuse_handler
        )

        print(f"  User query: {initial_state.chat_messages[-1].content}")
        print(f"  Conversation history: {len(chat_messages)} messages")
        print(f"  Thread ID: {initial_state.thread_id}")
        print("  ✅ Created initial state with conversation history")

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

        playbook_guidance = metadata.get('playbook_guidance', '')
        playbook_content = metadata.get('playbook_content', '')

        print(f"  Playbook guidance length: {len(playbook_guidance)} chars")
        print(f"  Original playbook length: {len(playbook_content)} chars")
        print(f"  Guidance was refined: {playbook_guidance != playbook_content}")

        if playbook_guidance != playbook_content:
            print("\n  📝 Refined guidance preview:")
            print(f"  {playbook_guidance[:200]}...")

        final_answer = result.get('final_answer', '')
        if final_answer:
            print(f"  Final answer: {final_answer[:100]}...")

        # Assertions
        assert result.get("execution_complete"), "Execution should be complete"
        assert metadata.get("policy_matched"), "Policy should match"
        assert metadata.get("policy_type") == "playbook"
        assert metadata.get("playbook_guidance") is not None

        # Verify that playbook was refined (guidance should differ from original content)
        # The refined plan should focus on remaining steps (3 and 4) since user completed 1 and 2
        assert playbook_guidance != playbook_content, "Playbook should be refined based on user progress"
        assert "Analyze Data" in playbook_guidance or "Generate Report" in playbook_guidance, (
            "Refined plan should mention remaining steps"
        )

        # Verify that the final answer includes actual calculated values from sample data
        # Sample data: spendings = [100, 200, 300]
        # Expected: average = 200, total = 600
        assert final_answer, "Final answer should be present"
        final_answer_lower = final_answer.lower()

        # Check for average spending value (200)
        assert "200" in final_answer or "200.0" in final_answer, (
            f"Final answer should contain calculated average (200) from sample data. Got: {final_answer[:200]}"
        )

        # Check for total spending value (600)
        assert "600" in final_answer, (
            f"Final answer should contain calculated total (600) from sample data. Got: {final_answer[:200]}"
        )

        # Verify it mentions average/total spending
        assert (
            "avg" in final_answer_lower or "average" in final_answer_lower
        ) and "spending" in final_answer_lower, "Final answer should mention average spending"
        assert "total" in final_answer_lower and "spending" in final_answer_lower, (
            "Final answer should mention total spending"
        )

        print("\n✅ E2E Playbook Refinement Test PASSED")
        print("=" * 80)

    finally:
        # Restore original setting
        settings.policy.playbook_refine = original_refine_setting
        if storage:
            await storage.disconnect()
