import pytest
import unittest

from system_tests.e2e.base_test import BaseTestServerStream
from system_tests.e2e.digital_sales_test_helpers import DigitalSalesTestHelpers

pytestmark = pytest.mark.e2e


class TestServerStreamAccurate(BaseTestServerStream):
    """
    Test class for Cuga agent in ACCURATE mode.
    """

    test_env_vars = {"DYNACONF_FEATURES__CUGA_MODE": "accurate"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helpers = DigitalSalesTestHelpers()

    async def test_get_top_account_by_revenue_stream_accurate(self):
        """Test getting the top account by revenue from my accounts."""
        await self.helpers.test_get_top_account_by_revenue_stream(self, "accurate")

    async def test_list_my_accounts_accurate(self):
        """Test listing all my accounts and how many are there."""
        await self.helpers.test_list_my_accounts(self, "accurate")

    async def test_find_vp_sales_active_high_value_accounts_accurate(self):
        """Test finding Vice President of Sales in Active, Tech Transformation Accounts."""
        await self.helpers.test_find_vp_sales_active_high_value_accounts(self, "accurate")


if __name__ == "__main__":
    unittest.main()
