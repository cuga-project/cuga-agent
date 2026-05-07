"""CUGA Loops — agent self-scheduling primitive.

Lets agents call `schedule_recurring(...)` and `schedule_wakeup(...)` as tools.
The scheduler persists loops in SQLite and re-invokes the originating agent
on the same `thread_id` when each fires. A FastAPI router exposes a global
HTML UI that lists all loops in the registry with pause/resume/delete.

Opt-in per agent via `CugaAgent(enable_loops=True)` or `CugaSupervisor(enable_loops=True)`.
"""

from cuga.backend.loops.service import LoopsService, get_loops_service
from cuga.backend.loops.models import Loop, LoopRun, LoopStatus, TriggerKind

__all__ = [
    "LoopsService",
    "get_loops_service",
    "Loop",
    "LoopRun",
    "LoopStatus",
    "TriggerKind",
]
