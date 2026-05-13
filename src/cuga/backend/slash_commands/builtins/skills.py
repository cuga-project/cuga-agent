"""``/skills`` — list every discovered skill with its description and requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cuga.backend.slash_commands.types import DispatchContext, DispatchResult


@dataclass
class SkillsCommand:
    name: str = "skills"
    description: str = "List installed skills."
    argument_hint: Optional[str] = None

    async def handle(self, ctx: DispatchContext) -> DispatchResult:
        if ctx.skill_registry is None:
            return DispatchResult(
                kind="builtin",
                text="_Skills are disabled in this server configuration._",
            )
        entries = ctx.skill_registry.entries()
        if not entries:
            return DispatchResult(
                kind="builtin",
                text="_No skills installed yet._",
            )
        lines = ["**Installed skills**", ""]
        for e in entries:
            lines.append(f"- **`{e.name}`** — {e.description}")
            if e.requirements:
                lines.append(f"  - requirements: {', '.join(e.requirements)}")
        return DispatchResult(kind="builtin", text="\n".join(lines).rstrip())


BUILTIN = SkillsCommand()
