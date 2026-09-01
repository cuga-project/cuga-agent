"""Manual integration: real HashiCorp Vault + Manage config vault ref round-trip.

Runbook (Docker — laptop):

    docker run --rm -d --name cuga-vault -p 8200:8200 \\
      -e VAULT_DEV_ROOT_TOKEN_ID=dev-root \\
      -e VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200 \\
      hashicorp/vault:1.18

Runbook (binary — cloud agent without Docker):

    curl -fsSL -o /tmp/vault.zip \\
      https://releases.hashicorp.com/vault/1.18.3/vault_1.18.3_linux_amd64.zip
    unzip -o /tmp/vault.zip -d /tmp
    /tmp/vault server -dev -dev-listen-address=127.0.0.1:8200 -dev-root-token-id=dev-root &

Common env:

    export VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=dev-root
    export DYNACONF_SECRETS__MODE=vault
    export DYNACONF_SECRETS__FORCE_ENV=false
    export DYNACONF_SECRETS__VAULT_ADDR=http://127.0.0.1:8200
    export DYNACONF_SECRETS__VAULT_AUTH_METHOD=token

    uv run pytest -m manual tests/integration/test_vault_llm_secret_hydration.py -v
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.secrets import resolve_secret
from cuga.backend.server.auth import require_auth, require_manage_access
from cuga.backend.server.config_store import load_draft, reset_config_db, save_draft
from cuga.backend.server.manage_routes import router
from cuga.backend.server.secrets_routes import router as secrets_router

TEST_SECRET_ID = "cuga-test-llm-key"
TEST_SECRET_VALUE = "sk-test-value-for-hydration"
VAULT_REF = f"vault://secret/{TEST_SECRET_ID}#value"


def _vault_available() -> bool:
    try:
        from cuga.backend.secrets.backends.vault_backend import VaultBackend

        return VaultBackend().available()
    except Exception:
        return False


@pytest.fixture
def vault_env(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200"))
    monkeypatch.setenv("VAULT_TOKEN", os.environ.get("VAULT_TOKEN", "dev-root"))
    monkeypatch.setenv("DYNACONF_SECRETS__MODE", "vault")
    monkeypatch.setenv("DYNACONF_SECRETS__FORCE_ENV", "false")
    monkeypatch.setenv("DYNACONF_SECRETS__VAULT_ADDR", os.environ["VAULT_ADDR"])
    monkeypatch.setenv("DYNACONF_SECRETS__VAULT_AUTH_METHOD", "token")


@pytest.fixture
def manage_client():
    app = FastAPI()
    app.include_router(router)
    app.include_router(secrets_router)
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[require_manage_access] = lambda: None
    return TestClient(app)


@pytest.mark.manual
def test_vault_llm_secret_hydration_round_trip(vault_env, manage_client):
    if not _vault_available():
        pytest.skip("Vault not available — start dev Vault and set VAULT_ADDR/VAULT_TOKEN")

    from cuga.backend.secrets.backends.vault_backend import VaultBackend

    vb = VaultBackend()
    assert vb.set(TEST_SECRET_ID, TEST_SECRET_VALUE), "Vault write failed"

    try:
        resolved = resolve_secret(VAULT_REF)
        assert resolved == TEST_SECRET_VALUE

        listed = vb.list()
        assert TEST_SECRET_ID in listed

        reset_config_db()
        asyncio.run(
            save_draft(
                {
                    "llm": {
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "api_key": VAULT_REF,
                    }
                },
                "cuga-default",
            )
        )

        get_resp = manage_client.get(
            "/api/manage/config",
            params={"draft": "1", "agent_id": "cuga-default"},
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["config"]["llm"]["api_key"] == VAULT_REF

        secrets_resp = manage_client.get("/api/secrets", params={"agent_id": "cuga-default"})
        assert secrets_resp.status_code == 200, secrets_resp.text
        items = secrets_resp.json().get("secrets") or []
        vault_ids = [s["id"] for s in items if s.get("source") == "vault"]
        assert TEST_SECRET_ID in vault_ids

        patch_resp = manage_client.patch(
            "/api/manage/config/draft/llm",
            params={"agent_id": "cuga-default"},
            json={"llm": {"provider": "openai", "model": "gpt-4o", "api_key": ""}},
        )
        assert patch_resp.status_code == 200, patch_resp.text

        draft = asyncio.run(load_draft("cuga-default"))
        assert draft["llm"]["api_key"] == VAULT_REF
        assert draft["llm"]["model"] == "gpt-4o"

        get_after = manage_client.get(
            "/api/manage/config",
            params={"draft": "1", "agent_id": "cuga-default"},
        )
        assert get_after.json()["config"]["llm"]["api_key"] == VAULT_REF
    finally:
        vb.delete(TEST_SECRET_ID)
