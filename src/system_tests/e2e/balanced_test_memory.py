import asyncio
import unittest
from pathlib import Path

from system_tests.e2e.base_test import BaseTestServerStream
from system_tests.e2e.digital_sales_test_helpers import DigitalSalesTestHelpers

from cuga.backend.memory.memory import Memory


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

    async def test_get_top_account_by_revenue_stream_balanced_memory(self):
        """Run a scenario, wait 30s, and assert memory run data exists."""
        trajectory_dir = Path(self.test_log_dir) / "logging" / "trajectory_data"
        runs = [p.name for p in trajectory_dir.iterdir()] if trajectory_dir.exists() else []

        await self.helpers.test_get_top_account_by_revenue_stream(self, "balanced")

        await asyncio.sleep(8)
        self.assertTrue(runs, "No memory run folder created in trajectory data directory.")

        memory = Memory()
        memory_runs = memory.list_runs(namespace_id="memory", limit=1)
        self.assertTrue(memory_runs, "No memory runs found in memory service.")
        run = memory.get_run(namespace_id="memory", run_id=memory_runs[0].id)
        self.assertGreater(
            len(run.steps or []),
            0,
            "Expected recorded steps in memory run after waiting for persistence.",
        )

    async def test_list_my_accounts_balanced_memory(self):
        """Run a scenario, wait 30s, and assert memory run data exists."""
        trajectory_dir = Path(self.test_log_dir) / "logging" / "trajectory_data"
        runs = [p.name for p in trajectory_dir.iterdir()] if trajectory_dir.exists() else []

        await self.helpers.test_list_my_accounts(self, "balanced")

        await asyncio.sleep(8)
        self.assertTrue(runs, "No memory run folder created in trajectory data directory.")

        memory = Memory()
        memory_runs = memory.list_runs(namespace_id="memory", limit=1)
        self.assertTrue(memory_runs, "No memory runs found in memory service.")
        run = memory.get_run(namespace_id="memory", run_id=memory_runs[0].id)
        self.assertGreater(
            len(run.steps or []),
            0,
            "Expected recorded steps in memory run after waiting for persistence.",
        )

    async def test_find_vp_sales_active_high_value_accounts_balanced_memory(self):
        """Run a scenario, wait 30s, and assert memory run data exists."""
        trajectory_dir = Path(self.test_log_dir) / "logging" / "trajectory_data"
        runs = [p.name for p in trajectory_dir.iterdir()] if trajectory_dir.exists() else []

        await self.helpers.test_find_vp_sales_active_high_value_accounts(self, "balanced")

        await asyncio.sleep(8)
        self.assertTrue(runs, "No memory run folder created in trajectory data directory.")

        memory = Memory()
        memory_runs = memory.list_runs(namespace_id="memory", limit=1)
        self.assertTrue(memory_runs, "No memory runs found in memory service.")
        run = memory.get_run(namespace_id="memory", run_id=memory_runs[0].id)
        self.assertGreater(
            len(run.steps or []),
            0,
            "Expected recorded steps in memory run after waiting for persistence.",
        )


if __name__ == "__main__":
    unittest.main()
