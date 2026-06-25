"""In-memory registry of discovered skills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    body: str
    source: str
    requirements: tuple[str, ...] = ()  # pip/npm packages declared in frontmatter
    arguments: tuple[str, ...] = ()  # named args declared in the `arguments` frontmatter key
    # `allowed-tools` whitelist semantics:
    #   None  — key absent in frontmatter; no restriction (status quo)
    #   ()    — key present but empty (`allowed-tools: []`); allow nothing, everything triggers approval
    #   (..)  — explicit whitelist
    allowed_tools: Optional[tuple[str, ...]] = None

    @property
    def pip_packages(self) -> list[str]:
        return [r for r in self.requirements if not r.startswith("npm:")]

    @property
    def npm_packages(self) -> list[str]:
        return [r[4:] for r in self.requirements if r.startswith("npm:")]


class SkillRegistry:
    def __init__(self, entries: List[SkillEntry]):
        self._by_name: Dict[str, SkillEntry] = {e.name: e for e in entries}

    def summaries(self) -> List[dict[str, str]]:
        return [{"name": e.name, "description": e.description} for e in self._by_name.values()]

    def entries(self) -> List[SkillEntry]:
        return list(self._by_name.values())

    def entry(self, name: str) -> Optional[SkillEntry]:
        return self._by_name.get(name.strip())

    def load_skill(self, name: str, args: str = "") -> str:
        entry = self._by_name.get(name.strip())
        if not entry:
            known = ", ".join(sorted(self._by_name.keys())) or "(none)"
            return f"Unknown skill: {name!r}. Known skills: {known}"

        # Substitution runs on the raw body before install/sandbox wrapping.
        if args:
            from cuga.backend.slash_commands.arg_substitution import substitute

            body = substitute(entry.body, args, entry.arguments)
        else:
            body = entry.body

        parts: list[str] = []

        if entry.requirements:
            pip_pkgs = entry.pip_packages
            npm_pkgs = entry.npm_packages
            setup_lines: list[str] = []
            if pip_pkgs:
                setup_lines.append(f"await run_command('uv pip install --quiet {' '.join(pip_pkgs)}')")
                setup_lines.append("await asyncio.sleep(5)")
                setup_lines.append(f"await run_command('uv pip show {' '.join(pip_pkgs)}')")
            if npm_pkgs:
                # Install locally in the working dir so require() resolves correctly
                setup_lines.append(f"await run_command('npm install {' '.join(npm_pkgs)}')")
                setup_lines.append("await asyncio.sleep(5)")
                setup_lines.append(f"await run_command('npm list {' '.join(npm_pkgs)}')")
            setup_script = "\n".join(setup_lines)
            parts.append(
                "⚠️ STEP 1 — INSTALL REQUIREMENTS (MANDATORY — your very first code block, no exceptions):\n"
                "Before opening companion files, before any other action, run the following installs "
                "in a single isolated ```python``` code block and print the output. "
                "Python package installs use `uv pip install ...`; npm installs are plain `npm ...` commands and must never be rewritten as `uv npm`. "
                "Do NOT skip or defer this step even if you think the package might already be present.\n\n"
                f"{setup_script}"
            )
            parts.append("")

        skill_dir = f"/workspace/skills/{entry.name}"
        parts.append(
            "The full skill instructions are already included below from `load_skill`; "
            "do NOT re-read `SKILL.md`. Companion files are available inside the sandbox at "
            f"`{skill_dir}/` (scripts, templates, docs, etc.). If these loaded instructions contain "
            "relative markdown links or say to read a companion file, treat those references as workflow "
            "routing instructions: choose the relevant companion file(s) based on the situation and read them "
            "before implementing that workflow. Use `await read_file('<path>')` only for those companion files "
            "when the instructions require them; use "
            f"`await run_command('ls {skill_dir}')` or `await list_files('{skill_dir}')` to explore."
        )
        parts.append("")
        parts.append(
            "What this loaded skill content may contain: trigger/usage rules, quick references, "
            "task workflows, companion scripts or docs, design or implementation guidance, QA/verification "
            "steps, export/conversion instructions, and dependency requirements. Treat those sections as the "
            "playbook to follow. QA, verification, validation, export, and conversion sections are mandatory "
            "before final response unless technically impossible."
        )
        parts.append("")
        parts.append(
            "Command normalization override for sandbox execution: skill docs may contain legacy Python commands. "
            "Do not execute `python ...`, `python -m ...`, `python -m pip ...`, `pip ...`, or `pip list` directly. "
            "Translate only Python commands at execution time: `python -m <module> ...` → `uv run python -m <module> ...`; "
            "`python /workspace/script.py` or `python script.py` → `uv run /workspace/script.py`; "
            "`python -c '...'` → `uv run python -c '...'`; `pip install ...` or `python -m pip install ...` "
            "→ `uv pip install ...`; and `pip list` / `pip show` / `pip freeze` → `uv pip list` / "
            "`uv pip show` / `uv pip freeze`. Never prefix Node/npm with uv: Node commands must start with plain "
            "`node ...`, npm commands must start with plain `npm ...`, "
            "and packages must be installed locally as `npm install <package>` in `/workspace`. "
            "Do not use `uv npm`, `uv run node`, or `uv run npm`."
        )
        parts.append("")
        parts.append(f"STEP 2 — SKILL INSTRUCTIONS:\n{body}")
        return "\n".join(parts)
