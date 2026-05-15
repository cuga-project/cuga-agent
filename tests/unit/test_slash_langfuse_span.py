"""Unit tests for the ``slash_command`` Langfuse span emitted by the dispatcher.

We don't run a real Langfuse server — instead we patch ``langfuse.get_client``
to return a spy and assert on the exact ``start_observation`` call. This
covers:

  * gating on ``advanced_features.langfuse_tracing`` (no client even
    constructed when tracing is off, which avoids the "no public_key"
    warning that pollutes logs)
  * span name + shape (input/output/metadata) for every dispatch kind
  * top-suggestion attribution for unknown-command resolutions
  * graceful no-op when Langfuse import or client construction fails
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.slash_commands import build_slash_registry, parse_and_dispatch


def _patch_settings(monkeypatch, *, langfuse_tracing: bool) -> None:
    """Toggle the ``advanced_features.langfuse_tracing`` flag the dispatcher reads."""
    from cuga.config import settings

    monkeypatch.setattr(
        settings, "advanced_features", SimpleNamespace(langfuse_tracing=langfuse_tracing), raising=False
    )


def _install_fake_langfuse(monkeypatch) -> MagicMock:
    """Make ``from langfuse import get_client`` return a MagicMock spy."""
    import sys

    span = MagicMock()
    client = MagicMock()
    client.start_observation.return_value = span
    fake_module = SimpleNamespace(get_client=MagicMock(return_value=client))
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    return client


def test_no_langfuse_call_when_tracing_disabled(monkeypatch):
    """When ``langfuse_tracing`` is off the dispatcher must not even import the
    Langfuse client — that prevents the noisy "client initialized without
    public_key" warning in production logs."""
    _patch_settings(monkeypatch, langfuse_tracing=False)
    fake_client = _install_fake_langfuse(monkeypatch)

    reg = build_slash_registry()
    asyncio.run(parse_and_dispatch("/help", slash_registry=reg))

    fake_client.start_observation.assert_not_called()


def test_emits_span_for_builtin_dispatch(monkeypatch):
    _patch_settings(monkeypatch, langfuse_tracing=True)
    fake_client = _install_fake_langfuse(monkeypatch)

    reg = build_slash_registry()
    asyncio.run(parse_and_dispatch("/help", slash_registry=reg))

    fake_client.start_observation.assert_called_once()
    kwargs = fake_client.start_observation.call_args.kwargs
    assert kwargs["name"] == "slash_command"
    assert kwargs["as_type"] == "span"
    assert kwargs["input"] == {"raw_input": "/help", "args": ""}
    assert kwargs["output"]["resolved_kind"] == "builtin"
    assert kwargs["output"]["resolved_name"] == "help"
    assert kwargs["output"]["top_suggestions"] == []
    assert "duration_ms" in kwargs["metadata"]
    assert isinstance(kwargs["metadata"]["duration_ms"], float)
    # End must be called so the span is closed even on fast paths.
    fake_client.start_observation.return_value.end.assert_called_once()


def test_emits_span_with_args_for_skill_dispatch(monkeypatch):
    _patch_settings(monkeypatch, langfuse_tracing=True)
    fake_client = _install_fake_langfuse(monkeypatch)

    skills = SkillRegistry([SkillEntry(name="deck", description="d", body="BODY", source="/p")])
    reg = build_slash_registry(skill_registry=skills)
    asyncio.run(parse_and_dispatch("/deck 3 slides", slash_registry=reg, skill_registry=skills))

    kwargs = fake_client.start_observation.call_args.kwargs
    assert kwargs["input"] == {"raw_input": "/deck 3 slides", "args": "3 slides"}
    assert kwargs["output"]["resolved_kind"] == "skill"
    assert kwargs["output"]["resolved_name"] == "deck"


def test_emits_span_for_unknown_with_suggestions(monkeypatch):
    """Unknown commands must record the ranked suggestions in
    ``output.top_suggestions`` so adoption can be mined for "skills users
    wished existed"."""
    from cuga.backend.slash_commands.command_resolver import CommandResolver, CommandSuggestion

    _patch_settings(monkeypatch, langfuse_tracing=True)
    fake_client = _install_fake_langfuse(monkeypatch)

    suggestions = [
        CommandSuggestion(name="summarize", kind="skill", description="", score=0.91),
        CommandSuggestion(name="summary-report", kind="skill", description="", score=0.74),
    ]

    async def factory(_slash_registry):
        resolver = MagicMock(spec=CommandResolver)
        resolver.resolve = MagicMock(return_value=asyncio.sleep(0, result=suggestions))
        return resolver

    reg = build_slash_registry()
    asyncio.run(
        parse_and_dispatch(
            "/sumarize",
            slash_registry=reg,
            command_resolver_factory=factory,
        )
    )

    kwargs = fake_client.start_observation.call_args.kwargs
    assert kwargs["output"]["resolved_kind"] == "unknown"
    assert kwargs["output"]["top_suggestions"] == [
        {"name": "summarize", "kind": "skill", "score": 0.91},
        {"name": "summary-report", "kind": "skill", "score": 0.74},
    ]


def test_langfuse_exception_is_swallowed(monkeypatch):
    """Telemetry must never break dispatch. If Langfuse blows up (auth error,
    transient network), the dispatcher should still return a normal result."""
    import sys

    _patch_settings(monkeypatch, langfuse_tracing=True)
    fake_module = SimpleNamespace(get_client=MagicMock(side_effect=RuntimeError("boom")))
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    reg = build_slash_registry()
    # Must not raise.
    result = asyncio.run(parse_and_dispatch("/help", slash_registry=reg))
    assert result.kind == "builtin"
