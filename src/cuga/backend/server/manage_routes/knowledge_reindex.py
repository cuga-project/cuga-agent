"""Knowledge collection migration and deferred hash promotion."""

import asyncio as _asyncio
from typing import Any

from loguru import logger

from cuga.backend.server.manage_routes.helpers import agent_draft_lock

_BACKGROUND_TASKS: set[_asyncio.Task] = set()

# Wall-clock cap on the deferred pointer-flip's wait for workers to terminate,
# so a wedged worker / engine crash can't leak the flip coroutine forever.
_DEFERRED_FLIP_TIMEOUT_S = 30 * 60  # 30 min


async def persist_active_vector_config(agent_id: str, live_engine: Any, target_hash: str) -> None:
    """Durably persist the active collection's vector-affecting config + hash
    after a successful flip. Startup reloads ``knowledge_config_hash`` from the
    PUBLISHED config's ``_vector_config_hash`` (main.py) and re-applies the
    published knowledge config, so an in-memory-only flip reverts on restart and
    orphans the migrated vectors. We write the engine's CURRENT vector-affecting
    fields (embedder/chunk/metric) AND the hash together so the persisted config
    stays self-consistent (the hash matches the embedder that built the
    collection). Best-effort: the in-memory flip already happened; a persistence
    failure only means the change reverts on the next restart."""
    try:
        from cuga.backend.server.config_store import (
            load_config,
            load_draft,
            save_draft,
            update_published_config_at_version,
        )

        cfg = live_engine._config
        vec = {
            "embedding_provider": getattr(cfg, "embedding_provider", None),
            "embedding_model": getattr(cfg, "embedding_model", None),
            "chunk_size": getattr(cfg, "chunk_size", None),
            "chunk_overlap": getattr(cfg, "chunk_overlap", None),
            "metric_type": getattr(cfg, "metric_type", None),
            "_vector_config_hash": target_hash,
        }
        vec = {k: v for k, v in vec.items() if v is not None}

        draft = await load_draft(agent_id) or {}
        if not isinstance(draft.get("knowledge"), dict):
            draft["knowledge"] = {}
        draft["knowledge"].update(vec)
        await save_draft(draft, agent_id)

        pub_cfg, ver = await load_config(version=None, agent_id=agent_id)
        if pub_cfg and ver and isinstance(pub_cfg.get("knowledge"), dict):
            pub_cfg["knowledge"].update(vec)
            await update_published_config_at_version(pub_cfg, agent_id, ver)
        logger.info(
            f"Deferred flip: persisted active vector config (hash={target_hash}) to draft"
            f"{(' + published v' + str(ver)) if (pub_cfg and ver) else ''} — survives restart."
        )
    except Exception as e:
        logger.warning(
            f"Deferred flip: promoted in-memory but failed to persist vector config durably "
            f"({e!r}); the active collection will revert on restart until re-indexed or published."
        )


