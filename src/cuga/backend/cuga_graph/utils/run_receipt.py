"""Per-run token/timing receipt for SDK invocations.

Enabled via ``advanced_features.run_receipt`` (default off). A fresh
``RunMetricsCollector`` is attached per ``CugaAgent.invoke()`` so concurrent
runs never share counters (unlike the process-global ActivityTracker).
Every code path here is fail-safe: a broken provider response or malformed
tool-call record degrades the receipt (or drops it), never the run.
"""

import time
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from loguru import logger
from pydantic import BaseModel, Field


class ToolTiming(BaseModel):
    name: str
    calls: int
    total_ms: float


class RunReceipt(BaseModel):
    """Token and latency picture of a single agent run.

    Reports token counts only, deliberately not cost: CUGA runs against
    self-hosted and internal deployments whose price we don't know, so any
    figure here would be wrong or misleading for some users. Multiply
    ``input_tokens`` / ``output_tokens`` by your own rates instead.
    """

    models: List[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0  # subset of input_tokens served from provider prompt cache
    reasoning_tokens: int = 0  # subset of output_tokens spent on reasoning (when reported)
    llm_calls: int = 0
    tool_call_count: int = 0
    llm_time_s: float = 0.0
    tool_time_s: float = 0.0
    wall_time_s: float = 0.0
    slowest_tool: Optional[str] = None
    tool_timings: List[ToolTiming] = Field(default_factory=list)

    def __str__(self) -> str:
        tokens_line = f"tokens: {self.input_tokens:,} in / {self.output_tokens:,} out ({self.total_tokens:,})"
        if self.cache_read_tokens and self.input_tokens:
            tokens_line += f" — {100 * self.cache_read_tokens // self.input_tokens}% cached"
        if self.reasoning_tokens:
            tokens_line += f" — {self.reasoning_tokens:,} reasoning"
        lines = [
            f"model: {', '.join(self.models) if self.models else 'unknown'}",
            tokens_line,
            f"llm calls: {self.llm_calls}   tool calls: {self.tool_call_count}",
            f"time: {self.wall_time_s:.1f}s (llm {self.llm_time_s:.1f}s / tools {self.tool_time_s:.1f}s)",
        ]
        if self.slowest_tool:
            slowest = next((t for t in self.tool_timings if t.name == self.slowest_tool), None)
            duration = f" {slowest.total_ms / 1000:.1f}s" if slowest else ""
            lines.append(f"slowest tool: {self.slowest_tool}{duration}")
        width = max(len(line) for line in lines) + 2
        title = "┌─ Run Receipt " + "─" * (width - 14) + "┐"
        body = [f"│ {line.ljust(width - 2)} │" for line in lines]
        return "\n".join([title, *body, "└" + "─" * width + "┘"])


class RunMetricsCollector(AsyncCallbackHandler):
    """Accumulates per-run LLM usage and wall time; methods never raise."""

    def __init__(self) -> None:
        self.llm_calls = 0
        self.llm_time_s = 0.0
        self.usage_by_model: Dict[str, Dict[str, int]] = {}
        self._started_at: Dict[str, float] = {}
        self._pending_model: Dict[str, str] = {}

    def _on_model_start(self, serialized: Any, run_id: Any, kwargs: Dict[str, Any]) -> None:
        try:
            self._started_at[str(run_id)] = time.monotonic()
            model_name = (kwargs.get("metadata") or {}).get("ls_model_name")
            if not model_name and isinstance(serialized, dict):
                model_kwargs = serialized.get("kwargs") or {}
                model_name = model_kwargs.get("model_name") or model_kwargs.get("model")
            if model_name:
                self._pending_model[str(run_id)] = str(model_name)
        except Exception as e:
            logger.debug(f"RunMetricsCollector.on_model_start skipped: {e}")

    async def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs) -> None:
        self._on_model_start(serialized, run_id, kwargs)

    async def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs) -> None:
        self._on_model_start(serialized, run_id, kwargs)

    async def on_llm_error(self, error, *, run_id=None, **kwargs) -> None:
        try:
            self._started_at.pop(str(run_id), None)
            self._pending_model.pop(str(run_id), None)
        except Exception as e:
            logger.debug(f"RunMetricsCollector.on_llm_error skipped: {e}")

    async def on_llm_end(self, response: LLMResult, *, run_id=None, **kwargs) -> None:
        try:
            self.llm_calls += 1
            started = self._started_at.pop(str(run_id), None)
            if started is not None:
                self.llm_time_s += time.monotonic() - started

            # generations can be empty/malformed on some providers; usage may
            # then still be available via llm_output.
            generations = getattr(response, "generations", None) or []
            first = generations[0][0] if generations and generations[0] else None
            message = getattr(first, "message", None)
            usage = getattr(message, "usage_metadata", None) or {}
            llm_output = getattr(response, "llm_output", None) or {}
            cache_read_tokens = int((usage.get("input_token_details") or {}).get("cache_read") or 0)
            reasoning_tokens = int((usage.get("output_token_details") or {}).get("reasoning") or 0)
            if not usage:
                legacy = llm_output.get("token_usage") or {}
                usage = {
                    "input_tokens": legacy.get("prompt_tokens", 0),
                    "output_tokens": legacy.get("completion_tokens", 0),
                    "total_tokens": legacy.get("total_tokens", 0),
                }

            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or 0)
            if not (input_tokens or output_tokens or total_tokens or cache_read_tokens):
                return  # no usage anywhere — don't record a zero "unknown" model

            model_name = (
                self._pending_model.pop(str(run_id), None)
                or llm_output.get("model_name")
                or (getattr(message, "response_metadata", None) or {}).get("model_name")
                or "unknown"
            )
            per_model = self.usage_by_model.setdefault(
                str(model_name),
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cache_read_tokens": 0,
                    "reasoning_tokens": 0,
                },
            )
            per_model["input_tokens"] += input_tokens
            per_model["output_tokens"] += output_tokens
            per_model["total_tokens"] += total_tokens
            per_model["cache_read_tokens"] += cache_read_tokens
            per_model["reasoning_tokens"] += reasoning_tokens
        except Exception as e:
            logger.debug(f"RunMetricsCollector.on_llm_end skipped: {e}")


