"""ACP agent helpers.

Provides utility functions for processing ACP input messages before they are
forwarded to the underlying CUGA agent.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import acp_sdk.server
from acp_sdk.models import Error, ErrorCode, Message, MessageAwaitRequest, MessageAwaitResume, MessagePart
from acp_sdk.models.errors import ACPError
from acp_sdk.models.types import AgentName
from acp_sdk.server.context import Context
from acp_sdk.server.types import RunYield, RunYieldResume

from cuga.backend.server.agent_protocol.simple_runner import _AFFIRM, _DENY, _MAX_AUTO_RESUMES

if TYPE_CHECKING:
    from cuga.backend.server.agent_protocol.protocol import AgentRunner

logger = logging.getLogger(__name__)

_INVALID_INPUT_MSG = "Input must contain at least one user message with plain-text content."

# Event names that signal a completed / terminal answer from the neutral runner.
_TERMINAL_NAMES = {"final_answer", "task_complete", "completed", "done"}

# Event names that signal an error from the neutral runner.
_ERROR_NAMES = {"error", "failed", "failure", "exception"}

# Substrings whose presence in an event name marks it as a HITL interrupt.
_HITL_SUBSTRINGS = ("approval", "input_required", "user_input", "interrupt", "hitl")

# Constant sanitized error yielded to the SDK on any unexpected exception.
_CUGA_ERROR = Error(code=ErrorCode.SERVER_ERROR, message="CUGA agent execution failed")


def _extract_text_input(messages: list[Message]) -> str:
    """Extract plain-text content from a list of ACP messages.

    Rules
    -----
    - Every message must have ``role == "user"``.
    - Every part must have ``content_type == "text/plain"``.
    - ``content_encoding`` must be ``"plain"`` or absent (``None``).
    - Parts must have inline ``content``; ``content_url`` is rejected.
    - Message and part order is preserved.
    - Parts within a single message are joined with ``""``
      (mirrors ``Message.__str__()``).
    - Separate messages are joined with ``"\\n"``.
    - An empty final string is rejected.

    Raises
    ------
    ACPError
        With ``ErrorCode.INVALID_INPUT`` when the above conditions are not
        satisfied.  The error message is a fixed, safe constant — it never
        echoes submitted content.
    """
    segments: list[str] = []

    for message in messages:
        if message.role != "user":
            _reject()

        parts_text: list[str] = []
        for part in message.parts:
            if part.content_type != "text/plain":
                _reject()
            if part.content_encoding not in ("plain", None):
                _reject()
            if part.content_url is not None:
                _reject()
            if part.content is None:
                _reject()
            parts_text.append(part.content)

        if parts_text:
            segments.append("".join(parts_text))

    result = "\n".join(segments)
    if not result:
        _reject()

    return result


def _reject() -> None:
    raise ACPError(Error(code=ErrorCode.INVALID_INPUT, message=_INVALID_INPUT_MSG))


def _is_hitl(event_name: str) -> bool:
    """Return True if *event_name* indicates a human-in-the-loop interrupt."""
    name_lower = event_name.lower()
    return any(sub in name_lower for sub in _HITL_SUBSTRINGS)


def _parse_approval(text: str) -> bool | None:
    """Parse *text* for an approval decision; return True/False/None (ambiguous)."""
    for line in reversed(text.splitlines() or [text]):
        tokens = {t.strip(".,!?;:\"'()[]") for t in line.lower().split()}
        affirm = bool(tokens & _AFFIRM)
        deny = bool(tokens & _DENY)
        if affirm and not deny:
            return True
        if deny and not affirm:
            return False
    return None


def _safe_text(data: object) -> str:
    """Extract a safe string from an event's data payload."""
    if data is None:
        return ""
    if isinstance(data, dict):
        for key in ("text", "message", "content", "data"):
            val = data.get(key)
            if isinstance(val, str):
                return val
        return ""
    return str(data)


