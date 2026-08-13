"""Start demo/CRM stacks once per test class for e2e and load suites."""

from __future__ import annotations

import os

import pytest

from system_tests.e2e.port_manager import allocate_stability_env, cleanup_ports
from system_tests.e2e.server_stack import start_crm_stack, start_digital_sales_stack, stop_stack

_MISSING = object()


def _sync_server_urls(env_ports: dict[str, str]) -> None:
    from system_tests.e2e import base_test

    demo_port = env_ports.get("DYNACONF_SERVER_PORTS__DEMO")
    if not demo_port:
        return

    server_url = f"http://localhost:{demo_port}"
    base_test.SERVER_URL = server_url
    base_test.STREAM_ENDPOINT = f"{server_url}/stream"
    base_test.STOP_ENDPOINT = f"{server_url}/stop"

    try:
        import system_tests.load.load_test as load_mod

        load_mod.STATE_ENDPOINT = f"{server_url}/api/agent/state"
        load_mod.SERVER_URL = server_url
    except ImportError:
        pass

    try:
        import system_tests.load.load_test_with_mocked_llm as mocked_load_mod

        mocked_load_mod.STATE_ENDPOINT = f"{server_url}/api/agent/state"
        mocked_load_mod.SERVER_URL = server_url
    except ImportError:
        pass


def _sync_settings_ports(env_ports: dict[str, str]) -> None:
    from cuga.config import settings

    settings.reload()
    for env_key, port in env_ports.items():
        if not env_key.startswith("DYNACONF_SERVER_PORTS__"):
            continue
        setting_key = env_key.removeprefix("DYNACONF_").lower()
        settings.set(setting_key, int(port))


def _setenv(saved: dict, key: str, value: str) -> None:
    saved.setdefault(key, os.environ.get(key, _MISSING))
    os.environ[key] = value


def _pinned_port_env(cls) -> dict[str, str]:
    return {
        key: value
        for key, value in getattr(cls, "test_env_vars", {}).items()
        if key.startswith("DYNACONF_SERVER_PORTS__") and value is not None
    }


@pytest.fixture(scope="class", autouse=True)
def e2e_class_stack(request):
    cls = getattr(request, "cls", None)
    stack_kind = getattr(cls, "_stability_stack", None) if cls else None
    if not stack_kind:
        yield
        return

    from cuga.config import settings
    from system_tests.e2e.base_test import _apply_base_test_env_defaults

    pinned = _pinned_port_env(cls)
    e2b_mode = os.getenv("CUGA_E2B_MODE", "false").lower() == "true"
    env_ports = pinned or allocate_stability_env(e2b_mode=e2b_mode)
    saved: dict = {}

    for key, value in env_ports.items():
        _setenv(saved, key, str(value))

    if stack_kind == "crm":
        _setenv(saved, "MCP_SERVERS_FILE", "none")
        _setenv(saved, "CUGA_TEST_ENV", "true")
    else:
        _apply_base_test_env_defaults()

    for key, value in getattr(cls, "test_env_vars", {}).items():
        if value is None:
            saved.setdefault(key, os.environ.get(key, _MISSING))
            os.environ.pop(key, None)
        else:
            _setenv(saved, key, value)

    settings.reload()
    _sync_settings_ports(env_ports)
    _sync_server_urls(env_ports)

    class_name = cls.__name__
    if stack_kind == "crm":
        handles = start_crm_stack(class_name, mode=getattr(cls, "mode", "default"))
    else:
        handles = start_digital_sales_stack(class_name)

    try:
        yield
    finally:
        stop_stack(handles)
        cleanup_ports(env_ports)
        for key, old in saved.items():
            if old is _MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        settings.reload()
