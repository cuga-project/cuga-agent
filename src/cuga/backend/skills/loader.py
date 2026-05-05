"""Discover SKILL.md files under .agents/skills with legacy .cuga/skills fallbacks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from loguru import logger

from cuga.backend.cuga_graph.policy.folder_loader import parse_markdown_with_frontmatter
from cuga.backend.skills.registry import SkillEntry


DEFAULT_GLOBAL_SKILLS_ROOT = "~/.config/agents/skills"
LEGACY_GLOBAL_SKILLS_ROOT = "~/.config/cuga/skills"


def _resolve_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    return p


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item) for item in value]
    return [str(value)]


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def get_skill_search_roots(
    cuga_folder: str | None,
    global_skills_root: str | None = None,
    legacy_global_skills_root: str | None = None,
) -> list[Path]:
    """Return skill roots from lowest to highest priority.

    New Agent-compatible paths override legacy Cuga paths by being scanned later.
    Project-local paths override global paths.
    """
    global_legacy_root = Path(
        legacy_global_skills_root or os.path.expanduser(LEGACY_GLOBAL_SKILLS_ROOT)
    ).expanduser()
    global_agents_root = Path(
        global_skills_root or os.path.expanduser(DEFAULT_GLOBAL_SKILLS_ROOT)
    ).expanduser()

    roots: list[Path] = [global_legacy_root, global_agents_root]

    if cuga_folder:
        cuga_root = _resolve_path(cuga_folder)
        agents_root = cuga_root.parent / ".agents"
    else:
        cuga_root = None
        agents_root = Path(os.getcwd()) / ".agents"

    # Legacy local roots are fallbacks; .agents/skills is the preferred project-local path.
    if cuga_root is not None:
        roots.extend([cuga_root / "skills", cuga_root / ".skills"])
    roots.append(agents_root / "skills")

    return _dedupe_paths(roots)


def _iter_skill_files(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    out: List[Path] = []
    for p in root.rglob("SKILL.md"):
        if p.is_file():
            out.append(p)
    return sorted(out)


def _normalize_requirements(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates: Iterable[Any] = [value]
    elif isinstance(value, dict):
        normalized: list[str] = []
        for key in ("pip", "pip_packages", "python", "python_packages"):
            normalized.extend(_as_list(value.get(key)))
        for key in ("npm", "npm_packages", "node", "node_packages"):
            normalized.extend(f"npm:{item}" for item in _as_list(value.get(key)))
        candidates = normalized
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        logger.warning(f"Ignoring unsupported skill requirements value: {value!r}")
        return ()

    return tuple(str(item).strip() for item in candidates if str(item).strip())


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

    return SkillEntry(
        name=str(name).strip(),
        description=str(description).strip(),
        body=body.strip(),
        source=str(path),
        requirements=_normalize_requirements(frontmatter.get("requirements")),
    )


def discover_skills(
    cuga_folder: str | None,
    global_skills_root: str | None = None,
    legacy_global_skills_root: str | None = None,
) -> List[SkillEntry]:
    """Scan skills so preferred .agents paths override legacy .cuga fallback paths."""
    by_name: dict[str, SkillEntry] = {}

    for skills_dir in get_skill_search_roots(
        cuga_folder,
        global_skills_root=global_skills_root,
        legacy_global_skills_root=legacy_global_skills_root,
    ):
        for path in _iter_skill_files(skills_dir):
            entry = _parse_skill_file(path)
            if entry:
                by_name[entry.name] = entry

    return list(by_name.values())
