"""Shared parse-and-dispatch entry point for slash commands.

Called from ``event_stream`` in the FastAPI server and from
``CugaAgent.invoke`` in the SDK so both paths produce identical semantics. The
caller is responsible for interpreting the returned :class:`DispatchResult`:

* ``passthrough`` — feed ``raw_input`` to the planner unchanged
* ``builtin`` — surface ``text`` as the agent's answer; do not run the planner
* ``unknown`` — surface ``text`` as an error; do not run the planner
* ``skill`` — inject ``injected_messages`` into the graph state, then run the
  planner (slice #17 wires this in; slice #14 returns the placeholder result)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

from cuga.backend.slash_commands.builtins import discover_builtins
from cuga.backend.slash_commands.parser import parse
from cuga.backend.slash_commands.registry import SlashRegistry
from cuga.backend.slash_commands.types import DispatchContext, DispatchResult

if TYPE_CHECKING:  # pragma: no cover
    from cuga.backend.skills.registry import SkillRegistry


def build_slash_registry(skill_registry: Optional["SkillRegistry"] = None) -> SlashRegistry:
    """Discover built-ins and return a fresh SlashRegistry.

    The registry is cheap to rebuild — both built-in discovery and the (already
    cached) skill list are small. Callers rebuild on each request so newly
    dropped SKILL.md files appear without restart.
    """
    return SlashRegistry(builtins=discover_builtins(), skill_registry=skill_registry)


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

    ctx = DispatchContext(
        parsed=parsed,
        slash_registry=slash_registry,
        skill_registry=skill_registry,
        thread_id=thread_id,
        clear_stop_event=clear_stop_event,
        extra=extra or {},
    )

    builtin = slash_registry.get_builtin(parsed.name)
    if builtin is not None:
        try:
            result = await builtin.handle(ctx)
        except Exception as e:
            logger.exception(f"Built-in slash command '/{parsed.name}' raised")
            return DispatchResult(
                kind="unknown",
                text=f"Error executing /{parsed.name}: {e}",
                resolved_name=parsed.name,
                raw_input=parsed.raw_input,
                raw_args=parsed.raw_args,
            )
        result.resolved_name = result.resolved_name or parsed.name
        result.raw_input = result.raw_input or parsed.raw_input
        result.raw_args = result.raw_args or parsed.raw_args
        return result

    if slash_registry.has_skill(parsed.name):
        # Slice #17 fills in injected_messages. Slice #14 returns the placeholder
        # so callers can detect the skill kind today.
        return DispatchResult(
            kind="skill",
            resolved_name=parsed.name,
            raw_input=parsed.raw_input,
            raw_args=parsed.raw_args,
        )

    return DispatchResult(
        kind="unknown",
        text=f"Unknown command: /{parsed.name}",
        resolved_name=parsed.name,
        raw_input=parsed.raw_input,
        raw_args=parsed.raw_args,
    )
