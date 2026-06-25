"""Merged registry of built-in commands and discovered skills.

Built-ins and skills share a single name namespace because users type both
through the same ``/<name>`` form. When the names collide, the built-in wins —
the skill is suppressed and a warning is logged at registry-build time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from loguru import logger

from cuga.backend.slash_commands.types import BuiltinCommand, CommandRef

if TYPE_CHECKING:  # pragma: no cover
    from cuga.backend.skills.registry import SkillRegistry


class SlashRegistry:
    def __init__(
        self,
        builtins: List[BuiltinCommand],
        skill_registry: Optional["SkillRegistry"] = None,
    ):
        self._builtins: dict[str, BuiltinCommand] = {}
        for b in builtins:
            if b.name in self._builtins:
                logger.warning(f"Duplicate built-in slash command '{b.name}'; keeping first occurrence")
                continue
            self._builtins[b.name] = b
        self._skill_registry = skill_registry
        self._suppressed_skills: set[str] = set()

        if skill_registry is not None:
            for summary in skill_registry.summaries():
                if summary["name"] in self._builtins:
                    self._suppressed_skills.add(summary["name"])
                    logger.warning(
                        f"Skill '{summary['name']}' shadows built-in command of the same name; "
                        "built-in takes precedence"
                    )

    def list_commands(self) -> List[CommandRef]:
        out: List[CommandRef] = []
        for b in self._builtins.values():
            out.append(
                CommandRef(
                    name=b.name,
                    description=b.description,
                    kind="builtin",
                    argument_hint=b.argument_hint,
                )
            )
        if self._skill_registry is not None:
            for summary in self._skill_registry.summaries():
                if summary["name"] in self._suppressed_skills:
                    continue
                out.append(
                    CommandRef(
                        name=summary["name"],
                        description=summary["description"],
                        kind="skill",
                        argument_hint=None,
                    )
                )
        return out

    def get_builtin(self, name: str) -> Optional[BuiltinCommand]:
        return self._builtins.get(name)

    def has_skill(self, name: str) -> bool:
        if name in self._builtins or name in self._suppressed_skills:
            return False
        if self._skill_registry is None:
            return False
        return any(s["name"] == name for s in self._skill_registry.summaries())

    @property
    def skill_registry(self) -> Optional["SkillRegistry"]:
        return self._skill_registry
