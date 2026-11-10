"""CugaAgent(enable_knowledge=...) decides whether the agent's knowledge client is enabled."""

from unittest.mock import patch

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig

pytestmark = pytest.mark.unit


class _FakeEngine:
    def __init__(self, config, chat_generator=None):
        self._config = config


def _scopes(enable_knowledge, settings_enabled):
    from cuga.sdk import CugaAgent

    agent = CugaAgent(enable_knowledge=enable_knowledge, auto_load_policies=False)
    with (
        patch.object(
            KnowledgeConfig, "from_settings", return_value=KnowledgeConfig(enabled=settings_enabled)
        ),
        patch("cuga.backend.knowledge.engine.KnowledgeEngine", _FakeEngine),
    ):
        return agent.knowledge.allowed_scopes()


def test_enable_knowledge_true_overrides_disabled_settings():
    assert "agent" in _scopes(enable_knowledge=True, settings_enabled=False)


def test_enable_knowledge_false_overrides_enabled_settings():
    assert _scopes(enable_knowledge=False, settings_enabled=True) == ()


def test_enable_knowledge_unset_follows_settings():
    assert "agent" in _scopes(enable_knowledge=None, settings_enabled=True)
    assert _scopes(enable_knowledge=None, settings_enabled=False) == ()
