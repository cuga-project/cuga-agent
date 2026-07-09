"""Discover SKILL.md files from a single configurable skills root."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from loguru import logger

from cuga.backend.cuga_graph.policy.folder_loader import parse_markdown_with_frontmatter
from cuga.backend.skills.registry import SkillEntry
from cuga.config import settings

DEFAULT_GLOBAL_SKILLS_ROOT = "~/.config/agents/skills"
LEGACY_GLOBAL_SKILLS_ROOT = "~/.config/cuga/skills"
VALID_SKILL_ROOTS = frozenset({"cuga", "agents", "global_agents", "global_cuga"})

# Matches Jinja2 expression/block/comment delimiters used in the system-prompt template.
# Stripping these at parse time prevents prompt-injection via malicious SKILL.md frontmatter.
_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)


def _sanitize_for_prompt(value: str, field: str, source: Path) -> str:
    """Strip Jinja2 template delimiters from a skill frontmatter string."""
    sanitized = _JINJA_RE.sub("", value)
    if sanitized != value:
        logger.warning(
            f"Skill {source}: {field!r} contained Jinja2 template syntax and was sanitized. "
            "This may indicate a malicious or misconfigured SKILL.md."
        )
    return sanitized


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


def _settings_skill_root() -> str:
    preset = getattr(settings.skills, "root", None) or "cuga"
    return str(preset).strip().lower() or "cuga"


def get_skill_root(
    cuga_folder: str | None,
    *,
    root: str | None = None,
    global_skills_root: str | None = None,
    legacy_global_skills_root: str | None = None,
) -> Path:
    """Return the single skills directory to scan.

    Controlled by ``settings.skills.root`` (default ``cuga`` → ``<CUGA folder>/skills``).
    Override with ``root=`` in tests or ``DYNACONF_SKILLS__ROOT`` env var.
    """
    preset = (root or _settings_skill_root()).strip().lower()
    if preset not in VALID_SKILL_ROOTS:
        raise ValueError(
            f"Invalid skills.root {preset!r}; expected one of: {', '.join(sorted(VALID_SKILL_ROOTS))}"
        )

    if preset == "global_agents":
        return Path(global_skills_root or os.path.expanduser(DEFAULT_GLOBAL_SKILLS_ROOT)).expanduser()
    if preset == "global_cuga":
        return Path(legacy_global_skills_root or os.path.expanduser(LEGACY_GLOBAL_SKILLS_ROOT)).expanduser()

    if cuga_folder:
        cuga_root = _resolve_path(cuga_folder)
        agents_root = cuga_root.parent / ".agents"
    else:
        cuga_root = Path(os.getcwd()) / ".cuga"
        agents_root = Path(os.getcwd()) / ".agents"

    if preset == "agents":
        return agents_root / "skills"
    return cuga_root / "skills"


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


def _normalize_tool_definitions(value: Any, source: Path) -> tuple[dict, ...]:
    """Normalize the ``tools:`` frontmatter block.

    Each entry's ``import_from`` follows the same dotted-path convention as
    ``supervisor_config.py``'s agent ``import_from``: everything before the
    last dot is the module path, the final segment is the attribute to import.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        logger.warning(f"Skill {source}: 'tools' frontmatter must be a list, got {type(value).__name__}")
        return ()

    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            logger.warning(f"Skill {source}: skipping invalid tool entry {item!r}")
            continue
        import_from = item.get("import_from")
        name = item.get("name")
        description = item.get("description")
        if not import_from or not name or not description:
            logger.warning(
                f"Skill {source}: tool entry missing required "
                f"'import_from', 'name', or 'description': {item!r}"
            )
            continue
        out.append(
            {
                "import_from": _sanitize_for_prompt(str(import_from).strip(), "tool.import_from", source),
                "name": _sanitize_for_prompt(str(name).strip(), "tool.name", source),
                "description": _sanitize_for_prompt(str(description).strip(), "tool.description", source),
            }
        )
    return tuple(out)


def _parse_skill_file(path: Path) -> SkillEntry | None:
    try:
        frontmatter, body = parse_markdown_with_frontmatter(str(path))
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not name or not description:
            raise ValueError("missing name or description in frontmatter")

        name_str = _sanitize_for_prompt(str(name).strip(), "name", path)
        if re.search(r'[/\\]|\.\.', name_str):
            raise ValueError(f"unsafe skill name {name_str!r}: path separators and '..' are not allowed")

        description_str = _sanitize_for_prompt(str(description).strip(), "description", path)
        return SkillEntry(
            name=name_str,
            description=description_str,
            body=body.strip(),
            source=str(path),
            requirements=_normalize_requirements(frontmatter.get("requirements")),
            tool_definitions=_normalize_tool_definitions(frontmatter.get("tools"), path),
        )
    except Exception as e:
        logger.warning(f"Skipping invalid skill file {path}: {e}")
        return None


_discover_skills_cache: dict[tuple, List[SkillEntry]] = {}


def clear_skills_cache() -> None:
    """Clear the process-level discover_skills cache (use in tests or after hot-reloading skills)."""
    _discover_skills_cache.clear()


def discover_skills(
    cuga_folder: str | None,
    global_skills_root: str | None = None,
    legacy_global_skills_root: str | None = None,
    *,
    root: str | None = None,
) -> List[SkillEntry]:
    """Scan a single configured skills root for SKILL.md files."""
    skills_dir = get_skill_root(
        cuga_folder,
        root=root,
        global_skills_root=global_skills_root,
        legacy_global_skills_root=legacy_global_skills_root,
    )
    by_name: dict[str, SkillEntry] = {}

    for path in _iter_skill_files(skills_dir):
        entry = _parse_skill_file(path)
        if entry:
            if entry.name in by_name:
                logger.debug(
                    f"Skill '{entry.name}' from {path} overrides earlier entry from {by_name[entry.name].source}"
                )
            by_name[entry.name] = entry

    entries = list(by_name.values())
    if entries:
        logger.info(f"Loaded {len(entries)} agent skill(s) from {skills_dir}")
    return entries
