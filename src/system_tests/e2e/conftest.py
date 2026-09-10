"""Pytest configuration for e2e tests.

Configures Windows event loop policy for server-backed tests under
``src/system_tests/e2e/``.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import warnings

import pytest


@pytest.fixture(scope="session", autouse=True)
def configure_windows_event_loop():
    if platform.system() != "Windows":
        return

    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        try:
            loop = asyncio.new_event_loop()
            loop.slow_callback_duration = 2.0
            loop.close()
        except Exception:
            pass

        warnings.filterwarnings(
            "ignore",
            message=".*Executing.*took.*seconds",
            category=RuntimeWarning,
            module="asyncio",
        )
        logging.getLogger("asyncio").setLevel(logging.ERROR)
