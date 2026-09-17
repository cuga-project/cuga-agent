"""Feature-gated APIs for inspecting and managing Evolve memory."""

from __future__ import annotations

import json
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
from cuga.config import get_service_instance_id


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

    policy_id: str = Field(min_length=1, max_length=128)
    scan_limit: Optional[int] = Field(default=None, ge=1, le=100_000)


def _user_id(current_user: Optional[UserInfo]) -> str:
    return current_user.sub if current_user else _DEFAULT_USER_ID


def _namespace_id() -> Optional[str]:
    return get_service_instance_id() or None


def _memory_result(result: Optional[dict[str, Any]]) -> dict[str, Any]:
    if result is None:
        raise HTTPException(status_code=503, detail="Evolve memory service is unavailable")
    error = str(result.get("error") or "")
    if not error:
        return result
    lowered = error.lower()
    if "permission denied" in lowered or "forbidden" in lowered:
        raise HTTPException(status_code=403, detail="Memory access denied")
    if any(word in lowered for word in ("conflict", "already exists", "referenced", "active jobs")):
        raise HTTPException(
            status_code=409, detail="Memory configuration changed or is in use; refresh and retry"
        )
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


async def _retention_policies() -> list[dict[str, Any]]:
    """Return the Evolve catalog, registering CUGA's built-in policy once."""
    from cuga.backend.evolve.retention import (
        default_retention_policy,
        DEFAULT_RETENTION_POLICY_DESCRIPTION,
        DEFAULT_RETENTION_POLICY_ID,
        DEFAULT_RETENTION_POLICY_NAME,
    )

    result = _memory_result(
        await EvolveIntegration.list_retention_policies(
            namespace_id=_namespace_id(),
            include_disabled=True,
        )
    )
    policies = [item for item in result.get("items", []) if isinstance(item, dict)]
    if any(policy.get("policy_id") == DEFAULT_RETENTION_POLICY_ID for policy in policies):
        return policies
    status = _memory_result(await EvolveIntegration.get_compliance_status(namespace_id=_namespace_id()))
    created = _memory_result(
        await EvolveIntegration.put_retention_policy(
            DEFAULT_RETENTION_POLICY_ID,
            DEFAULT_RETENTION_POLICY_NAME,
            default_retention_policy(status),
            description=DEFAULT_RETENTION_POLICY_DESCRIPTION,
            namespace_id=_namespace_id(),
        )
    )
    return [*policies, created]


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
    return JSONResponse(retention_capabilities(status or {}))


