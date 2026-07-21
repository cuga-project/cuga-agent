"""Shared parse-and-dispatch entry point for slash commands.

Called from ``event_stream`` in the FastAPI server and from
``CugaAgent.invoke`` in the SDK so both paths produce identical semantics. The
caller is responsible for interpreting the returned :class:`DispatchResult`:

* ``passthrough`` — feed ``raw_input`` to the planner unchanged
* ``unknown`` — ``/<name>`` did not resolve to any skill; the caller may
  fall back to the planner or surface an error.
* ``skill`` — feed ``planner_input`` (the translated suggestion) to the
  planner while keeping ``raw_input`` for display/history

Every recognized slash invocation emits a ``slash_command`` telemetry record
(structured log + best-effort Langfuse span) with the raw input, resolved
kind/name, args, and duration.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

from cuga.backend.slash_commands.parser import ParsedSlash, parse
from cuga.backend.slash_commands.translation import translate_skill_invocation
from cuga.backend.slash_commands.registry import SlashRegistry
from cuga.backend.slash_commands.types import DispatchContext, DispatchResult

if TYPE_CHECKING:  # pragma: no cover
    from cuga.backend.skills.registry import SkillRegistry


def build_slash_registry(skill_registry: Optional["SkillRegistry"] = None) -> SlashRegistry:
    """Return a fresh SlashRegistry (rebuilt per request so new SKILL.md files appear without restart)."""
    return SlashRegistry(skill_registry=skill_registry)


async def parse_and_dispatch(
    raw: str | None,
    *,
    slash_registry: SlashRegistry,
    skill_registry: Optional["SkillRegistry"] = None,
    thread_id: Optional[str] = None,
    clear_stop_event: Optional[Callable[[str], None]] = None,
    extra: Optional[dict] = None,
) -> DispatchResult:
    parsed = parse(raw)
    if parsed is None:
        return DispatchResult(kind="passthrough", raw_input=raw)

    start = time.monotonic()
    result = await _dispatch_parsed(
        parsed,
        slash_registry=slash_registry,
        skill_registry=skill_registry,
        thread_id=thread_id,
        clear_stop_event=clear_stop_event,
        extra=extra,
    )
    _emit_slash_telemetry(parsed, result, (time.monotonic() - start) * 1000.0)
    return result


async def _dispatch_parsed(
    parsed: ParsedSlash,
    *,
    slash_registry: SlashRegistry,
    skill_registry: Optional["SkillRegistry"],
    thread_id: Optional[str],
    clear_stop_event: Optional[Callable[[str], None]],
    extra: Optional[dict],
) -> DispatchResult:
    _ = DispatchContext(
        parsed=parsed,
        slash_registry=slash_registry,
        skill_registry=skill_registry,
        thread_id=thread_id,
        clear_stop_event=clear_stop_event,
        extra=extra or {},
    )

    # Kind-based dispatch: builtins (e.g. a future ``/clear``) hard-dispatch —
    # the command runs here and never reaches the planner — while skills
    # soft-suggest: the invocation is translated into a plain planner input
    # and the planner decides to call ``load_skill`` itself.
    if slash_registry.has_skill(parsed.name):
        return DispatchResult(
            kind="skill",
            planner_input=translate_skill_invocation(parsed.name, parsed.raw_args),
            resolved_name=parsed.name,
            raw_input=parsed.raw_input,
            raw_args=parsed.raw_args,
        )

    return DispatchResult(
        kind="unknown",
        resolved_name=parsed.name,
        raw_input=parsed.raw_input,
        raw_args=parsed.raw_args,
    )


def _emit_slash_telemetry(parsed: ParsedSlash, result: DispatchResult, duration_ms: float) -> None:
    """Emit a ``slash_command`` record: structured log + best-effort Langfuse span.

    The Langfuse span is gated on ``advanced_features.langfuse_tracing`` so the
    client is never constructed (and never logs an auth warning) when tracing
    is off. Any failure here is swallowed — telemetry must not break dispatch.
    """
    # Slash args are arbitrary user input and may contain secrets / PII —
    # log shape metadata only, never the raw strings.
    args_length = len(parsed.raw_args or "")
    logger.info(
        "slash_command dispatch: kind={} name={} command_name={} args_present={} args_length={} duration_ms={:.1f}",
        result.kind,
        result.resolved_name,
        parsed.name,
        bool(parsed.raw_args),
        args_length,
        duration_ms,
    )

    try:
        from cuga.config import settings

        if not getattr(getattr(settings, "advanced_features", None), "langfuse_tracing", False):
            return
        from langfuse import get_client

        span = get_client().start_observation(
            name="slash_command",
            as_type="span",
            input={
                "command_name": parsed.name,
                "args_present": bool(parsed.raw_args),
                "args_length": args_length,
            },
            output={
                "resolved_kind": result.kind,
                "resolved_name": result.resolved_name,
            },
            metadata={"duration_ms": duration_ms},
        )
        span.end()
    except Exception:  # pragma: no cover - telemetry is best-effort
        # Loguru ignores ``exc_info`` (a stdlib-logging keyword); use opt() so
        # the traceback is actually captured at debug level.
        logger.opt(exception=True).debug("Langfuse slash_command span not emitted")