class CugaACPAgent(acp_sdk.server.AgentManifest):
    """ACP SDK adapter that wraps a neutral :class:`AgentRunner`.

    Translates :class:`~cuga.backend.server.agent_protocol.events.AgentStreamEvent`
    instances into ACP ``RunYield`` values according to the event-mapping table
    defined in the task specification.
    """

    def __init__(self, *, runner: AgentRunner, name: str, description: str) -> None:
        self._runner = runner
        self._name: AgentName = name
        self._description = description

    @property
    def name(self) -> AgentName:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_content_types(self) -> list[str]:
        return ["text/plain"]

    @property
    def output_content_types(self) -> list[str]:
        return ["text/plain"]

    async def run(
        self,
        input: list[Message],
        context: Context,
    ) -> AsyncGenerator[RunYield, RunYieldResume]:
        """Run the CUGA agent for *input* and yield ACP-compatible events.

        Uses ``context.session.id`` as the CUGA ``context_id`` so that all
        turns of the same ACP session share one conversation thread.
        """
        context_id = str(context.session.id)
        try:
            user_text = _extract_text_input(input)
        except ACPError:
            raise

        try:
            resume_cycles = 0
            current_text = user_text
            current_approval: dict | None = None

            while True:
                hitl_pending: tuple[str | None, str] | None = None  # (action_id, prompt_text)

                async for event in self._runner.run(current_text, context_id, current_approval):
                    name = event.name
                    data = event.data
                    final = event.final

                    # --------------------------------------------------------
                    # Error events
                    # --------------------------------------------------------
                    if name in _ERROR_NAMES:
                        logger.warning("CUGA runner returned error event: name=%s", name)
                        yield _CUGA_ERROR
                        return

                    # --------------------------------------------------------
                    # HITL interrupt events
                    # --------------------------------------------------------
                    if _is_hitl(name):
                        action_id: str | None = None
                        if isinstance(data, dict):
                            action_id = str(data["action_id"]) if data.get("action_id") is not None else None
                        prompt_text = _safe_text(data) or "Approval required."
                        hitl_pending = (action_id, prompt_text)
                        break  # stop consuming the stream; handle HITL below

                    # --------------------------------------------------------
                    # Terminal answer events
                    # --------------------------------------------------------
                    if name in _TERMINAL_NAMES or (final and not _is_hitl(name) and name not in _ERROR_NAMES):
                        text = _safe_text(data) or "Agent completed processing"
                        yield Message(
                            role="agent",
                            parts=[MessagePart(content_type="text/plain", content=text)],
                        )
                        return

                    # Non-terminal progress — ignore per spec.

                if hitl_pending is not None:
                    # Enforce the resume cycle limit
                    if resume_cycles >= _MAX_AUTO_RESUMES:
                        yield Message(
                            role="agent",
                            parts=[
                                MessagePart(content_type="text/plain", content="Agent completed processing")
                            ],
                        )
                        return

                    action_id_str, prompt_text = hitl_pending

                    # Build the prompt message with action_id encoded in part name
                    prompt_part = MessagePart(
                        content_type="text/plain",
                        content=prompt_text,
                        name=action_id_str,
                    )
                    prompt_msg = Message(role="agent", parts=[prompt_part])

                    # Yield the await request and receive the resume
                    resume_value: RunYieldResume = yield MessageAwaitRequest(message=prompt_msg)

                    if not isinstance(resume_value, MessageAwaitResume):
                        # No resume provided — the caller owns the conversation
                        # from here; return without yielding anything further.
                        return

                    resumed_text = str(resume_value.message)
                    decision = _parse_approval(resumed_text)
                    current_approval = None
                    if decision is not None and action_id_str is not None:
                        current_approval = {"action_id": action_id_str, "confirmed": decision}

                    current_text = resumed_text
                    resume_cycles += 1
                    continue  # loop: run the runner again with the resumed text

                # Stream ended without a HITL or terminal event.
                yield Message(
                    role="agent",
                    parts=[MessagePart(content_type="text/plain", content="Agent completed processing")],
                )
                return

        except ACPError:
            raise
        except Exception:
            logger.exception("CugaACPAgent.run raised unexpectedly")
            yield _CUGA_ERROR
            return
