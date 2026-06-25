"""Shared parse-and-dispatch entry point for slash commands.

Called from ``event_stream`` in the FastAPI server and from
``CugaAgent.invoke`` in the SDK so both paths produce identical semantics. The
caller is responsible for interpreting the returned :class:`DispatchResult`:

* ``passthrough`` — feed ``raw_input`` to the planner unchanged
* ``builtin`` — surface ``text`` as the agent's answer; do not run the planner
* ``unknown`` — surface ``text`` as an error; do not run the planner. When an
  embedding resolver is wired in, ``suggestions`` carries the top semantic
  matches for the mistyped command.
* ``skill`` — inject ``injected_messages`` into the graph state, then run the
  planner

Every recognized slash invocation emits a ``slash_command`` telemetry record
(structured log + best-effort Langfuse span) with the raw input, resolved
kind/name, args, duration, and any unknown-command suggestions.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Tuple

from loguru import logger

from cuga.backend.slash_commands.builtins import discover_builtins
from cuga.backend.slash_commands.command_resolver import CommandResolver, CommandSuggestion
from cuga.backend.slash_commands.message_synthesis import synthesize_skill_invocation
from cuga.backend.slash_commands.parser import ParsedSlash, parse
from cuga.backend.slash_commands.registry import SlashRegistry
from cuga.backend.slash_commands.types import DispatchContext, DispatchResult

if TYPE_CHECKING:  # pragma: no cover
    from cuga.backend.skills.registry import SkillRegistry


CommandResolverFactory = Callable[[SlashRegistry], Awaitable[Optional[CommandResolver]]]


def build_slash_registry(skill_registry: Optional["SkillRegistry"] = None) -> SlashRegistry:
    """Return a fresh SlashRegistry (rebuilt per request so new SKILL.md files appear without restart)."""
    return SlashRegistry(builtins=discover_builtins(), skill_registry=skill_registry)


class _InMemoryEmbeddingStore:
    """Minimal in-process :class:`EmbeddingStoreBackend` for the command resolver.

    The resolver keeps its own in-memory index for ranking (see
    ``command_resolver.py``); it only writes through to the store. Unknown-
    command resolution does not need cross-process persistence, so backing it
    with SQLite/pgvector would only add config surface. This satisfies the
    protocol with no IO.
    """

    def __init__(self) -> None:
        self._rows: Dict[str, dict] = {}

    async def add(self, id: str, embedding: List[float], metadata: dict) -> None:  # noqa: A002
        self._rows[id] = {"id": id, "embedding": embedding, "metadata": metadata}

    async def search(self, query_embedding, limit, metadata_filter):  # pragma: no cover - unused
        return []

    async def get(self, id):  # noqa: A002
        return self._rows.get(id)

    async def delete(self, id) -> None:  # noqa: A002
        self._rows.pop(id, None)

    async def list(self, metadata_filter, limit):
        return list(self._rows.values())[:limit]


# Cache is intentionally size-1: registry contents change rarely (only on
# SKILL.md add/remove) and a stale entry is immediately discarded.
_resolver_cache: Optional[Tuple[int, CommandResolver]] = None
# Serialize rebuilds so two concurrent first-time callers don't both
# construct an embedding client and stomp on the size-1 cache.
_resolver_cache_lock = asyncio.Lock()


def _registry_key(slash_registry: SlashRegistry) -> int:
    return hash(tuple(sorted((c.name, c.kind, c.description or "") for c in slash_registry.list_commands())))


async def build_command_resolver(slash_registry: SlashRegistry) -> Optional[CommandResolver]:
    """Lazily build and index an embedding-based command resolver.

    Returns ``None`` when no embedding backend is available (offline, no model,
    misconfigured) so callers transparently fall back to a plain "unknown
    command" message. The result is cached by registry contents.
    """
    global _resolver_cache
    key = _registry_key(slash_registry)
    cached = _resolver_cache
    if cached is not None and cached[0] == key:
        return cached[1]

    async with _resolver_cache_lock:
        cached = _resolver_cache
        if cached is not None and cached[0] == key:
            return cached[1]

        try:
            from cuga.backend.storage.embedding import create_embedding_function

            embed_fn, _dim = await create_embedding_function()
        except Exception:
            logger.exception("Failed to create embedding function for command resolver")
            return None
        if embed_fn is None:
            return None

        resolver = CommandResolver(store=_InMemoryEmbeddingStore(), embed_fn=embed_fn)
        await resolver.index(slash_registry.list_commands())
        _resolver_cache = (key, resolver)
        return resolver


async def parse_and_dispatch(
    raw: str | None,
    *,
    slash_registry: SlashRegistry,
    skill_registry: Optional["SkillRegistry"] = None,
    thread_id: Optional[str] = None,
    clear_stop_event: Optional[Callable[[str], None]] = None,
    extra: Optional[dict] = None,
    command_resolver_factory: Optional[CommandResolverFactory] = None,
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
        command_resolver_factory=command_resolver_factory,
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
    command_resolver_factory: Optional[CommandResolverFactory],
) -> DispatchResult:
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
        except Exception:
            # Full traceback goes to the server log; the user-facing text is
            # intentionally generic so internal paths/config from the builtin
            # do not leak back into the chat surface.
            logger.exception(f"Built-in slash command '/{parsed.name}' raised")
            return DispatchResult(
                kind="unknown",
                text=f"Error executing /{parsed.name}.",
                resolved_name=parsed.name,
                raw_input=parsed.raw_input,
                raw_args=parsed.raw_args,
            )
        result.resolved_name = result.resolved_name or parsed.name
        result.raw_input = result.raw_input or parsed.raw_input
        result.raw_args = result.raw_args or parsed.raw_args
        return result

    if slash_registry.has_skill(parsed.name):
        assert skill_registry is not None  # has_skill is False without a registry
        try:
            wrapped_body = skill_registry.load_skill(parsed.name, parsed.raw_args)
        except Exception:
            logger.exception(f"Failed to load skill '/{parsed.name}'")
            return DispatchResult(
                kind="unknown",
                text=f"Failed to load skill /{parsed.name}.",
                resolved_name=parsed.name,
                raw_input=parsed.raw_input,
                raw_args=parsed.raw_args,
            )
        injected = synthesize_skill_invocation(
            raw_input=parsed.raw_input,
            raw_args=parsed.raw_args,
            resolved_name=parsed.name,
            wrapped_body=wrapped_body,
        )
        allowed_tools = skill_registry.entry(parsed.name).allowed_tools
        return DispatchResult(
            kind="skill",
            injected_messages=injected,
            resolved_name=parsed.name,
            raw_input=parsed.raw_input,
            raw_args=parsed.raw_args,
            allowed_tools=allowed_tools,
        )

    return await _resolve_unknown(parsed, slash_registry, command_resolver_factory)


async def _resolve_unknown(
    parsed: ParsedSlash,
    slash_registry: SlashRegistry,
    command_resolver_factory: Optional[CommandResolverFactory],
) -> DispatchResult:
    """Build the ``unknown`` result, enriching it with embedding suggestions.

    The resolver never auto-corrects — it only suggests. When no embedding
    backend is available the result degrades to a plain error message.
    """
    suggestions: List[CommandSuggestion] = []
    if command_resolver_factory is not None:
        try:
            resolver = await command_resolver_factory(slash_registry)
            if resolver is not None:
                suggestions = await resolver.resolve(parsed.name)
        except Exception:
            logger.exception(f"Command resolver failed for '/{parsed.name}'")

    text = f"Unknown command: /{parsed.name}"
    if suggestions:
        hint = ", ".join(f"/{s.name}" for s in suggestions)
        text = f"{text}. Did you mean: {hint}?"

    return DispatchResult(
        kind="unknown",
        text=text,
        suggestions=suggestions,
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
    top_suggestions = [
        {"name": s.name, "kind": s.kind, "score": round(s.score, 4)} for s in (result.suggestions or [])
    ]
    # Slash args are arbitrary user input and may contain secrets / PII —
    # log shape metadata only, never the raw strings.
    args_length = len(parsed.raw_args or "")
    logger.info(
        "slash_command dispatch: kind={} name={} command_name={} args_present={} args_length={} duration_ms={:.1f} suggestions={}",
        result.kind,
        result.resolved_name,
        parsed.name,
        bool(parsed.raw_args),
        args_length,
        duration_ms,
        top_suggestions,
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
                "top_suggestions": top_suggestions,
            },
            metadata={"duration_ms": duration_ms},
        )
        span.end()
    except Exception:  # pragma: no cover - telemetry is best-effort
        # Loguru ignores ``exc_info`` (a stdlib-logging keyword); use opt() so
        # the traceback is actually captured at debug level.
        logger.opt(exception=True).debug("Langfuse slash_command span not emitted")
