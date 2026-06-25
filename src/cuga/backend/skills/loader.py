"""Discover SKILL.md files under .agents/skills with legacy .cuga/skills fallbacks."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence

from loguru import logger

from cuga.backend.cuga_graph.policy.folder_loader import parse_markdown_with_frontmatter
from cuga.backend.skills.registry import SkillEntry
from cuga.backend.slash_commands.arg_substitution import validate_arg_names


DEFAULT_GLOBAL_SKILLS_ROOT = "~/.config/agents/skills"
LEGACY_GLOBAL_SKILLS_ROOT = "~/.config/cuga/skills"

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


def _normalize_arg_names(value: Any) -> tuple[str, ...]:
    """Parse the ``arguments`` frontmatter key (whitespace-separated string or YAML list)."""
    if value is None:
        return ()
    if isinstance(value, str):
        names: Iterable[Any] = value.split()
    elif isinstance(value, (list, tuple, set)):
        names = value
    else:
        logger.warning(f"Ignoring unsupported skill arguments value: {value!r}")
        return ()
    return tuple(s for s in (str(item).strip() for item in names) if s)


def _normalize_allowed_tools(value: Any) -> Optional[tuple[str, ...]]:
    """Parse the ``allowed-tools`` frontmatter key (string or YAML list).

    Returns ``None`` when the key is absent in frontmatter (no restriction).
    Returns an empty tuple ``()`` when the key is present but empty
    (``allowed-tools: []``) — "allow nothing, every tool requires approval".
    The whitelist-enforcement gate keys off this absent-vs-empty distinction.
    """
    if value is None:
        return None
    return tuple(s for s in (str(item).strip() for item in _as_list(value)) if s)


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

        arguments = _normalize_arg_names(frontmatter.get("arguments"))
        validate_arg_names(arguments)

        return SkillEntry(
            name=name_str,
            description=description_str,
            body=body.strip(),
            source=str(path),
            requirements=_normalize_requirements(frontmatter.get("requirements")),
            arguments=arguments,
            allowed_tools=_normalize_allowed_tools(frontmatter.get("allowed-tools")),
        )
    except Exception as e:
        logger.warning(f"Skipping invalid skill file {path}: {e}")
        return None


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
        # Later roots override earlier — by_name[entry.name] reassignment is intentional.
        for path in _iter_skill_files(skills_dir):
            entry = _parse_skill_file(path)
            if entry:
                if entry.name in by_name:
                    logger.debug(
                        f"Skill '{entry.name}' from {path} overrides earlier entry from {by_name[entry.name].source}"
                    )
                by_name[entry.name] = entry

    return list(by_name.values())
