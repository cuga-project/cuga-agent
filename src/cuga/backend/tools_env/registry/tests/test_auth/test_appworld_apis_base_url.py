"""Where AppWorldAuthManager sends its supervisor and login requests.

The AppWorld APIs server is usually on localhost, but in a container setup it
runs on another host (e.g. ``http://appworld:9000``). ``server_ports.apis_host``
selects that host; without it the old ``http://localhost:{apis_url}`` default
applies.
"""

import pytest

from cuga.backend.tools_env.registry.registry.authentication.appworld_auth_manager import (
    AppWorldAuthManager,
    get_appworld_apis_base_url,
)
from cuga.config import settings

pytestmark = pytest.mark.unit


def test_default_is_localhost_with_apis_port(monkeypatch):
    monkeypatch.setattr(settings.server_ports, "apis_host", None)
    monkeypatch.setattr(settings.server_ports, "apis_url", 9111)
    assert get_appworld_apis_base_url() == "http://localhost:9111"
    assert AppWorldAuthManager().base_url == "http://localhost:9111"


def test_apis_host_overrides_localhost(monkeypatch):
    monkeypatch.setattr(settings.server_ports, "apis_host", "http://appworld:9000/")
    assert get_appworld_apis_base_url() == "http://appworld:9000"
    assert AppWorldAuthManager().base_url == "http://appworld:9000"


def test_explicit_base_url_still_wins(monkeypatch):
    monkeypatch.setattr(settings.server_ports, "apis_host", "http://appworld:9000")
    assert AppWorldAuthManager("http://other:1234/").base_url == "http://other:1234"


def test_setting_is_read_at_construction_not_import(monkeypatch):
    """A runtime override (e.g. DYNACONF_SERVER_PORTS__APIS_URL applied after
    import) must reach new managers; the old default was frozen at import."""
    monkeypatch.setattr(settings.server_ports, "apis_host", None)
    monkeypatch.setattr(settings.server_ports, "apis_url", 9000)
    first = AppWorldAuthManager().base_url
    monkeypatch.setattr(settings.server_ports, "apis_url", 9222)
    assert first == "http://localhost:9000"
    assert AppWorldAuthManager().base_url == "http://localhost:9222"


def test_login_requests_use_the_configured_host(monkeypatch):
    import httpx

    from cuga.backend.tools_env.registry.registry.authentication import appworld_auth_manager as module

    monkeypatch.setattr(settings.server_ports, "apis_host", "http://appworld:9000")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"email": "a@b.c", "phone_number": "1"})

    real_client = httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(handler))
    )
    assert AppWorldAuthManager()._get_user_profile() == {"email": "a@b.c", "phone_number": "1"}
    assert seen == ["http://appworld:9000/supervisor/profile"]
