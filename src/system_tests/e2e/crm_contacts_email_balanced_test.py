import asyncio
import unittest
import uuid

from system_tests.e2e.base_crm_test import BaseCRMTestServerStream
from system_tests.e2e.digital_sales_test_helpers import DigitalSalesTestHelpers


class TestCRMContactsEmailWorkflowBalanced(BaseCRMTestServerStream):
    """
    Test class for CRM follow-up queries with file writing and email sending (balanced mode).
    Tests the flow of querying contacts.txt, retrieving CRM details, writing to file, and sending email.
    Uses lite_mode = false for balanced mode testing.
    """

    test_env_vars = {
        "DYNACONF_ADVANCED_FEATURES__LITE_MODE": "false",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helpers = DigitalSalesTestHelpers()
        self.thread_id = None

    async def asyncSetUp(self):
        """Set up test environment and generate thread ID."""
        await super().asyncSetUp()
        self.thread_id = str(uuid.uuid4())
        print(f"\n=== Test thread ID: {self.thread_id} ===")

    async def test_crm_contacts_write_and_email(self):
        """Test CRM contacts query with file writing and email sending."""
        print(f"Running test with thread ID: {self.thread_id}")

        query = """
Given the list of emails in contacts.txt, check which of these exist as contacts in our CRM system. For each match, retrieve the contact name and the associated account details, then write the full list of their accounts alongside their names details to a file registered-accounts.txt (sort contacts by descending account value). 

In addition send an email to my assistant requesting to schedule meetings with the 2 top accounts by annual revenue from these registered accounts.  Title of the email is "IMPORTANT - Please schedule meetings at the conference"). List the top 2 accounts by revenue and their contact details, but do not use attachments, and don't forget in the beginning of the email to explain all the steps you took."""

        all_events = await self.run_task(query, thread_id=self.thread_id)

        # Validate ground truth expectations
        self._assert_answer_event(
            all_events,
            expected_keywords=[
                "Sarah",
                "Ruth",
                "gamma",
                "sigma",
            ],
        )

        # Sleep to allow traces to be saved
        print("\n--- Sleeping for 10 seconds to allow traces to save ---")
        await asyncio.sleep(10)
        print("--- Sleep complete ---")


if __name__ == "__main__":
    unittest.main()
