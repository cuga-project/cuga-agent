"""In-memory registry of discovered skills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


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

        parts: list[str] = []

        if entry.requirements:
            pip_pkgs = [r for r in entry.requirements if not r.startswith("npm:")]
            npm_pkgs = [r[4:] for r in entry.requirements if r.startswith("npm:")]
            setup_cmds: list[str] = []
            if pip_pkgs:
                setup_cmds.append(f"pip install --quiet {' '.join(pip_pkgs)}")
            if npm_pkgs:
                setup_cmds.append(f"npm install -g {' '.join(npm_pkgs)}")
            setup_script = "\n".join(setup_cmds)
            parts.append(
                "STEP 1 — SETUP (run first):\n"
                f"Pass this exact string to open_sandbox(lang='bash', code=...):\n"
                f"{setup_script}"
            )
            parts.append("")

        parts.append(f"STEP 2 — SKILL INSTRUCTIONS:\n{entry.body}")
        return "\n".join(parts)
