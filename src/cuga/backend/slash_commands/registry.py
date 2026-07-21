"""Registry of discovered skills exposed as slash commands.

Users invoke skills via ``/<name>`` in the chat input. The registry holds
the skill registry (or ``None`` when skills are disabled) so the
dispatcher can look up skills by name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from cuga.backend.slash_commands.types import CommandRef

if TYPE_CHECKING:  # pragma: no cover
    from cuga.backend.skills.registry import SkillRegistry


class SlashRegistry:
    def __init__(
        self,
        skill_registry: Optional["SkillRegistry"] = None,
    ):
        self._skill_registry = skill_registry

    def list_commands(self) -> List[CommandRef]:
        out: List[CommandRef] = []
        if self._skill_registry is not None:
            for summary in self._skill_registry.summaries():
                out.append(
                    CommandRef(
                        name=summary["name"],
                        description=summary["description"],
                        kind="skill",
                        argument_hint=None,
                    )
                )
        return out

    def has_skill(self, name: str) -> bool:
        if self._skill_registry is None:
            return False
        return any(s["name"] == name for s in self._skill_registry.summaries())

    @property
    def skill_registry(self) -> Optional["SkillRegistry"]:
        return self._skill_registry
