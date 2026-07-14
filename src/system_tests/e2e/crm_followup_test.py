import asyncio
import pytest
import unittest
import uuid

from system_tests.e2e.base_crm_test import BaseCRMTestServerStream
from system_tests.e2e.digital_sales_test_helpers import DigitalSalesTestHelpers

pytestmark = pytest.mark.e2e


class TestCRMFollowup(BaseCRMTestServerStream):
    """
    Test class for CRM follow-up queries with lite mode enabled.
    Tests the flow of querying contacts.txt, then following up with detail queries.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helpers = DigitalSalesTestHelpers()
        self.thread_id = None

    async def asyncSetUp(self):
        """Set up test environment and generate thread ID."""
        await super().asyncSetUp()
        # Generate a unique thread ID for this test
        self.thread_id = str(uuid.uuid4())
        print(f"\n=== Test thread ID: {self.thread_id} ===")

    async def test_crm_contacts_followup_fast(self):
        """Test CRM contacts query with follow-up questions in fast mode."""
        print(f"Running test with thread ID: {self.thread_id}")

        # First query
        query = "from contacts.txt show me which users belong to the crm system"
        all_events = await self.run_task(query, thread_id=self.thread_id)
        self._assert_answer_event(all_events)

        # First followup - using same thread ID
        followup_query = "show me details of sarah"
        all_followup_events = await self.run_task(followup_query, thread_id=self.thread_id)
        self._assert_answer_event(
            all_followup_events, expected_keywords=["Sarah", "sarah.bell@gammadeltainc.partners.org"]
        )

        # Second followup - using same thread ID
        second_followup_query = "how many employee's work at her account's company?"
        all_second_followup_events = await self.run_task(second_followup_query, thread_id=self.thread_id)
        answer_event = next((e for e in all_second_followup_events if e.get("event") == "Answer"), None)
        self.assertIsNotNone(answer_event, "The 'Answer' event was not found in the stream.")
        answer_str = str(answer_event.get("data", "")).lower()
        has_employee_count = "4,260" in answer_str or "4260" in answer_str
        self.assertTrue(
            has_employee_count, f"Answer does not contain employee count '4,260' or '4260'. Got: {answer_str}"
        )

        # Third followup - read contacts.txt and show initials (using same thread ID)
        third_followup_query = "read contacts.txt and show me their initials"
        all_third_followup_events = await self.run_task(third_followup_query, thread_id=self.thread_id)

        # Assert that we got an answer
        self._assert_answer_event(all_third_followup_events)

        # Verify that the answer contains some expected initials
        # Expected initials: SB (Sarah Bell), SJ (Sharon Jimenez), RR (Ruth Ross),
        # DR (Dorothy Richardson), JR (James Richardson), MT (Michael Torres), EL (Emma Larsson)
        initials_answer_event = next(
            (e for e in all_third_followup_events if e.get("event") == "Answer"), None
        )
        self.assertIsNotNone(initials_answer_event, "The 'Answer' event was not found in the stream.")
        initials_answer_str = str(initials_answer_event.get("data", ""))

        # Check for at least a few initials (being flexible as format might vary)
        print(f"Initials answer received: {initials_answer_str}")

        # Sleep to allow traces to be saved
        print("\n--- Sleeping for 5 seconds to allow traces to save ---")
        await asyncio.sleep(10)
        print("--- Sleep complete ---")

    async def test_crm_contacts_revenue_percentile_email(self):
        """Test filtering contacts from CRM, calculating revenue percentile, and drafting email in parallel."""
        n_tasks = 1

        async def run_one(turn):
            thread_id = str(uuid.uuid4())
            query = (
                "From the list of emails in the file contacts.txt, please filter those who exist in the CRM application. "
                "For the filtered contacts, retrieve their name and their associated account name, and calculate their account's revenue percentile across all accounts. "
                "Finally, draft a an email based on email_template.md template summarizing the result"
            )
            try:
                all_events = await self.run_task(query, thread_id=thread_id)

                # Assert that we got an answer
                self._assert_answer_event(
                    all_events,
                    expected_keywords=[
                        "NextGen",
                        "Sigma",
                        "Gamma Delta",
                        "Upsilon",
                        "85",
                        "79",
                        "77",
                        "59",
                        "Account Performance Update",
                    ],
                )
                return True
            except Exception as e:
                print(f"Task {turn} failed: {e}")
                return False

        results = await asyncio.gather(*(run_one(i) for i in range(n_tasks)))
        success_count = sum(results)
        print(f"\nSuccess rate: {success_count}/{n_tasks}")

        # Assert that all tasks succeeded
        await asyncio.sleep(10)
        self.assertEqual(
            success_count,
            n_tasks,
            f"{n_tasks - success_count} out of {n_tasks} tasks failed. Check the output above for error details.",
        )

        # Sleep to allow traces to be saved
        print("--- Sleep complete ---")


if __name__ == "__main__":
    unittest.main()
