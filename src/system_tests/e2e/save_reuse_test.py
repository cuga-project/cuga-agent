import datetime
import pytest
import unittest

from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse, ActionType

from system_tests.e2e.base_test import BaseTestServerStream

pytestmark = pytest.mark.e2e


class TestServerStreamFast(BaseTestServerStream):
    """
    Test class for FastAPI server's streaming endpoint in FAST mode.
    """

    test_env_vars = {"DYNACONF_FEATURES__CUGA_MODE": "save_reuse_fast"}

    async def test_get_top_account_by_revenue_stream_fast(self):
        """
        Tests the save and reuse functionality with the query 'get top account by revenue' in fast mode.
        This test verifies the complete save/reuse flow including human approval steps and intent capture.
        Ground Truth: The final answer should contain keywords 'account' and 'revenue'.
        """
        query = "get top account by revenue"
        print(f"\n=== Running FAST mode test for query: '{query}' ===")

        all_events = await self.run_task(query)

        last_event_key, last_event_value = self.get_event_at(all_events, -1)
        assert last_event_key == "__interrupt__"
        last_event_key, last_event_value = self.get_event_at(all_events, -2)
        assert last_event_key == "SuggestHumanActions"
        assert last_event_value['action_id'] == "new_flow_approve"

        all_events = await self.run_task(
            "",
            followup_response=ActionResponse(
                action_id=last_event_value['action_id'],
                confirmed=True,
                response_type=ActionType.CONFIRMATION,
                timestamp=datetime.datetime.now().isoformat(),
            ),
        )

        # Use the helper function to run the task
        # all_events = await self.run_task(query)

        last_event_key, last_event_value = self.get_event_at(all_events, -1)
        assert last_event_key == "__interrupt__"
        last_event_key, last_event_value = self.get_event_at(all_events, -2)
        assert last_event_key == "SuggestHumanActions"
        assert last_event_value['action_id'] == "save_reuse"

        all_events = await self.run_task(
            "",
            followup_response=ActionResponse(
                action_id=last_event_value['action_id'],
                confirmed=True,
                response_type=ActionType.CONFIRMATION,
                timestamp=datetime.datetime.now().isoformat(),
            ),
        )

        last_event_key, last_event_value = self.get_event_at(all_events, -2)
        assert last_event_value['action_id'] == "save_reuse_intent"
        all_events = await self.run_task(
            "",
            followup_response=ActionResponse(
                action_id=last_event_value['action_id'],
                text_response="Get top two accounts by revenue",
                response_type=ActionType.NATURAL_LANGUAGE,
                timestamp=datetime.datetime.now().isoformat(),
            ),
        )

        all_events = await self.run_task("get top 4 accounts by revenue")
        last_event_key, last_event_value = self.get_event_at(all_events, -2)
        assert last_event_key == "SuggestHumanActions"
        assert last_event_value['action_id'] == "flow_approve"
        all_events = await self.run_task(
            "",
            followup_response=ActionResponse(
                action_id=last_event_value['action_id'],
                confirmed=True,
                response_type=ActionType.CONFIRMATION,
                timestamp=datetime.datetime.now().isoformat(),
            ),
        )
        self._assert_answer_event(all_events, expected_keywords=["account", "revenue"])


if __name__ == "__main__":
    unittest.main()
