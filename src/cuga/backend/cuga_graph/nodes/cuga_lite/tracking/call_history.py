"""Task-scoped memory of tool calls that have already succeeded (#596).

When an API refuses a call because the state it would establish already holds
("the song is already in the playlist", "the note already exists"), the agent
reads a satisfied goal as a failure and re-issues it. Across five AppWorld
bundles those responses were 57% of all errors (11,747 of 20,469).

Message text cannot tell the two apart without hardcoding one API's wording, and
the status code cannot either: every 409 observed across those bundles is a
genuine not-found ("The email thread with id N does not exist."), not an
idempotency conflict. What *does* separate them, with no vendor knowledge at all,
is our own history — if an identical call already succeeded in this task, the
state it establishes is one we set ourselves.

That is evidence, not proof, so this module only ever *annotates*: ``status`` and
``message`` are left untouched and the caller still sees a failure. A wrong
signal costs the model a misleading hint rather than a false success. Recency is
reported alongside the count because state can be reverted between the success
and the retry (``clear_cart`` after ``add_to_cart``), and nothing here can detect
that.

Deliberately narrow, each guard answering a case seen in the bundles:

* **Mutating methods only.** A failing GET establishes nothing — 22 observed
  ``show_thread`` "does not exist" responses follow an identical success.
* **Calls with arguments only.** ``amazon_place_order()`` and
  ``spotify_previous_song()`` take none; their effect is a function of
  server-side state, so a prior success says nothing about the current goal.
* **One task.** Keyed on the tracker's ``task_id`` and dropped when it changes,
  so a success in one task can never speak for another.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from loguru import logger

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# (signature) -> (number of successes, index of the most recent one)
_successes: Dict[str, Tuple[int, int]] = {}
_task_id: Optional[str] = None
_calls_seen: int = 0


def _current_task_id() -> str:
    from cuga.backend.activity_tracker.tracker import ActivityTracker

    return getattr(ActivityTracker(), "task_id", "default") or "default"


def _roll_task_if_needed() -> None:
    """Drop everything when the tracker moves to another task."""
    global _task_id, _successes, _calls_seen

    task_id = _current_task_id()
    if task_id != _task_id:
        _task_id = task_id
        _successes = {}
        _calls_seen = 0


def reset() -> None:
    """Forget all history. Exposed for tests and for explicit task boundaries."""
    global _task_id, _successes, _calls_seen

    _task_id = _current_task_id()
    _successes = {}
    _calls_seen = 0


def signature(app_name: str, api_name: str, args: Optional[Dict[str, Any]]) -> str:
    """Stable key for one call. Argument order must not matter."""
    try:
        rendered = json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:  # pragma: no cover - defensive; args are JSON-shaped
        rendered = str(args)
    return f"{app_name}::{api_name}::{rendered}"


def _is_failure(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "exception"


def _annotation_for(
    result: Dict[str, Any], args: Optional[Dict[str, Any]], sig: str
) -> Optional[Dict[str, int]]:
    """The evidence to attach, or None when a guard rejects this response."""
    if not args:
        # No arguments means the call's target is server-side state, not
        # anything we can match on.
        return None

    status_code = result.get("status_code")
    if not isinstance(status_code, int) or not 400 <= status_code < 500:
        return None

    method = str(result.get("method") or "").upper()
    if method not in MUTATING_METHODS:
        # Absent method included: without it we cannot tell a read from a write.
        return None

    prior = _successes.get(sig)
    if not prior:
        return None

    count, last_index = prior
    return {"count": count, "calls_since": max(0, _calls_seen - last_index)}


def observe(
    app_name: str,
    api_name: str,
    args: Optional[Dict[str, Any]],
    result: Any,
) -> Any:
    """Record a call, and annotate it when it repeats one that already worked.

    Returns ``result``, annotated in place with ``prior_identical_success`` when
    every guard passes. ``status`` and ``message`` are never modified.
    """
    from cuga.config import settings

    if not getattr(settings.advanced_features, "annotate_repeat_calls", False):
        return result

    global _calls_seen

    _roll_task_if_needed()
    _calls_seen += 1
    sig = signature(app_name, api_name, args)

    if not _is_failure(result):
        count, _ = _successes.get(sig, (0, 0))
        _successes[sig] = (count + 1, _calls_seen)
        return result

    annotation = _annotation_for(result, args, sig)
    if annotation is None:
        return result

    result["prior_identical_success"] = annotation
    logger.info(
        f"'{api_name}' failed with {result.get('status_code')} after {annotation['count']} "
        f"identical successful call(s) in this task "
        f"({annotation['calls_since']} call(s) ago); annotated, still reported as an error"
    )
    return result