def build_run_receipt(
    collector: RunMetricsCollector,
    tool_calls: Optional[List[Dict[str, Any]]],
    wall_time_s: float,
) -> Optional[RunReceipt]:
    """Aggregate collector + tracked tool calls into a receipt; None on any failure."""
    try:
        timings: Dict[str, ToolTiming] = {}
        for call in tool_calls or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "unknown")
            duration_ms = float(call.get("duration_ms") or 0)
            timing = timings.setdefault(name, ToolTiming(name=name, calls=0, total_ms=0.0))
            timing.calls += 1
            timing.total_ms += duration_ms
        tool_timings = sorted(timings.values(), key=lambda t: t.total_ms, reverse=True)

        input_tokens = sum(u["input_tokens"] for u in collector.usage_by_model.values())
        output_tokens = sum(u["output_tokens"] for u in collector.usage_by_model.values())
        total_tokens = sum(u["total_tokens"] for u in collector.usage_by_model.values())
        cache_read_tokens = sum(u.get("cache_read_tokens", 0) for u in collector.usage_by_model.values())
        reasoning_tokens = sum(u.get("reasoning_tokens", 0) for u in collector.usage_by_model.values())

        return RunReceipt(
            models=list(collector.usage_by_model.keys()),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read_tokens,
            reasoning_tokens=reasoning_tokens,
            llm_calls=collector.llm_calls,
            tool_call_count=sum(t.calls for t in tool_timings),
            llm_time_s=round(collector.llm_time_s, 3),
            tool_time_s=round(sum(t.total_ms for t in tool_timings) / 1000, 3),
            wall_time_s=round(wall_time_s, 3),
            slowest_tool=tool_timings[0].name if tool_timings else None,
            tool_timings=tool_timings,
        )
    except Exception as e:
        logger.debug(f"build_run_receipt failed, returning None: {e}")
        return None
