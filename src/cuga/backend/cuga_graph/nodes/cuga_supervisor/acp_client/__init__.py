"""Public outbound ACP configuration and delegation exports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import ACPProcessConfig

__all__ = ["ACPProcessConfig", "delegate_task_via_acp"]


def __getattr__(name: str) -> Any:
    if name == "ACPProcessConfig":
        from .config import ACPProcessConfig

        return ACPProcessConfig
    if name == "delegate_task_via_acp":
        from ..acp_protocol import delegate_task_via_acp

        return delegate_task_via_acp
    raise AttributeError(name)
