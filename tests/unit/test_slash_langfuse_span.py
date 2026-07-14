"""Unit tests for the ``slash_command`` Langfuse span emitted by the dispatcher.

We don't run a real Langfuse server — instead we patch ``langfuse.get_client``
to return a spy and assert on the exact ``start_observation`` call. This
covers:

  * gating on ``advanced_features.langfuse_tracing`` (no client even
    constructed when tracing is off, which avoids the "no public_key"
    warning that pollutes logs)
  * span name + shape (input/output/metadata) for skill dispatch
  * graceful no-op when Langfuse import or client construction fails
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.slash_commands import build_slash_registry, parse_and_dispatch

pytestmark = pytest.mark.unit


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

    skills = SkillRegistry([SkillEntry(name="deck", description="d", body="BODY", source="/p")])
    reg = build_slash_registry(skill_registry=skills)
    asyncio.run(parse_and_dispatch("/deck", slash_registry=reg, skill_registry=skills))

    fake_client.start_observation.assert_not_called()


def test_emits_span_with_args_for_skill_dispatch(monkeypatch):
    _patch_settings(monkeypatch, langfuse_tracing=True)
    fake_client = _install_fake_langfuse(monkeypatch)

    skills = SkillRegistry([SkillEntry(name="deck", description="d", body="BODY", source="/p")])
    reg = build_slash_registry(skill_registry=skills)
    asyncio.run(parse_and_dispatch("/deck 3 slides", slash_registry=reg, skill_registry=skills))

    fake_client.start_observation.assert_called_once()
    kwargs = fake_client.start_observation.call_args.kwargs
    assert kwargs["name"] == "slash_command"
    assert kwargs["as_type"] == "span"
    # Redacted: shape metadata only, never the raw "/deck 3 slides".
    assert kwargs["input"] == {
        "command_name": "deck",
        "args_present": True,
        "args_length": len("3 slides"),
    }
    assert kwargs["output"]["resolved_kind"] == "skill"
    assert kwargs["output"]["resolved_name"] == "deck"
    assert "duration_ms" in kwargs["metadata"]
    assert isinstance(kwargs["metadata"]["duration_ms"], float)
    # End must be called so the span is closed even on fast paths.
    fake_client.start_observation.return_value.end.assert_called_once()


def test_langfuse_exception_is_swallowed(monkeypatch):
    """Telemetry must never break dispatch. If Langfuse blows up (auth error,
    transient network), the dispatcher should still return a normal result."""
    import sys

    _patch_settings(monkeypatch, langfuse_tracing=True)
    fake_module = SimpleNamespace(get_client=MagicMock(side_effect=RuntimeError("boom")))
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    skills = SkillRegistry([SkillEntry(name="deck", description="d", body="BODY", source="/p")])
    reg = build_slash_registry(skill_registry=skills)
    # Must not raise.
    result = asyncio.run(parse_and_dispatch("/deck", slash_registry=reg, skill_registry=skills))
    assert result.kind == "skill"
