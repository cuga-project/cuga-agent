"""Tool definitions for agent spawning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    module: str
    function: str
    args_schema: Optional[str] = None  # Pydantic class name in module
