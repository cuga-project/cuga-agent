"""Knowledge collection migration and deferred hash promotion."""

import asyncio as _asyncio
from typing import Any

from loguru import logger

from cuga.backend.server.manage_routes.helpers import agent_draft_lock

_BACKGROUND_TASKS: set[_asyncio.Task] = set()


async def deferred_reindex_complete_and_flip(
    agent_id: str,
    live_engine: Any,
    live_state: Any,
    target: str,
    target_hash: str,
    task_ids: list[str],
) -> None:
    """Background task spawned by ``_migrate_and_reindex_for_agent`` after
    ``engine.reindex`` returns ``status=started``. Waits for every per-file
    ingest worker to reach a terminal state, then promotes
    ``app_state.knowledge_config_hash`` to ``target_hash`` ONLY IF at least
    one task completed successfully AND the engine's current config still
    hashes to ``target_hash`` (i.e., the user didn't change embedders
    behind our back via the SDK / a Layer 1 / Layer 2 bypass).

    The flip happens INSIDE ``agent_draft_lock`` so a concurrent PATCH
    can't interleave between the engine-config check and the pointer write.

    Bounded by a 30-minute wall clock so a hung worker / engine crash
    can't leak this coroutine forever.
    """
    deadline = _asyncio.get_event_loop().time() + 30 * 60  # 30 min hard cap

    # Poll until the engine clears its busy flag for our target. The flag
    # is the canonical "workers done" signal: engine.reindex sets it in
    # the lock prologue and clears it from the worker's finally-block.
    while target in live_engine._reindex_in_progress:
        if _asyncio.get_event_loop().time() > deadline:
            logger.warning(
                f"Deferred flip for {target}: workers didn't terminate in 30min; "
                f"NOT promoting knowledge_config_hash."
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
    n_completed = sum(1 for t in relevant if t["status"] == "completed")
    n_terminal = sum(1 for t in relevant if t["status"] in ("completed", "failed", "cancelled"))

    if n_terminal != len(relevant):
        logger.warning(
            f"Deferred flip for {target}: {n_terminal}/{len(relevant)} tasks at terminal "
            f"state; refusing partial flip."
        )
        return

    if n_completed == 0:
        logger.warning(
            f"Deferred flip for {target}: 0/{len(relevant)} tasks succeeded "
            f"(all failed or superseded); NOT promoting knowledge_config_hash. "
            f"The collection's vectors are empty or stale — user must Re-index again."
        )
        return

    # Acquire the per-agent lock so the engine-config check and the
    # pointer write are atomic against any concurrent PATCH.
    async with agent_draft_lock(agent_id):
        try:
            current_engine_hash = live_engine._config.vector_config_hash()
        except Exception as e:
            logger.warning(f"Deferred flip for {target}: vector_config_hash failed ({e}); skipping.")
            return
        if current_engine_hash != target_hash:
            # Engine moved on between when the reindex started and when it
            # finished — likely via an SDK bypass of Layer 1+2. Flipping
            # to ``target_hash`` now would point queries at a collection
            # whose content doesn't match the engine's current embedder.
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

    # Refuse if active dir is missing on disk — would otherwise fabricate
    # by merging siblings. (Source==target with a missing dir is fine:
    # the reindex below returns no_documents and we report that cleanly.)
    if do_copy and files_dir is not None and not (files_dir / source).exists():
        return {"triggered": False, "target": target, "error": "active_snapshot_missing"}

    if do_copy:
        # Busy flag on source so concurrent uploads hit ReindexBusyError;
        # per-collection locks serialize simultaneous Re-index clicks.
        live_engine._reindex_in_progress.add(source)
        try:
            async with (
                live_engine._get_collection_lock(source),
                live_engine._get_collection_lock(target),
            ):
                try:
                    n = await live_engine.copy_source_files(source, target)
                    triggered.append({"copied_from": source, "to": target, "files": n})
                except Exception as cerr:
                    logger.warning(f"copy {source} -> {target} failed: {cerr}")
                    return {"triggered": False, "target": target, "error": "copy_failed"}
        finally:
            live_engine._reindex_in_progress.discard(source)
            live_engine._reindex_deferred.discard(source)

    try:
        r = await live_engine.reindex(target)
        triggered.append({"collection": target, "result": r})
        ok = bool(r and r.get("status") not in (None, "no_documents"))
    except Exception as rerr:
        logger.warning(f"Reindex of {target} failed: {rerr}")
        triggered.append({"collection": target, "error": str(rerr)})
        ok = False

    if not ok:
        return {"triggered": False, "target": target, "collections": triggered, "error": "reindex_failed"}

    # Spawn the deferred pointer-flip. The HTTP response returns NOW with
    # task_ids so the UI shows progress immediately; the pointer flips
    # behind the scenes once workers terminate AND the engine config
    # still matches target_hash. See ``deferred_reindex_complete_and_flip``.
    task_ids = (r or {}).get("task_ids") or []
    if target_hash and live_state is not None and task_ids:
        task = _asyncio.create_task(
            deferred_reindex_complete_and_flip(
                agent_id, live_engine, live_state, target, target_hash, task_ids
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    return {"triggered": True, "target": target, "collections": triggered}
