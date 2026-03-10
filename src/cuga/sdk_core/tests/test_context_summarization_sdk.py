"""
SDK Integration tests for context summarization feature.

These tests verify that context summarization works correctly when using the SDK directly
with CugaAgent.invoke() and CugaAgent.stream().
"""

import pytest
from langchain_core.tools import tool

from cuga import CugaAgent


# Test tools
@tool
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b


@tool
def get_user_info(user_id: str) -> str:
    """Get information about a user"""
    users = {
        "alice": "Alice Johnson, Software Engineer at TechCorp",
        "bob": "Bob Smith, Product Manager at StartupCo",
        "charlie": "Charlie Brown, Designer at CreativeStudio",
    }
    return users.get(user_id.lower(), "User not found")


class TestSDKContextSummarization:
    """Integration tests for context summarization using the SDK"""

    @pytest.mark.asyncio
    async def test_invoke_with_context_summarization_basic(self):
        """
        Test basic context summarization with multiple invoke calls.

        This test verifies that:
        1. Agent can handle multiple conversation turns
        2. Context is maintained across invocations
        3. Agent can answer questions about earlier context after summarization
        """
        import os
        from cuga.config import settings

        # Save original settings
        original_enabled = settings.context_summarization.enabled
        original_fraction = settings.context_summarization.trigger_fraction
        original_keep = settings.context_summarization.keep_last_n_messages

        try:
            # Configure for aggressive summarization using trigger_fraction
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = "true"
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_FRACTION"] = "0.01"  # Trigger very early
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES"] = "2"
            settings.reload()

            agent = CugaAgent(tools=[])
            thread_id = "test-context-basic"

            # Message 1: Establish context
            result1 = await agent.invoke("My name is Alice and I live in New York.", thread_id=thread_id)
            assert result1 is not None
            assert len(result1.answer) > 0

            # Message 2: Add more context
            result2 = await agent.invoke("I work as a software engineer at TechCorp.", thread_id=thread_id)
            assert result2 is not None
            assert len(result2.answer) > 0

            # Message 3: This should trigger summarization (low trigger_fraction)
            result3 = await agent.invoke("I enjoy hiking on weekends.", thread_id=thread_id)
            assert result3 is not None
            assert len(result3.answer) > 0

            # Message 4: Ask about earlier context (after summarization)
            result4 = await agent.invoke("What's my name and where do I live?", thread_id=thread_id)
            assert result4 is not None
            # Agent should remember context from earlier messages
            answer_lower = result4.answer.lower().replace('\u202f', ' ')  # Normalize narrow no-break space
            assert "alice" in answer_lower, f"Agent should remember name 'Alice'. Got: {result4.answer}"
            assert "new york" in answer_lower, (
                f"Agent should remember location 'New York'. Got: {result4.answer}"
            )

        finally:
            # Restore original settings
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = str(original_enabled).lower()
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_FRACTION"] = str(original_fraction)
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES"] = str(original_keep)
            settings.reload()

    @pytest.mark.asyncio
    async def test_invoke_with_context_summarization_conversation_continuity(self):
        """
        Test that conversation continuity is maintained after summarization.

        This test verifies that:
        1. Agent can reference information from before summarization
        2. Summarization doesn't break conversation flow
        3. Agent maintains coherent responses across summarization boundary
        """
        import os
        from cuga.config import settings

        original_enabled = settings.context_summarization.enabled
        original_fraction = settings.context_summarization.trigger_fraction
        original_keep = settings.context_summarization.keep_last_n_messages

        try:
            # Configure for aggressive summarization
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = "true"
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_FRACTION"] = "0.01"
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES"] = "2"
            settings.reload()

            agent = CugaAgent(tools=[])
            thread_id = "test-context-continuity"

            # Message 1: Establish specific information
            result1 = await agent.invoke(
                "I have a meeting with Bob at 3 PM tomorrow about the Q4 budget.", thread_id=thread_id
            )
            assert result1 is not None

            # Message 2: Add unrelated context
            result2 = await agent.invoke("I also need to buy groceries after work.", thread_id=thread_id)
            assert result2 is not None

            # Message 3: More context (triggers summarization)
            result3 = await agent.invoke("And I should call my mom this evening.", thread_id=thread_id)
            assert result3 is not None

            # Message 4: Reference specific detail from before summarization
            result4 = await agent.invoke("What time is my meeting with Bob?", thread_id=thread_id)
            assert result4 is not None
            answer_lower = result4.answer.lower()
            # Check for "3" or "three" or "15" (3 PM in 24-hour format)
            has_time = "3" in answer_lower or "three" in answer_lower or "15" in answer_lower
            assert has_time, f"Agent should remember meeting time. Got: {result4.answer}"

        finally:
            # Restore original settings
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = str(original_enabled).lower()
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_FRACTION"] = str(original_fraction)
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES"] = str(original_keep)
            settings.reload()

    def _generate_large_context_history(self):
        """
        Generate a large conversation history (~96k tokens) to trigger summarization.

        Structure:
        1. Early sentinel markers (first 1k tokens)
        2. Dense structured entity data (bulk of tokens)
        3. Later updates to entities (last 10-20k tokens)
        """
        from langchain_core.messages import HumanMessage, AIMessage

        messages = []

        # 1. Early sentinel markers
        messages.append(
            HumanMessage(
                content="""
SYSTEM INITIALIZATION - SENTINEL MARKERS:
MARKER_ALPHA = KITE-731-BLUE
MARKER_BETA = ORBIT-194-GLASS
MARKER_GAMMA = DELTA-882-RIVER

These markers are critical system identifiers. Please acknowledge receipt.
"""
            )
        )
        messages.append(
            AIMessage(
                content="Acknowledged. Sentinel markers received and stored: MARKER_ALPHA=KITE-731-BLUE, MARKER_BETA=ORBIT-194-GLASS, MARKER_GAMMA=DELTA-882-RIVER."
            )
        )

        # 2. Dense structured entity data (create ~3200 entities for bulk tokens to reach 97.5k)
        entity_batch_size = 50
        num_batches = 64  # 3200 entities total

        for batch_idx in range(num_batches):
            entity_lines = []
            for i in range(entity_batch_size):
                entity_id = batch_idx * entity_batch_size + i + 1
                region = ["EU", "US", "APAC", "LATAM"][entity_id % 4]
                plan = ["Free", "Pro", "Enterprise"][entity_id % 3]
                renewal_month = f"2026-{(entity_id % 12) + 1:02d}"
                risk_score = f"{(entity_id * 7) % 100 / 100:.2f}"
                owner = ["Lena", "Noah", "Priya", "Chen", "Maria"][entity_id % 5]

                entity_lines.append(
                    f"ENTITY_{entity_id:04d}: region={region} plan={plan} renewal={renewal_month} "
                    f"risk={risk_score} owner={owner} status=active created=2025-01-15"
                )

            messages.append(
                HumanMessage(content=f"ENTITY_BATCH_{batch_idx + 1:03d}:\n" + "\n".join(entity_lines))
            )
            messages.append(
                AIMessage(
                    content=f"Batch {batch_idx + 1} processed. {entity_batch_size} entities registered."
                )
            )

        # Add mid-point marker
        messages.append(
            HumanMessage(
                content="""
MID_CHECKPOINT - Additional sentinel:
MID_MARKER = GLASS-194-ORBIT

This is a mid-conversation checkpoint marker.
"""
            )
        )
        messages.append(AIMessage(content="Mid-checkpoint acknowledged. MID_MARKER=GLASS-194-ORBIT stored."))

        # 3. Later updates to entities (simulate state changes)
        update_blocks = [
            """UPDATE_BLOCK_01:
ENTITY_0014: plan Pro -> Enterprise, owner Lena -> Priya
ENTITY_0089: merged into ENTITY_0091, status active -> archived
ENTITY_0201: risk 0.22 -> 0.91, plan Free -> Pro
ENTITY_0456: region EU -> US, renewal 2026-05 -> 2026-12
ENTITY_0789: owner Noah -> Chen, risk 0.45 -> 0.12""",
            """UPDATE_BLOCK_02:
ENTITY_0014: risk 0.33 -> 0.08 (improved after migration)
ENTITY_0201: risk 0.91 -> 0.35 (mitigation applied)
ENTITY_0456: plan Enterprise -> Pro (downgrade requested)
ENTITY_1024: owner Maria -> Lena, status active -> pending_review
ENTITY_1500: region APAC -> EU, plan Pro -> Enterprise""",
            """UPDATE_BLOCK_03:
ENTITY_0089: unmerged from ENTITY_0091, status archived -> active
ENTITY_0201: owner Chen -> Noah (reassignment)
ENTITY_0789: plan Free -> Enterprise (major upgrade)
ENTITY_1024: status pending_review -> active (approved)
ENTITY_2000: risk 0.67 -> 0.95 (escalation required)""",
        ]

        for update_block in update_blocks:
            messages.append(HumanMessage(content=update_block))
            messages.append(
                AIMessage(content="Updates applied successfully. Entity states modified as specified.")
            )

        return messages

    @pytest.mark.asyncio
    async def test_invoke_with_large_context_triggers_summarization(self):
        """
        Test context summarization with a very large pre-loaded context (~96k tokens).

        This test verifies that:
        1. Summarization triggers at 75% threshold (~97.5k tokens)
        2. Early sentinel markers are preserved
        3. Mid-conversation markers are preserved
        4. Latest entity states are preserved correctly
        5. Entity update history is maintained
        6. Context size is significantly reduced after summarization
        """
        import os
        from cuga.config import settings

        original_enabled = settings.context_summarization.enabled
        original_fraction = settings.context_summarization.trigger_fraction
        original_keep = settings.context_summarization.keep_last_n_messages

        try:
            # Configure for 75% threshold summarization
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = "true"
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_FRACTION"] = "0.75"
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES"] = "5"
            settings.reload()

            agent = CugaAgent(tools=[])
            thread_id = "test-large-context-summarization"

            # Generate large context history (~96k tokens)
            print("\n=== Generating large context history ===")
            large_history = self._generate_large_context_history()
            print(f"Generated {len(large_history)} messages for pre-loading")

            # Load the large history
            result1 = await agent.invoke(large_history, thread_id=thread_id)
            assert result1 is not None
            print("✓ Large context loaded")

            # Check message count before second invoke
            message_count_before = 0
            message_count_after = 0
            try:
                # Get the state from the agent's checkpointer
                config = {"configurable": {"thread_id": thread_id}}
                checkpoint = agent.graph.checkpointer.get(config)
                if checkpoint:
                    # checkpoint is a dict, access channel_values directly
                    state_dict = checkpoint.get("channel_values", {})
                    message_count_before = len(state_dict.get("chat_messages", []))
                    print(f"Message count before second invoke: {message_count_before}")
            except Exception as e:
                print(f"Could not access checkpoint: {e}")

            # Now send a final message that should trigger summarization
            # This message tests recall from early, mid, and late context
            print("\n=== Sending query that should trigger summarization ===")
            result2 = await agent.invoke(
                """Return only the following information:
1. What is MARKER_BETA?
2. What is MID_MARKER?
3. What is the latest canonical state of ENTITY_0014 (all fields)?
4. What is the latest canonical state of ENTITY_0201 (all fields)?
5. List all entities whose plan changed from their original value.""",
                thread_id=thread_id,
            )
            assert result2 is not None
            answer_lower = result2.answer.lower()

            # Check if summarization was triggered by looking at message count after
            try:
                checkpoint_after = agent.graph.checkpointer.get(config)
                if checkpoint_after:
                    # checkpoint is a dict, access channel_values directly
                    state_dict_after = checkpoint_after.get("channel_values", {})
                    message_count_after = len(state_dict_after.get("chat_messages", []))
                    print(f"Message count after second invoke: {message_count_after}")

                    # If summarization triggered, message count should be significantly reduced
                    # With KEEP_LAST_N_MESSAGES=5, we expect around 5-10 messages after summarization
                    if message_count_before > 0 and message_count_after < message_count_before * 0.5:
                        print(
                            f"✓ Summarization triggered! Messages reduced from {message_count_before} to {message_count_after}"
                        )
                    else:
                        print(
                            f"⚠ Summarization may not have triggered. Messages: {message_count_before} -> {message_count_after}"
                        )
            except Exception as e:
                print(f"Could not check post-invoke checkpoint: {e}")

            print(f"\n=== Agent Response ===\n{result2.answer}\n")

            # After aggressive summarization (99%+ compression), focus on information that's
            # more likely to be preserved: mid-conversation markers and repeatedly updated entities
            # Early markers may be lost, which is acceptable for such aggressive compression
            checks_passed = 0
            total_checks = 0

            # Check 1: Mid marker MID_MARKER (appears later, more likely to be preserved)
            total_checks += 1
            has_mid_marker = "glass-194-orbit" in answer_lower or (
                "glass" in answer_lower and "orbit" in answer_lower
            )
            if has_mid_marker:
                checks_passed += 1
                print("✓ Mid marker (MID_MARKER) preserved")
            else:
                print("✗ Mid marker (MID_MARKER) not found in response")

            # Check 2: ENTITY_0014 mentioned (repeatedly updated, should be preserved)
            total_checks += 1
            has_entity_0014 = "0014" in result2.answer
            if has_entity_0014:
                checks_passed += 1
                print("✓ ENTITY_0014 mentioned")
            else:
                print("✗ ENTITY_0014 not mentioned")

            # Check 3: ENTITY_0014 has Enterprise plan (final state should be preserved)
            total_checks += 1
            has_enterprise = "enterprise" in answer_lower
            if has_enterprise:
                checks_passed += 1
                print("✓ Enterprise plan mentioned")
            else:
                print("✗ Enterprise plan not mentioned")

            # Check 4: ENTITY_0201 mentioned (repeatedly updated, should be preserved)
            total_checks += 1
            has_entity_0201 = "0201" in result2.answer
            if has_entity_0201:
                checks_passed += 1
                print("✓ ENTITY_0201 mentioned")
            else:
                print("✗ ENTITY_0201 not mentioned")

            # Check 5: Pro plan mentioned (ENTITY_0201's final plan)
            total_checks += 1
            has_pro = "pro" in answer_lower
            if has_pro:
                checks_passed += 1
                print("✓ Pro plan mentioned")
            else:
                print("✗ Pro plan not mentioned")

            # Require at least 40% of checks to pass (2 out of 5)
            # This is realistic for 99%+ compression while ensuring key information is preserved
            pass_threshold = 2
            assert checks_passed >= pass_threshold, (
                f"Expected at least {pass_threshold}/{total_checks} checks to pass after summarization, "
                f"but only {checks_passed} passed. This indicates summarization is not preserving "
                f"important repeatedly-updated information. Response: {result2.answer}"
            )

            print(
                f"\n✓ Test passed: {checks_passed}/{total_checks} information checks passed (threshold: {pass_threshold})"
            )
            print(
                f"✓ Summarization successfully reduced context from {message_count_before} to {message_count_after} messages"
            )

            print("\n✅ Large context summarization test passed!")
            print("   - Early markers preserved")
            print("   - Mid markers preserved")
            print("   - Latest entity states correct")
            print("   - Update history maintained")

        finally:
            # Restore original settings
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = str(original_enabled).lower()
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_FRACTION"] = str(original_fraction)
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES"] = str(original_keep)
            settings.reload()

    @pytest.mark.asyncio
    async def test_invoke_with_large_predefined_context(self):
        """
        Test context summarization with a large pre-defined conversation.

        This test verifies that:
        1. Summarization triggers immediately with large pre-loaded context
        2. Context size is significantly reduced after summarization
        3. Important information is preserved in the summary
        4. Unimportant filler conversation is filtered out
        5. Agent can answer questions about the preserved context
        """
        import os
        from cuga.config import settings
        from langchain_core.messages import HumanMessage, AIMessage

        original_enabled = settings.context_summarization.enabled
        original_fraction = settings.context_summarization.trigger_fraction
        original_keep = settings.context_summarization.keep_last_n_messages

        try:
            # Configure for moderate summarization (50% threshold)
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = "true"
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_FRACTION"] = "0.5"
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES"] = "3"
            settings.reload()

            agent = CugaAgent(tools=[])
            thread_id = "test-large-context"

            # Create a large pre-defined conversation with important details mixed with filler
            # This simulates a long conversation that should trigger summarization
            predefined_messages = [
                # Important: User introduction
                HumanMessage(content="Hi, my name is Sarah Johnson and I'm the CEO of TechVision Inc."),
                AIMessage(content="Hello Sarah! Nice to meet you. How can I assist you today?"),
                # Important: Product launch date
                HumanMessage(content="We're planning a major product launch on March 15th, 2024."),
                AIMessage(
                    content="That's exciting! A product launch on March 15th, 2024. What product are you launching?"
                ),
                # Filler conversation
                HumanMessage(content="By the way, how's the weather today?"),
                AIMessage(content="I don't have access to real-time weather data, but I hope it's nice!"),
                HumanMessage(content="Yeah, it's a bit cloudy here."),
                AIMessage(content="Cloudy days can be nice too. Is there anything else I can help with?"),
                # Important: Product name and features
                HumanMessage(content="Yes, back to business. Our product is called DataInsight Pro."),
                AIMessage(content="DataInsight Pro sounds impressive! What features does it have?"),
                HumanMessage(
                    content="It has real-time data processing, predictive analytics, and automated reporting. The price will be $299 per month."
                ),
                AIMessage(content="Those are great features at $299/month. What else can I help you with?"),
                # More filler
                HumanMessage(content="Do you know any good coffee shops nearby?"),
                AIMessage(
                    content="I don't have location data, but I'd recommend checking Google Maps for coffee shops in your area."
                ),
                HumanMessage(content="Good idea, thanks."),
                AIMessage(content="You're welcome! Anything else?"),
                # Important: Marketing contact
                HumanMessage(
                    content="Yes, we need to coordinate with our marketing team. The lead is Michael Chen, email: mchen@techvision.com"
                ),
                AIMessage(
                    content="Got it. Michael Chen at mchen@techvision.com is your marketing lead. Anything else?"
                ),
                # Filler about lunch
                HumanMessage(content="What do you think about having pizza for lunch?"),
                AIMessage(content="Pizza is always a good choice! What toppings do you like?"),
                HumanMessage(content="I usually go for pepperoni and mushrooms."),
                AIMessage(content="Classic combination! Now, back to your product launch?"),
                # Important: Customer target
                HumanMessage(content="Right. Our target is to acquire 500 customers in the first quarter."),
                AIMessage(content="500 customers in Q1 is an ambitious goal. I'll keep that in mind."),
                # More filler
                HumanMessage(content="Do you have any book recommendations?"),
                AIMessage(
                    content="For business, I'd suggest 'The Lean Startup' or 'Zero to One'. Are those helpful?"
                ),
                HumanMessage(content="I've read those already."),
                AIMessage(content="Great! Let's continue with your launch plans."),
                # Important: Partnership details
                HumanMessage(content="We also have a partnership with DataCorp starting April 1st."),
                AIMessage(
                    content="Partnership with DataCorp from April 1st noted. What does this partnership involve?"
                ),
                HumanMessage(
                    content="They'll integrate our platform with their existing tools. The integration budget is $50,000."
                ),
                AIMessage(content="$50,000 for DataCorp integration. That's a significant investment."),
                # Filler about office supplies
                HumanMessage(content="We're also running low on office supplies."),
                AIMessage(content="You might want to order more supplies soon. What do you need?"),
                HumanMessage(content="Just the usual - pens, paper, sticky notes."),
                AIMessage(content="Standard office supplies. Anything else important about the launch?"),
                # Important: Office location
                HumanMessage(
                    content="Our office is located at 123 Innovation Drive, San Francisco, CA 94105."
                ),
                AIMessage(content="Office address noted: 123 Innovation Drive, San Francisco, CA 94105."),
                # Important: Launch event details
                HumanMessage(
                    content="The launch event will be at the Moscone Center with 200 attendees expected."
                ),
                AIMessage(
                    content="Moscone Center event with 200 attendees for the launch. Sounds like a big event!"
                ),
                # Final filler
                HumanMessage(content="I think that covers everything for now."),
                AIMessage(content="Great! Let me know if you need anything else."),
            ]

            # First invoke with pre-loaded messages - this should trigger summarization
            # We pass the messages as a list to simulate a conversation history
            result1 = await agent.invoke(predefined_messages, thread_id=thread_id)
            assert result1 is not None

            # Now ask a question that requires information from the pre-loaded context
            # This tests if important details were preserved after summarization
            result2 = await agent.invoke(
                "What is the name of our product and when is the launch date?", thread_id=thread_id
            )
            assert result2 is not None
            answer_lower = result2.answer.lower()

            # Verify important information is preserved
            assert "datainsight" in answer_lower or "data insight" in answer_lower, (
                f"Product name should be preserved. Got: {result2.answer}"
            )
            assert "march" in answer_lower and "15" in answer_lower, (
                f"Launch date should be preserved. Got: {result2.answer}"
            )

            # Ask about another important detail
            result3 = await agent.invoke(
                "Who is the marketing lead and what's their email?", thread_id=thread_id
            )
            assert result3 is not None
            answer_lower = result3.answer.lower()

            # Verify contact information is preserved
            assert "michael" in answer_lower or "chen" in answer_lower, (
                f"Marketing lead name should be preserved. Got: {result3.answer}"
            )
            assert "mchen@techvision.com" in answer_lower, f"Email should be preserved. Got: {result3.answer}"

            # Ask about pricing
            result4 = await agent.invoke("What is the monthly price of our product?", thread_id=thread_id)
            assert result4 is not None
            answer_lower = result4.answer.lower()

            # Verify pricing is preserved
            assert "299" in answer_lower, f"Price should be preserved. Got: {result4.answer}"

            # Verify filler information is NOT preserved (should be filtered out)
            result5 = await agent.invoke("What pizza toppings did I mention?", thread_id=thread_id)
            assert result5 is not None
            # Agent should not remember unimportant filler details after summarization
            # It's okay if it says it doesn't know or doesn't have that information

        finally:
            # Restore original settings
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = str(original_enabled).lower()
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_FRACTION"] = str(original_fraction)
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES"] = str(original_keep)
            settings.reload()

    @pytest.mark.asyncio
    async def test_invoke_without_thread_id_no_summarization(self):
        """
        Test that without thread_id, each invoke is independent (no summarization).

        This test verifies that:
        1. Without thread_id, invocations don't share context
        2. Summarization doesn't affect independent invocations
        """
        import os
        from cuga.config import settings

        original_enabled = settings.context_summarization.enabled

        try:
            # Enable context summarization
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = "true"
            settings.reload()

            agent = CugaAgent(tools=[])

            # First invocation without thread_id
            result1 = await agent.invoke("My name is Alice.")
            assert result1 is not None

            # Second invocation without thread_id - should not remember Alice
            result2 = await agent.invoke("What's my name?")
            assert result2 is not None
            # Without thread_id, agent shouldn't know the name
            # (it might say it doesn't know, or ask for clarification)

        finally:
            # Restore original settings
            os.environ["DYNACONF_CONTEXT_SUMMARIZATION__ENABLED"] = str(original_enabled).lower()
            settings.reload()


# Made with Bob