async def deferred_reindex_complete_and_flip(
    agent_id: str,
    live_engine: Any,
    live_state: Any,
    target: str,
    target_hash: str,
    task_ids: list[str],
) -> None:
    """Background task spawned by ``migrate_and_reindex_for_agent`` after
    ``engine.reindex`` returns ``status=started``. Waits for every per-file
    ingest worker to reach a terminal state, then promotes
    ``app_state.knowledge_config_hash`` to ``target_hash`` ONLY IF ALL tasks
    completed successfully (strict — no failures) AND the engine's current
    config still hashes to ``target_hash`` (i.e., the user didn't change
    embedders behind our back via the SDK / a Layer 1 / Layer 2 bypass).

    The flip happens INSIDE ``agent_draft_lock`` so a concurrent PATCH can't
    interleave between the engine-config check and the pointer write, and the
    successful flip is persisted durably so it survives a restart.

    Bounded by a wall clock (``_DEFERRED_FLIP_TIMEOUT_S``) so a hung worker /
    engine crash can't leak this coroutine forever.
    """
    deadline = _asyncio.get_running_loop().time() + _DEFERRED_FLIP_TIMEOUT_S

    # Poll until the engine clears its busy flag for our target. The flag
    # is the canonical "workers done" signal: engine.reindex sets it in
    # the lock prologue and clears it from the worker's finally-block.
    while target in live_engine._reindex_in_progress:
        if _asyncio.get_running_loop().time() > deadline:
            logger.warning(
                f"Deferred flip for {target}: workers didn't terminate before the "
                f"{_DEFERRED_FLIP_TIMEOUT_S}s cap; NOT promoting knowledge_config_hash."
            )
            return
        await _asyncio.sleep(0.5)

    # Snapshot terminal task statuses.
    try:
        all_tasks = await live_engine._metadata.list_tasks(target)
    except Exception as e:
        logger.warning(f"Deferred flip for {target}: failed to read task statuses ({e}); skipping flip.")
        return

    task_id_set = set(task_ids)
    relevant = [t for t in all_tasks if t["task_id"] in task_id_set]

    # A task row missing from the listing is NOT "done" — count it as
    # incomplete and refuse, else a dropped/GC'd record could let the flip
    # promote after only the surviving tasks reach terminal state.
    if len(relevant) != len(task_id_set):
        logger.warning(
            f"Deferred flip for {target}: {len(relevant)}/{len(task_id_set)} expected task "
            f"records present in metadata; a task row is missing — refusing to promote."
        )
        return

    n_completed = sum(1 for t in relevant if t["status"] == "completed")
    n_failed = sum(1 for t in relevant if t["status"] in ("failed", "cancelled"))
    n_terminal = n_completed + n_failed

    if n_terminal != len(relevant):
        logger.warning(
            f"Deferred flip for {target}: {n_terminal}/{len(relevant)} tasks at terminal "
            f"state; refusing partial flip."
        )
        return

    # STRICT mode: refuse promotion unless every task succeeded. PERMISSIVE
    # mode ("promote on any success") silently loses data — concrete repro:
    # switching fastembed -> watsonx/e5, one file hit 518>512, 4/5 succeeded,
    # pointer flipped, and search for that file's content returned nothing.
    # Strict choice: stay on the old collection (all files have working
    # vectors), surface the failure via task status, let the user retry.
    if n_failed > 0:
        logger.warning(
            f"Deferred flip for {target}: {n_completed}/{len(relevant)} succeeded, "
            f"{n_failed} failed/cancelled; NOT promoting knowledge_config_hash. "
            f"The old collection stays active so retrieval keeps working."
        )
        return

    if n_completed == 0:
        logger.warning(
            f"Deferred flip for {target}: 0/{len(relevant)} tasks succeeded; "
            f"NOT promoting knowledge_config_hash. The collection's vectors are "
            f"empty — user must Re-index again."
        )
        return

    # Acquire the per-agent lock so the engine-config check and the pointer
    # write are atomic against any concurrent PATCH.
    async with agent_draft_lock(agent_id):
        try:
            current_engine_hash = live_engine._config.vector_config_hash()
        except Exception as e:
            logger.warning(f"Deferred flip for {target}: vector_config_hash failed ({e}); skipping.")
            return
        if current_engine_hash != target_hash:
            # Engine moved on between when the reindex started and when it
            # finished — likely via an SDK bypass of Layer 1+2. Flipping to
            # ``target_hash`` now would point queries at a collection whose
            # content doesn't match the engine's current embedder.
            logger.info(
                f"Deferred flip for {target}: engine moved to {current_engine_hash!r} during "
                f"reindex (was {target_hash!r}); skipping flip. User must trigger a fresh Re-index."
            )
            return

        try:
            live_state.knowledge_config_hash = target_hash
            logger.info(
                f"Deferred flip for {target}: {n_completed}/{len(relevant)} tasks succeeded; "
                f"promoted knowledge_config_hash to {target_hash}."
            )
        except Exception as e:
            logger.warning(f"Deferred flip for {target}: failed to set knowledge_config_hash ({e}).")
            return

        # Durability (#5): make the successful flip survive a restart. Inside
        # the same lock so the persisted hash can't race a concurrent publish.
        await persist_active_vector_config(agent_id, live_engine, target_hash)


