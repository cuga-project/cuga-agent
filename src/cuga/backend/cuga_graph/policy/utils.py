"""Utility functions for policy system."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from cuga.backend.cuga_graph.policy.models import (
    CustomPolicy,
    IntentGuard,
    OutputFormatter,
    Playbook,
    Policy,
    PolicyType,
    ToolGuide,
    ToolApproval,
)
from cuga.backend.cuga_graph.policy.storage import PolicyStorage


def get_embedding_dimension(provider: str = "auto", model_name: Optional[str] = None) -> int:
    """Get embedding dimension for provider/model. Delegates to storage.embedding."""
    from cuga.backend.storage.embedding import get_embedding_dimension as _get_dim

    return _get_dim(provider, model_name, None, None)


def parse_markdown_to_steps(markdown_content: str) -> List[Dict[str, Any]]:
    """
    Parse markdown content into playbook steps.

    Args:
        markdown_content: Markdown content with numbered lists

    Returns:
        List of step dictionaries
    """
    steps = []
    lines = markdown_content.split("\n")
    current_step = None
    step_number = 0

    for line in lines:
        line = line.strip()

        # Check for numbered list items (1., 2., etc.)
        if line and line[0].isdigit() and "." in line[:4]:
            if current_step:
                steps.append(current_step)

            step_number += 1
            instruction = line.split(".", 1)[1].strip()
            # Remove markdown formatting
            instruction = instruction.lstrip("*").lstrip("#").strip()

            current_step = {
                "step_number": step_number,
                "instruction": instruction,
                "expected_outcome": None,
                "tools_allowed": None,
            }

        # Look for sub-items that might indicate expected outcome
        elif current_step and line.startswith("-"):
            if not current_step["expected_outcome"]:
                current_step["expected_outcome"] = line.lstrip("-").strip()

    # Add the last step
    if current_step:
        steps.append(current_step)

    return steps


async def apply_policies_data_to_storage(
    storage: PolicyStorage,
    policies_data: List[Dict[str, Any]],
    clear_existing: bool = True,
    filesystem_sync: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Apply a list of policy dicts (frontend/manage-config format) to storage.
    Used by load_policies_from_json, POST /api/config/policies, and manager config.
    """
    errors: List[str] = []
    count = 0
    if clear_existing:
        existing_policies = await storage.list_policies(enabled_only=False)
        for policy_obj in existing_policies:
            try:
                await storage.delete_policy(policy_obj.id)
            except Exception as e:
                logger.warning("Failed to delete existing policy %s: %s", policy_obj.id, e)
        logger.info("Cleared %d existing policies", len(existing_policies))

    for policy_data in policies_data or []:
        try:
            if "triggers" in policy_data and isinstance(policy_data["triggers"], list):
                for trigger in policy_data["triggers"]:
                    if trigger.get("type") == "natural_language" and "value" in trigger:
                        value = trigger["value"]
                        if not isinstance(value, list):
                            trigger["value"] = [value] if isinstance(value, str) else []

            policy_type = policy_data.get("policy_type") or policy_data.get("type")
            if policy_type == "tool_approval" and "triggers" in policy_data:
                policy_data.pop("triggers", None)
            if "intent_examples" in policy_data:
                policy_data.pop("intent_examples", None)

            if policy_type == "intent_guard":
                from cuga.backend.cuga_graph.policy.models import (
                    IntentGuardResponse,
                    KeywordTrigger,
                    NaturalLanguageTrigger,
                    AppTrigger,
                    StateTrigger,
                    ToolTrigger,
                    AlwaysTrigger,
                )

                # Parse triggers into proper model objects
                parsed_triggers = []
                for trigger_data in policy_data.get("triggers", []):
                    trigger_type = trigger_data.get("type")
                    if trigger_type == "keyword":
                        parsed_triggers.append(KeywordTrigger(**trigger_data))
                    elif trigger_type == "natural_language":
                        parsed_triggers.append(NaturalLanguageTrigger(**trigger_data))
                    elif trigger_type == "app":
                        parsed_triggers.append(AppTrigger(**trigger_data))
                    elif trigger_type == "state":
                        parsed_triggers.append(StateTrigger(**trigger_data))
                    elif trigger_type == "tool":
                        parsed_triggers.append(ToolTrigger(**trigger_data))
                    elif trigger_type == "always":
                        parsed_triggers.append(AlwaysTrigger(**trigger_data))
                    else:
                        logger.warning(f"Unknown trigger type: {trigger_type}")

                response_data = policy_data.get("response", {})
                policy = IntentGuard(
                    id=policy_data["id"],
                    name=policy_data["name"],
                    description=policy_data["description"],
                    triggers=parsed_triggers,
                    response=IntentGuardResponse(
                        response_type=response_data.get("response_type", "natural_language"),
                        content=response_data.get("content", ""),
                        status_code=response_data.get("status_code"),
                    ),
                    allow_override=policy_data.get("allow_override", False),
                    priority=policy_data.get("priority", 50),
                    enabled=policy_data.get("enabled", True),
                )
            elif policy_type == "playbook":
                from cuga.backend.cuga_graph.policy.models import PlaybookStep

                steps_data = policy_data.get("steps", [])
                steps = [
                    PlaybookStep(
                        step_number=step["step_number"],
                        instruction=step["instruction"],
                        expected_outcome=step["expected_outcome"],
                        tools_allowed=step.get("tools_allowed", []),
                    )
                    for step in steps_data
                ]
                policy = Playbook(
                    id=policy_data["id"],
                    name=policy_data["name"],
                    description=policy_data["description"],
                    triggers=policy_data["triggers"],
                    markdown_content=policy_data.get("markdown_content", ""),
                    steps=steps,
                    priority=policy_data.get("priority", 50),
                    enabled=policy_data.get("enabled", True),
                )
            elif policy_type == "tool_guide":
                policy = ToolGuide(
                    id=policy_data["id"],
                    name=policy_data["name"],
                    description=policy_data["description"],
                    triggers=policy_data.get("triggers", []),
                    target_tools=policy_data.get("target_tools", []),
                    target_apps=policy_data.get("target_apps"),
                    guide_content=policy_data.get("guide_content", ""),
                    prepend=policy_data.get("prepend", False),
                    priority=policy_data.get("priority", 50),
                    enabled=policy_data.get("enabled", True),
                )
            elif policy_type == "tool_approval":
                policy = ToolApproval(
                    id=policy_data["id"],
                    name=policy_data["name"],
                    description=policy_data["description"],
                    required_tools=policy_data.get("required_tools", []),
                    required_apps=policy_data.get("required_apps"),
                    approval_message=policy_data.get("approval_message"),
                    show_code_preview=policy_data.get("show_code_preview", True),
                    auto_approve_after=policy_data.get("auto_approve_after"),
                    priority=policy_data.get("priority", 50),
                    enabled=policy_data.get("enabled", True),
                )
            elif policy_type == "output_formatter":
                policy = OutputFormatter(
                    id=policy_data["id"],
                    name=policy_data["name"],
                    description=policy_data["description"],
                    triggers=policy_data["triggers"],
                    format_type=policy_data.get("format_type", "markdown"),
                    format_config=policy_data.get("format_config", ""),
                    priority=policy_data.get("priority", 50),
                    enabled=policy_data.get("enabled", True),
                    metadata=policy_data.get("metadata", {}),
                )
            elif policy_type == PolicyType.CUSTOM:
                policy = CustomPolicy(**policy_data)
            else:
                logger.warning("Unknown policy type: %s", policy_type)
                errors.append(
                    f"Unknown policy type '{policy_type}' for policy '{policy_data.get('name', 'unknown')}'"
                )
                continue

            await storage.add_policy(policy)
            count += 1
            if filesystem_sync:
                try:
                    filesystem_sync.save_policy_to_file(policy)
                except Exception as e:
                    logger.warning("Failed to save policy to filesystem: %s", e)
        except Exception as e:
            error_msg = f"Failed to load policy '{policy_data.get('name', 'unknown')}': {e}"
            logger.error(error_msg)
            errors.append(error_msg)
    return {"count": count, "errors": errors}


