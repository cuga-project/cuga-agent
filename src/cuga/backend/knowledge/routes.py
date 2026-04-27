"""FastAPI routes for the knowledge API.

Unified under /api/knowledge with scope param (agent|session).
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, Request
from fastapi.responses import FileResponse

from cuga.backend.knowledge.auth import (
    KnowledgeIdentity,
    ensure_agent_knowledge_manage_access,
    ensure_agent_scope_manage_if_needed,
    require_internal_or_auth,
    require_knowledge_agent_manage_identity,
    resolve_collection,
)
from cuga.backend.knowledge.engine import (
    DocumentExistsError,
    DocumentNotFoundError,
    FileTooLargeError,
    IngestionQueueFullError,
    KnowledgeEngine,
    ReindexBusyError,
    ReindexInProgressError,
)

logger = logging.getLogger("cuga.knowledge")

# Public HTTP path for the knowledge API (match include_router mount; use for client-side URL building).
KNOWLEDGE_HTTP_PREFIX = "/api/knowledge"

knowledge_router = APIRouter(prefix=KNOWLEDGE_HTTP_PREFIX, tags=["knowledge"])

knowledge_agent_manage_router = APIRouter(
    dependencies=[Depends(require_knowledge_agent_manage_identity)],
)


def _get_engine(request: Request) -> KnowledgeEngine:
    # Engine is stored on AppState object at app.state.app_state
    app_state = getattr(request.app.state, "app_state", None)
    engine = getattr(app_state, "knowledge_engine", None) if app_state else None
    if engine is None:
        raise HTTPException(status_code=503, detail="Knowledge engine not available")
    return engine


def _ensure_enabled(engine: KnowledgeEngine) -> None:
    """Raise 503 if knowledge is disabled. Only for mutation endpoints."""
    if not engine._config.enabled:
        raise HTTPException(status_code=503, detail="Knowledge engine is disabled")


def _extract_task_error(task: dict[str, Any], fallback: str = "Ingestion failed") -> str:
    """Return the most useful per-file ingestion error from a task payload."""
    file_tasks = task.get("file_tasks")
    if isinstance(file_tasks, dict):
        for file_task in file_tasks.values():
            if isinstance(file_task, dict):
                error = file_task.get("error")
                if isinstance(error, str) and error.strip():
                    return error
    return fallback


# --- Enable (on-demand engine start) ---


@knowledge_agent_manage_router.post("/enable")
async def enable_knowledge(request: Request):
    """Start the knowledge engine on-demand if it is not already running.

    Called by the UI when the user toggles knowledge ON from a disabled state.
    Returns the health status after initialization.
    """
    app_state = getattr(request.app.state, "app_state", None)
    engine = getattr(app_state, "knowledge_engine", None) if app_state else None
    if engine is not None:
        return {"status": "already_running"}

    initializer = getattr(app_state, "initialize_knowledge_engine", None)
    if not initializer:
        raise HTTPException(status_code=503, detail="Knowledge engine initializer not available")

    try:
        from cuga.backend.knowledge.config import KnowledgeConfig

        kb_config = KnowledgeConfig.from_settings(__import__("cuga.config", fromlist=["settings"]).settings)
        kb_config.enabled = True  # Force enable regardless of settings file
        await initializer(app_state, kb_config)
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start knowledge engine: {e}")


# --- Health ---


@knowledge_router.get("/health")
async def health(request: Request):
    app_state = getattr(request.app.state, "app_state", None)
    subsystem = getattr(app_state, "get_subsystem_status", lambda _name: {"state": "unknown"})("knowledge")
    engine = getattr(app_state, "knowledge_engine", None) if app_state else None
    enabled = bool(engine and getattr(getattr(engine, "_config", None), "enabled", False))

    if engine is None:
        return {
            "healthy": False,
            "enabled": False,
            "status": subsystem.get("state", "unknown"),
            "message": subsystem.get("message", "Knowledge engine not available"),
            "details": subsystem.get("details", {}),
        }

    # Resolve collection via identity if available (optional for health)
    collection = None
    try:
        identity = await require_internal_or_auth(request)
        collection = resolve_collection(identity, "agent", request)
    except HTTPException:
        pass  # Auth not available — return basic health without stale info
    h = await engine.health(collection=collection)
    status_state = subsystem.get("state", "unknown")
    result: dict[str, Any] = {
        "healthy": enabled and status_state == "ready" and h["status"] == "healthy",
        "enabled": enabled,
        "agent_level_enabled": bool(getattr(getattr(engine, "_config", None), "agent_level_enabled", True)),
        "session_level_enabled": bool(
            getattr(getattr(engine, "_config", None), "session_level_enabled", True)
        ),
        "status": status_state,
        "message": subsystem.get("message", ""),
        "details": subsystem.get("details", {}),
        "settings": h["settings"],
        "embeddings_initialized": h.get("embeddings_initialized", False),
    }
    if collection:
        result["stale"] = h.get("stale", False)
        result["reindex_deferred"] = h.get("reindex_deferred", False)
        result["reindex_in_progress"] = h.get("reindex_in_progress", [])
    return result


# --- Settings ---


@knowledge_agent_manage_router.get("/settings")
async def get_settings(request: Request):
    engine = _get_engine(request)
    return engine.get_settings()


@knowledge_agent_manage_router.post("/settings")
async def update_settings(request: Request):
    engine = _get_engine(request)
    body = await request.json()
    knowledge_settings = body.get("knowledge", body)
    return engine.update_settings(**knowledge_settings)


# --- Search ---


@knowledge_router.post("/search")
async def search(
    request: Request,
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    _ensure_enabled(engine)
    body = await request.json()
    scope = body.get("scope", "agent")
    collection = resolve_collection(identity, scope, request)

    results = await engine.search(
        collection=collection,
        query=body.get("query", ""),
        limit=body.get("limit", engine._config.default_limit),
        score_threshold=body.get("score_threshold", engine._config.default_score_threshold),
    )
    include_scores = body.get("include_scores", False)
    return {
        "results": [
            {
                "text": r.text,
                "filename": r.filename,
                "page": r.page,
                **({"score": r.score} if include_scores else {}),
            }
            for r in results
        ]
    }


# --- Documents ---


@knowledge_router.post("/documents")
async def upload_documents(
    request: Request,
    files: list[UploadFile] = File(...),
    scope: str = Form("agent"),
    replace_duplicates: bool = Form(True),
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    _ensure_enabled(engine)
    ensure_agent_scope_manage_if_needed(identity, scope)
    collection = resolve_collection(identity, scope, request)

    if len(files) > engine._config.max_files_per_request:
        raise HTTPException(
            status_code=400, detail=f"Max {engine._config.max_files_per_request} files per request"
        )

    import tempfile
    from pathlib import Path

    # Process each file: validate, ingest (awaited), return final status.
    # Frontend sends 1 file per request for real-time per-file feedback.
    # Multiple files still supported for SDK/MCP callers.
    results: list[dict] = []

    for upload_file in files:
        with tempfile.NamedTemporaryFile(suffix=f"_{upload_file.filename}", delete=False) as tmp:
            content = await upload_file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        original_name = upload_file.filename or "unnamed"

        try:
            filename_clean = await engine._sanitize_and_validate(
                collection, tmp_path, replace_duplicates, original_name
            )
        except ReindexInProgressError:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="Reindex in progress, try again later")
        except (DocumentExistsError, FileTooLargeError, IngestionQueueFullError) as e:
            tmp_path.unlink(missing_ok=True)
            if isinstance(e, DocumentExistsError):
                raise HTTPException(status_code=409, detail=f"file already indexed: {e.filename}")
            elif isinstance(e, FileTooLargeError):
                raise HTTPException(
                    status_code=400, detail=f"File too large: {e.size} bytes (max {e.max_size})"
                )
            else:
                raise HTTPException(status_code=429, detail=f"ingestion queue full (max {e.max_pending})")

        try:
            task_info = await engine._create_task_entry(collection, filename_clean)
        except ReindexInProgressError:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="Reindex in progress, try again later")

        # Await ingestion — response returns final status (no polling)
        try:
            await engine._run_ingest(
                collection, tmp_path, filename_clean, task_info["task_id"], replace_duplicates
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        task = await engine.get_task(task_info["task_id"])
        task = task or {"task_id": task_info["task_id"], "filename": filename_clean, "status": "unknown"}

        if len(files) == 1 and task.get("status") == "failed":
            raise HTTPException(status_code=400, detail=_extract_task_error(task))

        results.append(task)

    if len(results) == 1:
        return results[0]
    return {"tasks": results}


@knowledge_router.post("/documents/url")
async def ingest_url(
    request: Request,
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    _ensure_enabled(engine)
    body = await request.json()
    scope = body.get("scope", "agent")
    ensure_agent_scope_manage_if_needed(identity, scope)
    collection = resolve_collection(identity, scope, request)
    url = body.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        return await engine.ingest_url(collection, url)
    except ReindexInProgressError:
        raise HTTPException(status_code=409, detail="Reindex in progress, try again later")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IngestionQueueFullError as e:
        raise HTTPException(status_code=429, detail=f"ingestion queue full (max {e.max_pending})")


@knowledge_router.get("/documents")
async def list_documents(
    request: Request,
    scope: str = Query("agent"),
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    collection = resolve_collection(identity, scope, request)
    docs = await engine.list_documents(collection)
    return {
        "documents": [
            {
                "filename": d.filename,
                "chunk_count": d.chunk_count,
                "status": d.status,
                "ingested_at": d.ingested_at,
                "preview": d.preview,
            }
            for d in docs
        ]
    }


@knowledge_router.get("/documents/file")
async def get_document_file(
    request: Request,
    scope: str = Query("agent"),
    filename: str = Query(...),
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    collection = resolve_collection(identity, scope, request)
    try:
        file_path = engine.get_document_file_path(collection, filename)
    except DocumentNotFoundError:
        raise HTTPException(status_code=404, detail="document not found")

    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        file_path,
        filename=file_path.name,
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{file_path.name}"'},
    )


@knowledge_router.delete("/documents")
async def delete_document(
    request: Request,
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    _ensure_enabled(engine)
    body = await request.json()
    scope = body.get("scope", "agent")
    ensure_agent_scope_manage_if_needed(identity, scope)
    filename = body.get("filename", "")
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")

    collection = resolve_collection(identity, scope, request)
    try:
        await engine.delete_document(collection, filename)
        return {"status": "ok"}
    except DocumentNotFoundError:
        raise HTTPException(status_code=404, detail="document not found")


@knowledge_router.delete("/session")
async def delete_session_collection(
    request: Request,
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    _ensure_enabled(engine)
    collection = resolve_collection(identity, "session", request)

    await engine.drop_collection(collection)

    app_state = getattr(request.app.state, "app_state", None)
    provider = getattr(app_state, "knowledge_provider", None) if app_state else None
    if provider and identity.thread_id:
        provider.delete_session(identity.thread_id)

    return {"status": "ok"}


# --- Reindex ---


@knowledge_router.post("/reindex")
async def reindex_collection(
    request: Request,
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    _ensure_enabled(engine)
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    scope = body.get("scope", "agent")
    ensure_agent_scope_manage_if_needed(identity, scope)
    collection = resolve_collection(identity, scope, request)
    try:
        return await engine.reindex(collection)
    except ReindexBusyError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reindex: {e.pending_count} upload(s) in progress",
        )


# --- Tasks ---


@knowledge_router.get("/tasks")
async def list_tasks(
    request: Request,
    scope: str = Query("agent"),
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    collection = resolve_collection(identity, scope, request)
    tasks = await engine.get_tasks(collection)
    return {"tasks": tasks}


@knowledge_router.get("/tasks/{task_id}")
async def get_task(
    request: Request,
    task_id: str,
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    task = await engine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    # Verify caller has access to task's collection
    scope = "session" if task["collection"].startswith("kb_sess_") else "agent"
    try:
        expected_collection = resolve_collection(identity, scope, request)
    except HTTPException as e:
        # Re-raise 400 (missing headers) as-is; only mask unknown errors as 403
        if e.status_code == 400:
            raise
        raise HTTPException(status_code=403, detail="access denied")

    if task["collection"] != expected_collection:
        raise HTTPException(status_code=403, detail="access denied")

    return task


@knowledge_router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    request: Request,
    task_id: str,
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    engine = _get_engine(request)
    task = await engine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    # Verify ownership
    scope = "session" if task["collection"].startswith("kb_sess_") else "agent"
    try:
        expected_collection = resolve_collection(identity, scope, request)
    except HTTPException as e:
        if e.status_code == 400:
            raise
        raise HTTPException(status_code=403, detail="access denied")

    if task["collection"] != expected_collection:
        raise HTTPException(status_code=403, detail="access denied")

    if scope == "agent":
        ensure_agent_knowledge_manage_access(identity)

    result = await engine.cancel_task(task_id)
    return result


knowledge_router.include_router(knowledge_agent_manage_router)
