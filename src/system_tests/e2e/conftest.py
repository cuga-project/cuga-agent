"""
Pytest configuration for e2e tests.
Configures Windows event loop policy for better asyncio performance.
"""

import asyncio
import platform
import pytest


@pytest.fixture(scope="session", autouse=True)
def configure_windows_event_loop():
    """
    Configure Windows event loop policy for better asyncio performance.
    This runs once per test session before any tests execute.
    """
    if platform.system() == "Windows":
        # Use WindowsSelectorEventLoopPolicy for better I/O performance on Windows
        # This avoids the slow ProactorEventLoopPolicy which can cause significant delays.
        # The default ProactorEventLoopPolicy on Windows can cause tasks to take 0.1-2 seconds
        # each, leading to extremely slow test execution. WindowsSelectorEventLoopPolicy
        # uses select() instead of I/O completion ports, which is more efficient for
        # the network I/O patterns used in these tests.
        if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

            # Increase slow callback threshold on Windows (default is 0.1s)
            # Windows event loop can legitimately take longer due to system overhead
            # Setting to 2.0s reduces false positive warnings while still catching real issues
            try:
                loop = asyncio.new_event_loop()
                loop.slow_callback_duration = 2.0
                loop.close()
            except Exception:
                pass  # If we can't set it, that's okay

            # Suppress asyncio slow callback warnings on Windows
            # These warnings are often false positives due to Windows event loop implementation
            import warnings
            import logging

            warnings.filterwarnings(
                "ignore",
                message=".*Executing.*took.*seconds",
                category=RuntimeWarning,
                module="asyncio",
            )

            # Also suppress at the logging level for asyncio
            logging.getLogger("asyncio").setLevel(logging.ERROR)
