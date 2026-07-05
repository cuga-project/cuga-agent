"""Pytest configuration for e2e tests.

Configures Windows event loop policy and dynamic port allocation for
server-backed tests under ``src/system_tests/e2e/``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import warnings

import pytest

from system_tests.e2e.port_manager import allocate_stability_env, cleanup_ports


def _needs_dynamic_ports(request) -> bool:
    keywords = request.node.keywords
    if any(marker in keywords for marker in ("stability", "windows_smoke", "load")):
        return True
    node_path = str(getattr(request.node, "path", "")).replace("\\", "/")
    return "/system_tests/e2e/" in node_path


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


@pytest.fixture(autouse=True)
def dynamic_server_ports(request, monkeypatch):
    if not _needs_dynamic_ports(request):
        yield
        return

    e2b_mode = os.getenv("CUGA_E2B_MODE", "false").lower() == "true"
    env_ports = allocate_stability_env(e2b_mode=e2b_mode)
    for key, value in env_ports.items():
        monkeypatch.setenv(key, value)
    yield
    cleanup_ports(env_ports)
