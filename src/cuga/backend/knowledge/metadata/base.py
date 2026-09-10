"""Shared typing and helpers for knowledge metadata backends."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

#: Error recorded on file-task entries that a restart interrupted mid-ingest.
INTERRUPTED_ERROR = "interrupted by server restart"

#: File-task statuses that a restart should re-mark as ``failed``. A missing
#: ``status`` is treated as ``pending`` and therefore recovered too — see
#: ``normalize_file_tasks`` for why the key can be absent.
_RECOVERABLE_FILE_STATUSES = ("pending", "processing")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_cutoff_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def normalize_file_tasks(raw: Any) -> dict[str, Any]:
    """Coerce a stored ``file_tasks_json`` value into a mapping.

    Accepts the raw JSON text from the DB or an already-deserialized value,
    and always returns a dict so callers can iterate and re-serialize without
    guarding every access.

    Two shapes on disk make this necessary:

    1. **A file-task entry may legitimately have no ``status`` key.** The
       ingest worker's progress emits replace the whole entry with
       ``{filename, stage, progress}`` on purpose, so a late emit cannot
       un-flip ``status="completed"`` back to ``"processing"``
       (see ``_emit_progress`` in ``knowledge/engine.py``). Any task killed
       mid-ingest therefore leaves a status-less entry behind. Readers must
       treat a missing status as *non-terminal*, never assume the key exists
       — assuming it crashed Postgres warmup with ``KeyError: 'status'``
       (#683) while SQLite tolerated it, and the two backends silently
       diverged for weeks.
    2. **The payload may not be an object at all** — corrupt or truncated
       JSON, ``null``, or a list written by an older build. Returning ``{}``
       keeps the caller's re-serialization from persisting a non-object back
       into a column the rest of the code reads as a mapping.
    """
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw if isinstance(raw, dict) else {}


def mark_file_tasks_interrupted(file_tasks: dict[str, Any]) -> dict[str, Any]:
    """Re-mark non-terminal file-task entries as failed, in place.

    Shared by every backend's ``recover_stale_tasks`` so the tolerance rules
    live in one place. Entries that are not dicts are skipped rather than
    dropped, and an existing ``error`` is preserved because it is more
    specific than the generic restart message.
    """
    for ft in file_tasks.values():
        if not isinstance(ft, dict):
            continue
        if ft.get("status", "pending") in _RECOVERABLE_FILE_STATUSES:
            ft["status"] = "failed"
            ft["error"] = ft.get("error") or INTERRUPTED_ERROR
    return file_tasks


class KnowledgeMetadataStore(Protocol):
    async def ensure_ready(self) -> None: ...
    async def close(self) -> None: ...
    async def add_document(
        self, collection: str, filename: str, chunk_count: int, preview: str = ""
    ) -> None: ...
    async def mark_deleting(self, collection: str, filename: str) -> bool: ...
    async def remove_document(self, collection: str, filename: str) -> None: ...
    async def list_documents(self, collection: str) -> list[dict[str, Any]]: ...
    async def get_deleting_documents(self) -> list[dict[str, Any]]: ...
    async def document_exists(self, collection: str, filename: str) -> bool: ...
    async def create_task(
        self, task_id: str, collection: str, total_files: int, file_tasks: dict[str, dict]
    ) -> dict[str, Any]: ...
    async def get_task(self, task_id: str) -> dict[str, Any] | None: ...
    async def update_task(self, task_id: str, **kwargs: Any) -> None: ...
    async def list_tasks(self, collection: str | None = None) -> list[dict[str, Any]]: ...
    async def recover_stale_tasks(self) -> int: ...
    async def purge_old_tasks(self, max_age_days: int = 7) -> int: ...
    async def get_collection_config(self, collection: str) -> dict[str, Any] | None: ...
    async def set_collection_config(
        self, collection: str, embedding_provider: str, embedding_model: str, embedding_dim: int
    ) -> None: ...
    async def list_all_collection_configs(self) -> list[str]: ...
    async def delete_collection_metadata(self, collection: str) -> None: ...
    async def get_setting(self, key: str, default: str = "") -> str: ...
    async def set_setting(self, key: str, value: str) -> None: ...
    async def get_all_settings(self) -> dict[str, str]: ...
