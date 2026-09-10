import unittest
import uuid

import pytest

from system_tests.e2e.base_crm_test import BaseCRMTestServerStream


class TestCRMContactsEmailWorkflowFindTools(BaseCRMTestServerStream):
    """Same CRM write+email workflow as ``crm_contacts_email_test.py``, with find_tools ON.

    The pair is deliberate: this class inherits the default
    ``advanced_features.shortlisting_tool_threshold`` (35), which the demo CRM tool count
    exceeds, so tool shortlisting/find_tools is exercised. The sibling test raises the
    threshold to disable it. Same query and same assertions in both, so a failure here
    points at the shortlister rather than the workflow.
    """

    async def asyncSetUp(self):
        self.thread_id = str(uuid.uuid4())
        print(f"\n=== Test thread ID: {self.thread_id} ===")

    @pytest.mark.stability
    async def test_crm_contacts_write_and_email_find_tools(self):
        query = """
Given the list of emails in contacts.txt, check which of these exist as contacts in our CRM system. For each match, retrieve the contact name and the associated account details, then write the full list of their accounts alongside their names details to a file registered-accounts.txt (sort contacts by descending account value). 

In addition send an email to my assistant requesting to schedule meetings with the 2 top accounts by annual revenue from these registered accounts.  Title of the email is "IMPORTANT - Please schedule meetings at the conference"). List the top 2 accounts by revenue and their contact details, but do not use attachments, and don't forget in the beginning of the email to explain all the steps you took."""

        all_events = await self.run_task(query, thread_id=self.thread_id)
        self._assert_answer_event(
            all_events,
            expected_keywords=[
                "Sarah",
                "Ruth",
                "gamma",
                "sigma",
            ],
        )


if __name__ == "__main__":
    unittest.main()
