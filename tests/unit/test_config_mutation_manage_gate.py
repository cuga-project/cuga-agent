"""POST /api/config/tools and /api/config/model must require a management role (#920)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from cuga.backend.server.auth.models import UserInfo
from cuga.backend.server.main import app

DEPS = "cuga.backend.server.auth.dependencies"


@pytest.fixture
def as_user(monkeypatch):
    """Turn on authentication and role authorization and sign every request in as a user with ``roles``."""
    monkeypatch.setattr(f"{DEPS}._auth_enabled", lambda: True)
    monkeypatch.setattr(f"{DEPS}._authorization_enabled", lambda: True)
    monkeypatch.setattr(f"{DEPS}._get_manage_roles", lambda: ["ServiceOwner", "ServiceAdmin"])

    def sign_in(roles: list[str]) -> TestClient:
        async def current_user(request):
            return UserInfo(sub="user-1", roles=roles)

        monkeypatch.setattr(f"{DEPS}.get_current_user", current_user)
        return TestClient(app)

    return sign_in


@pytest.fixture
def tools_config(monkeypatch, tmp_path):
    """Point the tools config write at a temporary package root and return the file it writes."""
    config_dir = tmp_path / "backend" / "tools_env" / "registry" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr("cuga.backend.server.main.PACKAGE_ROOT", str(tmp_path))
    return config_dir / "mcp_servers_crm.yaml"


@pytest.mark.unit
def test_service_user_cannot_save_tools_config(as_user, tools_config):
    response = as_user(["ServiceUser"]).post("/api/config/tools", json={"mcpServers": {}})

    assert response.status_code == 403
    assert not tools_config.exists()


@pytest.mark.unit
def test_service_admin_can_save_tools_config(as_user, tools_config):
    response = as_user(["ServiceAdmin"]).post("/api/config/tools", json={"mcpServers": {}})

    assert response.status_code == 200
    assert tools_config.exists()


@pytest.mark.unit
def test_service_user_cannot_change_model_config(as_user, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "before")

    response = as_user(["ServiceUser"]).post("/api/config/model", json={"model": "after"})

    assert response.status_code == 403
    assert os.environ["MODEL_NAME"] == "before"


@pytest.mark.unit
def test_service_admin_can_change_model_config(as_user, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "before")
    for name in ("MODEL_PROVIDER", "MODEL_TEMPERATURE", "MODEL_MAX_TOKENS", "MODEL_TOP_P"):
        monkeypatch.delenv(name, raising=False)

    response = as_user(["ServiceAdmin"]).post("/api/config/model", json={"model": "after"})

    assert response.status_code == 200
    assert os.environ["MODEL_NAME"] == "after"
