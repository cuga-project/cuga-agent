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
    resolve_agent_collection,
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


# Stage weights for the unified upload progress bar. Tuned empirically
# from per-stage timings (parse_s / embed_s / insert_s) over a mixed
# corpus; values sum to 1.0 and intentionally overweight parse since
# it's the most variable stage.
_STAGE_WEIGHTS = {"parse": 0.45, "embed": 0.40, "insert": 0.15}

# Module-level strong-ref set for fire-and-forget background ingests. The
# asyncio event loop holds only WEAK references to tasks created with
# ``create_task`` (CPython docs explicitly warn: "A task that isn't
# referenced elsewhere may be garbage collected at any time, even before
# it's done."). Request-local lists drop refs the moment the handler
# returns — exactly the window where a fire-and-forget ingest is most
# vulnerable. Keep them here; the done_callback discards on completion
# so the set bounds at "currently-running ingests".
_BACKGROUND_INGEST_TASKS: set[Any] = set()


def _weighted_pct_for_file_task(file_task: dict[str, Any]) -> float:
    """Map a file_task's current stage + progress into a 0..1 overall fraction.

    Each engine emit overwrites the whole file_task entry, so we infer prior
    stages from the current one: being in "embed" implies parse is done, etc.
    Indeterminate windows (status=processing but no stage yet) report a small
    nonzero value so the bar starts moving immediately.
    """
    status = file_task.get("status")
    if status == "indexed":
        return 1.0
    if status in ("failed", "cancelled", "skipped"):
        return 0.0

    parse_w = _STAGE_WEIGHTS["parse"]
    embed_w = _STAGE_WEIGHTS["embed"]
    insert_w = _STAGE_WEIGHTS["insert"]

    stage = file_task.get("stage")
    progress = file_task.get("progress") or {}
    done = int(progress.get("done", 0) or 0)
    total = int(progress.get("total", 0) or 0)
    frac = (done / total) if total > 0 else 0.0
    frac = max(0.0, min(1.0, frac))

    if stage == "parsed":
        return parse_w
    if stage == "embed":
        return parse_w + embed_w * frac
    if stage == "insert_start":
        return parse_w + embed_w
    if stage == "insert":
        return parse_w + embed_w + insert_w * frac
    if status == "processing":
        return 0.03  # just started, give the bar a visible nudge
    return 0.0


def _enrich_task(task: dict[str, Any]) -> dict[str, Any]:
    """Attach UI-friendly fields to the task payload.

    - ``weighted_pct`` (0..1): single number driving the frontend bar; stage
      internals are kept server-side so we can re-tune weights without a
      frontend deploy.
    - ``ui_phase``: distinguishes the indistinguishable-from-the-outside
      states the UI must render differently. ``queued`` (waiting on the
      per-collection lock), ``working`` (ingest actively running),
      ``done``, ``failed``.

    Single-file uploads use the first file_task entry; multi-file (legacy)
    tasks aggregate via mean.
    """
    file_tasks = task.get("file_tasks") or {}
    if isinstance(file_tasks, dict) and file_tasks:
        pcts = [_weighted_pct_for_file_task(ft) for ft in file_tasks.values() if isinstance(ft, dict)]
        if pcts:
            task["weighted_pct"] = round(sum(pcts) / len(pcts), 4)
    if "weighted_pct" not in task:
        task["weighted_pct"] = 1.0 if task.get("status") == "completed" else 0.0

    status = task.get("status")
    if status == "completed":
        task["ui_phase"] = "done"
    elif status in ("failed", "cancelled"):
        task["ui_phase"] = "failed"
    elif status == "running":
        task["ui_phase"] = "working"
    else:
        # pending: either freshly created or blocked on the per-collection lock.
        # Either way, no stage progress has been emitted yet — surface this so
        # the UI can show "Queued" instead of a frozen 3% bar.
        task["ui_phase"] = "queued"
    return task


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
        # Live availability of the ACTIVE embedder. When false, the collection's
        # vectors can't be searched (query embedding fails) — the UI warns.
        "embedder_available": h.get("embedder_available"),
        "embedder_error": h.get("embedder_error"),
        "embedder_model": h.get("embedder_model"),
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
    try:
        return engine.update_settings(**knowledge_settings)
    except ValueError as e:
        # Config validation errors (typo'd enum, bad numeric range, etc.)
        # surface as 400 with the validator's message instead of a 500
        # stacktrace. The validator messages already name the offending
        # field, so the operator can fix the request without digging logs.
        raise HTTPException(status_code=400, detail=str(e))


