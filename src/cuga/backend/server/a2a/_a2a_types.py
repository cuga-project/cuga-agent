"""Single import site for the A2A pydantic types we use.

The installed ``a2a-sdk`` (1.x) re-exports protobuf classes from
``a2a.types``; the pydantic models that support ``model_validate`` /
``model_dump`` live under ``a2a.compat.v0_3.types``. We import from there
once so the rest of the package — and the tests — stay agnostic to the
SDK's layout.
"""

from __future__ import annotations

from a2a.compat.v0_3.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Artifact,
    HTTPAuthSecurityScheme,
    Message,
    MessageSendParams,
    Part,
    Role,
    SecurityScheme,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

__all__ = [
    "AgentCapabilities",
    "AgentCard",
    "AgentSkill",
    "Artifact",
    "HTTPAuthSecurityScheme",
    "Message",
    "MessageSendParams",
    "Part",
    "Role",
    "SecurityScheme",
    "SendMessageRequest",
    "Task",
    "TaskState",
    "TaskStatus",
    "TaskStatusUpdateEvent",
    "TextPart",
]