@router.get("/manage/memory/retention")
async def get_admin_memory_retention(
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import retention_capabilities

    status = await EvolveIntegration.get_compliance_status(namespace_id=_namespace_id())
    return JSONResponse(retention_capabilities(status or {}))


@router.get("/manage/memory/retention/policies")
async def list_admin_retention_policies(
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import project_retention_policy

    policies = await _retention_policies()
    return JSONResponse({"items": [project_retention_policy(policy) for policy in policies]})


@router.post("/manage/memory/retention/validate")
async def validate_admin_retention_policy(
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import default_retention_policy

    status = _memory_result(await EvolveIntegration.get_compliance_status(namespace_id=_namespace_id()))
    result = _memory_result(
        await EvolveIntegration.validate_retention_policy(default_retention_policy(status))
    )
    return JSONResponse(
        {key: result[key] for key in ("valid", "errors", "warnings", "normalized_policy") if key in result}
    )


def _retention_report_response(report: dict) -> dict:
    from pydantic import ValidationError

    from cuga.backend.evolve.retention import project_retention_report

    try:
        return project_retention_report(report)
    except ValidationError:
        raise HTTPException(status_code=502, detail="Evolve returned an invalid retention report") from None


@router.post("/manage/memory/retention/runs")
async def run_admin_memory_retention(
    body: RetentionRunRequest,
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    from cuga.backend.evolve.retention import DEFAULT_RETENTION_POLICY_ID

    if body.policy_id == DEFAULT_RETENTION_POLICY_ID:
        await _retention_policies()
    result = _memory_result(
        await EvolveIntegration.run_retention(
            body.policy_id,
            scan_limit=body.scan_limit,
            namespace_id=_namespace_id(),
            initiated_by=_user_id(current_user),
        )
    )
    return JSONResponse(_retention_report_response(result))


@router.get("/manage/memory/retention/runs")
async def list_admin_memory_retention_runs(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    result = _memory_result(
        await EvolveIntegration.list_retention_runs(
            namespace_id=_namespace_id(),
            limit=limit,
        )
    )
    rows = [row for row in result.get("items", []) if isinstance(row, dict)]
    return JSONResponse(
        {
            "items": [
                {
                    key: row[key]
                    for key in ("run_id", "policy_id", "initiated_by", "status", "created_at")
                    if key in row
                }
                | {"report": _retention_report_response(row.get("report", {}))}
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


async def _require_durable_retention() -> None:
    from cuga.backend.evolve.retention import supports_durable_retention

    status = _memory_result(await EvolveIntegration.get_compliance_status(namespace_id=_namespace_id()))
    if not supports_durable_retention(status):
        raise HTTPException(
            status_code=409, detail="This retention operation requires Evolve's PostgreSQL backend"
        )


@router.get("/manage/memory/retention/candidates")
async def list_retention_candidates(current_user: Optional[UserInfo] = Depends(require_manage_access)):
    await _require_durable_retention()
    return _memory_result(
        await EvolveIntegration._call_structured_tool(
            "list_retention_candidates", {"namespace_id": _namespace_id(), "limit": 1000}
        )
    )


@router.get("/manage/memory/retention/audit")
async def list_retention_audit(current_user: Optional[UserInfo] = Depends(require_manage_access)):
    await _require_durable_retention()
    return _memory_result(
        await EvolveIntegration._call_structured_tool(
            "list_retention_audit", {"namespace_id": _namespace_id(), "limit": 1000}
        )
    )


@router.post("/manage/memory/retention/policies/{policy_id}/mark")
async def mark_retention(policy_id: str, current_user: Optional[UserInfo] = Depends(require_manage_access)):
    await _require_durable_retention()
    await _retention_policies()
    return _memory_result(
        await EvolveIntegration._call_structured_tool(
            "mark_retention",
            {"namespace_id": _namespace_id(), "policy_id": policy_id, "initiated_by": _user_id(current_user)},
        )
    )


@router.post("/manage/memory/retention/policies/{policy_id}/sweep")
async def sweep_retention(policy_id: str, current_user: Optional[UserInfo] = Depends(require_manage_access)):
    await _require_durable_retention()
    return _memory_result(
        await EvolveIntegration._call_structured_tool(
            "sweep_retention",
            {"namespace_id": _namespace_id(), "policy_id": policy_id, "initiated_by": _user_id(current_user)},
        )
    )


class RetentionScheduleWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=128)
    spec: dict[str, Any]
    expected_revision: int = Field(default=0, ge=0)


class RetentionScheduleRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class RetentionSchedulePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]


async def _schedule_call(tool: str, **arguments: Any) -> dict:
    return _memory_result(
        await EvolveIntegration._call_structured_tool(tool, {**arguments, "namespace_id": _namespace_id()})
    )


@router.get("/manage/memory/retention/schedules")
async def list_retention_schedules(current_user: Optional[UserInfo] = Depends(require_manage_access)):
    return JSONResponse(await _schedule_call("list_retention_schedules"))


@router.post("/manage/memory/retention/schedules/preview")
async def preview_retention_schedule(
    body: RetentionSchedulePreview,
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    # Evolve 1.2 exposes upcoming times for saved schedules over MCP. For an
    # unsaved form, use its same timing implementation without writing a record.
    from datetime import datetime, timezone

    from pydantic import ValidationError

    if not EvolveIntegration.is_enabled():
        raise HTTPException(status_code=503, detail="Evolve memory is unavailable")
    try:
        from altk_evolve.retention.schedule import CronJobSpec
    except ImportError:
        raise HTTPException(status_code=503, detail="Install the Evolve extra to preview schedules") from None
    try:
        spec = CronJobSpec.model_validate(body.spec)
        instant = datetime.now(timezone.utc)
        occurrences = []
        for _ in range(5):
            instant = spec.next_time(instant)
            occurrences.append(instant.isoformat())
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid schedule or IANA timezone") from None
    return JSONResponse({"next_runs": occurrences, "timeZone": spec.timeZone, "suspended": spec.suspend})


@router.get("/manage/memory/retention/schedules/{schedule_id}")
async def get_retention_schedule(
    schedule_id: str,
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    return JSONResponse(await _schedule_call("get_retention_schedule", schedule_id=schedule_id))


@router.put("/manage/memory/retention/schedules/{schedule_id}")
async def save_retention_schedule(
    schedule_id: str,
    body: RetentionScheduleWrite,
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    if body.policy_id == "cuga-standard":
        await _retention_policies()
    return JSONResponse(
        await _schedule_call(
            "put_retention_schedule",
            schedule_id=schedule_id,
            definition={"policy_id": body.policy_id, "spec": body.spec, "dry_run": False, "agent_id": None},
            expected_revision=body.expected_revision,
            initiated_by=_user_id(current_user),
        )
    )


@router.post("/manage/memory/retention/schedules/{schedule_id}/start")
async def start_retention_schedule(
    schedule_id: str,
    body: RetentionScheduleRevision,
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    return JSONResponse(
        await _schedule_call(
            "start_retention_schedule",
            schedule_id=schedule_id,
            expected_revision=body.expected_revision,
            initiated_by=_user_id(current_user),
        )
    )


@router.post("/manage/memory/retention/schedules/{schedule_id}/stop")
async def stop_retention_schedule(
    schedule_id: str,
    body: RetentionScheduleRevision,
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    return JSONResponse(
        await _schedule_call(
            "stop_retention_schedule",
            schedule_id=schedule_id,
            expected_revision=body.expected_revision,
            initiated_by=_user_id(current_user),
        )
    )


@router.delete("/manage/memory/retention/schedules/{schedule_id}")
async def delete_retention_schedule(
    schedule_id: str,
    expected_revision: int = Query(ge=1),
    current_user: Optional[UserInfo] = Depends(require_manage_access),
):
    return JSONResponse(
        await _schedule_call(
            "delete_retention_schedule",
            schedule_id=schedule_id,
            expected_revision=expected_revision,
        )
    )
