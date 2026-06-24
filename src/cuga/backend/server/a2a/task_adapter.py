"""Map CUGA StreamEvents to A2A task lifecycle events.

The adapter keeps the protocol surface narrow: it accepts whatever the
CUGA graph yields (just a duck-typed object exposing ``name``, optional
``data``, and an optional ``final`` flag) and emits A2A
``TaskStatusUpdateEvent`` instances. We don't import CUGA's internal
event types here — that would couple the A2A layer to graph internals.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator

from cuga.backend.server.a2a._a2a_types import (
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

# Event names CUGA already uses (or is likely to use) that signal a HITL
# interrupt. Matching is substring-based so we forward-compat new variants.
_HITL_HINTS = ("approval", "input_required", "user_input", "interrupt", "hitl")


def _is_hitl(event: Any) -> bool:
    """Return True when ``event.name`` looks like a human-in-the-loop interrupt."""
    name = str(getattr(event, "name", "") or "").lower()
    return any(hint in name for hint in _HITL_HINTS)


def _is_final(event: Any) -> bool:
    """Return True when ``event`` should close the A2A task."""
    if bool(getattr(event, "final", False)):
        return True
    name = str(getattr(event, "name", "") or "").lower()
    return name in {"final_answer", "task_complete", "completed", "done"} or _is_error(event)


def _is_error(event: Any) -> bool:
    """Return True when ``event`` represents a terminal failure.

    Used by ``_is_final`` to recognize the event AND by the dispatch
    branch to distinguish ``TaskState.failed`` from ``TaskState.completed``
    — without this, error terminals were silently mapped to success.
    """
    name = str(getattr(event, "name", "") or "").lower()
    return name in {"error", "failed", "failure", "exception"}


def _event_text(event: Any) -> str:
    """Best-effort extract the user-visible text payload from an event.

    Probes the common shapes CUGA's graph emits (a raw string, or a dict
    with one of ``text``/``message``/``prompt``/``content``), falling
    back to the event name so the A2A frame is never empty.
    """
    data = getattr(event, "data", None)
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "message", "prompt", "content"):
            v = data.get(key)
            if isinstance(v, str):
                return v
    return str(getattr(event, "name", "") or "")


def _message(text: str, message_id: str, context_id: str, metadata: dict[str, Any] | None = None) -> Message:
    """Build an agent-role A2A Message wrapping ``text`` as a single TextPart.

    ``metadata`` is attached verbatim when provided — used to carry the HITL
    ``action_id`` so a caller can correlate an approval response.
    """
    return Message(
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
        message_id=message_id,
        context_id=context_id,
        metadata=metadata,
    )


def stream_events_to_a2a(
    events: Iterable[Any],
    *,
    task_id: str,
    context_id: str,
) -> Iterator[TaskStatusUpdateEvent]:
    """Translate a stream of CUGA events into A2A TaskStatusUpdateEvents.

    Guarantees:
    - At least one terminal event is always emitted (completed or failed),
      so an A2A client never hangs on an open task.
    - Unknown event names are coerced into a ``working`` update rather
      than raising — protocol forward-compatibility.
    - ``context_id`` round-trips verbatim onto every emitted event.
    """
    saw_terminal = False
    counter = 0

    for ev in events:
        counter += 1
        if _is_hitl(ev):
            # A HITL interrupt can be terminal (the task pauses awaiting input
            # and the stream closes) when the runner marks the event final.
            is_final = bool(getattr(ev, "final", False))
            if is_final:
                saw_terminal = True
            data = getattr(ev, "data", None)
            meta = (
                {"action_id": data["action_id"]} if isinstance(data, dict) and data.get("action_id") else None
            )
            yield TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=is_final,
                status=TaskStatus(
                    state=TaskState.input_required,
                    message=_message(
                        _event_text(ev) or "Input required",
                        f"{task_id}-msg-{counter}",
                        context_id,
                        metadata=meta,
                    ),
                ),
            )
            continue

        if _is_final(ev):
            saw_terminal = True
            terminal_state = TaskState.failed if _is_error(ev) else TaskState.completed
            yield TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=True,
                status=TaskStatus(
                    state=terminal_state,
                    message=_message(_event_text(ev) or "", f"{task_id}-final", context_id),
                ),
            )
            continue

        # Default: any other (named or unknown) event is in-flight progress.
        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            final=False,
            status=TaskStatus(
                state=TaskState.working,
                message=_message(_event_text(ev), f"{task_id}-msg-{counter}", context_id),
            ),
        )

    if not saw_terminal:
        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            final=True,
            status=TaskStatus(
                state=TaskState.completed,
                message=_message("", f"{task_id}-final", context_id),
            ),
        )
