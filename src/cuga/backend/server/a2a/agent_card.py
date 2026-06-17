"""Build an AgentCard from CUGA settings + a list of skill descriptors.

Pure function; no I/O. The output is the JSON served at
``/.well-known/agent.json`` and consumed by any A2A client.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from cuga.backend.server.a2a._a2a_types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityScheme,
)


def _coerce_skill(spec: Mapping[str, Any]) -> AgentSkill:
    """Build an ``AgentSkill`` from a loose mapping.

    Accepts whatever the caller has lying around (config dict, registry
    entry, hand-written test fixture) and produces a valid SDK skill.
    Falls back to the skill id for any missing display fields so the
    output round-trips through the SDK's pydantic model.
    """
    skill_id = spec["id"]
    return AgentSkill(
        id=skill_id,
        name=spec.get("name") or skill_id,
        description=spec.get("description") or spec.get("name") or skill_id,
        tags=list(spec.get("tags") or []),
        examples=list(spec.get("examples") or []) or None,
        input_modes=list(spec.get("input_modes") or []) or None,
        output_modes=list(spec.get("output_modes") or []) or None,
    )


def build_agent_card(
    settings: Mapping[str, Any],
    skills: Sequence[Mapping[str, Any]],
) -> AgentCard:
    """Construct an A2A AgentCard from a settings dict and skill specs."""
    agent_skills = [_coerce_skill(s) for s in skills]

    security_schemes: dict[str, SecurityScheme] | None = None
    if settings.get("auth_required"):
        security_schemes = {
            "bearer": SecurityScheme(
                root=HTTPAuthSecurityScheme(
                    scheme="bearer",
                    bearer_format="JWT",
                    description="Bearer token used by CUGA's existing chat-access dependency.",
                )
            )
        }

    return AgentCard(
        name=settings.get("agent_name") or "cuga",
        description=settings.get("agent_description") or "CUGA agent exposed over A2A.",
        version=settings.get("agent_version") or "0.0.0",
        url=settings.get("agent_url") or "http://localhost",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=agent_skills,
        security_schemes=security_schemes,
    )
