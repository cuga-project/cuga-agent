"""Per-execution context for supervisor code runs.

Delegation tools are built at prepare time but run inside ``execute_agent_tool``.
Runtime state (supervisor state, variable manager) is injected into the code
executor locals under ``SUPERVISOR_EXEC_KEY`` for the duration of each execute
call — never stored on the long-lived graph adapter.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Optional

SUPERVISOR_EXEC_KEY = "__supervisor_exec__"


@dataclass
class SupervisorExecutionContext:
    state: Any
    variable_manager: Any = None


def resolve_supervisor_execution_context() -> Optional[SupervisorExecutionContext]:
    """Read the active execution context from the delegating code's call stack."""
    frame = inspect.currentframe()
    try:
        current = frame.f_back if frame is not None else None
        while current is not None:
            for scope in (current.f_locals, current.f_globals):
                ctx = scope.get(SUPERVISOR_EXEC_KEY)
                if isinstance(ctx, SupervisorExecutionContext):
                    return ctx
            current = current.f_back
    finally:
        del frame
    return None
