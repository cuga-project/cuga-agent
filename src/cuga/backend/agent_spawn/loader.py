"""Discover AGENT.md files under an agents directory."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List, Sequence

from loguru import logger

from cuga.backend.cuga_graph.policy.folder_loader import parse_markdown_with_frontmatter
from cuga.backend.agent_spawn.registry import AgentDescriptorEntry, ToolDefinition

# Matches {{ ... }} or {% ... %} — strip to prevent Jinja injection in prompts.
_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)


def _sanitize_for_prompt(value: str) -> str:
    return _JINJA_RE.sub("", value)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _iter_agent_files(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    out: List[Path] = []
    for p in root.rglob("AGENT.md"):
        if p.is_file():
            out.append(p)
    return sorted(out)


def _parse_tool_definitions(raw: Any, source: Path) -> tuple[ToolDefinition, ...]:
    if not raw or not isinstance(raw, list):
        return ()
    result: list[ToolDefinition] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        module = item.get("module")
        function = item.get("function")
        if not name or not module or not function:
            raise ValueError(
                f"AGENT.md at {source}: tool_definitions entry missing required key(s) "
                f"(name={name!r}, module={module!r}, function={function!r}). "
                "All three are required."
            )
        result.append(
            ToolDefinition(
                name=str(name),
                description=str(item.get("description", "")),
                module=str(module),
                function=str(function),
                args_schema=item.get("args_schema"),
            )
        )
    return tuple(result)


def _parse_agent_file(path: Path) -> AgentDescriptorEntry | None:
    try:
        frontmatter, _body = parse_markdown_with_frontmatter(str(path))
    except Exception as e:
        logger.warning(f"Skipping invalid AGENT.md at {path}: {e}")
        return None

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name or not description:
        logger.warning(f"AGENT.md {path} missing name or description in frontmatter")
        return None

    name = _sanitize_for_prompt(str(name).strip())
    description = _sanitize_for_prompt(str(description).strip())

    # Guard against path traversal in name
    if ".." in name or "/" in name or "\\" in name:
        logger.warning(f"AGENT.md {path}: name {name!r} contains path separators, skipping")
        return None

    tools = tuple(_as_list(frontmatter.get("tools")))
    skill_tools = tuple(_as_list(frontmatter.get("skill_tools")))

    try:
        tool_definitions = _parse_tool_definitions(frontmatter.get("tool_definitions"), path)
    except ValueError:
        raise

    return AgentDescriptorEntry(
        name=name,
        description=description,
        source=str(path),
        tools=tools,
        skill_tools=skill_tools,
        tool_definitions=tool_definitions,
        model=frontmatter.get("model") or None,
        thread_id_prefix=str(frontmatter.get("thread_id_prefix", "agent")),
        max_steps=int(frontmatter.get("max_steps", 8)),
        inherit_parent_tools=bool(frontmatter.get("inherit_parent_tools", False)),
    )


def discover_agents(agents_dir: str | Path) -> List[AgentDescriptorEntry]:
    """Scan agents_dir for AGENT.md files; return parsed descriptors.

    Returns [] when directory does not exist or contains no valid AGENT.md files.
    On name collision, the last file discovered wins (alphabetical order).
    """
    root = Path(agents_dir)
    if not root.exists():
        return []

    by_name: dict[str, AgentDescriptorEntry] = {}
    for path in _iter_agent_files(root):
        try:
            entry = _parse_agent_file(path)
        except ValueError:
            raise
        if entry:
            by_name[entry.name] = entry

    return list(by_name.values())
