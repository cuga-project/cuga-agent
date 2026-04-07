from __future__ import annotations

import pytest
from fastapi import HTTPException

from cuga.backend.knowledge.auth import KnowledgeIdentity, ensure_agent_knowledge_manage_access


def _ext_identity(*, roles: frozenset[str] | None) -> KnowledgeIdentity:
    return KnowledgeIdentity(
        user_id="sub-1",
        tenant_id=None,
        agent_id="agent-1",
        thread_id=None,
        auth_mode="external",
        roles=roles,
    )


def test_manage_gate_allows_internal():
    ensure_agent_knowledge_manage_access(
        KnowledgeIdentity(
            user_id=None,
            tenant_id=None,
            agent_id="a",
            thread_id=None,
            auth_mode="internal",
            roles=None,
        )
    )


def test_manage_gate_skips_when_auth_disabled(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._auth_enabled", lambda: False)
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._authorization_enabled", lambda: True)
    ensure_agent_knowledge_manage_access(_ext_identity(roles=frozenset({"ServiceUser"})))


def test_manage_gate_skips_when_authorization_disabled(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._auth_enabled", lambda: True)
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._authorization_enabled", lambda: False)
    ensure_agent_knowledge_manage_access(_ext_identity(roles=frozenset({"ServiceUser"})))


def test_manage_gate_blocks_service_user_when_authz_on(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._auth_enabled", lambda: True)
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._authorization_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.auth.dependencies._get_manage_roles",
        lambda: ["ServiceOwner", "ServiceAdmin"],
    )
    with pytest.raises(HTTPException) as exc:
        ensure_agent_knowledge_manage_access(_ext_identity(roles=frozenset({"ServiceUser"})))
    assert exc.value.status_code == 403


def test_manage_gate_allows_service_admin_when_authz_on(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._auth_enabled", lambda: True)
    monkeypatch.setattr("cuga.backend.server.auth.dependencies._authorization_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.auth.dependencies._get_manage_roles",
        lambda: ["ServiceOwner", "ServiceAdmin"],
    )
    ensure_agent_knowledge_manage_access(_ext_identity(roles=frozenset({"ServiceAdmin"})))
