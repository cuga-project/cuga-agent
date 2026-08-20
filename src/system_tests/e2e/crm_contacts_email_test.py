import unittest
import uuid

import pytest

from system_tests.e2e.base_crm_test import BaseCRMTestServerStream


class TestCRMContactsEmailWorkflow(BaseCRMTestServerStream):
    """CRM write+email workflow with find_tools OFF.

    The raised thresholds below keep tool shortlisting disabled.
    ``crm_contacts_email_test_find_tools.py`` runs the same query with the default
    threshold so find_tools is exercised; keep the two in sync.
    """

    test_env_vars = {
        "DYNACONF_ADVANCED_FEATURES__SHORTLISTING_TOOL_THRESHOLD": "100",
        "DYNACONF_ADVANCED_FEATURES__LITE_MODE_TOOL_THRESHOLD": "40",
        "DYNACONF_POLICY__ENABLED": "false",
    }

    async def asyncSetUp(self):
        self.thread_id = str(uuid.uuid4())
        print(f"\n=== Test thread ID: {self.thread_id} ===")

    @pytest.mark.stability
    async def test_crm_contacts_write_and_email(self):
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
