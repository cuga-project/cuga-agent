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


def _sync_server_urls(env_ports: dict[str, str]) -> None:
    """Refresh module-level URLs after dynamic port env overrides.

    ``base_test.SERVER_URL`` is computed at import time; monkeypatched env
    vars update subprocess servers and runtime ``settings`` access, but the
    cached constants used by ``run_task`` must be updated explicitly.
    """
    from system_tests.e2e import base_test

    demo_port = env_ports.get("DYNACONF_SERVER_PORTS__DEMO")
    if not demo_port:
        return

    server_url = f"http://localhost:{demo_port}"
    base_test.SERVER_URL = server_url
    base_test.STREAM_ENDPOINT = f"{server_url}/stream"
    base_test.STOP_ENDPOINT = f"{server_url}/stop"

    try:
        import system_tests.load.load_test_with_mocked_llm as load_mod

        load_mod.STATE_ENDPOINT = f"{server_url}/api/agent/state"
    except ImportError:
        pass


def _needs_dynamic_ports(request) -> bool:
    keywords = request.node.keywords
    if any(marker in keywords for marker in ("stability", "windows_smoke")):
        return True
    node_path = str(getattr(request.node, "path", "")).replace("\\", "/")
    return "/system_tests/e2e/" in node_path


def _sync_settings_ports(env_ports: dict[str, str]) -> None:
    """Reload dynaconf after monkeypatched port env vars."""
    from cuga.config import settings

    settings.reload()
    for env_key, port in env_ports.items():
        if not env_key.startswith("DYNACONF_SERVER_PORTS__"):
            continue
        setting_key = env_key.removeprefix("DYNACONF_").lower()
        settings.set(setting_key, int(port))


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
    _sync_settings_ports(env_ports)
    _sync_server_urls(env_ports)
    yield
    cleanup_ports(env_ports)
