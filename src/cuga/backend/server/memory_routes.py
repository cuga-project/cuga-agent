"""Feature-gated APIs for inspecting and managing Evolve memory."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from cuga.backend.evolve.integration import EvolveIntegration
from cuga.backend.evolve.memory_store import (
    get_available_conversation_thread_ids,
    get_memory_usage_summaries,
)
from cuga.backend.server.auth import require_chat_access, require_manage_access
from cuga.backend.server.auth.models import UserInfo
from cuga.config import get_tenant_id


def require_evolve_memory() -> None:
    if not EvolveIntegration.is_enabled():
        raise HTTPException(status_code=404, detail="Evolve memory is disabled")


router = APIRouter(
    prefix="/api",
    tags=["memory"],
    dependencies=[Depends(require_evolve_memory)],
)

_DEFAULT_USER_ID = "default_user"
_MEMORY_METADATA_FIELDS = {
    "category",
    "display_name",
    "last_accessed",
    "legal_hold",
    "person",
    "retention_flagged_at",
    "retention_rule",
    "session_id",
    "thread_id",
    "title",
    "user_name",
}
_ADMIN_MEMORY_METADATA_FIELDS = _MEMORY_METADATA_FIELDS | {"owner_id", "user_id"}
_USER_EDITABLE_FIELDS = {"category", "title"}
_ADMIN_EDITABLE_FIELDS = {"category", "legal_hold", "title"}


class MemoryMetadataPatchRequest(BaseModel):
    metadata: dict[str, Any]


class MemoryAccessRequest(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=200)


class RetentionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: Optional[str] = None
    scan_limit: Optional[int] = Field(default=None, ge=1, le=100_000)


def _user_id(current_user: Optional[UserInfo]) -> str:
    return current_user.sub if current_user else _DEFAULT_USER_ID


def _namespace_id() -> Optional[str]:
    return get_tenant_id() or None


def _memory_result(result: Optional[dict[str, Any]]) -> dict[str, Any]:
    if result is None:
        raise HTTPException(status_code=503, detail="Evolve memory service is unavailable")
    error = str(result.get("error") or "")
    if not error:
        return result
    lowered = error.lower()
    if "permission denied" in lowered or "forbidden" in lowered:
        raise HTTPException(status_code=403, detail="Memory access denied")
    if "not found" in lowered:
        raise HTTPException(status_code=404, detail="Memory not found")
    raise HTTPException(status_code=400, detail="Memory request rejected")


def _metadata_filters(value: Optional[str]) -> Optional[dict[str, Any]]:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="metadata_filters must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="metadata_filters must be a JSON object")
    blocked = {"agent_id", "namespace_id", "owner_id", "tenant_id", "user_id"}
    if blocked.intersection(parsed):
        raise HTTPException(status_code=422, detail="metadata_filters cannot override memory scope")
    return parsed


def _project_item(
    item: dict[str, Any],
    *,
    audience: Literal["user", "admin"],
    include_content: bool,
    usage: Optional[dict[str, Any]] = None,
    available_thread_ids: Optional[set[str]] = None,
) -> dict[str, Any]:
    metadata = item.get("metadata")
    allowed = _ADMIN_MEMORY_METADATA_FIELDS if audience == "admin" else _MEMORY_METADATA_FIELDS
    safe_metadata = {
        key: value
        for key, value in (metadata.items() if isinstance(metadata, dict) else [])
        if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
    }
    source_thread_id = None
    if audience == "user":
        source_thread_id = next(
            (
                candidate.strip()
                for candidate in (safe_metadata.get("thread_id"), safe_metadata.get("session_id"))
                if isinstance(candidate, str) and candidate.strip()
            ),
            None,
        )
        safe_metadata.pop("thread_id", None)
        safe_metadata.pop("session_id", None)
    projected: dict[str, Any] = {
        "id": item.get("id"),
        "type": item.get("type"),
        "created_at": item.get("created_at"),
        "metadata": safe_metadata,
        "usage": {
            "count": int((usage or {}).get("count") or 0),
            "last_used_at": (usage or {}).get("last_used_at"),
            "recent": [
                {
                    "thread_id": entry.get("thread_id"),
                    "conversation_label": entry.get("conversation_label"),
                    "used_at": entry.get("used_at"),
                }
                for entry in (usage or {}).get("recent", [])
                if isinstance(entry, dict)
            ],
        },
    }
    if include_content:
        projected["content"] = item.get("content")
    if audience == "user":
        source_available = bool(
            source_thread_id and available_thread_ids is not None and source_thread_id in available_thread_ids
        )
        projected["source_thread_id"] = source_thread_id if source_available else None
        projected["source_available"] = source_available
    return projected


def _project_inventory(
    result: dict[str, Any],
    *,
    audience: Literal["user", "admin"],
    include_content: bool,
    usage_by_id: dict[str, dict[str, Any]],
    available_thread_ids: Optional[set[str]] = None,
) -> dict[str, Any]:
    items = [item for item in result.get("items", []) if isinstance(item, dict)]
    return {
        "items": [
            _project_item(
                item,
                audience=audience,
                include_content=include_content,
                usage=usage_by_id.get(str(item.get("id") or "")),
                available_thread_ids=available_thread_ids,
            )
            for item in items
        ],
        "total": int(result.get("total") or 0),
        "next_cursor": result.get("next_cursor"),
    }


def _validate_metadata_patch(metadata: dict[str, Any], allowed: set[str], audience: str) -> None:
    unsupported = sorted(set(metadata) - allowed)
    if unsupported:
        raise HTTPException(
            status_code=422,
            detail=f"{audience}-editable memory fields are limited to: {', '.join(sorted(allowed))}",
        )


async def _list_retention_inventory(*, agent_id: str, scan_limit: Optional[int]) -> list[dict[str, Any]]:
    remaining = scan_limit or 100_000
    cursor: Optional[str] = None
    seen_cursors: set[str] = set()
    entities: list[dict[str, Any]] = []
    while remaining > 0:
        result = _memory_result(
            await EvolveIntegration.list_entities(
                agent_id=agent_id,
                cursor=cursor,
                limit=min(remaining, 200),
                include_content=False,
                namespace_id=_namespace_id(),
            )
        )
        page = [item for item in result.get("items", []) if isinstance(item, dict)]
        entities.extend(page[:remaining])
        remaining -= len(page)
        next_cursor = result.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return entities


async def _apply_orphaned_memory_retention(
    report: dict[str, Any],
    orphaned: list[dict[str, Any]],
    *,
    agent_id: str,
) -> dict[str, Any]:
    from cuga.backend.evolve.retention import memory_title

    merged = {
        **report,
        **{
            bucket: [item for item in report.get(bucket, []) if isinstance(item, dict)]
            for bucket in ("flagged", "deleted", "skipped")
        },
    }
    deleted_ids = {str(item.get("entity_id") or "") for item in merged["deleted"]}
    orphan_ids = {str(entity.get("id") or "") for entity in orphaned}
    superseded_ids = orphan_ids - deleted_ids
    for bucket in ("flagged", "skipped"):
        merged[bucket] = [
            item for item in merged[bucket] if str(item.get("entity_id") or "") not in superseded_ids
        ]

    for entity in orphaned:
        entity_id = str(entity.get("id") or "")
        if not entity_id or entity_id in deleted_ids:
            continue
        item = {
            "entity_id": entity_id,
            "entity_type": entity.get("type"),
            "created_at": entity.get("created_at"),
            "action": "delete",
            **({"title": title} if (title := memory_title(entity)) else {}),
        }
        try:
            deletion = _memory_result(
                await EvolveIntegration.delete_entity(
                    entity_id,
                    agent_id=agent_id,
                    namespace_id=_namespace_id(),
                )
            )
        except Exception:
            deletion = {}
        if deletion.get("success"):
            merged["deleted"].append({**item, "outcome": "deleted"})
        else:
            merged["skipped"].append({**item, "outcome": "skipped"})
            errors = merged.setdefault("errors", [])
            if isinstance(errors, list):
                errors.append("An orphaned memory could not be deleted")
            else:
                merged["errors"] = ["An orphaned memory could not be deleted"]
    return merged


@router.get("/memory/entities")
async def list_user_memory_entities(
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    entity_type: Optional[list[str]] = Query(default=None),
    session_id: Optional[str] = None,
    metadata_filters: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    include_content: bool = False,
    current_user: Optional[UserInfo] = Depends(require_chat_access),
):
    user_id = _user_id(current_user)
    result = _memory_result(
        await EvolveIntegration.list_entities(
            entity_types=entity_type,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            metadata_filters=_metadata_filters(metadata_filters),
            cursor=cursor,
            limit=limit,
            include_content=include_content,
            namespace_id=_namespace_id(),
        )
    )
    available_thread_ids = await get_available_conversation_thread_ids(
        agent_id=agent_id,
        user_id=user_id,
    )
    usage = await get_memory_usage_summaries(
        agent_id=agent_id,
        user_id=user_id,
        entity_ids=[str(item.get("id") or "") for item in result.get("items", [])],
        available_thread_ids=available_thread_ids,
    )
    return JSONResponse(
        _project_inventory(
            result,
            audience="user",
            include_content=include_content,
            usage_by_id=usage,
            available_thread_ids=available_thread_ids,
        )
    )


@router.get("/memory/entities/{entity_id}")
async def get_user_memory_entity(
    entity_id: str,
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    current_user: Optional[UserInfo] = Depends(require_chat_access),
):
    user_id = _user_id(current_user)
    result = _memory_result(
        await EvolveIntegration.get_entity(
            entity_id,
            user_id=user_id,
            agent_id=agent_id,
            namespace_id=_namespace_id(),
        )
    )
    available_thread_ids = await get_available_conversation_thread_ids(
        agent_id=agent_id,
        user_id=user_id,
    )
    usage = await get_memory_usage_summaries(
        agent_id=agent_id,
        user_id=user_id,
        entity_ids=[entity_id],
        available_thread_ids=available_thread_ids,
    )
    return JSONResponse(
        _project_item(
            result,
            audience="user",
            include_content=True,
            usage=usage.get(entity_id),
            available_thread_ids=available_thread_ids,
        )
    )


@router.patch("/memory/entities/{entity_id}/metadata")
async def patch_user_memory_entity(
    entity_id: str,
    body: MemoryMetadataPatchRequest,
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    current_user: Optional[UserInfo] = Depends(require_chat_access),
):
    _validate_metadata_patch(body.metadata, _USER_EDITABLE_FIELDS, "User")
    result = _memory_result(
        await EvolveIntegration.patch_entity_metadata(
            entity_id,
            body.metadata,
            user_id=_user_id(current_user),
            agent_id=agent_id,
            namespace_id=_namespace_id(),
        )
    )
    available_thread_ids = await get_available_conversation_thread_ids(
        agent_id=agent_id,
        user_id=_user_id(current_user),
    )
    return JSONResponse(
        _project_item(
            result,
            audience="user",
            include_content=True,
            available_thread_ids=available_thread_ids,
        )
    )


@router.delete("/memory/entities/{entity_id}")
async def delete_user_memory_entity(
    entity_id: str,
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    current_user: Optional[UserInfo] = Depends(require_chat_access),
):
    result = _memory_result(
        await EvolveIntegration.delete_entity(
            entity_id,
            user_id=_user_id(current_user),
            agent_id=agent_id,
            namespace_id=_namespace_id(),
        )
    )
    return JSONResponse(
        {
            key: result[key]
            for key in ("success", "entity_id", "updated_ids", "denied_ids", "missing_ids")
            if key in result
        }
    )


@router.post("/memory/access")
async def record_user_memory_access(
    body: MemoryAccessRequest,
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    current_user: Optional[UserInfo] = Depends(require_chat_access),
):
    result = _memory_result(
        await EvolveIntegration.record_access(
            list(dict.fromkeys(body.entity_ids)),
            user_id=_user_id(current_user),
            agent_id=agent_id,
            namespace_id=_namespace_id(),
        )
    )
    return JSONResponse(
        {
            key: result[key]
            for key in ("updated_ids", "denied_ids", "missing_ids", "accessed_at")
            if key in result
        }
    )


@router.get("/manage/memory/entities")
async def list_admin_memory_entities(
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    entity_type: Optional[list[str]] = Query(default=None),
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata_filters: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    result = _memory_result(
        await EvolveIntegration.list_entities(
            entity_types=entity_type,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            metadata_filters=_metadata_filters(metadata_filters),
            cursor=cursor,
            limit=limit,
            include_content=False,
            namespace_id=_namespace_id(),
        )
    )
    usage = await get_memory_usage_summaries(
        agent_id=agent_id,
        entity_ids=[str(item.get("id") or "") for item in result.get("items", [])],
        include_recent=False,
    )
    return JSONResponse(
        _project_inventory(result, audience="admin", include_content=False, usage_by_id=usage)
    )


@router.get("/manage/memory/entities/{entity_id}")
async def get_admin_memory_entity(
    entity_id: str,
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    result = _memory_result(
        await EvolveIntegration.get_entity(
            entity_id,
            agent_id=agent_id,
            namespace_id=_namespace_id(),
        )
    )
    usage = await get_memory_usage_summaries(
        agent_id=agent_id,
        entity_ids=[entity_id],
        include_recent=False,
    )
    return JSONResponse(
        _project_item(result, audience="admin", include_content=False, usage=usage.get(entity_id))
    )


@router.patch("/manage/memory/entities/{entity_id}/metadata")
async def patch_admin_memory_entity(
    entity_id: str,
    body: MemoryMetadataPatchRequest,
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    _validate_metadata_patch(body.metadata, _ADMIN_EDITABLE_FIELDS, "Admin")
    result = _memory_result(
        await EvolveIntegration.patch_entity_metadata(
            entity_id,
            body.metadata,
            agent_id=agent_id,
            namespace_id=_namespace_id(),
        )
    )
    return JSONResponse(_project_item(result, audience="admin", include_content=False))


@router.get("/memory/retention")
async def get_user_memory_retention(
    current_user: Optional[UserInfo] = Depends(require_chat_access),
):
    from cuga.backend.evolve.retention import retention_capabilities

    status = await EvolveIntegration.get_compliance_status(namespace_id=_namespace_id())
    return JSONResponse(
        retention_capabilities(retention_available=bool(status and status.get("retention_available")))
    )


@router.get("/manage/memory/retention")
async def get_admin_memory_retention(
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import retention_capabilities

    status = await EvolveIntegration.get_compliance_status(namespace_id=_namespace_id())
    return JSONResponse(
        retention_capabilities(retention_available=bool(status and status.get("retention_available")))
    )


@router.post("/manage/memory/retention/validate")
async def validate_admin_retention_policy(
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import DEFAULT_RETENTION_POLICY

    result = _memory_result(await EvolveIntegration.validate_retention_policy(DEFAULT_RETENTION_POLICY))
    return JSONResponse(
        {key: result[key] for key in ("valid", "errors", "warnings", "normalized_policy") if key in result}
    )


@router.post("/manage/memory/retention/runs")
async def run_admin_memory_retention(
    body: RetentionRunRequest,
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import (
        DEFAULT_RETENTION_POLICY,
        find_orphaned_memory_entities,
        project_retention_report,
        retention_reference_time,
        sanitize_retention_report,
    )
    from cuga.backend.evolve.retention_store import save_retention_run
    from cuga.backend.server.conversation_history import get_conversation_db

    run_id = str(uuid.uuid4())
    try:
        reference_time = retention_reference_time(body.as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="as_of must be an ISO-8601 timestamp") from exc
    inventory = await _list_retention_inventory(agent_id=agent_id, scan_limit=body.scan_limit)
    conversation_keys = await get_conversation_db().get_thread_owners_for_agent(agent_id)
    orphaned = find_orphaned_memory_entities(inventory, conversation_keys, now=reference_time)
    provider_report = _memory_result(
        await EvolveIntegration.run_retention(
            DEFAULT_RETENTION_POLICY,
            dry_run=False,
            as_of=body.as_of,
            scan_limit=body.scan_limit,
            run_id=run_id,
            namespace_id=_namespace_id(),
            metadata_filters={"agent_id": agent_id},
        )
    )
    result = await _apply_orphaned_memory_retention(
        provider_report,
        orphaned,
        agent_id=agent_id,
    )
    sanitized = sanitize_retention_report({**result, "run_id": run_id})
    await save_retention_run(
        run_id=run_id,
        agent_id=agent_id,
        actor_id=_user_id(current_user),
        report=sanitized,
    )
    return JSONResponse(project_retention_report(sanitized))


@router.get("/manage/memory/retention/runs")
async def list_admin_memory_retention_runs(
    agent_id: str = Query(default="cuga-default", min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import project_retention_report
    from cuga.backend.evolve.retention_store import list_retention_runs

    rows = await list_retention_runs(agent_id=agent_id, limit=limit)
    return JSONResponse(
        {
            "items": [
                {key: row[key] for key in ("run_id", "actor_id", "status", "created_at")}
                | {"report": project_retention_report(row["report"])}
                for row in rows
            ]
        }
    )


@router.get("/manage/memory/compliance/status")
async def get_admin_memory_compliance_status(
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import project_compliance_status

    result = _memory_result(await EvolveIntegration.get_compliance_status(namespace_id=_namespace_id()))
    return JSONResponse(project_compliance_status(result))
