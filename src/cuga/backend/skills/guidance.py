"""Shared skill prompt text (load_skill output + available_skills block)."""

from cuga.backend.skills.sandbox_uv import SANDBOX_UV_COMMAND_NORMALIZATION

LOAD_SKILL_GUIDANCE = (
    "If the skill instructions below include Dependencies, Requirements, or other install/setup sections, "
    "follow that skill's own structure and ordering — run those installs before the rest of the workflow "
    "when the skill says to. When executing install commands from the skill text: use `uv pip install ...` "
    "for Python packages (never bare `pip` or `python -m pip`); use plain `npm install ...` for Node "
    "packages (never `uv npm`). Do not skip installs the skill explicitly requires; do not invent packages "
    "the skill does not mention."
)

LOAD_SKILL_COMPANIONS = (
    "The full skill instructions are already included below from `load_skill`; "
    "do NOT re-read `SKILL.md`. Companion files are available inside the sandbox at "
    "`{skill_dir}/` (scripts, templates, docs, etc.). If these loaded instructions contain "
    "relative markdown links or say to read a companion file, treat those references as workflow "
    "routing instructions: choose the relevant companion file(s) based on the situation and read them "
    "before implementing that workflow. Use `await read_file('<path>')` only for those companion files "
    "when the instructions require them. Do NOT list or explore `{skill_dir}` after "
    "`load_skill` unless SKILL.md explicitly links to a companion path."
)

LOAD_SKILL_PLAYBOOK = (
    "What this loaded skill content may contain: trigger/usage rules, quick references, "
    "task workflows, companion scripts or docs, design or implementation guidance, QA/verification "
    "steps, export/conversion instructions, and dependency requirements. Treat those sections as the "
    "playbook to follow. QA, verification, validation, export, and conversion sections are mandatory "
    "before final response unless technically impossible."
)

LOAD_SKILL_COMMAND_NORMALIZATION = (
    "Command normalization override for sandbox `run_command` execution. " + SANDBOX_UV_COMMAND_NORMALIZATION
)

AVAILABLE_SKILLS_USAGE = (
    "When a task matches a skill: call `await load_skill(\"<skill_name>\")` in its own code block, "
    "print the returned text, and follow those instructions (installs, companion files, QA steps). "
    "The `load_skill` output is authoritative — do not re-read `SKILL.md`. "
    "Skill loading takes precedence over todos, `find_tools`, and application tools when a skill clearly matches."
)