async def migrate_and_reindex_for_agent(agent_id: str, live_engine: Any, live_state: Any) -> dict[str, Any]:
    """Re-embed the active snapshot (kb_agent_<id>_<active_hash>) into the
    target (kb_agent_<id>_<current_hash>). Single source — historicals
    untouched. Pointer flips DEFERRED to a background task that waits for
    worker terminal state (see ``deferred_reindex_complete_and_flip``).
    The HTTP response returns with task_ids so the UI can show progress
    immediately, while the integrity-critical pointer flip happens behind
    the scenes only after workers finish AND the engine config still
    matches.
    Returns {triggered, target, collections, error?}."""
    import re as _re

    sanitized = _re.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
    prefix = f"kb_agent_{sanitized}"
    try:
        target_hash = live_engine._config.vector_config_hash()
    except Exception:
        target_hash = ""
    target = f"{prefix}_{target_hash}" if target_hash else prefix
    active_hash = getattr(live_state, "knowledge_config_hash", "") or ""
    source = f"{prefix}_{active_hash}" if active_hash else prefix

    files_dir = getattr(live_engine, "_files_dir", None)
    do_copy = source != target
    triggered: list[dict[str, Any]] = []

    # Refuse if active dir is missing on disk — would otherwise fabricate by
    # merging siblings. (Source==target with a missing dir is fine: the
    # reindex below returns no_documents and we report that cleanly.)
    if do_copy and files_dir is not None and not (files_dir / source).exists():
        return {"triggered": False, "target": target, "error": "active_snapshot_missing"}

    if do_copy:
        # Flag SOURCE for the WHOLE Path A lifetime — NOT just the copy window.
        # The active pointer still resolves to SOURCE until the deferred flip,
        # so concurrent uploads/deletes to the active collection must be
        # rejected until then; releasing the flag after copy (the old behavior)
        # left a window where an upload lands in SOURCE only and is lost on flip,
        # and where a second Re-index slips the 409 guard. Released in the flip
        # wrapper's finally below (or here on copy failure / no-flip).
        live_engine._reindex_in_progress.add(source)
        try:
            async with (
                live_engine._get_collection_lock(source),
                live_engine._get_collection_lock(target),
            ):
                n = await live_engine.copy_source_files(source, target)
                triggered.append({"copied_from": source, "to": target, "files": n})
        except Exception as cerr:
            logger.warning(f"copy {source} -> {target} failed: {cerr}")
            live_engine._reindex_in_progress.discard(source)
            live_engine._reindex_deferred.discard(source)
            return {"triggered": False, "target": target, "error": "copy_failed"}

    from cuga.backend.knowledge.engine import ReindexBusyError

    def _release_source() -> None:
        # Release the SOURCE busy flag held across Path A (no-op when source
        # wasn't flagged, i.e. the in-place source==target case).
        if do_copy:
            live_engine._reindex_in_progress.discard(source)
            live_engine._reindex_deferred.discard(source)

    try:
        r = await live_engine.reindex(target)
        triggered.append({"collection": target, "result": r})
        ok = bool(r and r.get("status") not in (None, "no_documents"))
    except ReindexBusyError as berr:
        # Distinguish "uploads in progress; wait" from the generic
        # reindex_failed toast. Same JSON shape as the reindex_in_progress
        # guard so the FE toast handler can share the path.
        logger.warning(f"reindex_busy on {target} (agent={agent_id}): {berr}")
        triggered.append({"collection": target, "error": f"busy: {berr}"})
        _release_source()
        return {"triggered": False, "target": target, "collections": triggered, "error": "reindex_busy"}
    except Exception as rerr:
        logger.warning(f"Reindex of {target} failed: {rerr}")
        triggered.append({"collection": target, "error": str(rerr)})
        ok = False

    if not ok:
        _release_source()
        return {"triggered": False, "target": target, "collections": triggered, "error": "reindex_failed"}

    # Spawn the deferred pointer-flip. The HTTP response returns NOW with
    # task_ids so the UI shows progress immediately; the pointer flips behind
    # the scenes once workers terminate AND the engine config still matches
    # target_hash. SOURCE stays flagged until the flip finishes; the wrapper's
    # finally releases it so it can never leak.
    task_ids = (r or {}).get("task_ids") or []
    if target_hash and live_state is not None and task_ids:

        async def _flip_then_release_source():
            try:
                await deferred_reindex_complete_and_flip(
                    agent_id, live_engine, live_state, target, target_hash, task_ids
                )
            finally:
                _release_source()

        _bg = _asyncio.create_task(_flip_then_release_source())
        _BACKGROUND_TASKS.add(_bg)
        _bg.add_done_callback(_BACKGROUND_TASKS.discard)
        _bg.add_done_callback(lambda t: t.exception())
    else:
        # No flip will run (no task_ids / no hash) — release SOURCE now.
        _release_source()

    return {"triggered": True, "target": target, "collections": triggered}
