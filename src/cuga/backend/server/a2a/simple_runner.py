"""Simple A2A runner that directly uses CUGA's event stream.

This runner provides a lightweight alternative to the SupervisorA2ARunner
for deployments that want to expose a single CUGA agent over A2A without
the multi-agent orchestration overhead of CugaSupervisor.

It consumes the *same* SSE frames ``event_stream`` produces for the web UI
(``event: <name>\\ndata: <payload>``) and translates them into the small
``A2AStreamEvent`` triples the task adapter understands. When the graph
pauses on a human-in-the-loop interrupt, behaviour is controlled by the
``auto_approve`` flag: either auto-confirm and resume, or surface the
pending approval to the A2A caller and stop.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional, Tuple

from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse
from cuga.backend.cuga_graph.utils.agent_loop import StreamEvent
from cuga.backend.server.a2a.runner import A2AStreamEvent

logger = logging.getLogger(__name__)

# Cap on auto-approval resume cycles so a graph that re-interrupts forever
# cannot spin this runner into an unbounded loop.
_MAX_AUTO_RESUMES = 12

# Event names that carry a terminal answer / error from event_stream.
_ANSWER_NAMES = {"Answer", "final_answer"}
_ERROR_NAMES = {"Error", "error", "failed", "failure", "exception"}

# Text-fallback vocabulary for reading approve/deny out of a plain message
# when no structured approval signal is supplied.
_AFFIRM = {
    "approve",
    "approved",
    "yes",
    "y",
    "ok",
    "okay",
    "confirm",
    "confirmed",
    "accept",
    "accepted",
    "run",
    "proceed",
    "go",
}
_DENY = {
    "deny",
    "denied",
    "no",
    "n",
    "reject",
    "rejected",
    "cancel",
    "cancelled",
    "canceled",
    "decline",
    "declined",
    "abort",
    "stop",
}


class SimpleA2ARunner:
    """Run inbound A2A messages directly through CUGA's event stream.

    This runner provides a simple collaborator agent that:
    - Uses the existing CUGA agent graph via ``event_stream``
    - Maintains conversation context via ``thread_id``
    - Returns the agent's actual answer
    - Requires no supervisor configuration
    """

    def __init__(self, app_state_ref: Any, event_stream_func: Any, auto_approve: bool = False):
        """Initialize with a reference to app_state and the event_stream function.

        Args:
            app_state_ref: Reference to the FastAPI app_state (used to read
                graph state when detecting human-in-the-loop interrupts).
            event_stream_func: The ``event_stream`` async generator from main.py.
            auto_approve: When True, HITL interrupts are auto-confirmed and the
                graph is resumed; when False (default), the interrupt is surfaced
                to the A2A caller as an ``input_required`` terminal event.
        """
        self._app_state = app_state_ref
        self._event_stream = event_stream_func
        self._auto_approve = auto_approve

    async def run(
        self,
        message: str,
        context_id: Optional[str] = None,
        approval: Optional[dict] = None,
    ) -> AsyncIterator[A2AStreamEvent]:
        """Process ``message`` through the CUGA agent and yield response events.

        Args:
            message: The user message to process.
            context_id: Optional thread ID for conversation continuity. A
                stable id is required to read graph state on interrupt, so one
                is generated when the caller omits it.
            approval: Optional structured approval signal extracted from the
                inbound A2A message (e.g. ``{"action_id": ..., "confirmed": bool}``).
                Only consulted when the thread is parked on a HITL interrupt.

        Yields:
            A2AStreamEvent instances representing the agent's response.
        """
        thread_id = context_id or uuid.uuid4().hex
        resume: Optional[ActionResponse] = None

        try:
            # If the thread is already parked on a HITL interrupt, this inbound
            # message is the approval response — resume the graph rather than
            # starting a fresh query (the auto_approve=False round-trip).
            parked = self._pending_action(thread_id)
            if parked is not None:
                decision = self._decide(parked, message, approval)
                logger.info(
                    "A2A approval reply on parked thread %s: action=%s decision=%s",
                    thread_id,
                    parked.get("action_id"),
                    decision,
                )
                if decision is None:
                    # Ambiguous reply: re-ask without resuming.
                    yield self._input_required_event(parked)
                    return
                resume = self._build_response(parked, decision, message)

            for _ in range(_MAX_AUTO_RESUMES + 1):
                async for frame in self._event_stream(
                    query=message if resume is None else None,
                    api_mode=True,  # Skip browser environment for A2A
                    thread_id=thread_id,
                    agent=None,  # Use default (published) agent
                    disable_history=False,  # Keep conversation history
                    user_id="a2a_user",  # Special user ID for A2A requests
                    user_attachments=None,
                    resume=resume,
                ):
                    name, text = self._decode(frame)
                    if name is None:
                        continue
                    if name in _ANSWER_NAMES:
                        yield A2AStreamEvent("final_answer", {"text": text}, final=True)
                        return
                    if name in _ERROR_NAMES:
                        yield A2AStreamEvent("error", {"text": text or "Agent error"}, final=True)
                        return
                    # Anything else is in-flight progress.
                    yield A2AStreamEvent(name, {"text": text}, final=False)

                # Stream ended without a terminal answer. The graph may be
                # paused on a human-in-the-loop interrupt awaiting approval.
                pending = self._pending_action(thread_id)
                if not pending:
                    break

                if not self._auto_approve:
                    yield self._input_required_event(pending)
                    return

                logger.info(f"A2A auto-approving HITL action {pending.get('action_id')}")
                resume = self._build_response(pending, True, None)

            # Exhausted the resume budget (or the stream ended cleanly with no
            # answer and no pending interrupt) without a terminal answer.
            logger.warning(f"A2A event stream completed without a final answer (thread_id={thread_id})")
            yield A2AStreamEvent("final_answer", {"text": "Agent completed processing"}, final=True)

        except Exception as exc:
            logger.exception("Simple A2A runner failed")
            yield A2AStreamEvent("error", {"text": f"Agent error: {type(exc).__name__}"}, final=True)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _decode(frame: Any) -> Tuple[Optional[str], Optional[str]]:
        """Decode one SSE frame into ``(event_name, text)``.

        Handles two shapes:
        - The real ``event_stream`` framing ``event: <name>\\ndata: <payload>``.
        - The ``data: {"name": ..., "data": ...}`` envelope used by tests.

        Returns ``(None, None)`` for frames we can't attribute to a named
        event (e.g. the bare-data progress frames OutputFormat.DEFAULT emits).
        """
        text = frame.decode("utf-8") if isinstance(frame, (bytes, bytearray)) else str(frame)
        if not text or not text.strip():
            return None, None
        stripped = text.strip()

        # Real StreamEvent framing carries an explicit ``event:`` line.
        if stripped.startswith("event:") or "\nevent:" in text:
            try:
                ev = StreamEvent.parse(text)
                return ev.name, SimpleA2ARunner._payload_text(ev.data)
            except Exception:
                return None, None

        # Envelope framing used by the test mocks.
        if stripped.startswith("data:"):
            body = stripped[len("data:") :].strip()
            if not body:
                return None, None
            try:
                obj = json.loads(body)
            except json.JSONDecodeError:
                return None, None
            if isinstance(obj, dict) and "name" in obj:
                return obj.get("name", "unknown"), SimpleA2ARunner._payload_text(obj.get("data"))

        # Bare-data progress frame: no name to dispatch on.
        return None, None

    @staticmethod
    def _payload_text(data: Any) -> str:
        """Best-effort extract human-visible text from an event payload.

        ``Answer`` frames carry a JSON string like
        ``{"data": <answer>, "variables": {...}, "active_policies": [...]}``;
        we surface the ``data`` field. Dict payloads (test envelopes) are
        probed for the usual text-bearing keys.
        """
        if data is None:
            return ""
        if isinstance(data, dict):
            return SimpleA2ARunner._from_mapping(data)
        if isinstance(data, str):
            s = data.strip()
            if s[:1] in ("{", "["):
                try:
                    parsed = json.loads(s)
                except json.JSONDecodeError:
                    return data
                if isinstance(parsed, dict):
                    return SimpleA2ARunner._from_mapping(parsed)
            return data
        return str(data)

    @staticmethod
    def _from_mapping(mapping: dict) -> str:
        """Pull the first string-valued text key from a mapping, else dump it."""
        for key in ("data", "text", "message", "content"):
            value = mapping.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(mapping, ensure_ascii=False)

    def _pending_action(self, thread_id: str) -> Optional[dict]:
        """Return the pending ``hitl_action`` dict if the graph is paused, else None.

        Reads the published agent's graph state for ``thread_id``. The graph is
        only treated as paused when it has a ``next`` node *and* a non-empty
        ``hitl_action`` in state (set by the producing node, cleared after
        WaitForResponse resumes).
        """
        agent = getattr(self._app_state, "agent", None)
        graph = getattr(agent, "graph", None)
        if graph is None:
            return None
        try:
            snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
        except Exception:
            logger.debug(f"A2A: could not read graph state for thread {thread_id}", exc_info=True)
            return None

        if not getattr(snapshot, "next", None):
            return None
        values = getattr(snapshot, "values", None) or {}
        pending = values.get("hitl_action")
        if not pending:
            return None
        if not isinstance(pending, dict):
            # A FollowUpAction object rather than its dumped dict.
            dump = getattr(pending, "model_dump", None)
            if not callable(dump):
                return None
            try:
                pending = dump()
            except Exception:
                return None
        return pending

    @staticmethod
    def _input_required_event(pending: dict) -> A2AStreamEvent:
        """Terminal event that surfaces a pending HITL action to the A2A caller.

        The raw graph description (e.g. "I will run a new flow autonomously")
        is meaningless to an external caller on its own, so we frame it as an
        explicit approval request and spell out the reply contract. This both
        reads sensibly to a human (e.g. in watsonx Orchestrate) and primes the
        plain-text approve/deny fallback for clients that can't send a
        structured signal.
        """
        desc = pending.get("description") or "the agent wants to continue."
        text = f'Approval required: {desc}\n\nReply "approve" to proceed or "deny" to cancel.'
        return A2AStreamEvent(
            "input_required",
            {"text": text, "action_id": pending.get("action_id")},
            final=True,
        )

    @staticmethod
    def _decide(pending: dict, message: Optional[str], approval: Optional[dict]) -> Optional[bool]:
        """Resolve a pending HITL action to confirm (True) / deny (False) / ambiguous (None).

        Confirmation and button gates need an explicit decision: a structured
        ``approval["confirmed"]`` wins; otherwise the message text is matched
        against affirmative / negative word sets. Anything unrecognized returns
        None so the caller can re-ask instead of guessing. Non-confirmation
        gates (text / natural-language follow-ups) just take the message text
        and always proceed.
        """
        ptype = (pending.get("type") or "confirmation").lower()
        if ptype not in ("confirmation", "button"):
            return True

        if isinstance(approval, dict) and approval.get("confirmed") is not None:
            return bool(approval["confirmed"])

        # Some callers (e.g. watsonx Orchestrate) echo the whole conversation
        # transcript back as one message — including our own prompt line
        # ("...approve...deny..."), which contains both words. Matching the
        # full blob would always read as ambiguous, so scan lines from the
        # bottom and take the first decisive (affirm-XOR-deny) one; the real
        # reply is the last line. Lines with both words (our echoed prompt) or
        # neither are skipped. Surrounding punctuation is stripped so
        # "approve." still matches.
        for line in reversed((message or "").splitlines() or [message or ""]):
            tokens = {t.strip(".,!?;:\"'()[]") for t in line.lower().split()}
            affirm = bool(tokens & _AFFIRM)
            deny = bool(tokens & _DENY)
            if affirm and not deny:
                return True
            if deny and not affirm:
                return False
        return None

    @staticmethod
    def _build_response(pending: dict, decision: bool, message: Optional[str]) -> ActionResponse:
        """Build an ActionResponse for a pending HITL action.

        WaitForResponse reconstructs ``ActionResponse(**response)`` and
        overwrites ``additional_data`` from the original action, so the
        response only needs the identifying fields plus the decision.
        """
        return ActionResponse(
            action_id=pending.get("action_id", "unknown"),
            response_type=pending.get("type", "confirmation"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id="a2a_user",
            confirmed=bool(decision),
            button_clicked=bool(decision),
            text_response=message or None,
            additional_data=pending.get("additional_data"),
        )
