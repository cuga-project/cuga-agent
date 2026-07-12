"""In-memory registry of discovered skills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from cuga.backend.skills.guidance import (
    LOAD_SKILL_COMMAND_NORMALIZATION,
    LOAD_SKILL_COMPANIONS,
    LOAD_SKILL_GUIDANCE,
    LOAD_SKILL_PLAYBOOK,
)


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    body: str
    source: str
    requirements: tuple[str, ...] = ()  # pip/npm packages declared in frontmatter


class SkillRegistry:
    def __init__(self, entries: List[SkillEntry]):
        self._by_name: Dict[str, SkillEntry] = {e.name: e for e in entries}

    def summaries(self) -> List[dict[str, str]]:
        return [{"name": e.name, "description": e.description} for e in self._by_name.values()]

    def load_skill(self, name: str) -> str:
        entry = self._by_name.get(name.strip())
        if not entry:
            known = ", ".join(sorted(self._by_name.keys())) or "(none)"
            return f"Unknown skill: {name!r}. Known skills: {known}"

        skill_dir = f"/workspace/skills/{entry.name}"
        parts = [
            LOAD_SKILL_GUIDANCE,
            "",
            LOAD_SKILL_COMPANIONS.format(skill_dir=skill_dir),
            "",
            LOAD_SKILL_PLAYBOOK,
            "",
            LOAD_SKILL_COMMAND_NORMALIZATION,
            "",
            f"STEP 1 — SKILL INSTRUCTIONS:\n{entry.body}",
        ]
        return "\n".join(parts)