async def load_policies_from_json(
    file_path: str,
    storage: PolicyStorage,
    clear_existing: bool = False,
) -> Dict[str, Any]:
    """
    Load policies from a JSON file into storage.

    Supports both frontend export format (with `enablePolicies` and `policies` array)
    and simple array format.

    Embeddings will be generated automatically by the storage layer.

    Args:
        file_path: Path to JSON file containing policies
        storage: PolicyStorage instance
        clear_existing: If True, clear all existing policies before loading

    Returns:
        Dictionary with:
            - count: Number of policies loaded
            - enabled: Whether policies are enabled (from frontend format, if present)
            - errors: List of error messages (if any)
    """
    enabled = True
    try:
        with open(file_path, "r") as f:
            data = json.load(f)

        # Handle frontend export format: {"enablePolicies": true, "policies": [...]}
        if isinstance(data, dict) and "policies" in data:
            enabled = data.get("enablePolicies", True)
            policies_data = data["policies"]
            logger.info(
                f"Loading {len(policies_data)} policies from frontend export format (enabled: {enabled})"
            )
        # Handle simple array format: [...]
        elif isinstance(data, list):
            policies_data = data
            logger.info(f"Loading {len(policies_data)} policies from array format")
        # Handle single policy object: {...}
        else:
            policies_data = [data]
            logger.info("Loading single policy from object format")

        result = await apply_policies_data_to_storage(
            storage, policies_data, clear_existing=clear_existing, filesystem_sync=None
        )
        count = result["count"]
        errors = result["errors"]
        logger.info(f"📦 Successfully loaded {count} policies from {file_path}")
        if errors:
            logger.warning(f"⚠️  Encountered {len(errors)} errors during loading")
        return {"count": count, "enabled": enabled, "errors": errors}

    except Exception as e:
        error_msg = f"Failed to load policies from {file_path}: {e}"
        logger.error(error_msg)
        return {"count": 0, "enabled": True, "errors": [error_msg]}


