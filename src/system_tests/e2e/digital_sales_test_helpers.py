"""
Common test methods for digital sales test cases.
"""


class DigitalSalesTestHelpers:
    """
    Helper class containing common test methods for digital sales tests.
    """

    async def test_get_top_account_by_revenue_stream(self, test_instance, mode_suffix):
        """
        Test getting the top account by revenue from my accounts.
        Ground Truth: The top account by revenue should be Andromeda Inc.
        """
        query = "get top account by revenue from my accounts only"
        all_events = await test_instance.run_task(query)
        test_instance._assert_answer_event(all_events, expected_keywords=["Andromeda"])

    async def test_list_my_accounts(self, test_instance, mode_suffix):
        """
        Test listing all my accounts and how many are there.
        Ground Truth: There should be 50 accounts.
        """
        query = "list all my accounts, how many are there?"
        all_events = await test_instance.run_task(query)
        test_instance._assert_answer_event(all_events, expected_keywords=["50"])

    async def test_find_vp_sales_active_high_value_accounts(self, test_instance, mode_suffix):
        """
        Test finding Vice President of Sales in Active, Tech Transformation Accounts.
        Ground Truth: The final list of contacts should contain Fiona Garcia, Ethan Martinez, Helen Wilson, and Helen Garcia.
        """
        query = "Get the names of 'Vice President of Sales' contacts for Tech Transformation campaign."
        all_events = await test_instance.run_task(query)
        test_instance._assert_answer_event(
            all_events, expected_keywords=["Fiona Garcia", "Ethan Martinez", "Helen Wilson", "Helen Garcia"]
        )
