"""
Filesystem synchronization for policy storage.

This module provides bidirectional sync between .cuga folder and policy storage:
1. Load policies from .cuga folder on init
2. Save policies to .cuga folder when added/updated via SDK or UI
3. Remove policies from storage when removed from filesystem
"""

import os
from typing import Dict, List, Optional, Set
from loguru import logger

from cuga.backend.cuga_graph.policy.models import (
    Policy,
    Playbook,
    OutputFormatter,
    ToolGuide,
    IntentGuard,
    ToolApproval,
    KeywordTrigger,
    NaturalLanguageTrigger,
    AlwaysTrigger,
)


class PolicyFilesystemSync:
    """
    Handles bidirectional sync between .cuga folder and policy storage.

    Features:
    - Auto-save policies to filesystem when added via SDK/UI
    - Auto-load policies from filesystem on init
    - Auto-remove from storage when file deleted from filesystem
    """

    def __init__(self, cuga_folder: str = ".cuga"):
        """
        Initialize filesystem sync.

        Args:
            cuga_folder: Path to .cuga folder (default: ".cuga")
        """
        self.cuga_folder = cuga_folder
        self._policy_files_map: Dict[str, str] = {}  # policy_id -> file_path

    def _ensure_folder_structure(self):
        """Ensure .cuga folder structure exists."""
        subfolders = [
            'playbooks',
            'output_formatters',
            'tool_guides',
            'intent_guards',
            'tool_approvals',
        ]

        for subfolder in subfolders:
            folder_path = os.path.join(self.cuga_folder, subfolder)
            os.makedirs(folder_path, exist_ok=True)

    def _get_subfolder_for_policy(self, policy: Policy) -> str:
        """Get subfolder name for policy type."""
        from cuga.backend.cuga_graph.policy.models import PolicyType

        # Get policy type from either 'type' or 'policy_type' attribute
        policy_type = getattr(policy, 'type', None) or getattr(policy, 'policy_type', None)

        if isinstance(policy, Playbook) or policy_type == PolicyType.PLAYBOOK:
            return 'playbooks'
        elif isinstance(policy, OutputFormatter) or policy_type == PolicyType.OUTPUT_FORMATTER:
            return 'output_formatters'
        elif isinstance(policy, ToolGuide) or policy_type == PolicyType.TOOL_GUIDE:
            return 'tool_guides'
        elif isinstance(policy, IntentGuard) or policy_type == PolicyType.INTENT_GUARD:
            return 'intent_guards'
        elif isinstance(policy, ToolApproval) or policy_type == PolicyType.TOOL_APPROVAL:
            return 'tool_approvals'
        else:
            return 'policies'

    def _policy_to_markdown(self, policy: Policy) -> str:
        """
        Convert policy to markdown with frontmatter.

        Args:
            policy: Policy object

        Returns:
            Markdown string with YAML frontmatter
        """
        # Build frontmatter
        # Get policy type - try 'type' first, then 'policy_type'
        policy_type = getattr(policy, 'type', None) or getattr(policy, 'policy_type', None)
        policy_type_value = (
            policy_type.value
            if policy_type is not None and hasattr(policy_type, 'value')
            else str(policy_type)
        )
        frontmatter = {
            'id': policy.id,
            'name': policy.name,
            'description': policy.description,
            'type': policy_type_value,
            'priority': policy.priority,
            'enabled': policy.enabled,
        }

        # Add triggers if present
        policy_triggers = getattr(policy, 'triggers', None)
        if policy_triggers:
            triggers_config = {}

            for trigger in policy_triggers:
                if isinstance(trigger, KeywordTrigger):
                    triggers_config['keywords'] = trigger.value
                    triggers_config['target'] = trigger.target
                    triggers_config['case_sensitive'] = trigger.case_sensitive
                    triggers_config['operator'] = trigger.operator
                elif isinstance(trigger, NaturalLanguageTrigger):
                    triggers_config['natural_language'] = trigger.value
                    triggers_config['threshold'] = trigger.threshold
                    if not triggers_config.get('target'):
                        triggers_config['target'] = trigger.target
                elif isinstance(trigger, AlwaysTrigger):
                    triggers_config['always'] = True

            if triggers_config:
                frontmatter['triggers'] = triggers_config

        # Add type-specific fields
        if isinstance(policy, Playbook):
            content = policy.markdown_content or ""
        elif isinstance(policy, OutputFormatter):
            frontmatter['format_type'] = policy.format_type
            content = policy.format_config or ""
        elif isinstance(policy, ToolGuide):
            frontmatter['target_tools'] = policy.target_tools
            if policy.target_apps:
                frontmatter['target_apps'] = policy.target_apps
            frontmatter['prepend'] = policy.prepend
            frontmatter['guards_enabled'] = policy.guards_enabled
            content = policy.guide_content or ""
            if policy.tool_guards:
                frontmatter['tool_guards'] = {
                    tool_name: guard.model_dump() if hasattr(guard, "model_dump") else guard
                    for tool_name, guard in policy.tool_guards.items()
                }
        elif isinstance(policy, IntentGuard):
            if policy.response:
                frontmatter['response_type'] = policy.response.response_type
                content = policy.response.content or ""
            else:
                content = ""
            frontmatter['allow_override'] = policy.allow_override
        elif isinstance(policy, ToolApproval):
            frontmatter['required_tools'] = policy.required_tools
            if policy.required_apps:
                frontmatter['required_apps'] = policy.required_apps
            if policy.approval_message:
                frontmatter['approval_message'] = policy.approval_message
            frontmatter['show_code_preview'] = policy.show_code_preview
            if policy.auto_approve_after:
                frontmatter['auto_approve_after'] = policy.auto_approve_after
            content = policy.approval_message or ""
        else:
            content = ""

        # Build markdown file
        import yaml

        frontmatter_yaml = yaml.safe_dump(frontmatter, default_flow_style=False, allow_unicode=True)

        markdown = f"---\n{frontmatter_yaml}---\n\n{content}"
        return markdown

    def save_policy_to_file(self, policy: Policy) -> str:
        """
        Save a policy to a markdown file in .cuga folder.

        Args:
            policy: Policy to save

        Returns:
            Path to saved file
        """
        try:
            self._ensure_folder_structure()

            # Determine subfolder
            subfolder = self._get_subfolder_for_policy(policy)

            # Create filename from policy ID (sanitize for filesystem)
            filename = f"{policy.id}.md"
            file_path = os.path.join(self.cuga_folder, subfolder, filename)

            # Convert policy to markdown
            markdown_content = self._policy_to_markdown(policy)

            # Write to file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)

            # Track file path
            self._policy_files_map[policy.id] = file_path

            logger.debug(f"Saved policy '{policy.name}' to {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to save policy '{policy.name}' to filesystem: {e}")
            raise

    def delete_policy_file(self, policy_id: str) -> bool:
        """
        Delete a policy file from .cuga folder.

        Args:
            policy_id: ID of policy to delete

        Returns:
            True if file was deleted, False if not found
        """
        try:
            # Check if we have the file path cached
            file_path = self._policy_files_map.get(policy_id)

            if not file_path:
                # Try to find it by searching all subfolders
                file_path = self._find_policy_file(policy_id)

            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                self._policy_files_map.pop(policy_id, None)
                logger.debug(f"Deleted policy file: {file_path}")
                return True
            else:
                logger.warning(f"Policy file not found for deletion: {policy_id}")
                return False

        except Exception as e:
            logger.error(f"Failed to delete policy file '{policy_id}': {e}")
            return False

    def _find_policy_file(self, policy_id: str) -> Optional[str]:
        """
        Find a policy file by ID across all subfolders.

        Args:
            policy_id: Policy ID to search for

        Returns:
            File path if found, None otherwise
        """
        if not os.path.exists(self.cuga_folder):
            return None

        # Search all subfolders
        for root, dirs, files in os.walk(self.cuga_folder):
            for filename in files:
                if filename.endswith('.md') and filename.startswith(policy_id):
                    return os.path.join(root, filename)

        return None

    def get_filesystem_policy_ids(self) -> Set[str]:
        """
        Get all policy IDs present in .cuga folder.

        Returns:
            Set of policy IDs found in filesystem
        """
        policy_ids = set()

        if not os.path.exists(self.cuga_folder):
            return policy_ids

        # Parse all markdown files to extract IDs
        for root, dirs, files in os.walk(self.cuga_folder):
            for filename in files:
                if not filename.endswith('.md'):
                    continue

                file_path = os.path.join(root, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # Extract ID from frontmatter
                    if content.startswith('---'):
                        parts = content.split('---', 2)
                        if len(parts) >= 3:
                            import yaml

                            frontmatter = yaml.safe_load(parts[1])
                            if isinstance(frontmatter, dict) and 'id' in frontmatter:
                                policy_ids.add(frontmatter['id'])
                                # Cache the file path
                                self._policy_files_map[frontmatter['id']] = file_path
                except Exception as e:
                    logger.warning(f"Failed to parse policy file {file_path}: {e}")

        return policy_ids

    async def sync_removals(self, storage) -> List[str]:
        """
        Remove policies from storage that are no longer in filesystem.

        Args:
            storage: PolicyStorage instance

        Returns:
            List of policy IDs that were removed
        """
        try:
            # Get all policy IDs from filesystem
            fs_policy_ids = self.get_filesystem_policy_ids()

            # Get all policy IDs from storage
            storage_policies = await storage.list_policies(enabled_only=False)
            storage_policy_ids = {p.id for p in storage_policies}

            # Find policies that are in storage but not in filesystem
            removed_ids = storage_policy_ids - fs_policy_ids

            # Remove them from storage
            for policy_id in removed_ids:
                try:
                    await storage.delete_policy(policy_id)
                    logger.info(f"Removed policy '{policy_id}' from storage (deleted from filesystem)")
                except Exception as e:
                    logger.error(f"Failed to remove policy '{policy_id}' from storage: {e}")

            return list(removed_ids)

        except Exception as e:
            logger.error(f"Failed to sync policy removals: {e}")
            return []