async def export_policies_to_json(
    storage: PolicyStorage,
    output_path: str,
    policy_type: Optional[PolicyType] = None,
) -> bool:
    """
    Export policies from storage to a JSON file.

    Args:
        storage: PolicyStorage instance
        output_path: Path to output JSON file
        policy_type: Optional filter by policy type

    Returns:
        True if successful, False otherwise
    """
    try:
        policies = await storage.list_policies(policy_type=policy_type, enabled_only=False)

        policies_data = [policy.model_dump() for policy in policies]

        with open(output_path, "w") as f:
            json.dump(policies_data, f, indent=2)

        logger.info(f"Exported {len(policies_data)} policies to {output_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to export policies to {output_path}: {e}")
        return False


async def backup_policies(storage: PolicyStorage, backup_dir: str) -> bool:
    """
    Backup all policies to a directory.

    Args:
        storage: PolicyStorage instance
        backup_dir: Directory to store backups

    Returns:
        True if successful, False otherwise
    """
    try:
        backup_path = Path(backup_dir)
        backup_path.mkdir(parents=True, exist_ok=True)

        # Export each policy type separately
        for policy_type in PolicyType:
            output_file = backup_path / f"policies_{policy_type.value}.json"
            await export_policies_to_json(storage, str(output_file), policy_type)

        logger.info(f"Backed up all policies to {backup_dir}")
        return True

    except Exception as e:
        logger.error(f"Failed to backup policies: {e}")
        return False


