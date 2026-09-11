from __future__ import annotations

import pytest

from cuga.backend.server.auth.dependencies import has_manage_access
from cuga.backend.server.auth.models import UserInfo

pytestmark = pytest.mark.unit


def test_has_manage_access_uses_configured_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._authorization_enabled", lambda: True)
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._get_manage_roles", lambda: ["MemoryOperator"])

    assert has_manage_access(UserInfo(sub="operator", roles=["MemoryOperator"])) is True
    assert has_manage_access(UserInfo(sub="default-admin", roles=["ServiceAdmin"])) is False


def test_has_manage_access_allows_requests_when_authorization_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._authorization_enabled", lambda: False)

    assert has_manage_access(None) is True