# --- Search ---


_SCOPE_LEGEND = {
    "agent": "Persistent knowledge available across all conversations (configured at agent setup).",
    "session": "Documents uploaded in this conversation only.",
}


@knowledge_router.post("/search")
async def search(
    request: Request,
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    """Search the knowledge base.

    ``scope`` accepts:
      - ``"agent"`` — search only the persistent agent KB.
      - ``"session"`` — search only this conversation's KB. **Auto-fallback**:
        if 0 results above threshold AND the agent scope is wired, the
        engine internally retries as ``scope="all"`` and the response
        carries ``retrieval.fallback_from = "session"``. Saves the LLM a
        round-trip + makes "prefer narrow" safe.
      - ``"all"`` — search both, merge via per-scope quota + cross-scope
        RRF (so a thin scope can't be drowned by a fat one). Scopes the
        caller is not authorized for, or that are disabled in config,
        are silently skipped; if none remain the response is empty.

    Response envelope shape — see ``envelope.build_retrieval_envelope``.
    The single source of truth for the wire schema lives there so the
    SDK + HTTP route can't drift.
    """
    from cuga.backend.knowledge.envelope import build_retrieval_envelope

    engine = _get_engine(request)
    _ensure_enabled(engine)
    body = await request.json()
    scope = body.get("scope", "agent")
    query = body.get("query", "")
    limit = body.get("limit", engine._config.default_limit)
    score_threshold = body.get("score_threshold", engine._config.default_score_threshold)
    include_scores = body.get("include_scores", False)
    filter_mode = getattr(engine._config, "search_junk_filter", "dry_run")

    _tid_preview = (identity.thread_id or "")[:12] + "..." if identity.thread_id else "-"
    _query_preview = (query or "")[:60]

    async def _run_multi(scope_requested: str, fallback_from: str | None):
        """Run ``scope='all'`` style multi-scope search. Returns the
        finalized envelope dict ready to ship to the wire. Centralized
        here so the auto-fallback path and the direct ``scope='all'``
        path produce identical envelopes.
        """
        scoped_collections: list[tuple[str, str]] = []
        for _scope in ("agent", "session"):
            try:
                scoped_collections.append((_scope, resolve_collection(identity, _scope, request)))
            except HTTPException as e:
                logger.debug(
                    "search scope=all: dropping scope=%s (%s: %s)",
                    _scope,
                    e.status_code,
                    e.detail,
                )
        # Per-scope cap (Option B) is the right default when the caller
        # *asked* for "all" — they want each side's best chunks. The
        # auto-fallback path (session → 0 hits → retry as "all") instead
        # passes ``fallback_from="session"``; in that case revert to the
        # fixed-total budget so the LLM's response-size expectation is
        # preserved (it asked for ``limit``, not ``limit × n_scopes``).
        results, multi_stats = await engine.search_multi(
            scoped_collections=scoped_collections,
            query=query,
            limit=limit,
            score_threshold=score_threshold,
            per_scope_limit=(fallback_from is None),
        )
        env = build_retrieval_envelope(
            results=results,
            scope_requested=scope_requested,
            multi_stats=multi_stats,
            single_stats=None,
            single_scope_name=None,
            filter_mode=filter_mode,
            fallback_from=fallback_from,
            include_scores=include_scores,
        )
        _emit_canonical_log(
            tid_preview=_tid_preview,
            query_preview=_query_preview,
            scope_requested=scope_requested,
            env=env,
        )
        return env

    if scope == "all":
        return await _run_multi(scope_requested="all", fallback_from=None)

    # Single-scope path. ``resolve_collection`` raises 403/400 on
    # disabled/unauthorized so explicit callers get a clear error
    # instead of silent emptiness.
    collection = resolve_collection(identity, scope, request)
    results, single_stats = await engine.search_with_stats(
        collection=collection,
        query=query,
        limit=limit,
        score_threshold=score_threshold,
        scope=scope,
    )
    # Stamp ``scope`` on any result that's missing it (legacy paths,
    # mock fakes) so the wire shape is identical to the multi-scope
    # branch — one chunk schema regardless of how the search was issued.
    for r in results:
        if not r.scope:
            r.scope = scope

    # Auto-fallback: scope="session" + 0 hits above threshold + agent is
    # wired → internally re-run as "all". Makes B's "prefer narrow"
    # contract rule risk-free for the LLM. Single internal retry, no
    # loops (the second call switches scope so termination is structural).
    if scope == "session" and not results:
        # Check if agent is even available before retrying. If not,
        # just return the empty session envelope — there's nowhere to
        # fall back to.
        agent_available = True
        try:
            resolve_collection(identity, "agent", request)
        except HTTPException:
            agent_available = False
        if agent_available:
            logger.info(
                "search session: 0 hits → auto-fallback to scope='all' (thread_id=%s query=%r)",
                _tid_preview,
                _query_preview,
            )
            return await _run_multi(scope_requested="all", fallback_from="session")

    env = build_retrieval_envelope(
        results=results,
        scope_requested=scope,
        multi_stats=None,
        single_stats=single_stats,
        single_scope_name=scope,
        filter_mode=filter_mode,
        fallback_from=None,
        include_scores=include_scores,
    )
    _emit_canonical_log(
        tid_preview=_tid_preview,
        query_preview=_query_preview,
        scope_requested=scope,
        env=env,
    )
    return env


def _emit_canonical_log(
    *,
    tid_preview: str,
    query_preview: str,
    scope_requested: str,
    env: dict[str, Any],
) -> None:
    """Emit a single canonical INFO line per search.

    One log per search × one schema = SRE can grep ``search retrieval``
    and answer "did session match?", "what was filtered and why?",
    "did the engine auto-fallback?" without enabling DEBUG. Replaces
    the previous "interesting case → INFO; else DEBUG" branching
    which missed the user-reported failure (1 session hit looked
    fine in the trigger but the user couldn't tell what was filtered).
    """
    retrieval = env.get("retrieval", {})
    by_scope = retrieval.get("by_scope", {})
    hits_by_scope = {s: v.get("returned", 0) for s, v in by_scope.items()}
    filtered_by_scope = {s: v.get("filtered", 0) for s, v in by_scope.items()}
    candidates_by_scope = {s: v.get("candidates", 0) for s, v in by_scope.items()}
    reasons_by_scope = {s: v["reasons"] for s, v in by_scope.items() if v.get("reasons")}
    logger.info(
        "search retrieval thread_id=%s query=%r scope=%s "
        "searched=%s failed=%s partial=%s fallback_from=%s "
        "hits_by_scope=%s candidates_by_scope=%s "
        "filtered_by_scope=%s reasons_by_scope=%s "
        "recommendation=%s",
        tid_preview,
        query_preview,
        scope_requested,
        list(by_scope.keys()),
        retrieval.get("failed_scopes", []),
        retrieval.get("partial", False),
        retrieval.get("fallback_from"),
        hits_by_scope,
        candidates_by_scope,
        filtered_by_scope,
        reasons_by_scope,
        retrieval.get("recommendation"),
    )


# --- Documents ---


@knowledge_router.post("/documents")
async def upload_documents(
    request: Request,
    files: list[UploadFile] = File(...),
    scope: str = Form("agent"),
    replace_duplicates: bool = Form(True),
    wait: bool = Form(True),
    identity: KnowledgeIdentity = Depends(require_internal_or_auth),
):
    """Upload one or more documents.

    ``wait=true`` (default) preserves the legacy synchronous contract: the
    response carries the terminal task (status=completed|failed). The MCP
    ``ingest_knowledge`` tool and the session-attachment hook depend on this.

    ``wait=false`` returns 202 with a ``queued`` task per file as soon as
    validation passes. The caller polls ``GET /tasks/{task_id}`` for
    ``weighted_pct`` and the terminal status. Used by the Knowledge Settings
    UI so the user gets a live progress bar instead of a frozen "Processing".
    """
    engine = _get_engine(request)
    _ensure_enabled(engine)
    ensure_agent_scope_manage_if_needed(identity, scope)
    collection = resolve_collection(identity, scope, request)

    if len(files) > engine._config.max_files_per_request:
        raise HTTPException(
            status_code=400, detail=f"Max {engine._config.max_files_per_request} files per request"
        )

    import asyncio
    import tempfile
    from pathlib import Path

    results: list[dict] = []

    async def _cleanup_after_ingest(coro: Any, tmp_path: Path, task_id: str, filename_for_error: str) -> None:
        """Run the background ingest and guarantee terminal state in metadata.

        ``_ingest_inner`` already marks the task as ``failed`` on parse/embed/
        insert errors, but ``_run_ingest`` runs setup steps (config load,
        vector-store cache, collection lock) outside that try/except. A raise
        there would leave the task stuck in ``pending`` forever, and the
        polling UI would spin until its 10-min cap. We catch unconditionally
        and write a terminal ``failed`` row so the frontend bar resolves.
        """
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 — intentional catch-all for background task
            logger.exception("Background ingest failed for task %s: %s", task_id, exc)
            try:
                await engine._metadata.update_task(
                    task_id,
                    status="failed",
                    processed_files=1,
                    failed_files=1,
                    file_tasks={
                        filename_for_error: {
                            "filename": filename_for_error,
                            "status": "failed",
                            "error": str(exc) or exc.__class__.__name__,
                        }
                    },
                )
            except Exception as meta_exc:  # noqa: BLE001
                logger.exception("Failed to mark task %s as failed: %s", task_id, meta_exc)
        finally:
            tmp_path.unlink(missing_ok=True)

    # Note: strong refs to fire-and-forget tasks live in the module-level
    # ``_BACKGROUND_INGEST_TASKS`` set, not here. Request-local storage
    # would be GC'd the moment the handler returns.

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

        if wait:
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

            results.append(_enrich_task(task))
        else:
            # Fire-and-forget background ingest. ``_cleanup_after_ingest``
            # guarantees the temp file is unlinked AND the task lands in a
            # terminal state even if ``_run_ingest`` raises before
            # ``_ingest_inner`` can record the failure.
            bg = asyncio.create_task(
                _cleanup_after_ingest(
                    engine._run_ingest(
                        collection,
                        tmp_path,
                        filename_clean,
                        task_info["task_id"],
                        replace_duplicates,
                    ),
                    tmp_path,
                    task_info["task_id"],
                    filename_clean,
                )
            )
            _BACKGROUND_INGEST_TASKS.add(bg)
            # Two callbacks: discard from the strong-ref set on completion
            # (bounds the set at currently-running ingests), and retrieve
            # the exception so we don't see "Task exception was never
            # retrieved" warnings in the loop log.
            bg.add_done_callback(_BACKGROUND_INGEST_TASKS.discard)
            bg.add_done_callback(lambda t: t.exception())
            results.append(
                _enrich_task(
                    {
                        "task_id": task_info["task_id"],
                        "collection": collection,
                        "filename": filename_clean,
                        "status": "pending",
                        "file_tasks": {filename_clean: {"filename": filename_clean, "status": "pending"}},
                    }
                )
            )

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
    # Explicit guard: session scope without a thread_id is an error, not an
    # empty result. ``resolve_collection`` already 400s on this, but stating
    # it at the route level makes the contract obvious and matches the
    # /search route — and rules out any chance of an unrelated session
    # initialization path silently producing an empty list when the caller
    # never identified the conversation.
    if scope == "session" and not (identity.thread_id or "").strip():
        raise HTTPException(
            status_code=400,
            detail="X-Thread-ID required for scope=session (cannot list session documents without identifying the conversation)",
        )
    engine = _get_engine(request)
    collection = resolve_collection(identity, scope, request)
    docs = await engine.list_documents(collection)
    # When session-scope list comes back empty even though the caller
    # identified a thread, log it. Pairs with the search route returning
    # docs from the same collection — if the two diverge, this log gives
    # us the collection name to inspect.
    if scope == "session" and not docs:
        logger.info(
            "list_documents: session scope returned empty for collection=%s, thread_id=%s",
            collection,
            (identity.thread_id or "")[:12] + "...",
        )
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
    except ReindexInProgressError:
        # Delete is rejected while this collection is being reindexed —
        # otherwise the doc would be re-embedded into the in-flight target and
        # resurrect after the pointer flips. 409 mirrors the upload guard shape.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "reindex_in_progress",
                "collections": [collection],
                "message": "A re-index is running. Wait for it to finish before deleting documents.",
            },
        )


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
    # Enforce scope-enabled + ownership (raises 403/400); also the exact
    # collection for the session branch.
    collection = resolve_collection(identity, scope, request)
    if scope == "agent":
        # Return tasks across ALL of this agent's collections (active +
        # in-flight), not just the active one. A config-change reindex
        # (reindex_for_config) ingests into a NEW collection and DEFERS the
        # pointer flip, so the in-flight collection is not the active one
        # until promotion. Polling only the active collection leaves the
        # reindex tile frozen at "Pending" for the whole run, then jumps to
        # done at flip — no live per-document progress. The base prefix (no
        # hash) matches every collection this agent owns, and the FE filters
        # to its own task_ids; other agents'/sessions' tasks are excluded.
        base = resolve_agent_collection(identity.agent_id, None)
        # ponytail: full task scan + Python prefix filter. The task table is
        # small (ingestion jobs, not user traffic). If it ever grows, push
        # the prefix into the store query (collection = base OR LIKE base_%).
        all_tasks = await engine.get_tasks(None)
        tasks = [
            t for t in all_tasks if (c := str(t.get("collection", ""))) == base or c.startswith(f"{base}_")
        ]
    else:
        tasks = await engine.get_tasks(collection)
    return {"tasks": tasks}


def _caller_owns_task_collection(
    identity: KnowledgeIdentity, scope: str, collection: str, expected_collection: str
) -> bool:
    """Whether the caller owns the task's collection.

    Agent tasks may live on ANY of the agent's collections — including the
    in-flight TARGET of a deferred-flip reindex, whose hash differs from the
    active pointer — so match the base prefix (the SAME ownership rule the
    GET /tasks list endpoint uses). Session tasks require an exact match.
    An exact-match here would 403 every in-flight reindex task for the whole
    deferred window, freezing the live-progress tile.
    """
    if scope == "agent":
        base = resolve_agent_collection(identity.agent_id, None)
        return collection == base or collection.startswith(f"{base}_")
    return collection == expected_collection


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

    if not _caller_owns_task_collection(identity, scope, task["collection"], expected_collection):
        raise HTTPException(status_code=403, detail="access denied")

    return _enrich_task(task)


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

    if not _caller_owns_task_collection(identity, scope, task["collection"], expected_collection):
        raise HTTPException(status_code=403, detail="access denied")

    if scope == "agent":
        ensure_agent_knowledge_manage_access(identity)

    result = await engine.cancel_task(task_id)
    return result


knowledge_router.include_router(knowledge_agent_manage_router)
