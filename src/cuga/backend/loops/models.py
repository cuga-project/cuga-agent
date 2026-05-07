"""Pydantic models for CUGA Loops."""
from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TriggerKind(str, Enum):
    DELAY = "delay"
    INTERVAL = "interval"
    CRON = "cron"


class LoopStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ORPHANED = "orphaned"


class RunOutcome(str, Enum):
    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"
    MISSED = "missed"


class Loop(BaseModel):
    id: str
    app_name: str
    agent_name: str
    thread_id: str
    prompt: str
    trigger_kind: TriggerKind
    trigger_spec: str  # raw spec text, e.g. "5m", "0 9 * * *", "60" (seconds)
    cron: Optional[str] = None  # normalized cron when applicable
    interval_seconds: Optional[int] = None  # for interval/delay
    status: LoopStatus = LoopStatus.ACTIVE
    created_at: float = Field(default_factory=time.time)
    expires_at: Optional[float] = None
    last_fire_at: Optional[float] = None
    next_fire_at: Optional[float] = None
    fire_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    metadata_json: Optional[str] = None  # arbitrary JSON the agent attached


class LoopRun(BaseModel):
    id: int | None = None
    loop_id: str
    started_at: float
    finished_at: Optional[float] = None
    outcome: RunOutcome = RunOutcome.OK
    summary: Optional[str] = None  # truncated agent answer
    error: Optional[str] = None
