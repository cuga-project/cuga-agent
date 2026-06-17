"""Unit tests for cuga.backend.server.a2a.agent_card.

These pin the AgentCard contract: required fields, capability flags,
skill construction, and round-trip validity against the a2a-sdk Pydantic
models. The build is pure — no I/O, no app wiring — so these tests are
the cheapest place to catch breakage.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

a2a_types = pytest.importorskip("a2a.compat.v0_3.types")


@pytest.fixture
def agent_card_module():
    return pytest.importorskip("cuga.backend.server.a2a.agent_card")


@pytest.fixture
def base_settings():
    return {
        "enabled": True,
        "path_prefix": "",
        "agent_name": "cuga",
        "agent_description": "CUGA exposed over A2A.",
        "agent_version": "1.2.3",
        "agent_url": "https://cuga.example/a2a",
        "skill_ids": ["delegate_task"],
    }


def test_agent_card_minimal_fields(agent_card_module, base_settings):
    card = agent_card_module.build_agent_card(base_settings, skills=[])
    assert card.name == "cuga"
    assert card.description == "CUGA exposed over A2A."
    assert card.version == "1.2.3"
    parsed_url = urlparse(str(card.url))
    assert parsed_url.scheme == "https"
    assert parsed_url.hostname == "cuga.example"


def test_agent_card_capabilities_streaming_true(agent_card_module, base_settings):
    """CUGA already streams via SSE — the AgentCard must advertise it."""
    card = agent_card_module.build_agent_card(base_settings, skills=[])
    assert card.capabilities is not None
    assert card.capabilities.streaming is True


def test_agent_card_skills_built_from_registry(agent_card_module, base_settings):
    skills_input = [
        {"id": "delegate_task", "name": "Delegate task", "description": "Run a CUGA task"},
        {"id": "summarize", "name": "Summarize", "description": "Summarize text"},
    ]
    card = agent_card_module.build_agent_card(base_settings, skills=skills_input)
    assert len(card.skills) == 2
    ids = {s.id for s in card.skills}
    assert ids == {"delegate_task", "summarize"}


def test_agent_card_skill_ids_are_stable(agent_card_module, base_settings):
    """A skill id is what an A2A client routes against. It must round-trip
    verbatim — no slugifying, no renaming."""
    card = agent_card_module.build_agent_card(
        base_settings, skills=[{"id": "delegate_task", "name": "Delegate task"}]
    )
    assert card.skills[0].id == "delegate_task"


def test_agent_card_default_input_output_modes_include_text(agent_card_module, base_settings):
    """A2A clients negotiate on default modes; CUGA must accept text."""
    card = agent_card_module.build_agent_card(base_settings, skills=[])
    assert "text" in (card.default_input_modes or [])
    assert "text" in (card.default_output_modes or [])


def test_agent_card_validates_against_a2a_sdk_types(agent_card_module, base_settings):
    """The strongest check we can run cheaply: round-trip through the SDK's
    Pydantic model. If the SDK rejects it, no real client will accept it."""
    card = agent_card_module.build_agent_card(
        base_settings, skills=[{"id": "delegate_task", "name": "Delegate task"}]
    )
    dumped = card.model_dump(mode="json", exclude_none=True)
    rebuilt = a2a_types.AgentCard.model_validate(dumped)
    assert rebuilt.name == card.name
    assert {s.id for s in rebuilt.skills} == {s.id for s in card.skills}


def test_agent_card_security_schemes_when_auth_required(agent_card_module, base_settings):
    """When auth is on, the card must declare a securityScheme so callers
    know to attach a token. When off, security can be empty/None."""
    settings_with_auth = {**base_settings, "auth_required": True}
    card = agent_card_module.build_agent_card(settings_with_auth, skills=[])
    assert card.security_schemes is not None and len(card.security_schemes) > 0


def test_agent_card_no_security_when_auth_off(agent_card_module, base_settings):
    settings_no_auth = {**base_settings, "auth_required": False}
    card = agent_card_module.build_agent_card(settings_no_auth, skills=[])
    assert not card.security_schemes
