"""Agent skills: SKILL.md discovery and load_skill tool.

Skills are discovered from one root only (``settings.skills.root``, default ``cuga``):
  - cuga          → ``<CUGA folder>/skills/**/SKILL.md`` (default: ``.cuga/skills``)
  - agents        → ``<project>/.agents/skills/**/SKILL.md``
  - global_agents → ~/.config/agents/skills/**/SKILL.md
  - global_cuga   → ~/.config/cuga/skills/**/SKILL.md

Code execution uses the standard executor pipeline — the same Python sandbox the
agent always writes code in (local, E2B, or OpenSandbox depending on config).
Enable OpenSandbox via settings.toml:

    [advanced_features]
    opensandbox_sandbox = true   # requires Docker + opensandbox SDK
    enable_shell_tool = true   # opt-in: shell prompt + run_command / sandbox tools (defaults false)

    # uv add opensandbox opensandbox-code-interpreter
"""

from cuga.backend.skills.loader import discover_skills
from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.skills.tools import create_skill_tools, format_available_skills_block

__all__ = [
    "SkillEntry",
    "SkillRegistry",
    "discover_skills",
    "create_skill_tools",
    "format_available_skills_block",
]
