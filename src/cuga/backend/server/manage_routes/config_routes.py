"""Config read/publish/history/delete endpoints."""

from collections.abc import Mapping
from typing import Any, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

import asyncio as _asyncio

from cuga.backend.server.manage_routes.apply import apply_published_config, rebuild_production_agent
from cuga.backend.server.manage_routes.helpers import (
    agent_draft_lock,
    app_state,
    merge_feature_flags_defaults,
    merge_mcp_yaml_into_config,
    redact_secrets_in_config,
)
from cuga.backend.server.manage_routes.knowledge_reindex import (
    _BACKGROUND_TASKS,
    deferred_reindex_complete_and_flip,
)
from cuga.backend.server.manage_routes.router import router


@router.get("/config")
async def get_manage_config(
    request: Request,
    version: Optional[str] = None,
    draft: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    """Get config: ?draft=1 returns draft; ?version=N returns that version; ?agent_id=X for specific agent; else latest published."""
    try:
        from cuga.backend.server.config_store import load_config, load_draft

        # Determine agent_id from parameter or X-Use-Draft header (backward compatibility)
        if agent_id is None:
            agent_id = "cuga-default"
        use_draft = str(draft or "").lower() in ("1", "true", "yes", "on")
        if use_draft:
            config = await load_draft(agent_id)
            if config is None:
                config, _ = await load_config(None, agent_id)
            if config is None:
                return JSONResponse({"config": {}, "version": "draft", "agent_id": agent_id})
            merge_mcp_yaml_into_config(config)
            merge_feature_flags_defaults(config)
            redact_secrets_in_config(config)
            return JSONResponse({"config": config, "version": "draft", "agent_id": agent_id})
        config, ver = await load_config(version, agent_id)
        if config is None:
            return JSONResponse({"config": {}, "agent_id": agent_id})
        merge_mcp_yaml_into_config(config)
        merge_feature_flags_defaults(config)
        redact_secrets_in_config(config)
        return JSONResponse({"config": config, "version": ver, "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to load manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config")
async def save_manage_config_publish(request: Request, agent_id: Optional[str] = None):
    """Create new version from current config and apply to agent (live)."""
    # IMPORTANT: apply_published_config is called at startup (main.py:488,582).
    # Do NOT add knowledge/reindex logic there. Knowledge apply belongs here only.
    if agent_id is None:
        agent_id = "cuga-default"

    state = app_state(request)
    if state is None:
        raise HTTPException(status_code=500, detail="App state not available")

    # Reject a publish while a reindex is in flight for this agent. Publish
    # calls prepare/commit_knowledge_update directly (not apply_knowledge_config),
    # so the engine's mid-reindex guard never runs here; without this a
    # vector-affecting publish during an in-flight Re-index supersedes every
    # in-flight worker (wasting the reindex) and races the active-pointer write.
    import re as _re_guard

    _pfx = f"kb_agent_{_re_guard.sub(r'[^a-zA-Z0-9_]', '_', agent_id)}"
    _pub_engine = getattr(state, "knowledge_engine", None)
    _in_flight = getattr(_pub_engine, "_reindex_in_progress", None)
    if _in_flight:
        _busy = sorted(c for c in _in_flight if c == _pfx or c.startswith(f"{_pfx}_"))
        if _busy:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reindex_in_progress",
                    "collections": _busy,
                    "message": "A re-index is running for this agent. Wait for it to finish before publishing.",
                },
            )

    data = await request.json()
    config = data.get("config", data) or {}
    agent_meta = config.get("agent")
    if not isinstance(agent_meta, dict) or not (agent_meta.get("name") or "").strip():
        return JSONResponse(
            status_code=400,
            content={"detail": "Agent name is required"},
        )

    # Serialize the whole publish critical section against a concurrent
    # same-agent PATCH / reindex_for_config_change — all three mutate
    # engine._config + bump _apply_generation; the lock (non-reentrant) makes
    # them mutually exclusive. The deferred-flip tasks are fire-and-forget and
    # acquire the lock later, after this handler releases it.
    _pub_lock = agent_draft_lock(str(agent_id))
    await _pub_lock.acquire()
    engine = None
    _old_collection = None
    _flip_spawned = False
    try:
        # Re-check the in-flight guard now that we hold the lock. The pre-lock
        # check above can go stale: a reindex_for_config_change may have acquired
        # the lock first and started workers between that check and this acquire.
        _busy2 = sorted(
            c
            for c in (getattr(getattr(state, "knowledge_engine", None), "_reindex_in_progress", None) or ())
            if c == _pfx or c.startswith(f"{_pfx}_")
        )
        if _busy2:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reindex_in_progress",
                    "collections": _busy2,
                    "message": "A re-index is running for this agent. Wait for it to finish before publishing.",
                },
            )
        from cuga.backend.server.config_store import (
            load_config,
            load_draft,
            save_config,
            save_draft,
            update_published_config_at_version,
        )

        # Phase A: Validate + preflight knowledge (no mutation, no version save yet)
        prepared_knowledge = None
        knowledge_result = None
        knowledge_cfg = (config or {}).get("knowledge")
        engine = getattr(state, "knowledge_engine", None)

        # Ensure knowledge config is always present in published versions.
        # If missing, snapshot current engine config or use defaults.
        if not knowledge_cfg or not isinstance(knowledge_cfg, dict):
            from cuga.backend.knowledge.config import KnowledgeConfig as _KC

            knowledge_cfg = None
            if engine is not None:
                eng_attr = getattr(engine, "config", None)
                if eng_attr is not None:
                    to_dict = getattr(eng_attr, "to_dict", None)
                    if callable(to_dict):
                        # Publish snapshot — strip secrets so API keys do not
                        # cross machines via the snapshot store. Importing
                        # instances must supply their own keys.
                        try:
                            _snap = to_dict(include_secrets=False)
                        except TypeError:
                            _snap = to_dict()
                        if isinstance(_snap, dict):
                            knowledge_cfg = _snap
                    elif isinstance(eng_attr, dict):
                        knowledge_cfg = dict(eng_attr)
                if knowledge_cfg is None:
                    for _getter in ("get_config", "get_knowledge_config"):
                        _fn = getattr(engine, _getter, None)
                        if callable(_fn):
                            _cand = _fn()
                            if isinstance(_cand, dict):
                                knowledge_cfg = _cand
                                break
            if knowledge_cfg is None:
                knowledge_cfg = _KC().to_dict(include_secrets=False)
            if config is None:
                config = {}
            config["knowledge"] = knowledge_cfg

        if knowledge_cfg and isinstance(knowledge_cfg, dict):
            # Lazy engine creation: if knowledge was disabled at startup but user
            # enables it via Publish, create the engine now (full init with MCP + warmup).
            if not engine and knowledge_cfg.get("enabled", False):
                try:
                    from cuga.backend.knowledge.config import KnowledgeConfig

                    kb_config = KnowledgeConfig.coerce_and_validate(knowledge_cfg)
                    _initializer = getattr(state, "initialize_knowledge_engine", None)
                    if _initializer:
                        await _initializer(state, kb_config)
                        engine = state.knowledge_engine
                        logger.info("Knowledge engine created via publish (was disabled at startup)")
                    else:
                        logger.warning("Knowledge engine initializer not available")
                except Exception as e:
                    logger.warning(f"Failed to create knowledge engine on publish: {e}")
            if engine:
                try:
                    prepared_knowledge = engine.prepare_knowledge_update(knowledge_cfg)
                except (ValueError, TypeError) as ve:
                    raise HTTPException(status_code=400, detail=f"Invalid knowledge config: {ve}")

        # Vector-config hash: fields that affect stored vectors (embedding model,
        # chunk size, metric). When a file copy + reindex migration is required,
        # the persisted _vector_config_hash stays at the previous value until
        # that migration succeeds; see promotion after copy_source_files/reindex.
        from cuga.backend.knowledge.config import KnowledgeConfig as _KC
        from cuga.backend.knowledge.config import client_adaptation_hash as _cah
        from cuga.backend.knowledge.config import client_glossary_hash as _cgh

        _prev_hash = ""
        _prev_adapt_hash = ""
        _prev_gloss_hash = ""
        try:
            _prev_config, _ = await load_config(version=None, agent_id=agent_id)
            if _prev_config:
                _prev_hash = (_prev_config.get("knowledge") or {}).get("_vector_config_hash", "")
                _prev_adapt_hash = (_prev_config.get("knowledge") or {}).get("_adaptation_hash", "")
                _prev_gloss_hash = (_prev_config.get("knowledge") or {}).get("_glossary_hash", "")
        except Exception:
            pass

        try:
            _kb_obj = _KC.coerce_and_validate(knowledge_cfg)
            _vec_hash = _kb_obj.vector_config_hash()
        except Exception:
            _vec_hash = ""

        # Adaptation + glossary hashes for snapshot-level diffability.
        # NOT folded into _vector_config_hash on purpose (prompt edits don't
        # invalidate vectors). Stored as separate top-level metadata keys so
        # the UI can show "v3 → v4: adaptation changed / glossary changed"
        # without re-reading the bodies.
        _adapt_text = (
            knowledge_cfg.get("client_adaptation_text", "") if isinstance(knowledge_cfg, dict) else ""
        )
        _glossary_list = (
            knowledge_cfg.get("client_adaptation_glossary", []) if isinstance(knowledge_cfg, dict) else []
        )
        _new_adapt_hash = _cah(_adapt_text)
        _new_gloss_hash = _cgh(_glossary_list)
        if _prev_adapt_hash != _new_adapt_hash:
            logger.info(
                "cuga.knowledge.adaptation_published",
                extra={
                    "cuga_knowledge_adaptation_old_hash": _prev_adapt_hash,
                    "cuga_knowledge_adaptation_new_hash": _new_adapt_hash,
                    "cuga_knowledge_adaptation_new_len": len(_adapt_text),
                    "cuga_knowledge_agent_id": str(agent_id),
                    "cuga_knowledge_source": "publish",
                },
            )
        if _prev_gloss_hash != _new_gloss_hash:
            logger.info(
                "cuga.knowledge.glossary_published",
                extra={
                    "cuga_knowledge_glossary_old_hash": _prev_gloss_hash,
                    "cuga_knowledge_glossary_new_hash": _new_gloss_hash,
                    "cuga_knowledge_glossary_new_count": len(_glossary_list)
                    if isinstance(_glossary_list, list)
                    else 0,
                    "cuga_knowledge_agent_id": str(agent_id),
                    "cuga_knowledge_source": "publish",
                },
            )

        import re as _re_coll

        _san_id = _re_coll.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
        _new_collection = f"kb_agent_{_san_id}_{_vec_hash}" if _vec_hash else f"kb_agent_{_san_id}"
        _old_collection = f"kb_agent_{_san_id}_{_prev_hash}" if _prev_hash else f"kb_agent_{_san_id}"

        _migration_precheck_done = False
        _defer_vector_hash_promotion = False
        _target_docs_precheck = None
        _old_docs_precheck = None

        if engine and _vec_hash and _prev_hash != _vec_hash:
            try:
                _target_docs_precheck = await engine.list_documents(_new_collection)
                _old_docs_precheck = await engine.list_documents(_old_collection)
                _migration_precheck_done = True
                if not _target_docs_precheck and _old_docs_precheck:
                    _defer_vector_hash_promotion = True
            except Exception as _pre_err:
                logger.warning(f"Vector migration precheck failed: {_pre_err}")

        if _vec_hash:
            if _defer_vector_hash_promotion:
                config["knowledge"]["_vector_config_hash"] = _prev_hash
            else:
                config["knowledge"]["_vector_config_hash"] = _vec_hash

        # Persist the adaptation + glossary hashes alongside the vector hash
        # so the version history can show "adaptation changed" / "glossary
        # changed" indicators without re-reading snapshot bodies. Underscore
        # prefix follows the existing pattern for snapshot-level metadata
        # keys (filtered out by coerce_and_validate's known_fields filter on
        # the next round-trip).
        config["knowledge"]["_adaptation_hash"] = _new_adapt_hash
        config["knowledge"]["_glossary_hash"] = _new_gloss_hash

        # Snapshot knowledge state for import/export portability.
        # Stores collection name, persist_dir, pinned collection config, and
        # document metadata (filenames + file paths) so an imported config can
        # reconnect to existing on-disk knowledge data without re-ingestion.
        if engine:
            import re as _re_snap

            _current_hash = (
                (_prev_hash if _defer_vector_hash_promotion else _vec_hash)
                or getattr(state, "knowledge_config_hash", "")
                or _prev_hash
            )
            _snap_id = _re_snap.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
            _snap_col = f"kb_agent_{_snap_id}_{_current_hash}" if _current_hash else f"kb_agent_{_snap_id}"
            try:
                _state: dict[str, Any] = {
                    "collection": _snap_col,
                    "persist_dir": str(engine._config.persist_dir),
                }
                _col_cfg = await engine._metadata.get_collection_config(_snap_col)
                if _col_cfg and (isinstance(_col_cfg, dict) or isinstance(_col_cfg, Mapping)):
                    _state["collection_config"] = {
                        "embedding_provider": _col_cfg.get("embedding_provider", ""),
                        "embedding_model": _col_cfg.get("embedding_model", ""),
                        "embedding_dim": _col_cfg.get("embedding_dim"),
                    }
                _docs = await engine.list_documents(_snap_col)
                if _docs:
                    _files_dir = engine._files_dir / _snap_col
                    _state["documents"] = [
                        {
                            "filename": d.filename,
                            "file_path": str(_files_dir / d.filename),
                            "chunk_count": d.chunk_count,
                            "status": d.status,
                            "ingested_at": d.ingested_at,
                        }
                        for d in _docs
                    ]
                config["knowledge_state"] = _state
            except Exception as _snap_err:
                logger.warning(f"Failed to snapshot knowledge state: {_snap_err}")

        # Phase B: Save version + commit knowledge immediately after
        # Keep draft aligned with the just-published configuration because
        # Manage loads draft first on re-entry.
        await save_draft(config or {}, agent_id)
        # Strip secrets from the PUBLISHED snapshot only — the draft (saved
        # above) keeps the key so the local engine survives a restart.
        # Importing instances on other machines must supply their own keys.
        try:
            from cuga.backend.knowledge.config import KnowledgeConfig as _KC_strip

            published_config = dict(config or {})
            kb_pub = published_config.get("knowledge")
            if isinstance(kb_pub, dict):
                kb_pub = dict(kb_pub)
                for _secret in _KC_strip._SECRET_FIELDS:
                    if _secret in kb_pub and kb_pub[_secret]:
                        kb_pub[_secret] = ""
                published_config["knowledge"] = kb_pub
        except Exception as strip_err:
            # Fail CLOSED: never fall back to saving the unredacted config — that
            # would persist API keys into the published snapshot (which crosses
            # machines). A 500 is better than leaking a key.
            logger.error(f"Failed to strip secrets from published config: {strip_err!r}")
            raise HTTPException(status_code=500, detail="Failed to sanitize published config") from None
        ver = await save_config(published_config, agent_id)
        state.config_version = ver
        state.tools_include_version = int(ver) if ver else 0

        # NOTE: knowledge_config_hash is updated AFTER migration completes (below)
        # to avoid a race where queries hit the new (empty) collection during migration.

        # Commit knowledge to runtime (pure in-memory, cannot fail from infra)
        if prepared_knowledge:
            knowledge_result = engine.commit_knowledge_update(prepared_knowledge)

        # Phase C: Apply LLM/tools/policies + rebuild (existing, best-effort)
        # Strip knowledge_state before runtime apply — it's for export/import only.
        config.pop("knowledge_state", None)
        await apply_published_config(state, config or {})

        try:
            await rebuild_production_agent(state, config or {})
        except Exception as rebuild_err:
            logger.error(f"Failed to rebuild production agent graph: {rebuild_err}")

        response_data: dict[str, Any] = {"status": "success", "version": ver, "agent_id": agent_id}

        reindex_info = None

        if engine and _vec_hash and _prev_hash != _vec_hash:
            try:
                if (
                    _migration_precheck_done
                    and _target_docs_precheck is not None
                    and _old_docs_precheck is not None
                ):
                    target_docs = _target_docs_precheck
                else:
                    target_docs = await engine.list_documents(_new_collection)
                if target_docs:
                    logger.info(
                        "Knowledge config hash changed (%s -> %s) but target collection "
                        "already has %d doc(s), skipping migration",
                        _prev_hash or "(none)",
                        _vec_hash,
                        len(target_docs),
                    )
                else:
                    if _migration_precheck_done and _old_docs_precheck is not None:
                        old_docs = _old_docs_precheck
                    else:
                        old_docs = await engine.list_documents(_old_collection)
                    if old_docs:
                        logger.info(
                            "Knowledge config hash changed (%s -> %s), migrating %d doc(s) to new collection",
                            _prev_hash or "(none)",
                            _vec_hash,
                            len(old_docs),
                        )
                        # Keep the OLD (active) collection busy for the whole
                        # migration + embed window so uploads/deletes can't land
                        # in it and be lost when the deferred flip promotes the
                        # new hash (mirrors the Re-index path). Released in the
                        # deferred flip's finally, or in the promotion block /
                        # outer finally for the non-deferred outcomes.
                        engine._reindex_in_progress.add(_old_collection)
                        try:
                            await engine.copy_source_files(_old_collection, _new_collection)
                            reindex_info = await engine.reindex(_new_collection)
                        except Exception as mig_err:
                            logger.warning(f"Document migration failed: {mig_err}")
                            reindex_info = {"status": "failed", "error": str(mig_err)}
            except Exception as e:
                logger.warning(f"Failed to check docs for migration: {e}")

        if _defer_vector_hash_promotion and ver:
            _persist_status = reindex_info.get("status") if reindex_info else None
            # Persist the new vector hash ONLY for a terminal success. For
            # "started" (async reindex in flight) the strict deferred flip owns
            # the promotion + durable persist, AFTER all workers succeed — writing
            # the hash here would activate the new/incomplete collection on a
            # restart or a later worker failure, breaking the "old collection
            # stays active until the flip succeeds" guarantee. failed/busy/None
            # must not promote either.
            if _persist_status not in ("failed", "started", "busy", None):
                try:
                    pub_cfg, _ = await load_config(version=ver, agent_id=agent_id)
                    if pub_cfg and isinstance(pub_cfg.get("knowledge"), dict):
                        pub_cfg["knowledge"]["_vector_config_hash"] = _vec_hash
                        await update_published_config_at_version(pub_cfg, agent_id, ver)
                    draft_for_hash = await load_draft(agent_id)
                    if draft_for_hash and isinstance(draft_for_hash.get("knowledge"), dict):
                        draft_for_hash["knowledge"]["_vector_config_hash"] = _vec_hash
                        await save_draft(draft_for_hash, agent_id)
                except Exception as promo_err:
                    logger.warning(
                        "Migrated knowledge files but failed to persist new _vector_config_hash: %s",
                        promo_err,
                    )

        if not reindex_info and knowledge_result and knowledge_result.get("reindex_recommended") and engine:
            try:
                docs = await engine.list_documents(_new_collection)
                if docs:
                    try:
                        reindex_info = await engine.reindex(_new_collection)
                    except Exception as reindex_err:
                        from cuga.backend.knowledge.engine import ReindexBusyError

                        if isinstance(reindex_err, ReindexBusyError):
                            reindex_info = {
                                "status": "busy",
                                "detail": f"{reindex_err.pending_count} upload(s) in progress",
                                "pending_count": reindex_err.pending_count,
                            }
                            engine._reindex_deferred.add(_new_collection)
                        else:
                            logger.warning(f"Reindex failed: {reindex_err}")
            except Exception as e:
                logger.warning(f"Failed to check docs for reindex: {e}")

        if _vec_hash:
            _reindex_status = reindex_info.get("status") if reindex_info else None
            _pub_task_ids = (reindex_info or {}).get("task_ids") or []
            if _reindex_status == "started" and _pub_task_ids:
                # Async reindex in flight: promoting now would activate an
                # empty/still-filling collection and skip the strict flip.
                # Defer to the SAME strict flip as the Re-index path — it
                # promotes + persists ONLY after all workers succeed and the
                # engine still hashes to _vec_hash. The OLD collection stays
                # active AND busy until the flip's finally releases it, so
                # concurrent uploads/deletes can't be lost meanwhile.
                async def _pub_flip_then_release():
                    try:
                        await deferred_reindex_complete_and_flip(
                            agent_id, engine, state, _new_collection, _vec_hash, _pub_task_ids
                        )
                    finally:
                        engine._reindex_in_progress.discard(_old_collection)
                        engine._reindex_deferred.discard(_old_collection)

                _bg = _asyncio.create_task(_pub_flip_then_release())
                _BACKGROUND_TASKS.add(_bg)
                _bg.add_done_callback(_BACKGROUND_TASKS.discard)
                _bg.add_done_callback(lambda t: t.exception())
                _flip_spawned = True
                logger.info(
                    "Publish: reindex started for %s; deferring strict pointer flip "
                    "(all workers must succeed). Old collection stays active+busy meanwhile.",
                    _new_collection,
                )
            elif _reindex_status == "failed":
                logger.error(
                    "Keeping knowledge_config_hash=%r after vector-config migration/reindex failure "
                    "(would have switched to %s)",
                    getattr(state, "knowledge_config_hash", None),
                    _vec_hash,
                )
            elif _reindex_status == "started":
                logger.warning(
                    "Publish: reindex of %s returned 'started' without task_ids; "
                    "NOT flipping pointer (would risk an empty active collection).",
                    _new_collection,
                )
            else:
                # No async reindex in flight (target already populated, no docs,
                # or migration skipped) — safe to promote immediately.
                state.knowledge_config_hash = _vec_hash
        # The OLD-collection busy flag set during migration is released once —
        # in the outer finally for every non-deferred path, or in the flip
        # wrapper's finally when a deferred flip took ownership.

        if reindex_info:
            response_data["reindex"] = reindex_info

        return JSONResponse(response_data)
    except HTTPException:
        raise
    except Exception as e:
        from cuga.backend.knowledge.engine import EmbeddingModelLoadError

        if isinstance(e, EmbeddingModelLoadError):
            # A bad/just-switched embedding model (e.g. a large local model still
            # downloading) must not 500 — return an actionable 400 the UI can show.
            logger.warning(f"Embedding model load failed during publish: {e}")
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Couldn't load embedding model '{e.model}' ({e.provider}). If it's a large "
                    "local model it may still be downloading — retry in a moment. Otherwise check "
                    "the model name and your provider key / connectivity."
                ),
            )
        logger.error(f"Failed to save manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if engine is not None and not _flip_spawned and _old_collection is not None:
            # Safety: release the OLD-collection busy flag on any path that
            # didn't hand ownership to a deferred flip (errors / early returns).
            try:
                engine._reindex_in_progress.discard(_old_collection)
                engine._reindex_deferred.discard(_old_collection)
            except Exception:
                pass
        _pub_lock.release()


@router.get("/config/history")
async def get_manage_config_history(agent_id: Optional[str] = None):
    """List published config versions (newest first)."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        from cuga.backend.server.config_store import list_versions

        versions = await list_versions(agent_id)
        return JSONResponse({"versions": versions})
    except Exception as e:
        logger.error(f"Failed to list config history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/config")
async def delete_manage_config(agent_id: Optional[str] = None, reset_db: Optional[bool] = None):
    """Delete all configs for agent, or reset entire config db. Use ?reset_db=1 for full reset."""
    try:
        from cuga.backend.server.config_store import delete_all_configs, reset_config_db

        if reset_db:
            reset_config_db()
            return JSONResponse({"status": "success", "message": "Config db reset"})
        aid = agent_id or "cuga-default"
        count = await delete_all_configs(aid)
        return JSONResponse({"status": "success", "deleted": count, "agent_id": aid})
    except Exception as e:
        logger.error(f"Failed to delete manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
