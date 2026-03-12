"""
End-to-end tests for context summarization feature.

These tests verify that context summarization works correctly in the server streaming mode.
"""

import os
import unittest

from system_tests.e2e.base_test import BaseTestServerStream

# Set test environment
os.environ["CUGA_TEST_ENV"] = "true"
os.environ["DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED"] = "true"
os.environ["DYNACONF_POLICY__ENABLED"] = "false"


class TestContextSummarizationE2E(BaseTestServerStream):
    """
    End-to-end tests for context summarization feature.
    Tests the server streaming endpoint with context summarization enabled.
    """

    # Enable context summarization with low thresholds for testing
    test_env_vars = {
        "DYNACONF_FEATURES__CUGA_MODE": "fast",
        "DYNACONF_CONTEXT_SUMMARIZATION__ENABLED": "true",
        "DYNACONF_CONTEXT_SUMMARIZATION__TRIGGER_MESSAGES": "3",
        "DYNACONF_CONTEXT_SUMMARIZATION__KEEP_LAST_N_MESSAGES": "2",
    }

    async def test_context_summarization_basic(self):
        """
        Test basic context summarization with multiple conversation turns.

        This test verifies that:
        1. Agent can handle multiple conversation turns
        2. Context is maintained across messages
        3. Agent can answer questions about earlier context after summarization
        """
        import uuid

        thread_id = str(uuid.uuid4())
        print(f"\n=== Starting context summarization test with thread ID: {thread_id} ===")

        # Message 1: Establish context
        query1 = "My name is Alice and I live in New York."
        events1 = await self.run_task(query1, thread_id=thread_id)
        self._assert_answer_event(events1)
        print("✓ Message 1 completed")

        # Message 2: Add more context
        query2 = "I work as a software engineer at TechCorp."
        events2 = await self.run_task(query2, thread_id=thread_id)
        self._assert_answer_event(events2)
        print("✓ Message 2 completed")

        # Message 3: This should trigger summarization (threshold=3)
        query3 = "I enjoy hiking on weekends."
        events3 = await self.run_task(query3, thread_id=thread_id)
        self._assert_answer_event(events3)
        print("✓ Message 3 completed (summarization should have triggered)")

        # Message 4: Ask about earlier context (after summarization)
        query4 = "What's my name and where do I live?"
        events4 = await self.run_task(query4, thread_id=thread_id)
        self._assert_answer_event(events4, expected_keywords=["Alice", "New York"])
        print("✓ Message 4 completed - agent remembered context after summarization")

        print("\n✅ Context summarization basic test passed!")

    async def test_context_summarization_with_followup(self):
        """
        Test context summarization with follow-up questions.

        This test verifies that:
        1. Follow-up questions work correctly after summarization
        2. Agent maintains context across multiple follow-ups
        3. Summarization preserves enough context for coherent follow-ups
        """
        import uuid

        thread_id = str(uuid.uuid4())
        print(f"\n=== Starting follow-up test with thread ID: {thread_id} ===")

        # Message 1: Provide list of items
        query1 = "I have three tasks: write report, review code, and attend meeting."
        events1 = await self.run_task(query1, thread_id=thread_id)
        self._assert_answer_event(events1)
        print("✓ Message 1 completed")

        # Message 2: Add priority information
        query2 = "The report is highest priority, then the code review."
        events2 = await self.run_task(query2, thread_id=thread_id)
        self._assert_answer_event(events2)
        print("✓ Message 2 completed")

        # Message 3: Add timing (triggers summarization)
        query3 = "The meeting is at 2 PM."
        events3 = await self.run_task(query3, thread_id=thread_id)
        self._assert_answer_event(events3)
        print("✓ Message 3 completed (summarization triggered)")

        # Message 4: Follow-up about priorities (after summarization)
        query4 = "What's my highest priority task?"
        events4 = await self.run_task(query4, thread_id=thread_id)
        self._assert_answer_event(events4, expected_keywords=["report"])
        print("✓ Message 4 completed - agent remembered priorities after summarization")

        print("\n✅ Follow-up test passed!")


if __name__ == "__main__":
    unittest.main()

# Made with Bob
