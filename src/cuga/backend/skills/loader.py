"""Discover SKILL.md files under .cuga/skills and ~/.config/cuga/skills."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from loguru import logger

from cuga.backend.cuga_graph.policy.folder_loader import parse_markdown_with_frontmatter
from cuga.backend.skills.registry import SkillEntry


def _iter_skill_files(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    out: List[Path] = []
    for p in root.rglob("SKILL.md"):
        if p.is_file():
            out.append(p)
    return sorted(out)


def _parse_skill_file(path: Path) -> SkillEntry | None:
    try:
        frontmatter, body = parse_markdown_with_frontmatter(str(path))
    except Exception as e:
        logger.warning(f"Skipping invalid skill file {path}: {e}")
        return None
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name or not description:
        logger.warning(f"Skill {path} missing name or description in frontmatter")
        return None
    raw_reqs = frontmatter.get("requirements", [])
    if isinstance(raw_reqs, str):
        raw_reqs = [r.strip() for r in raw_reqs.split(",") if r.strip()]
    requirements = tuple(str(r).strip() for r in raw_reqs if r)

    return SkillEntry(
        name=str(name).strip(),
        description=str(description).strip(),
        body=body.strip(),
        source=str(path),
        requirements=requirements,
    )


def discover_skills(
    cuga_folder: str | None,
    global_skills_root: str | None = None,
) -> List[SkillEntry]:
    """Scan global then project-local skills; project-local entries override by name."""
    global_root = Path(global_skills_root or os.path.expanduser("~/.config/cuga/skills"))
    by_name: dict[str, SkillEntry] = {}

    for path in _iter_skill_files(global_root):
        entry = _parse_skill_file(path)
        if entry:
            by_name[entry.name] = entry

    if cuga_folder:
        local_root = Path(cuga_folder).expanduser()
        if not local_root.is_absolute():
            local_root = Path(os.getcwd()) / local_root
        # Load legacy skills/ first, then .skills/ overrides (takes priority)
        for skills_dir in [local_root / "skills", local_root / ".skills"]:
            for path in _iter_skill_files(skills_dir):
                entry = _parse_skill_file(path)
                if entry:
                    by_name[entry.name] = entry

    return list(by_name.values())
