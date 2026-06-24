"""Shared skill prompt text (load_skill output + available_skills block)."""

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
    "when the instructions require them; use "
    "`await run_command('ls {skill_dir}')` or `await list_files('{skill_dir}')` to explore."
)

LOAD_SKILL_PLAYBOOK = (
    "What this loaded skill content may contain: trigger/usage rules, quick references, "
    "task workflows, companion scripts or docs, design or implementation guidance, QA/verification "
    "steps, export/conversion instructions, and dependency requirements. Treat those sections as the "
    "playbook to follow. QA, verification, validation, export, and conversion sections are mandatory "
    "before final response unless technically impossible."
)

LOAD_SKILL_COMMAND_NORMALIZATION = (
    "Command normalization override for sandbox execution: skill docs may contain legacy Python commands. "
    "Do not execute `python ...`, `python -m ...`, `python -m pip ...`, `pip ...`, or `pip list` directly. "
    "Translate only Python commands at execution time: `python -m <module> ...` → `uv run python -m <module> ...`; "
    "`python /workspace/script.py` or `python script.py` → `uv run /workspace/script.py`; "
    "`python -c '...'` → `uv run python -c '...'`; `pip install ...` or `python -m pip install ...` "
    "→ `uv pip install ...`; and `pip list` / `pip show` / `pip freeze` → `uv pip list` / "
    "`uv pip show` / `uv pip freeze`. Never prefix Node/npm with uv: Node commands must start with plain "
    "`node ...`, npm commands must start with plain `npm ...`, and packages must be installed locally as "
    "`npm install <package>` in `/workspace`. "
    "Do not use `uv npm`, `uv run node`, or `uv run npm`."
)

AVAILABLE_SKILLS_USAGE = (
    "When a task matches a skill: call `await load_skill(\"<skill_name>\")` in its own code block, "
    "print the returned text, and follow those instructions (installs, companion files, QA steps). "
    "The `load_skill` output is authoritative — do not re-read `SKILL.md`. "
    "Skill loading takes precedence over todos, `find_tools`, and application tools when a skill clearly matches."
)
