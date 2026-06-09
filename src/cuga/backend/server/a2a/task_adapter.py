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
    name = str(getattr(event, "name", "") or "").lower()
    return any(hint in name for hint in _HITL_HINTS)


def _is_final(event: Any) -> bool:
    if bool(getattr(event, "final", False)):
        return True
    name = str(getattr(event, "name", "") or "").lower()
    return name in {"final_answer", "task_complete", "completed", "done"}


def _event_text(event: Any) -> str:
    data = getattr(event, "data", None)
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "message", "prompt", "content"):
            v = data.get(key)
            if isinstance(v, str):
                return v
    return str(getattr(event, "name", "") or "")


def _message(text: str, message_id: str, context_id: str) -> Message:
    return Message(
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
        message_id=message_id,
        context_id=context_id,
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
            yield TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=False,
                status=TaskStatus(
                    state=TaskState.input_required,
                    message=_message(
                        _event_text(ev) or "Input required",
                        f"{task_id}-msg-{counter}",
                        context_id,
                    ),
                ),
            )
            continue

        if _is_final(ev):
            saw_terminal = True
            yield TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=True,
                status=TaskStatus(
                    state=TaskState.completed,
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