async def restore_policies(storage: PolicyStorage, backup_dir: str) -> int:
    """
    Restore policies from a backup directory.

    Args:
        storage: PolicyStorage instance
        backup_dir: Directory containing backups

    Returns:
        Number of policies restored
    """
    try:
        backup_path = Path(backup_dir)
        if not backup_path.exists():
            logger.error(f"Backup directory not found: {backup_dir}")
            return 0

        total_count = 0

        for policy_type in PolicyType:
            backup_file = backup_path / f"policies_{policy_type.value}.json"
            if backup_file.exists():
                count = await load_policies_from_json(str(backup_file), storage)
                total_count += count

        logger.info(f"Restored {total_count} policies from {backup_dir}")
        return total_count

    except Exception as e:
        logger.error(f"Failed to restore policies: {e}")
        return 0


def validate_policy(policy: Policy) -> tuple[bool, List[str]]:
    """
    Validate a policy for common issues.

    Args:
        policy: Policy to validate

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check required fields
    if not policy.id:
        errors.append("Policy ID is required")
    if not policy.name:
        errors.append("Policy name is required")
    if not policy.description:
        errors.append("Policy description is required")
    if not policy.triggers:
        errors.append("At least one trigger is required")

    # Type-specific validation
    if isinstance(policy, Playbook):
        if not policy.markdown_content:
            errors.append("Playbook must have markdown_content")

    elif isinstance(policy, IntentGuard):
        if not policy.response:
            errors.append("IntentGuard must have response configuration")

    elif isinstance(policy, CustomPolicy):
        if not policy.action_type:
            errors.append("CustomPolicy must have action_type")
        if not policy.action_config:
            errors.append("CustomPolicy must have action_config")

    return len(errors) == 0, errors


async def get_policy_statistics(storage: PolicyStorage) -> Dict[str, Any]:
    """
    Get statistics about policies in storage.

    Args:
        storage: PolicyStorage instance

    Returns:
        Dictionary with statistics
    """
    try:
        total = await storage.count_policies()
        by_type = {}

        for policy_type in PolicyType:
            count = await storage.count_policies(policy_type=policy_type)
            by_type[policy_type.value] = count

        policies = await storage.list_policies(enabled_only=False)
        enabled_count = sum(1 for p in policies if p.enabled)
        disabled_count = total - enabled_count

        priorities = [p.priority for p in policies]
        avg_priority = sum(priorities) / len(priorities) if priorities else 0

        return {
            "total_policies": total,
            "by_type": by_type,
            "enabled": enabled_count,
            "disabled": disabled_count,
            "average_priority": avg_priority,
        }

    except Exception as e:
        logger.error(f"Failed to get policy statistics: {e}")
        return {}


def format_policy_summary(policy: Policy) -> str:
    """
    Format a policy as a human-readable summary.

    Args:
        policy: Policy to format

    Returns:
        Formatted summary string
    """
    lines = [
        f"Policy: {policy.name} ({policy.id})",
        f"Type: {policy.type}",
        f"Description: {policy.description}",
        f"Priority: {policy.priority}",
        f"Enabled: {'Yes' if policy.enabled else 'No'}",
        f"Triggers: {len(policy.triggers)}",
    ]

    for i, trigger in enumerate(policy.triggers):
        value = getattr(trigger, 'value', 'N/A')
        if isinstance(value, list):
            value_str = ', '.join(value) if value else '[]'
        else:
            value_str = str(value)
        lines.append(f"  {i + 1}. {trigger.type}: {value_str}")

    if isinstance(policy, Playbook):
        lines.append(f"Steps: {len(policy.steps) if policy.steps else 'Not parsed'}")

    elif isinstance(policy, IntentGuard):
        lines.append(f"Response Type: {policy.response.response_type}")

    elif isinstance(policy, CustomPolicy):
        lines.append(f"Action Type: {policy.action_type}")

    return "\n".join(lines)
