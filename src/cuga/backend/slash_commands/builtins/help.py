"""``/help`` — list all available slash commands.

Renders a markdown table of every command in the merged registry (built-ins
plus skills). The output is suitable for direct display in the chat panel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cuga.backend.slash_commands.types import DispatchContext, DispatchResult


@dataclass
class HelpCommand:
    name: str = "help"
    description: str = "Show available slash commands."
    argument_hint: Optional[str] = None

    async def handle(self, ctx: DispatchContext) -> DispatchResult:
        commands = sorted(ctx.slash_registry.list_commands(), key=lambda c: (c.kind, c.name))
        builtins = [c for c in commands if c.kind == "builtin"]
        skills = [c for c in commands if c.kind == "skill"]

        lines = ["**Available slash commands**", ""]
        if builtins:
            lines.append("_Built-ins_")
            for c in builtins:
                hint = f" `{c.argument_hint}`" if c.argument_hint else ""
                lines.append(f"- `/{c.name}`{hint} — {c.description}")
            lines.append("")
        if skills:
            lines.append("_Skills_")
            for c in skills:
                hint = f" `{c.argument_hint}`" if c.argument_hint else ""
                lines.append(f"- `/{c.name}`{hint} — {c.description}")
            lines.append("")
        if not builtins and not skills:
            lines.append("_(none discovered)_")

        return DispatchResult(kind="builtin", text="\n".join(lines).rstrip())


BUILTIN = HelpCommand()
