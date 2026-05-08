"""In-memory registry of discovered skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    body: str
    source: str
    requirements: tuple[str, ...] = ()  # pip/npm packages declared in frontmatter


def _sandbox_active() -> bool:
    """True when the agent will execute via a remote sandbox (opensandbox or e2b).

    Used to decide whether companion files live at the cuga-specific upload
    location ``/tmp/skills/<name>/`` or at the skill's actual on-disk folder.
    """
    try:
        from cuga.config import settings  # local import keeps this module lightweight
    except Exception:
        return False
    adv = getattr(settings, "advanced_features", None)
    if adv is None:
        return False
    return bool(
        getattr(adv, "opensandbox_sandbox", False) or getattr(adv, "e2b_sandbox", False)
    )


def _resolve_skill_dir(entry: SkillEntry) -> str:
    """Path the agent should reference for this skill's companion files.

    Resolution order:
      1. ``/tmp/skills/<name>/`` if it exists on disk — this is the convention
         used by both OpenSandbox uploads and the cuga-skills-ui marketplace's
         host-side ``run_command``. When present, point at it so the agent's
         load_skill instructions match what its shell tool actually mounts.
      2. Sandboxed modes (``opensandbox_sandbox`` / ``e2b_sandbox``) report
         ``/tmp/skills/<name>/`` even before upload — the upload happens at
         sandbox startup and the path will exist by the time it runs.
      3. Otherwise the actual source folder — Anthropic-style local mode where
         files stay where they live.
    """
    sandbox_path = Path("/tmp/skills") / entry.name
    if sandbox_path.is_dir() or _sandbox_active():
        return f"/tmp/skills/{entry.name}"
    source_parent = Path(entry.source).parent
    if source_parent.is_dir() and source_parent.name == entry.name:
        return str(source_parent)
    return f"/tmp/skills/{entry.name}"


def _list_companion_files(entry: SkillEntry, limit: int = 40) -> list[str]:
    """Return relative paths of companion files alongside SKILL.md.

    Showing real filenames in the load_skill output keeps weaker models from
    inventing scripts that don't exist (e.g. ``hike_finder.py`` when the file
    is actually ``hike_tools.py``).
    """
    source_root = Path(entry.source).parent
    if not source_root.is_dir():
        return []
    skipped_suffixes = {".pyc", ".xsd"}
    skipped_dirs = {"__pycache__", ".git"}
    files: list[str] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "SKILL.md":
            continue
        if path.suffix.lower() in skipped_suffixes:
            continue
        if any(part in skipped_dirs for part in path.relative_to(source_root).parts):
            continue
        files.append(str(path.relative_to(source_root)))
        if len(files) >= limit:
            break
    return files


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
            setup_lines: list[str] = []
            if pip_pkgs:
                setup_lines.append(f"await run_command('uv pip install --quiet {' '.join(pip_pkgs)}')")
                setup_lines.append("await asyncio.sleep(5)")
            if npm_pkgs:
                # Install locally in the working dir so require() resolves correctly
                setup_lines.append(f"await run_command('cd /tmp && npm install {' '.join(npm_pkgs)}')")
                setup_lines.append("await asyncio.sleep(5)")
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

        skill_dir = _resolve_skill_dir(entry)
        parts.append(
            "The full skill instructions are already included below from `load_skill`; "
            "do NOT re-read `SKILL.md`. Companion files for this skill are at "
            f"`{skill_dir}/` (scripts, templates, docs, etc.). If these loaded instructions contain "
            "relative markdown links or say to read a companion file, treat those references as workflow "
            "routing instructions: choose the relevant companion file(s) based on the situation and read them "
            "before implementing that workflow. Use `await read_file('<path>')` only for those companion files "
            "when the instructions require them; use "
            f"`await run_command('ls {skill_dir}')` or `await list_files('{skill_dir}')` to explore."
        )
        parts.append("")

        companion_files = _list_companion_files(entry)
        if companion_files:
            listing = "\n".join(f"  - {skill_dir}/{rel}" for rel in companion_files)
            parts.append(
                "Companion files present in this skill (use these exact paths — do not invent filenames):\n"
                f"{listing}"
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
            "`python /tmp/script.py` or `python script.py` → `uv run /tmp/script.py`; "
            "`python -c '...'` → `uv run python -c '...'`; `pip install ...` or `python -m pip install ...` "
            "→ `uv pip install ...`; and `pip list` / `pip show` / `pip freeze` → `uv pip list` / "
            "`uv pip show` / `uv pip freeze`. Never prefix Node/npm with uv: Node commands must start with plain "
            "`node ...`, npm commands must start with plain `npm ...`, and packages must be installed locally as "
            "`cd /tmp && npm install <package>`. Do not use `uv npm`, `uv run node`, or `uv run npm`."
        )
        parts.append("")
        parts.append(f"STEP 2 — SKILL INSTRUCTIONS:\n{entry.body}")
        return "\n".join(parts)
