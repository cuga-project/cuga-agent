"""
Load policies from .cuga folder structure with markdown files.

This module provides functionality to load policies (playbooks, output formatters, etc.)
from a folder structure where each policy is defined in a markdown file with frontmatter.
"""

import os
from pathlib import Path
from typing import Dict, Any, List
import yaml
from loguru import logger

from cuga.backend.cuga_graph.policy.models import (
    Playbook,
    OutputFormatter,
    ToolGuide,
    ToolGuard,
    IntentGuard,
    ToolApproval,
    KeywordTrigger,
    NaturalLanguageTrigger,
    AlwaysTrigger,
    IntentGuardResponse,
)


def parse_markdown_with_frontmatter(file_path: str) -> tuple[Dict[str, Any], str]:
    """Parse a markdown file with YAML frontmatter.

    Args:
        file_path: Path to the markdown file

    Returns:
        Tuple of (frontmatter_dict, markdown_content)

    Raises:
        ValueError: If frontmatter is invalid or missing
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.splitlines(keepends=True)

    # Check for frontmatter delimiters on their own lines. ToolGuard policy code
    # can contain strings/comments with "---", which must not terminate YAML.
    if not lines or lines[0].rstrip('\r\n') != '---':
        raise ValueError(f"File {file_path} missing frontmatter (should start with ---)")

    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip('\r\n') == '---':
            closing_index = index
            break

    if closing_index is None:
        raise ValueError(f"File {file_path} has invalid frontmatter format")

    frontmatter_text = ''.join(lines[1:closing_index])
    markdown_content = ''.join(lines[closing_index + 1 :]).strip()

    # Parse YAML frontmatter
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            raise ValueError("Frontmatter must be a YAML dictionary")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in frontmatter: {e}")

    return frontmatter, markdown_content


def create_triggers_from_metadata(triggers_config: Dict[str, Any]) -> List[Any]:
    """Create trigger objects from metadata configuration.

    Args:
        triggers_config: Dictionary with 'keywords', 'natural_language', 'threshold'

    Returns:
        List of trigger objects
    """
    triggers = []

    # Keyword triggers
    keywords = triggers_config.get('keywords', [])
    if keywords:
        triggers.append(
            KeywordTrigger(
                value=keywords,
                target=triggers_config.get('target', 'intent'),
                case_sensitive=triggers_config.get('case_sensitive', False),
                operator=triggers_config.get('operator', 'or'),
            )
        )

    # Natural language triggers
    nl_triggers = triggers_config.get('natural_language', [])
    if nl_triggers:
        triggers.append(
            NaturalLanguageTrigger(
                value=nl_triggers,
                target=triggers_config.get('target', 'intent'),
                threshold=triggers_config.get('threshold', 0.7),
            )
        )

    # Always trigger
    if triggers_config.get('always', False):
        triggers.append(AlwaysTrigger())

    return triggers


def create_playbook_from_markdown(
    file_path: str,
    frontmatter: Dict[str, Any],
    content: str,
) -> Playbook:
    """Create a Playbook policy from markdown file.

    Args:
        file_path: Path to the file (for error messages)
        frontmatter: Parsed frontmatter metadata
        content: Markdown content

    Returns:
        Playbook instance
    """
    name = frontmatter.get('name')
    if not name:
        raise ValueError(f"Playbook in {file_path} missing 'name' in frontmatter")

    triggers_config = frontmatter.get('triggers', {})
    triggers = create_triggers_from_metadata(triggers_config)

    if not triggers:
        raise ValueError(f"Playbook {name} must have at least one trigger")

    return Playbook(
        id=frontmatter.get('id', f"playbook_{Path(file_path).stem}"),
        name=name,
        description=frontmatter.get('description', f"Playbook: {name}"),
        triggers=triggers,
        markdown_content=content,
        steps=frontmatter.get('steps'),
        priority=frontmatter.get('priority', 50),
        enabled=frontmatter.get('enabled', True),
    )


def create_output_formatter_from_markdown(
    file_path: str,
    frontmatter: Dict[str, Any],
    content: str,
) -> OutputFormatter:
    """Create an OutputFormatter policy from markdown file.

    Args:
        file_path: Path to the file (for error messages)
        frontmatter: Parsed frontmatter metadata
        content: Markdown content

    Returns:
        OutputFormatter instance
    """
    name = frontmatter.get('name')
    if not name:
        raise ValueError(f"OutputFormatter in {file_path} missing 'name' in frontmatter")

    triggers_config = frontmatter.get('triggers', {})
    triggers = create_triggers_from_metadata(triggers_config)

    if not triggers:
        triggers = [AlwaysTrigger()]

    format_type = frontmatter.get('format_type', 'markdown')
    if format_type not in ['markdown', 'json_schema', 'direct']:
        raise ValueError(f"Invalid format_type '{format_type}' for OutputFormatter {name}")

    return OutputFormatter(
        id=frontmatter.get('id', f"output_formatter_{Path(file_path).stem}"),
        name=name,
        description=frontmatter.get('description', f"Output formatter: {name}"),
        triggers=triggers,
        format_type=format_type,
        format_config=content,
        priority=frontmatter.get('priority', 50),
        enabled=frontmatter.get('enabled', True),
    )


def create_tool_guide_from_markdown(
    file_path: str,
    frontmatter: Dict[str, Any],
    content: str,
) -> ToolGuide:
    """Create a ToolGuide policy from markdown file.

    Args:
        file_path: Path to the file (for error messages)
        frontmatter: Parsed frontmatter metadata
        content: Markdown content

    Returns:
        ToolGuide instance
    """
    name = frontmatter.get('name')
    if not name:
        raise ValueError(f"ToolGuide in {file_path} missing 'name' in frontmatter")

    target_tools = frontmatter.get('target_tools', [])
    if not target_tools:
        raise ValueError(f"ToolGuide {name} must specify 'target_tools'")

    triggers_config = frontmatter.get('triggers', {})
    triggers = create_triggers_from_metadata(triggers_config)

    if not triggers:
        triggers = [AlwaysTrigger()]

    raw_tool_guards = frontmatter.get('tool_guards')
    tool_guards: dict = {}
    if isinstance(raw_tool_guards, dict):
        for tool_name, guard_config in raw_tool_guards.items():
            try:
                tool_guards[tool_name] = (
                    guard_config if isinstance(guard_config, ToolGuard) else ToolGuard(**guard_config)
                )
            except Exception as e:
                logger.warning(f"Skipping invalid guard for tool '{tool_name}' in {file_path}: {e}")

    return ToolGuide(
        id=frontmatter.get('id', f"tool_guide_{Path(file_path).stem}"),
        name=name,
        description=frontmatter.get('description', f"Tool guide: {name}"),
        triggers=triggers,
        target_tools=target_tools,
        target_apps=frontmatter.get('target_apps'),
        guide_content=content,
        tool_guards=tool_guards,
        prepend=frontmatter.get('prepend', False),
        guards_enabled=frontmatter.get('guards_enabled', True),
        priority=frontmatter.get('priority', 50),
        enabled=frontmatter.get('enabled', True),
    )


def create_intent_guard_from_markdown(
    file_path: str,
    frontmatter: Dict[str, Any],
    content: str,
) -> IntentGuard:
    """Create an IntentGuard policy from markdown file.

    Args:
        file_path: Path to the file (for error messages)
        frontmatter: Parsed frontmatter metadata
        content: Markdown content (used as response)

    Returns:
        IntentGuard instance
    """
    name = frontmatter.get('name')
    if not name:
        raise ValueError(f"IntentGuard in {file_path} missing 'name' in frontmatter")

    triggers_config = frontmatter.get('triggers', {})
    triggers = create_triggers_from_metadata(triggers_config)

    if not triggers:
        raise ValueError(f"IntentGuard {name} must have at least one trigger")

    response_type = frontmatter.get('response_type', 'natural_language')
    if response_type not in ['natural_language', 'json', 'template']:
        raise ValueError(f"Invalid response_type '{response_type}' for IntentGuard {name}")

    return IntentGuard(
        id=frontmatter.get('id', f"intent_guard_{Path(file_path).stem}"),
        name=name,
        description=frontmatter.get('description', f"Intent guard: {name}"),
        triggers=triggers,
        response=IntentGuardResponse(
            response_type=response_type,
            content=content,
        ),
        allow_override=frontmatter.get('allow_override', False),
        priority=frontmatter.get('priority', 50),
        enabled=frontmatter.get('enabled', True),
    )


def create_tool_approval_from_markdown(
    file_path: str,
    frontmatter: Dict[str, Any],
    content: str,
) -> ToolApproval:
    """Create a ToolApproval policy from markdown file.

    Args:
        file_path: Path to the file (for error messages)
        frontmatter: Parsed frontmatter metadata
        content: Markdown content (used as approval message if not specified)

    Returns:
        ToolApproval instance
    """
    name = frontmatter.get('name')
    if not name:
        raise ValueError(f"ToolApproval in {file_path} missing 'name' in frontmatter")

    required_tools = frontmatter.get('required_tools', [])
    if not required_tools:
        raise ValueError(f"ToolApproval {name} must specify 'required_tools'")

    approval_message = frontmatter.get('approval_message', content if content else None)

    return ToolApproval(
        id=frontmatter.get('id', f"tool_approval_{Path(file_path).stem}"),
        name=name,
        description=frontmatter.get('description', f"Tool approval: {name}"),
        required_tools=required_tools,
        required_apps=frontmatter.get('required_apps'),
        approval_message=approval_message,
        show_code_preview=frontmatter.get('show_code_preview', True),
        auto_approve_after=frontmatter.get('auto_approve_after'),
        priority=frontmatter.get('priority', 50),
        enabled=frontmatter.get('enabled', True),
    )


POLICY_CREATORS = {
    'playbook': create_playbook_from_markdown,
    'output_formatter': create_output_formatter_from_markdown,
    'tool_guide': create_tool_guide_from_markdown,
    'intent_guard': create_intent_guard_from_markdown,
    'tool_approval': create_tool_approval_from_markdown,
}


async def load_policies_from_folder(
    folder_path: str,
    storage,  # PolicyStorage instance
    clear_existing: bool = False,
) -> Dict[str, Any]:
    """Load policies from a .cuga folder structure directly to storage.

    Args:
        folder_path: Path to .cuga folder
        storage: PolicyStorage instance
        clear_existing: If True, clear existing policies before loading

    Returns:
        Dictionary with count, errors, and files processed
    """
    if not os.path.exists(folder_path):
        return {"count": 0, "errors": [f"Folder {folder_path} does not exist"], "files": []}

    if clear_existing:
        # Clear all existing policies from storage
        policies = await storage.list_policies(enabled_only=False)
        for policy in policies:
            await storage.delete_policy(policy.id)
        logger.info(f"Cleared {len(policies)} existing policies")

    errors = []
    loaded_count = 0
    processed_files = []

    # Define subfolder structure
    subfolders = {
        'playbooks': 'playbook',
        'output_formatters': 'output_formatter',
        'tool_guides': 'tool_guide',
        'intent_guards': 'intent_guard',
        'tool_approvals': 'tool_approval',
    }

    # Process each subfolder
    for subfolder_name, policy_type in subfolders.items():
        subfolder_path = os.path.join(folder_path, subfolder_name)

        if not os.path.exists(subfolder_path):
            logger.debug(f"Subfolder {subfolder_path} does not exist, skipping")
            continue

        # Find all markdown files in subfolder
        for filename in os.listdir(subfolder_path):
            if not filename.endswith('.md'):
                continue

            file_path = os.path.join(subfolder_path, filename)
            processed_files.append(file_path)

            try:
                # Parse markdown with frontmatter
                frontmatter, content = parse_markdown_with_frontmatter(file_path)

                # Override type from frontmatter if specified, otherwise use folder type
                detected_type = frontmatter.get('type', policy_type)

                # Get creator function
                creator_func = POLICY_CREATORS.get(detected_type)
                if not creator_func:
                    raise ValueError(f"Unknown policy type: {detected_type}")

                # Create policy object
                policy = creator_func(file_path, frontmatter, content)

                # Add to storage
                await storage.add_policy(policy)

                loaded_count += 1
                logger.info(f"✅ Loaded {detected_type} '{policy.name}' from {filename}")

            except Exception as e:
                error_msg = f"Failed to load {file_path}: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg)

    return {
        "count": loaded_count,
        "errors": errors,
        "files": processed_files,
    }
