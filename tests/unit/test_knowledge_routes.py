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


def test_upload_documents_async_returns_queued_task_when_wait_false():
    """wait=false short-circuits to a 202-style queued task with weighted_pct=0,
    so the UI can switch to polling without waiting for ingest to finish."""
    app = FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[require_internal_or_auth] = _identity_override
    app.state.app_state = SimpleNamespace(
        knowledge_engine=_FakeEngine({}),
        knowledge_provider=None,
    )

    client = TestClient(app)
    response = client.post(
        "/api/knowledge/documents",
        files={"files": ("notes.txt", b"hello", "text/plain")},
        data={"scope": "agent", "replace_duplicates": "true", "wait": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "task-1"
    assert body["status"] == "pending"
    assert body["weighted_pct"] == 0.0
    # ``ui_phase=queued`` lets the UI render a distinct "Queued" indicator
    # for the gap between accept-the-upload and start-of-ingest.
    assert body["ui_phase"] == "queued"


def test_get_task_attaches_weighted_pct_during_embed():
    """Polling GET /tasks/{id} must surface a single 0..1 number that
    monotonically rises across the parse → embed → insert pipeline."""
    task = {
        "task_id": "task-1",
        "collection": "kb_agent_cuga_default",
        "status": "running",
        "file_tasks": {
            "report.pdf": {
                "filename": "report.pdf",
                "stage": "embed",
                "progress": {"done": 100, "total": 400},
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
    response = client.get(
        "/api/knowledge/tasks/task-1",
        headers={"X-Agent-ID": "cuga-default"},
    )

    assert response.status_code == 200
    body = response.json()
    # parse(0.45) + embed(0.40) * 100/400 = 0.55
    assert body["weighted_pct"] == 0.55


def test_list_tasks_agent_scope_spans_inflight_collection():
    """Reindex-tile live progress: GET /tasks?scope=agent must return tasks
    for ALL of this agent's collections — the ACTIVE one AND an in-flight
    reindex target (different config hash, not yet promoted) — while
    EXCLUDING other agents' and session tasks. Without this, a deferred-flip
    reindex (reindex_for_config -> new collection) leaves the tile frozen at
    'Pending' until promotion, because the poll only saw the active hash."""

    active = "kb_agent_cuga_default_aaaa"
    inflight = "kb_agent_cuga_default_bbbb"
    every_task = [
        {"task_id": "t-active", "collection": active, "status": "completed"},
        {"task_id": "t-inflight", "collection": inflight, "status": "running"},
        {"task_id": "t-other-agent", "collection": "kb_agent_other_cccc", "status": "running"},
        {"task_id": "t-session", "collection": "kb_sess_xyz", "status": "running"},
    ]

    class _TasksEngine:
        # _config.enabled gates agent scope in resolve_collection.
        _config = SimpleNamespace(enabled=True)

        async def get_tasks(self, collection=None):
            if collection is None:
                return list(every_task)
            return [t for t in every_task if t["collection"] == collection]

    app = FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[require_internal_or_auth] = _identity_override
    app.state.app_state = SimpleNamespace(
        knowledge_engine=_TasksEngine(),
        knowledge_provider=None,
        knowledge_config_hash="aaaa",
    )

    client = TestClient(app)
    resp = client.get(
        "/api/knowledge/tasks?scope=agent",
        headers={"X-Agent-ID": "cuga-default"},
    )

    assert resp.status_code == 200, resp.text
    ids = {t["task_id"] for t in resp.json()["tasks"]}
    assert ids == {"t-active", "t-inflight"}, f"agent scope must span active+inflight only: {ids}"


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
