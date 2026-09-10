import unittest
import uuid

import pytest

from system_tests.e2e.base_crm_test import BaseCRMTestServerStream


class TestCRMHF_Examples(BaseCRMTestServerStream):
    mode = "hf"

    async def asyncSetUp(self):
        self.thread_id = str(uuid.uuid4())
        print(f"\n=== Test thread ID: {self.thread_id} ===")

    @pytest.mark.stability
    async def test_filter_contacts_and_calculate_revenue_percentile(self):
        query = """From the list of emails in the file contacts.txt, please filter those who exist in the CRM application. For the filtered contacts, retrieve their name and their associated account name, and calculate their account's revenue percentile across all accounts. Finally, draft a an email based on email_template.md template summarizing the result and show it to me"""

        all_events = await self.run_task(query, thread_id=self.thread_id)
        self._assert_answer_event(
            all_events,
            expected_keywords=[
                "sarah",
                "dorothy",
                "ruth",
                "Account Performance Update - Q1 2026",
                "sharon",
            ],
        )

    @pytest.mark.stability
    async def test_get_top_n_accounts_revenue(self):
        query = "get the top 5 accounts by revenue"

        all_events = await self.run_task(query, thread_id=self.thread_id)
        self._assert_answer_event(
            all_events,
            expected_keywords=[
                "Sigma Systems",
                "Approved Technologies",
                "Chi Systems",
                "Profitable Inc",
                "Phi Chi Inc",
            ],
        )

    @pytest.mark.stability
    async def test_what_is_cuga(self):
        query = "What is CUGA?"

        all_events = await self.run_task(query, thread_id=self.thread_id)
        self._assert_answer_event(
            all_events,
            expected_keywords=["cuga", "configurable", "generalist", "agent"],
        )

    @pytest.mark.stability
    async def test_playbook_execution(self):
        query = "./cuga_workspace/cuga_playbook.md"

        all_events = await self.run_task(query, thread_id=self.thread_id)
        self._assert_answer_event(
            all_events,
            expected_keywords=["start", "middle", "right", "left"],
        )


if __name__ == "__main__":
    unittest.main()
