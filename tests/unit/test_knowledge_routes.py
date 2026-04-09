from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from cuga.backend.knowledge.auth import KnowledgeIdentity, require_internal_or_auth
from cuga.backend.knowledge.routes import knowledge_router


class _FakeEngine:
    def __init__(self, task: dict, *, enabled: bool = True):
        self._config = SimpleNamespace(enabled=enabled, max_files_per_request=5)
        self._task = task

    async def _sanitize_and_validate(
        self, collection: str, tmp_path, replace_duplicates: bool, original_name: str
    ) -> str:
        return original_name

    async def _create_task_entry(self, collection: str, filename: str) -> dict[str, str]:
        return {"task_id": "task-1"}

    async def _run_ingest(
        self,
        collection: str,
        tmp_path,
        filename: str,
        task_id: str,
        replace_duplicates: bool,
        skip_file_copy: bool = False,
    ) -> None:
        return None

    async def get_task(self, task_id: str) -> dict:
        return self._task

    async def health(self, collection: str | None = None) -> dict:
        return {
            "status": "healthy",
            "settings": {"knowledge": {"enabled": self._config.enabled}},
            "embeddings_initialized": self._config.enabled,
        }

    async def list_documents(self, collection: str) -> list[dict]:
        return []


async def _identity_override(request: Request) -> KnowledgeIdentity:
    return KnowledgeIdentity(
        user_id=None,
        tenant_id=None,
        agent_id="cuga-default",
        thread_id=request.headers.get("X-Thread-ID"),
        auth_mode="external",
    )


def test_upload_documents_returns_400_when_single_file_ingestion_fails():
    task = {
        "task_id": "task-1",
        "status": "failed",
        "file_tasks": {
            "secret.pdf": {
                "filename": "secret.pdf",
                "status": "failed",
                "error": "PDF is password-protected and cannot be indexed without a password: secret.pdf",
            }
        },
    }
    app = FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[require_internal_or_auth] = _identity_override
    app.state.app_state = SimpleNamespace(
        knowledge_engine=_FakeEngine(task),
        knowledge_provider=None,
    )

    client = TestClient(app)
    response = client.post(
        "/api/knowledge/documents",
        files={"files": ("secret.pdf", b"%PDF-1.7", "application/pdf")},
        data={"scope": "agent", "replace_duplicates": "true"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == task["file_tasks"]["secret.pdf"]["error"]


def test_health_reports_disabled_when_engine_is_disabled():
    app = FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[require_internal_or_auth] = _identity_override
    app.state.app_state = SimpleNamespace(
        knowledge_engine=_FakeEngine({}, enabled=False),
        knowledge_provider=None,
        get_subsystem_status=lambda _name: {
            "state": "ready",
            "message": "Knowledge subsystem ready",
            "details": {},
        },
    )

    client = TestClient(app)
    response = client.get("/api/knowledge/health")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["healthy"] is False


def test_list_documents_rejects_disabled_session_scope():
    app = FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[require_internal_or_auth] = _identity_override
    app.state.app_state = SimpleNamespace(
        knowledge_engine=_FakeEngine({}, enabled=True),
        knowledge_provider=None,
    )
    app.state.app_state.knowledge_engine._config.session_level_enabled = False

    client = TestClient(app)
    response = client.get(
        "/api/knowledge/documents?scope=session",
        headers={"X-Agent-ID": "cuga-default", "X-Thread-ID": "thread-123"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Session-level knowledge is disabled for this agent"


def test_list_documents_rejects_disabled_agent_scope():
    app = FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[require_internal_or_auth] = _identity_override
    app.state.app_state = SimpleNamespace(
        knowledge_engine=_FakeEngine({}, enabled=True),
        knowledge_provider=None,
    )
    app.state.app_state.knowledge_engine._config.agent_level_enabled = False

    client = TestClient(app)
    response = client.get(
        "/api/knowledge/documents?scope=agent",
        headers={"X-Agent-ID": "cuga-default"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Agent-level knowledge is disabled for this agent"
