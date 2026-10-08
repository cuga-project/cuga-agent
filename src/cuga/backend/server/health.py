"""Health and readiness probing helpers for CUGA backend server.

Supports evaluating subsystem statuses, defining required vs optional/disabled
subsystems, returning HTTP 503 when mandatory subsystems are starting or degraded/failed,
and preserving diagnostic details in the response payload.
"""

from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi.responses import JSONResponse


def build_subsystem_status(
    state: str,
    message: str = "",
    details: Optional[Dict[str, Any]] = None,
    required: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build a structured subsystem status record.

    By default, any subsystem whose state is not 'disabled' is considered required.
    Disabled subsystems do not gate service readiness.
    """
    if required is None:
        required = state != "disabled"

    payload: Dict[str, Any] = {
        "state": state,
        "message": message,
        "required": required,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "details": details if details is not None else {},
    }
    return payload


def evaluate_readiness(statuses: Dict[str, Dict[str, Any]]) -> Tuple[str, bool]:
    """Evaluate overall readiness based on registered subsystem statuses.

    Subsystems that are 'disabled' or explicitly marked 'required=False'
    do not gate overall service readiness.

    Returns:
        A tuple of (overall_status_string, is_ready_boolean).
        Status string is one of 'ready', 'starting', or 'degraded'.
    """
    # Filter for active subsystems that are required and not disabled
    active_statuses = {
        name: info
        for name, info in statuses.items()
        if info.get("state") != "disabled" and info.get("required", True)
    }
    active_states = [info.get("state") for info in active_statuses.values()]

    overall = "ready"
    if any(state == "failed" for state in active_states):
        overall = "degraded"
    elif any(state != "ready" for state in active_states):
        overall = "starting"

    return overall, overall == "ready"


def make_readiness_response(
    subsystem: Optional[str],
    statuses: Dict[str, Dict[str, Any]],
    get_subsystem_status_fn: Callable[[str], Dict[str, Any]],
) -> JSONResponse:
    """Generate the JSONResponse for the /health/readiness endpoint.

    Returns HTTP 200 when ready, and HTTP 503 when mandatory dependencies
    are starting, failed, or unknown.
    """
    if subsystem:
        status = get_subsystem_status_fn(subsystem)
        is_ready = status.get("state") == "ready"
        return JSONResponse(
            {
                "subsystem": subsystem,
                "status": status.get("state", "unknown"),
                "ready": is_ready,
                "required": status.get("required", False),
                "message": status.get("message", ""),
                "details": status.get("details", {}),
                "updated_at": status.get("updated_at"),
            },
            status_code=200 if is_ready else 503,
        )

    overall, is_ready = evaluate_readiness(statuses)
    return JSONResponse(
        {
            "status": overall,
            "ready": is_ready,
            "subsystems": statuses,
        },
        status_code=200 if is_ready else 503,
    )
