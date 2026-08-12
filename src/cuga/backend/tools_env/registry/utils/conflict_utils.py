"""Classification of idempotent-conflict responses (#596).

Some 4xx responses report that the requested post-condition is ALREADY true — the
note exists, the song is in the playlist, the thread is already marked read. Those
are not failures: the goal state holds. Surfacing them as exceptions made the agent
re-issue the same call, which accounted for 57% of all error responses observed
across five AppWorld evaluation bundles (11,747 of 20,469).

Tool errors are built in two places — ``mcp_manager.adapter`` (requests-based,
serves AppWorld traffic) and ``registry.api_registry`` (httpx-based). Both must
apply the same rule, so it lives here rather than in either module.
"""

from typing import Any, Dict, Optional

# 409 Conflict denotes an idempotency conflict by definition, so it needs no
# message inspection. 422 is also used for genuine validation failures, so only an
# explicit allow-list qualifies there — a broad match on "already" would mask real
# errors such as "Your payment card doesn't have enough balance".
IDEMPOTENT_CONFLICT_STATUS = 409

ALREADY_SATISFIED_MESSAGES = (
    "already in the playlist",
    "already exists",
    "already marked as archived",
    "already marked as read",
    "already marked as unread",
)


def is_already_satisfied(status_code: Optional[int], message: Any) -> bool:
    """True when a 4xx response reports that the desired state already holds.

    Args:
        status_code: HTTP status from the tool response.
        message: Response message or body text. May be the raw
            ``"422 Client Error: ... {\"message\": \"...\"}"`` string, since the
            adapter appends the response body to its message.
    """
    if status_code == IDEMPOTENT_CONFLICT_STATUS:
        return True
    if status_code == 422 and message:
        lowered = str(message).lower()
        return any(pattern in lowered for pattern in ALREADY_SATISFIED_MESSAGES)
    return False


def satisfied_result(status_code: Optional[int], message: Any, **extra: Any) -> Dict[str, Any]:
    """Build the non-error result for an already-satisfied call.

    Keeps the original status and message so genuine conflicts stay debuggable.
    Success paths in these modules return the raw response body and carry no
    ``status`` key; consumers only special-case ``"exception"``, so this shape
    cannot be mistaken for a failure.
    """
    result: Dict[str, Any] = {
        "status": "success",
        "already_satisfied": True,
        "status_code": status_code,
        "message": message,
    }
    result.update(extra)
    return result
