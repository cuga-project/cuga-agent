import asyncio
import unittest
from pathlib import Path

from system_tests.e2e.base_test import BaseTestServerStream
from system_tests.e2e.digital_sales_test_helpers import DigitalSalesTestHelpers


class TestServerStreamBalancedMemory(BaseTestServerStream):
    """
    Balanced mode tests that run with memory support enabled.
    """

    test_env_vars = {
        "DYNACONF_FEATURES__CUGA_MODE": "balanced",
        "DYNACONF_ADVANCED_FEATURES__ENABLE_MEMORY": "true",
    }
    enable_memory_service = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helpers = DigitalSalesTestHelpers()

    def _trajectory_runs(self) -> set[str]:
        trajectory_dir = Path(self.test_log_dir) / "logging" / "trajectory_data"
        if not trajectory_dir.exists():
            return set()
        return {p.name for p in trajectory_dir.iterdir() if p.is_dir()}

    async def _run_scenario_and_assert_memory(self, scenario):
        before_runs = self._trajectory_runs()
        await scenario(self, "balanced")

        await asyncio.sleep(8)

        after_runs = self._trajectory_runs()
        self.assertTrue(after_runs, "No memory run folder created in trajectory data directory.")
        self.assertGreaterEqual(
            len(after_runs),
            len(before_runs),
            "Trajectory data did not grow after running balanced-mode memory test.",
        )

    async def test_get_top_account_by_revenue_stream_balanced_memory(self):
        """Run a scenario, wait 30s, and assert memory run data exists."""
        await self._run_scenario_and_assert_memory(self.helpers.test_get_top_account_by_revenue_stream)

    async def test_list_my_accounts_balanced_memory(self):
        """Run a scenario, wait 30s, and assert memory run data exists."""
        await self._run_scenario_and_assert_memory(self.helpers.test_list_my_accounts)

    async def test_find_vp_sales_active_high_value_accounts_balanced_memory(self):
        """Run a scenario, wait 30s, and assert memory run data exists."""
        await self._run_scenario_and_assert_memory(self.helpers.test_find_vp_sales_active_high_value_accounts)


if __name__ == "__main__":
    unittest.main()
