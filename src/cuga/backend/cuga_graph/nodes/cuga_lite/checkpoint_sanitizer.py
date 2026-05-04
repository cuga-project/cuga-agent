"""Checkpoint-safe normalization helpers for CugaLite graph state."""

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict

from langchain_core.messages import BaseMessage


_SANITIZE_KEYS = (
    "variables_storage",
    "cuga_lite_metadata",
    "tool_calls",
    "task_todos",
    "metrics",
    "last_summarization_metrics",
)


def normalize_checkpoint_value(value: Any) -> Any:
    """Normalize msgpack-unsafe runtime values while preserving LangChain messages."""
    if isinstance(value, BaseMessage):
        return value

    value_module = getattr(value.__class__, "__module__", "")
    if value_module.startswith("numpy"):
        if hasattr(value, "item") and callable(value.item):
            try:
                return normalize_checkpoint_value(value.item())
            except Exception:
                pass
        if hasattr(value, "tolist") and callable(value.tolist):
            try:
                return normalize_checkpoint_value(value.tolist())
            except Exception:
                pass

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Enum):
        return normalize_checkpoint_value(value.value)

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()

    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return normalize_checkpoint_value(value.model_dump())
        except Exception:
            pass

    if hasattr(value, "dict") and callable(value.dict):
        try:
            return normalize_checkpoint_value(value.dict())
        except Exception:
            pass

    if is_dataclass(value) and not isinstance(value, type):
        return normalize_checkpoint_value(asdict(value))

    if isinstance(value, dict):
        return {
            normalize_checkpoint_value(key): normalize_checkpoint_value(item) for key, item in value.items()
        }

    if isinstance(value, list):
        return [normalize_checkpoint_value(item) for item in value]

    if isinstance(value, tuple):
        return tuple(normalize_checkpoint_value(item) for item in value)

    if isinstance(value, set):
        return [normalize_checkpoint_value(item) for item in value]

    return str(value)


def sanitize_cuga_lite_update(update: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize the mutable parts of CugaLite graph state before checkpoint writes."""
    sanitized = dict(update)
    for key in _SANITIZE_KEYS:
        if key in sanitized:
            sanitized[key] = normalize_checkpoint_value(sanitized[key])
    return sanitized
