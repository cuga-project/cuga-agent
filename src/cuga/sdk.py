"""
CUGA SDK - Simple interface for using CugaAgent

This module provides a clean, minimal API for using CUGA's agent capabilities.
The agent is built on LangGraph and can be used as a compiled graph or invoked directly.

Basic Example:
    ```python
    from cuga.sdk import CugaAgent
    from langchain_core.tools import tool

    # Define your tools
    @tool
    def search_database(query: str) -> str:
        '''Search the database for information'''
        return "Database results for: " + query

    # Create and run the agent
    agent = CugaAgent(tools=[search_database])
    result = await agent.invoke("Find all users in the database")
    print(result)
    ```

Tool Approval Example (with HITL):
    ```python
    from cuga.sdk import CugaAgent
    from langchain_core.tools import tool
    from datetime import datetime
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionResponse, ActionType
    )

    @tool
    def delete_database(table: str) -> str:
        '''Delete a database table'''
        return f"Deleted table: {table}"

    # Create agent
    agent = CugaAgent(tools=[delete_database])

    # Add tool approval policy
    await agent.policies.add_tool_approval(
        name="Approve Deletions",
        required_tools=["delete_database"],
        approval_message="This will delete data. Please confirm."
    )

    # Invoke - will interrupt if approval needed
    thread_id = "user-123"
    result = await agent.invoke("Delete the users table", thread_id=thread_id)

    # Check if interrupted for approval
    if "Execution paused for approval" in result:
        # Create approval response
        approval = ActionResponse(
            action_id="tool_approval",
            response_type=ActionType.CONFIRMATION,
            confirmed=True,  # or False to deny
            timestamp=datetime.now().isoformat(),
            user_id=thread_id,
            session_id=thread_id
        )

        # Resume execution with approval (use None as message to resume)
        result = await agent.invoke(None, thread_id=thread_id, action_response=approval)

    print(result)
    ```
"""

from typing import List, Optional, Dict, Any, Union, TYPE_CHECKING
import uuid
from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_core.language_models import BaseChatModel
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    pass

from cuga.backend.llm.models import LLMManager
from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
    create_cuga_lite_graph,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.direct_langchain_tools_provider import (
    DirectLangChainToolsProvider,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.state.agent_state import AgentState
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from cuga.backend.cuga_graph.policy.models import (
    IntentGuard,
    Playbook,
    ToolGuide,
    ToolApproval,
    OutputFormatter,
    KeywordTrigger,
    NaturalLanguageTrigger,
    IntentGuardResponse,
    AlwaysTrigger,
)
from langchain_core.messages import HumanMessage, BaseMessage


class InvokeResult(BaseModel):
    """Result from CugaAgent.invoke() containing answer and metadata."""

    answer: str = Field(default="", description="The agent's final answer")
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of tool calls made during execution (when track_tool_calls is enabled)",
    )
    thread_id: str = Field(default="", description="Thread ID used for this invocation")
    error: Optional[str] = Field(default=None, description="Error message if execution failed")

    def __str__(self) -> str:
        """Return the answer when converting to string for backward compatibility."""
        return self.answer


class PoliciesManager:
    """
    Manager for policy operations on a CugaAgent instance.

    Provides a clean API for adding, removing, and managing policies.

    Example:
        ```python
        agent = CugaAgent(tools=[my_tool])

        # Add an intent blocker
        agent.policies.add_intent_guard(
            name="Block Delete Operations",
            keywords=["delete", "remove"],
            response="Deletion operations are not allowed."
        )

        # Add a playbook
        agent.policies.add_playbook(
            name="Customer Onboarding",
            keywords=["onboard", "signup"],
            content="# Customer Onboarding Guide\n\n..."
        )

        # Delete a policy
        agent.policies.delete("policy_id_123")
        ```
    """

    def __init__(self, agent: "CugaAgent"):
        """Initialize policies manager with reference to agent."""
        self._agent = agent
        self._fs_sync = None

    async def _ensure_policy_system(self) -> Optional[PolicyConfigurable]:
        """Ensure policy system is initialized if enabled.

        Returns:
            PolicyConfigurable if enabled, None if disabled via settings.policy.enabled
        """
        from cuga.config import settings

        if not settings.policy.enabled:
            return None

        if not hasattr(self._agent, '_policy_system') or self._agent._policy_system is None:
            self._agent._policy_system = PolicyConfigurable()
            await self._agent._policy_system.initialize()

            # Initialize filesystem sync if enabled and .cuga folder is configured
            # Initialize filesystem sync if enabled
            import os

            if self._agent._filesystem_sync and self._agent.cuga_folder:
                from cuga.backend.cuga_graph.policy.filesystem_sync import PolicyFilesystemSync

                self._fs_sync = PolicyFilesystemSync(cuga_folder=self._agent.cuga_folder)
                logger.debug(f"Initialized filesystem sync for {self._agent.cuga_folder}")

        # Reset/clear all existing policies if requested (must be outside the init block)
        # This ensures reset works even when policy system is already initialized
        # Only reset ONCE during initialization, then clear the flag
        if self._agent._reset_policy_storage:
            try:
                logger.info("Resetting policy storage - clearing all existing policies")
                await self.clear()
                logger.info("✅ Policy storage reset complete")
                # Clear the flag so we don't reset again on subsequent calls
                self._agent._reset_policy_storage = False
            except Exception as e:
                logger.error(f"Failed to reset policy storage: {e}")
                raise e

        # Auto-load policies from .cuga folder if enabled
        # BUT skip auto-load if we just reset the storage (to keep it clean)
        if self._agent._auto_load_policies and not self._agent._reset_policy_storage:
            try:
                # Resolve to absolute path for clarity in logs
                import os

                folder_path = os.path.abspath(self._agent.cuga_folder)
                cwd = os.getcwd()

                logger.debug(f"Checking for policy folder: {self._agent.cuga_folder}")
                logger.debug(f"  Current working directory: {cwd}")
                logger.debug(f"  Resolved absolute path: {folder_path}")

                if os.path.exists(folder_path):
                    await self.load_from_folder(self._agent.cuga_folder)
                    logger.info(
                        f"Auto-loaded policies from {self._agent.cuga_folder} (resolved: {folder_path})"
                    )

                    # Validate and sync: ensure filesystem and storage are in sync
                    if self._fs_sync:
                        sync_result = await self._validate_and_sync(
                            self._agent._policy_system.storage, folder_path
                        )
                        if (
                            sync_result['removed']
                            or sync_result['added_to_storage']
                            or sync_result['added_to_filesystem']
                        ):
                            logger.info(
                                f"Sync validation complete: "
                                f"removed={sync_result['removed']}, "
                                f"added_to_storage={sync_result['added_to_storage']}, "
                                f"added_to_filesystem={sync_result['added_to_filesystem']}"
                            )
                else:
                    logger.warning(
                        f"Policy folder not found: {self._agent.cuga_folder} (resolved: {folder_path})"
                    )
                    logger.warning(f"  Current directory: {cwd}")
                    logger.warning("  Skipping auto-load")
            except Exception as e:
                logger.error(f"Failed to auto-load policies from {self._agent.cuga_folder}: {e}")
                raise e

        return self._agent._policy_system

    async def add_intent_guard(
        self,
        name: str,
        description: str = "",
        keywords: Optional[List[str]] = None,
        intent_examples: Optional[List[str]] = None,
        response: str = "This action is not allowed.",
        response_type: str = "natural_language",
        priority: int = 50,
        enabled: bool = True,
        allow_override: bool = False,
        policy_id: Optional[str] = None,
    ) -> str:
        """
        Add an Intent Guard policy (blocker).

        Args:
            name: Policy name
            description: Policy description
            keywords: List of keywords to trigger on (uses OR operator by default)
            intent_examples: List of example intents for semantic matching
            response: Response message when guard triggers
            response_type: Response type ("natural_language", "json", or "template")
            priority: Priority (higher = checked first)
            enabled: Whether policy is enabled
            allow_override: Whether user can override this guard
            policy_id: Optional custom ID (auto-generated if not provided)

        Returns:
            Policy ID

        Example:
            ```python
            policy_id = await agent.policies.add_intent_guard(
                name="Block Delete",
                keywords=["delete", "remove", "erase"],
                response="Deletion operations are not permitted."
            )
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - skipping add_intent_guard")
            return None

        triggers = []
        if keywords:
            triggers.append(
                KeywordTrigger(
                    value=keywords,
                    target="intent",
                    case_sensitive=False,
                    operator="or",
                )
            )

        if intent_examples:
            triggers.append(
                NaturalLanguageTrigger(
                    value=intent_examples,
                    target="intent",
                    threshold=0.7,
                )
            )

        policy = IntentGuard(
            id=policy_id or f"intent_guard_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description or f"Intent guard: {name}",
            triggers=triggers,
            response=IntentGuardResponse(
                # Map old response types to new ones if necessary, but "natural_language" is supported
                response_type=response_type
                if response_type in ["natural_language", "json", "template"]
                else "natural_language",
                content=response,
            ),
            allow_override=allow_override,
            priority=priority,
            enabled=enabled,
        )

        await policy_system.storage.add_policy(policy)
        await policy_system.initialize()  # Reload policies

        # Save to filesystem if sync is enabled
        if self._fs_sync:
            try:
                self._fs_sync.save_policy_to_file(policy)
                logger.debug(f"Saved policy '{policy.id}' to filesystem")
            except Exception as e:
                logger.warning(f"Failed to save policy to filesystem: {e}")

        logger.info(f"Added Intent Guard policy: {policy.id}")
        return policy.id

    async def add_playbook(
        self,
        name: str,
        content: str,
        description: str = "",
        keywords: Optional[List[str]] = None,
        natural_language_trigger: Optional[List[str]] = None,
        threshold: float = 0.7,
        priority: int = 50,
        enabled: bool = True,
        policy_id: Optional[str] = None,
    ) -> str:
        """
        Add a Playbook policy (guidance).

        Args:
            name: Policy name
            content: Markdown content of the playbook
            description: Policy description
            keywords: List of keywords to trigger on (uses OR operator by default)
            natural_language_trigger: Natural language description for semantic matching
            threshold: Similarity threshold for NL matching (0.0-1.0)
            priority: Priority (higher = checked first)
            enabled: Whether policy is enabled
            policy_id: Optional custom ID (auto-generated if not provided)

        Returns:
            Policy ID

        Example:
            ```python
            policy_id = await agent.policies.add_playbook(
                name="Customer Onboarding",
                keywords=["onboard", "signup", "register"],
                content="# Customer Onboarding\n\n1. Verify email\n2. Create account..."
            )
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - skipping add_playbook")
            return None

        triggers = []
        if keywords:
            triggers.append(
                KeywordTrigger(
                    value=keywords,
                    target="intent",
                    case_sensitive=False,
                    operator="or",
                )
            )
        if natural_language_trigger:
            triggers.append(
                NaturalLanguageTrigger(
                    value=natural_language_trigger,
                    target="intent",
                    threshold=threshold,
                )
            )

        if not triggers:
            raise ValueError("Must provide either keywords or natural_language_trigger")

        policy = Playbook(
            id=policy_id or f"playbook_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description or f"Playbook: {name}",
            triggers=triggers,
            markdown_content=content,
            steps=None,
            priority=priority,
            enabled=enabled,
        )
        try:
            await policy_system.storage.add_policy(policy)
            logger.info(f"✅ Added Playbook policy to storage: {policy.id}")

            # Reinitialize to ensure policy is loaded in memory
            await policy_system.initialize()
            logger.debug(f"Reinitialized policy system after adding {policy.id}")

            # Save to filesystem if sync is enabled
            if self._fs_sync:
                try:
                    self._fs_sync.save_policy_to_file(policy)
                    logger.debug(f"Saved policy '{policy.id}' to filesystem")
                except Exception as e:
                    logger.warning(f"Failed to save policy to filesystem: {e}")

            return policy.id
        except Exception as e:
            logger.error(f"Failed to add Playbook policy {policy.id}: {e}")
            raise

    async def add_tool_guide(
        self,
        name: str,
        content: str,
        target_tools: List[str],
        description: str = "",
        keywords: Optional[List[str]] = None,
        target_apps: Optional[List[str]] = None,
        prepend: bool = False,
        priority: int = 50,
        enabled: bool = True,
        policy_id: Optional[str] = None,
    ) -> str:
        """
        Add a Tool Guide policy.

        Args:
            name: Policy name
            content: Markdown content to add to tool descriptions
            target_tools: List of tool names to enrich (use ["*"] for all tools)
            description: Policy description
            keywords: List of keywords to trigger on (uses OR operator by default)
            target_apps: Optional list of app names to filter by
            prepend: Whether to prepend content (False = append)
            priority: Priority (higher = applied first)
            enabled: Whether policy is enabled
            policy_id: Optional custom ID (auto-generated if not provided)

        Returns:
            Policy ID

        Example:
            ```python
            policy_id = await agent.policies.add_tool_guide(
                name="Security Guidelines",
                content="## Security Notes\n\nAlways verify permissions...",
                target_tools=["*"],
                keywords=["sensitive", "secure"]
            )
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - skipping add_tool_guide")
            return None

        triggers = []
        if keywords:
            triggers.append(
                KeywordTrigger(
                    value=keywords,
                    target="intent",
                    case_sensitive=False,
                    operator="or",
                )
            )
        else:
            # Default to always trigger if no keywords
            triggers.append(AlwaysTrigger())

        policy = ToolGuide(
            id=policy_id or f"tool_guide_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description or f"Tool guide: {name}",
            triggers=triggers,
            target_tools=target_tools,
            target_apps=target_apps,
            guide_content=content,
            prepend=prepend,
            priority=priority,
            enabled=enabled,
        )
        await policy_system.storage.add_policy(policy)
        await policy_system.initialize()  # Reload policies

        # Save to filesystem if sync is enabled
        if self._fs_sync:
            try:
                self._fs_sync.save_policy_to_file(policy)
                logger.debug(f"Saved policy '{policy.id}' to filesystem")
            except Exception as e:
                logger.warning(f"Failed to save policy to filesystem: {e}")

        logger.info(f"Added Tool Guide policy: {policy.id}")
        return policy.id

    async def add_tool_approval(
        self,
        name: str,
        required_tools: List[str],
        description: str = "",
        required_apps: Optional[List[str]] = None,
        approval_message: Optional[str] = None,
        show_code_preview: bool = True,
        auto_approve_after: Optional[int] = None,
        priority: int = 50,
        enabled: bool = True,
        policy_id: Optional[str] = None,
    ) -> str:
        """
        Add a Tool Approval policy.

        Args:
            name: Policy name
            required_tools: List of tool names requiring approval (use ["*"] for all tools)
            description: Policy description
            required_apps: Optional list of app names whose tools require approval
            approval_message: Custom message shown when requesting approval
            show_code_preview: Whether to show code preview in approval request
            auto_approve_after: Auto-approve after N seconds (None = no auto-approve)
            priority: Priority (higher = checked first)
            enabled: Whether policy is enabled
            policy_id: Optional custom ID (auto-generated if not provided)

        Returns:
            Policy ID

        Example:
            ```python
            policy_id = await agent.policies.add_tool_approval(
                name="Database Operations",
                required_tools=["db_write", "db_delete"],
                approval_message="Database modifications require approval."
            )
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - skipping add_tool_approval")
            return None

        policy = ToolApproval(
            id=policy_id or f"tool_approval_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description or f"Tool approval: {name}",
            required_tools=required_tools,
            required_apps=required_apps,
            approval_message=approval_message,
            show_code_preview=show_code_preview,
            auto_approve_after=auto_approve_after,
            priority=priority,
            enabled=enabled,
        )

        await policy_system.storage.add_policy(policy)
        await policy_system.initialize()  # Reload policies

        # Save to filesystem if sync is enabled
        if self._fs_sync:
            try:
                self._fs_sync.save_policy_to_file(policy)
                logger.debug(f"Saved policy '{policy.id}' to filesystem")
            except Exception as e:
                logger.warning(f"Failed to save policy to filesystem: {e}")

        logger.info(f"Added Tool Approval policy: {policy.id}")
        return policy.id

    async def add_output_formatter(
        self,
        name: str,
        format_config: str,
        format_type: str = "markdown",
        description: str = "",
        keywords: Optional[List[str]] = None,
        natural_language_trigger: Optional[List[str]] = None,
        threshold: float = 0.7,
        priority: int = 50,
        enabled: bool = True,
        policy_id: Optional[str] = None,
    ) -> str:
        """
        Add an OutputFormatter policy.

        Args:
            name: Policy name
            format_config: Formatting configuration (markdown instructions, JSON schema, or direct string)
            format_type: Type of formatting ("markdown", "json_schema", or "direct")
            description: Policy description
            keywords: List of keywords to trigger on (checked against agent response)
            natural_language_trigger: Natural language descriptions for semantic matching
            threshold: Similarity threshold for NL matching (0.0-1.0)
            priority: Priority (higher = checked first)
            enabled: Whether policy is enabled
            policy_id: Optional custom ID (auto-generated if not provided)

        Returns:
            Policy ID

        Example:
            ```python
            policy_id = await agent.policies.add_output_formatter(
                name="Format as Summary",
                format_config="Format the response as a structured summary with:\n- A clear title\n- Key points as bullets",
                format_type="markdown",
                keywords=["summary", "result", "output"],
            )
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - skipping add_output_format")
            return None

        triggers = []
        if keywords:
            triggers.append(
                KeywordTrigger(
                    value=keywords,
                    target="agent_response",
                    case_sensitive=False,
                    operator="or",
                )
            )
        if natural_language_trigger:
            triggers.append(
                NaturalLanguageTrigger(
                    value=natural_language_trigger,
                    target="agent_response",
                    threshold=threshold,
                )
            )

        if not triggers:
            # Default to always trigger if no triggers provided
            triggers.append(AlwaysTrigger())

        if format_type not in ["markdown", "json_schema", "direct"]:
            raise ValueError("format_type must be one of: 'markdown', 'json_schema', 'direct'")

        policy = OutputFormatter(
            id=policy_id or f"output_formatter_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description or f"Output formatter: {name}",
            triggers=triggers,
            format_type=format_type,
            format_config=format_config,
            priority=priority,
            enabled=enabled,
        )

        try:
            await policy_system.storage.add_policy(policy)
            logger.info(f"✅ Added OutputFormatter policy to storage: {policy.id}")

            # Reinitialize to ensure policy is loaded in memory
            await policy_system.initialize()
            logger.debug(f"Reinitialized policy system after adding {policy.id}")

            # Save to filesystem if sync is enabled
            if self._fs_sync:
                try:
                    self._fs_sync.save_policy_to_file(policy)
                    logger.debug(f"Saved policy '{policy.id}' to filesystem")
                except Exception as e:
                    logger.warning(f"Failed to save policy to filesystem: {e}")

            return policy.id
        except Exception as e:
            logger.error(f"Failed to add OutputFormatter policy {policy.id}: {e}")
            raise

    async def delete(self, policy_id: str) -> bool:
        """
        Delete a policy by ID.

        Args:
            policy_id: ID of the policy to delete

        Returns:
            True if deleted, False if not found

        Example:
            ```python
            success = await agent.policies.delete("policy_id_123")
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - skipping delete")
            return False

        try:
            # Check if policy exists first
            policy = await self.get(policy_id)
            if policy is None:
                logger.warning(f"Policy {policy_id} not found")
                return False

            await policy_system.storage.delete_policy(policy_id)
            await policy_system.initialize()  # Reload policies

            # Delete from filesystem if sync is enabled
            if self._fs_sync:
                try:
                    deleted = self._fs_sync.delete_policy_file(policy_id)
                    if deleted:
                        logger.debug(f"Deleted policy '{policy_id}' from filesystem")
                except Exception as e:
                    logger.warning(f"Failed to delete policy from filesystem: {e}")

            logger.info(f"Deleted policy: {policy_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete policy {policy_id}: {e}")
            return False

    async def list(self) -> List[Dict[str, Any]]:
        """
        List all policies.

        Returns:
            List of policy dictionaries

        Example:
            ```python
            policies = await agent.policies.list()
            for policy in policies:
                print(f"{policy['name']} ({policy['id']})")
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - returning empty list")
            return []

        policies = await policy_system.storage.list_policies(enabled_only=False)
        return [
            {
                "id": p.id,
                "name": p.name,
                "type": p.policy_type.value if hasattr(p, 'policy_type') else p.type.value,
                "enabled": p.enabled,
                "priority": p.priority,
            }
            for p in policies
        ]

    async def get(self, policy_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a policy by ID.

        Args:
            policy_id: ID of the policy to retrieve

        Returns:
            Policy dictionary or None if not found

        Example:
            ```python
            policy = await agent.policies.get("policy_id_123")
            if policy:
                print(f"Policy: {policy['name']}")
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - returning None")
            return None

        policies = await policy_system.storage.list_policies(enabled_only=False)
        for p in policies:
            if p.id == policy_id:
                return {
                    "id": p.id,
                    "name": p.name,
                    "type": p.policy_type.value if hasattr(p, 'policy_type') else p.type.value,
                    "enabled": p.enabled,
                    "priority": p.priority,
                    "policy": p,  # Include full policy object
                }
        return None

    async def load_from_json(
        self,
        file_path: str,
        clear_existing: bool = False,
    ) -> Dict[str, Any]:
        """
        Load policies from a JSON file.

        Supports both frontend export format (with `enablePolicies` and `policies` array)
        and simple array format. Embeddings will be generated automatically.

        Args:
            file_path: Path to JSON file containing policies
            clear_existing: If True, clear all existing policies before loading

        Returns:
            Dictionary with:
                - count: Number of policies loaded
                - enabled: Whether policies are enabled (from frontend format, if present)
                - errors: List of error messages (if any)

        Example:
            ```python
            # Load policies from frontend export format
            result = await agent.policies.load_from_json("policies-export.json")
            print(f"Loaded {result['count']} policies")

            # Load and replace all existing policies
            result = await agent.policies.load_from_json(
                "policies-export.json",
                clear_existing=True
            )
            if result['errors']:
                print(f"Encountered {len(result['errors'])} errors")
            ```
        """
        from cuga.backend.cuga_graph.policy.utils import load_policies_from_json

        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - skipping load_from_json")
            return {"count": 0, "enabled": 0, "errors": ["Policy system is disabled"]}

        result = await load_policies_from_json(
            file_path=file_path,
            storage=policy_system.storage,
            clear_existing=clear_existing,
        )

        # Reload policies in the system
        await policy_system.initialize()

        logger.info(f"✅ Loaded {result['count']} policies from {file_path} (enabled: {result['enabled']})")

        return result

    async def clear(self) -> bool:
        """
        Clear all policies from storage.

        Returns:
            True if successful

        Example:
            ```python
            await agent.policies.clear()
            ```
        """
        # Check if policy system is already initialized to avoid recursion
        if hasattr(self._agent, '_policy_system') and self._agent._policy_system is not None:
            policy_system = self._agent._policy_system
        else:
            policy_system = await self._ensure_policy_system()
            if policy_system is None:
                logger.warning("Policy system is disabled - skipping clear")
                return False

        try:
            # Get all policies
            policies = await policy_system.storage.list_policies(enabled_only=False)

            # Delete each policy
            for policy in policies:
                await policy_system.storage.delete_policy(policy.id)

            await policy_system.initialize()
            logger.info(f"Cleared {len(policies)} policies from storage")
            return True
        except Exception as e:
            logger.error(f"Failed to clear policies: {e}")
            return False

    async def load_from_folder(
        self,
        folder_path: str = ".cuga",
        clear_existing: bool = False,
    ) -> Dict[str, Any]:
        """
        Load policies from a .cuga folder structure containing markdown files.

        Folder structure:
        ```
        .cuga/
        ├── playbooks/
        │   ├── search-strategy.md
        │   └── ibm-docs.md
        ├── output_formatters/
        │   └── ibm-format.md
        ├── tool_guides/
        │   └── security-guide.md
        └── intent_guards/
            └── block-delete.md
        ```

        Each markdown file should have frontmatter with policy metadata:
        ```markdown
        ---
        name: IBM Documentation Search
        type: playbook
        priority: 100
        enabled: true
        triggers:
          keywords:
            - search
            - IBM docs
          natural_language:
            - search IBM documentation
            - find in IBM docs
          threshold: 0.7
        ---

        # Policy Content

        Your markdown content here...
        ```

        Args:
            folder_path: Path to .cuga folder (default: ".cuga")
            clear_existing: If True, clear all existing policies before loading

        Returns:
            Dictionary with:
                - count: Number of policies loaded
                - errors: List of error messages (if any)
                - files: List of files processed

        Example:
            ```python
            # Load all policies from .cuga folder
            result = await agent.policies.load_from_folder(".cuga")
            print(f"Loaded {result['count']} policies from {result['files']} files")

            # Replace all existing policies
            result = await agent.policies.load_from_folder(
                ".cuga",
                clear_existing=True
            )
            ```
        """
        from cuga.backend.cuga_graph.policy.folder_loader import load_policies_from_folder

        # Check if policy system is already initialized to avoid recursion
        if hasattr(self._agent, '_policy_system') and self._agent._policy_system is not None:
            policy_system = self._agent._policy_system
        else:
            # Ensure policy system is initialized BEFORE loading
            policy_system = await self._ensure_policy_system()
            if policy_system is None:
                logger.warning("Policy system is disabled - skipping load_from_folder")
                return {"count": 0, "errors": ["Policy system is disabled"], "files": []}

        # Verify storage is connected
        if not hasattr(policy_system, 'storage') or policy_system.storage is None:
            error_msg = "Policy storage not initialized"
            logger.error(error_msg)
            return {"count": 0, "errors": [error_msg], "files": []}

        logger.info(f"Loading policies from {folder_path} (clear_existing={clear_existing})")

        result = await load_policies_from_folder(
            folder_path=folder_path,
            storage=policy_system.storage,  # Pass storage directly
            clear_existing=clear_existing,
        )

        # Reinitialize policy system after loading
        await policy_system.initialize()

        if result['errors']:
            logger.warning(f"Encountered {len(result['errors'])} errors while loading policies:")
            for error in result['errors']:
                logger.warning(f"  - {error}")

        logger.info(
            f"✅ Loaded {result['count']} policies from {len(result['files'])} files in {folder_path}"
        )

        return result

    async def _validate_and_sync(self, storage, folder_path: str) -> Dict[str, Any]:
        """
        Validate and sync policies between filesystem and storage.

        This ensures:
        1. Policies in filesystem are in storage
        2. Policies only in storage are saved to filesystem
        3. Policies deleted from filesystem are removed from storage

        Args:
            storage: PolicyStorage instance
            folder_path: Path to .cuga folder

        Returns:
            Dictionary with sync statistics
        """
        if not self._fs_sync:
            return {"removed": 0, "added_to_storage": 0, "added_to_filesystem": 0, "errors": []}

        try:
            # Get policy IDs from both sources
            fs_policy_ids = self._fs_sync.get_filesystem_policy_ids()
            storage_policies = await storage.list_policies(enabled_only=False)
            storage_policy_ids = {p.id for p in storage_policies}

            removed_count = 0
            added_to_storage_count = 0
            added_to_filesystem_count = 0
            errors = []

            # 1. Remove from storage if deleted from filesystem
            policies_to_remove = storage_policy_ids - fs_policy_ids
            for policy_id in policies_to_remove:
                try:
                    await storage.delete_policy(policy_id)
                    removed_count += 1
                    logger.info(f"Removed policy '{policy_id}' from storage (not in filesystem)")
                except Exception as e:
                    error_msg = f"Failed to remove policy '{policy_id}': {e}"
                    errors.append(error_msg)
                    logger.error(error_msg)

            # 2. Save to filesystem if only in storage
            policies_to_save_to_fs = storage_policy_ids - fs_policy_ids
            for policy_id in policies_to_save_to_fs:
                try:
                    # Find the policy object
                    policy = next((p for p in storage_policies if p.id == policy_id), None)
                    if policy:
                        self._fs_sync.save_policy_to_file(policy)
                        added_to_filesystem_count += 1
                        logger.info(f"Saved policy '{policy_id}' to filesystem (was only in storage)")
                except Exception as e:
                    error_msg = f"Failed to save policy '{policy_id}' to filesystem: {e}"
                    errors.append(error_msg)
                    logger.error(error_msg)

            return {
                "removed": removed_count,
                "added_to_storage": added_to_storage_count,
                "added_to_filesystem": added_to_filesystem_count,
                "errors": errors,
            }
        except Exception as e:
            logger.error(f"Failed to validate and sync: {e}")
            return {
                "removed": 0,
                "added_to_storage": 0,
                "added_to_filesystem": 0,
                "errors": [str(e)],
            }

    async def sync_from_filesystem(self) -> Dict[str, Any]:
        """
        Sync policies from filesystem to storage.

        This will:
        1. Load new policies from .cuga folder
        2. Remove policies from storage that are no longer in filesystem

        Returns:
            Dictionary with sync statistics

        Example:
            ```python
            result = await agent.policies.sync_from_filesystem()
            print(f"Synced: {result['loaded']} loaded, {result['removed']} removed")
            ```
        """
        policy_system = await self._ensure_policy_system()
        if policy_system is None:
            logger.warning("Policy system is disabled - skipping sync")
            return {"loaded": 0, "removed": 0, "errors": ["Policy system is disabled"]}

        if not self._fs_sync:
            logger.warning("Filesystem sync not initialized - skipping")
            return {"loaded": 0, "removed": 0, "errors": ["Filesystem sync not initialized"]}

        try:
            # Load policies from filesystem
            load_result = await self.load_from_folder(self._agent.cuga_folder, clear_existing=False)

            # Sync removals
            removed_ids = await self._fs_sync.sync_removals(policy_system.storage)

            return {
                "loaded": load_result['count'],
                "removed": len(removed_ids),
                "errors": load_result['errors'],
                "files": load_result['files'],
            }
        except Exception as e:
            logger.error(f"Failed to sync from filesystem: {e}")
            return {"loaded": 0, "removed": 0, "errors": [str(e)]}


class CugaAgent:
    """
    Simple SDK interface for CUGA Agent.

    This class provides a minimal API for creating and invoking CUGA agents.
    Under the hood, it uses LangGraph to create a stateful agent graph.

    Args:
        tools: Optional list of LangChain tools to provide to the agent
        tool_provider: Optional custom tool provider (advanced usage)
        model: Optional language model (defaults to configured model)
        callbacks: Optional list of callback handlers for monitoring
        cuga_folder: Path to .cuga folder containing policy markdown files (default: ".cuga")
        auto_load_policies: If True, automatically loads policies from cuga_folder on first invoke/stream
        filesystem_sync: If True, automatically saves policies to .cuga folder when added/updated (default: True)

    Attributes:
        graph: The underlying LangGraph StateGraph (compiled)
        tool_provider: The tool provider interface being used
        cuga_folder: Path to the .cuga folder

    Note:
        When auto_load_policies=True, policies are loaded lazily on first invoke() or stream() call.
        To load policies immediately after agent creation, call await agent.initialize().

    Example:
        ```python
        from cuga.sdk import CugaAgent
        from langchain_core.tools import tool

        @tool
        def get_weather(city: str) -> str:
            '''Get weather for a city'''
            return f"Weather in {city}: Sunny, 72°F"

        # Load policies from .cuga folder automatically with filesystem sync
        agent = CugaAgent(
            tools=[get_weather],
            cuga_folder=".cuga",
            auto_load_policies=True,
            filesystem_sync=True  # Auto-save policies to .cuga when added/updated
        )
        # Policies will be loaded on first invoke() or stream() call
        # Or explicitly: await agent.initialize()

        # Or with fresh/clean policy storage
        agent = CugaAgent(
            tools=[get_weather],
            cuga_folder=".cuga",
            auto_load_policies=True,
            reset_policy_storage=True,  # Clears all existing policies first
            filesystem_sync=True
        )

        # Or load manually later (filesystem sync still works)
        agent = CugaAgent(tools=[get_weather], filesystem_sync=True)
        await agent.policies.load_from_folder(".cuga")

        # Add a policy - it will be saved to .cuga/playbooks/ automatically
        await agent.policies.add_playbook(
            name="Search Strategy",
            keywords=["search", "find"],
            content="# Search Strategy\n\n..."
        )

        result = await agent.invoke("What's the weather in San Francisco?")
        print(result)  # Agent will use the tool and return an answer
        ```
    """

    def __init__(
        self,
        tools: Optional[List[BaseTool]] = None,
        tool_provider: Optional[ToolProviderInterface] = None,
        model: Optional[BaseChatModel] = None,
        callbacks: Optional[List[BaseCallbackHandler]] = None,
        policy_system: Optional[PolicyConfigurable] = None,
        special_instructions: Optional[str] = None,
        cuga_folder: Optional[str] = None,
        auto_load_policies: Optional[bool] = None,
        reset_policy_storage: bool = False,
        filesystem_sync: Optional[bool] = None,
    ):
        """
        Initialize the CUGA Agent.

        Args:
            tools: List of LangChain tools (BaseTool or @tool decorated functions)
            tool_provider: Custom tool provider (overrides tools parameter)
            model: Language model to use (defaults to configured model)
            callbacks: List of callback handlers
            policy_system: Optional PolicyConfigurable instance (auto-created if not provided)
            special_instructions: Optional special instructions to add to the agent's system prompt
            cuga_folder: Path to .cuga folder containing policy markdown files
            auto_load_policies: If True, automatically loads policies from cuga_folder
            reset_policy_storage: If True, clears all existing policies from storage on init
            filesystem_sync: If True, saves policies to .cuga when added/updated (default: True)

        Example with tool approval policy:
            ```python
            agent = CugaAgent(tools=[my_tool])

            # Add a tool approval policy
            await agent.policies.add_tool_approval(
                name="Approve Sensitive Tools",
                required_tools=["delete_database"]
            )

            # Invoke - will interrupt if approval needed
            config = {"configurable": {"thread_id": "user-123"}}
            result = await agent.graph.ainvoke({"input": "Delete all data"}, config)

            # Check if interrupted
            state = agent.graph.get_state(config)
            if state.next:  # Has pending nodes = interrupted
                # Handle approval (see docs for full example)
                agent.graph.update_state(config, {"hitl_response": {...}})
                result = await agent.graph.ainvoke(None, config)  # Resume
            ```
        """
        # Load settings
        from cuga.config import settings

        self._model = model
        self._callbacks = callbacks
        self._graph = None
        self._compiled_graph = None
        self._policy_system = policy_system
        self._special_instructions = special_instructions

        # Use settings defaults if not provided
        self.cuga_folder = cuga_folder if cuga_folder is not None else settings.policy.cuga_folder
        self._auto_load_policies = (
            auto_load_policies if auto_load_policies is not None else settings.policy.auto_load_policies
        )
        self._filesystem_sync = (
            filesystem_sync if filesystem_sync is not None else settings.policy.filesystem_sync
        )
        self._reset_policy_storage = reset_policy_storage

        # Setup tool provider
        if tool_provider:
            self.tool_provider = tool_provider
            logger.info("Using custom tool provider")
        elif tools:
            self.tool_provider = DirectLangChainToolsProvider(tools=tools, app_name="runtime_tools")
            logger.info(f"Created DirectLangChainToolsProvider with {len(tools)} tools")
        else:
            self.tool_provider = DirectLangChainToolsProvider(tools=[], app_name="runtime_tools")
            logger.warning("No tools provided - agent will have limited capabilities")

        # Initialize model
        if not self._model:
            from cuga.config import settings

            llm_manager = LLMManager()
            self._model = llm_manager.get_model(settings.agent.code.model)
            logger.info(f"Using default model: {self._model.__class__.__name__}")

        # Initialize policies manager (cached instance)
        self._policies_manager = None

    async def initialize(self):
        """
        Initialize the agent asynchronously.

        This method should be called after creating the agent to ensure:
        - Tool provider is initialized
        - Policy system is initialized (if auto_load_policies=True)
        - Policies are loaded from .cuga folder (if auto_load_policies=True)

        Example:
            ```python
            agent = CugaAgent(
                tools=[my_tool],
                auto_load_policies=True,
                cuga_folder=".cuga"
            )
            await agent.initialize()  # Trigger policy loading
            ```
        """
        # Initialize tool provider
        await self._ensure_initialized()

        # Initialize policy system (triggers auto-load if enabled)
        if self._auto_load_policies or self._reset_policy_storage:
            await self.policies._ensure_policy_system()
            logger.debug("Policy system initialized during agent.initialize()")

    async def _ensure_initialized(self):
        """Ensure tool provider is initialized."""
        if not hasattr(self.tool_provider, 'initialized') or not self.tool_provider.initialized:
            await self.tool_provider.initialize()

    def _create_graph(self, thread_id: Optional[str] = None):
        """Create the LangGraph graph with HITL support."""
        if self._graph is None:
            # Always create wrapper graph with HITL nodes (for policy support)
            self._graph = self._create_hitl_wrapper_graph(thread_id)
            logger.debug("Created CugaLite graph with HITL wrapper")
        return self._graph

    def _create_hitl_wrapper_graph(self, thread_id: Optional[str] = None):
        """Create a wrapper graph with HITL nodes around CugaLite subgraph.

        Graph structure (simplified for SDK):
        START -> CugaLiteSubgraph -> SDKCallback

        SDKCallback routes via Command:
        - If hitl_action exists -> SuggestHumanActions -> WaitForResponse (interrupt)
        - WaitForResponse -> back to SDKCallback (via sender tracking)
        - SDKCallback handles response -> back to CugaLiteSubgraph or FinalAnswerAgent
        - Otherwise -> FinalAnswerAgent -> END

        Dummy nodes (APIPlannerAgent, ChatAgent, CugaLite) are added to support
        internal routing from CugaLiteSubgraph that references these nodes.
        """
        from cuga.backend.cuga_graph.nodes.human_in_the_loop.suggest_actions import SuggestHumanActions
        from cuga.backend.cuga_graph.nodes.human_in_the_loop.wait_for_response import WaitForResponse
        from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
        from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.final_answer_agent import (
            FinalAnswerAgent,
        )
        from cuga.backend.cuga_graph.utils.nodes_names import NodeNames, ActionIds
        from langgraph.types import Command
        from typing import Literal

        # Create CugaLite subgraph
        cuga_lite_subgraph = create_cuga_lite_graph(
            model=self._model,
            tool_provider=self.tool_provider,
            thread_id=thread_id,
            callbacks=self._callbacks,
            special_instructions=self._special_instructions,
        )
        # Compile subgraph without checkpointer so it streams internal updates
        compiled_subgraph = cuga_lite_subgraph.compile()

        # Dummy nodes to support internal CugaLiteSubgraph routing
        async def dummy_api_planner_node(state: AgentState) -> Command[Literal['SDKCallback']]:
            """Dummy APIPlannerAgent node - routes back to SDK callback."""
            logger.debug("Dummy APIPlannerAgent node - routing to SDKCallback")
            return Command(update=state.model_dump(), goto="SDKCallback")

        async def dummy_chat_agent_node(state: AgentState) -> Command[Literal['SDKCallback']]:
            """Dummy ChatAgent node - routes back to SDK callback."""
            logger.debug("Dummy ChatAgent node - routing to SDKCallback")
            return Command(update=state.model_dump(), goto="SDKCallback")

        async def dummy_cuga_lite_node(state: AgentState) -> Command[Literal['SDKCallback']]:
            """Dummy CugaLite node - routes back to SDK callback."""
            logger.debug("Dummy CugaLite node - routing to SDKCallback")
            return Command(update=state.model_dump(), goto="SDKCallback")

        # Create custom callback node for SDK (simpler than full CugaLiteNode)
        async def sdk_callback_node(
            state: AgentState,
            config: Optional[RunnableConfig] = None,
        ) -> Command[Literal['FinalAnswerAgent', 'SuggestHumanActions', 'CugaLiteSubgraph']]:
            """Process results after CugaLite subgraph execution (SDK version)."""
            logger.info("SDK callback node - processing subgraph results")

            # Handle human-in-the-loop responses (when coming back from WaitForResponse)
            if state.sender == NodeNames.WAIT_FOR_RESPONSE and state.hitl_response:
                logger.info(
                    f"Callback handling HITL response with action_id: {state.hitl_response.action_id}"
                )

                # Check if user approved or denied
                confirmed = state.hitl_response.confirmed

                if confirmed:
                    logger.info("User approved tool execution - continuing with code execution")
                    # Clear the approval requirement and continue execution
                    state.cuga_lite_metadata = {
                        **state.cuga_lite_metadata,
                        "approval_required": False,
                        "user_approved": True,
                    }
                    state.sender = "SDKCallback"
                    # Route back to CugaLite subgraph to continue execution
                    return Command(update=state.model_dump(), goto="CugaLiteSubgraph")
                else:
                    logger.warning("User denied tool execution - stopping execution")
                    # User denied - set final answer and end
                    policy_name = state.cuga_lite_metadata.get("policy_name", "Tool Approval Policy")
                    state.final_answer = f"❌ **Execution Cancelled**\n\nYou denied the execution of restricted tools required by **{policy_name}**.\n\nThe agent will not proceed with this task."
                    state.execution_complete = True
                    # Set sender to CugaLite so FinalAnswerAgent handles it properly
                    state.sender = NodeNames.CUGA_LITE
                    return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)

            # Check if we need to route to HITL for tool approval (first time, after subgraph)
            if state.hitl_action and state.hitl_action.action_id == ActionIds.TOOL_APPROVAL:
                logger.info("Tool approval required - routing to SuggestHumanActions")
                # IMPORTANT: Set sender so WaitForResponse knows where to return to
                state.sender = "SDKCallback"
                logger.info(f"Set sender to: {state.sender}")
                return Command(
                    update=state.model_dump(),
                    goto=NodeNames.SUGGEST_HUMAN_ACTIONS,
                )

            # Check for OutputFormatter policies before finalizing response
            from cuga.backend.cuga_graph.policy.output_formatter_utils import apply_output_formatter_policies

            await apply_output_formatter_policies(state, config, context="SDK Callback")

            # Otherwise, route to FinalAnswerAgent
            # Set sender to CugaLite so FinalAnswerAgent handles it properly (see final_answer.py line 106)
            answer = state.final_answer or "No answer found"
            logger.info(f"Routing to FinalAnswerAgent with answer: {answer}")
            state.sender = NodeNames.CUGA_LITE
            return Command(
                update=state.model_dump(),
                goto=NodeNames.FINAL_ANSWER_AGENT,
            )

        # Create nodes
        suggest_actions = SuggestHumanActions()
        wait_for_response = WaitForResponse()
        final_answer_node = FinalAnswerNode(FinalAnswerAgent.create())

        # Create wrapper graph using AgentState (compatible with HITL nodes)
        wrapper = StateGraph(AgentState)

        # Add nodes
        wrapper.add_node("CugaLiteSubgraph", compiled_subgraph)
        wrapper.add_node("SDKCallback", sdk_callback_node)
        wrapper.add_node(suggest_actions.name, suggest_actions.node)
        wrapper.add_node(wait_for_response.name, wait_for_response.node)
        wrapper.add_node(final_answer_node.final_answer_agent.name, final_answer_node.node)

        # Add dummy nodes for internal CugaLiteSubgraph routing
        wrapper.add_node(NodeNames.API_PLANNER_AGENT, dummy_api_planner_node)
        wrapper.add_node(NodeNames.CHAT_AGENT, dummy_chat_agent_node)
        wrapper.add_node(NodeNames.CUGA_LITE, dummy_cuga_lite_node)

        # Add static edges (routing is done via Command objects in nodes)
        wrapper.add_edge(START, "CugaLiteSubgraph")
        wrapper.add_edge("CugaLiteSubgraph", "SDKCallback")
        # SDKCallback routes via Command to:
        #   - SuggestHumanActions (if hitl_action)
        #   - CugaLiteSubgraph (after approval)
        #   - FinalAnswerAgent (if complete)
        # SuggestHumanActions routes to WaitForResponse via Command
        # WaitForResponse routes back to sender (SDKCallback) via Command
        wrapper.add_edge(final_answer_node.final_answer_agent.name, END)

        logger.debug("Created HITL wrapper graph with SuggestHumanActions and WaitForResponse")
        return wrapper

    @property
    def policies(self) -> PoliciesManager:
        """
        Get the policies manager for this agent.

        Provides methods to add, remove, and manage policies.

        Returns:
            PoliciesManager instance

        Example:
            ```python
            agent = CugaAgent(tools=[my_tool])

            # Add an intent guard
            await agent.policies.add_intent_guard(
                name="Block Delete",
                keywords=["delete", "remove"]
            )

            # List all policies
            policies = await agent.policies.list()
            ```
        """
        if self._policies_manager is None:
            self._policies_manager = PoliciesManager(self)
        return self._policies_manager

    @property
    def graph(self):
        """
        Get the underlying LangGraph StateGraph (compiled).

        This allows advanced users to interact with the graph directly,
        use custom checkpointers, or integrate with LangGraph Cloud.

        Returns:
            Compiled LangGraph graph

        Example:
            ```python
            agent = CugaAgent(tools=[my_tool])
            compiled_graph = agent.graph

            # Simple usage
            result = await compiled_graph.ainvoke(
                {"chat_messages": [HumanMessage(content="Hello")]},
                config={"configurable": {"thread_id": "user-123"}},
            )

            # With tool approval policy
            await agent.policies.add_tool_approval(
                name="Approve Sensitive Tools",
                required_tools=["delete_database"]
            )

            config = {"configurable": {"thread_id": "user-123"}}
            result = await agent.graph.ainvoke({"input": "Run tool"}, config)

            # Check if interrupted for approval
            state = agent.graph.get_state(config)
            if state.next:  # Interrupted
                # Handle approval and resume
                agent.graph.update_state(config, {"hitl_response": approval})
                result = await agent.graph.ainvoke(None, config)
            ```
        """
        if self._compiled_graph is None:
            graph = self._create_graph()

            # Always compile with checkpointer and interrupt for HITL support
            checkpointer = MemorySaver()
            self._compiled_graph = graph.compile(
                checkpointer=checkpointer,
                interrupt_before=["WaitForResponse"],  # Interrupt before waiting for user
            )
            logger.debug("Compiled graph with checkpointer and HITL support")

        return self._compiled_graph

    async def invoke(
        self,
        message: Union[str, List[BaseMessage], None] = None,
        thread_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        action_response: Optional[Any] = None,
        user_context: Optional[str] = None,
        track_tool_calls: bool = False,
        variables: Optional[Dict[str, Any]] = None,
    ) -> InvokeResult:
        """
        Invoke the agent with a message and get the response.

        This method handles message formatting, graph execution, and response extraction.
        Can also resume execution after a human-in-the-loop interaction.

        Args:
            message: User message (string), list of messages, or None to resume execution
            thread_id: Thread ID (required for resume, auto-generated for new conversations)
            config: Optional LangGraph config (for advanced usage)
            action_response: Optional ActionResponse for resuming after approval/interruption
            track_tool_calls: If True, tracks all tool calls with metadata (name, arguments,
                result, operation_id, duration_ms, etc.) and returns them in result.tool_calls
            variables: Optional dict of variables to make available in the agent's context.
                Used when delegating from a supervisor to pass context to sub-agents.

        Returns:
            InvokeResult containing:
            - answer: The agent's final answer
            - tool_calls: List of tool calls made (when track_tool_calls=True)
            - thread_id: Thread ID used for this invocation
            - error: Error message if execution failed

        Example:
            ```python
            # Simple single-turn with tool call tracking
            result = await agent.invoke("What's 2+2?", track_tool_calls=True)
            print(result.answer)  # Access the answer
            print(result.tool_calls)  # Access tool calls

            # The result also converts to string for backward compatibility
            print(result)  # Prints the answer

            # Access tool calls with operation_id (original OpenAPI operationId)
            for call in result.tool_calls:
                print(f"Tool: {call['name']}, Operation ID: {call.get('operation_id')}")

            # Multi-turn conversation
            messages = [
                HumanMessage(content="My name is Alice"),
                AIMessage(content="Nice to meet you, Alice!"),
                HumanMessage(content="What's my name?"),
            ]
            result = await agent.invoke(messages)

            # Resume after approval
            from datetime import datetime
            from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
                ActionResponse, ActionType
            )
            approval = ActionResponse(
                action_id="tool_approval",
                response_type=ActionType.CONFIRMATION,
                confirmed=True,
                timestamp=datetime.now().isoformat(),
            )
            result = await agent.invoke(None, thread_id="user-123", action_response=approval)
            ```
        """
        await self._ensure_initialized()

        # Initialize policy system if auto_load_policies is enabled and not yet initialized
        # This ensures policies are loaded before first invocation
        if self._auto_load_policies and (not hasattr(self, '_policy_system') or self._policy_system is None):
            await self.policies._ensure_policy_system()
            logger.debug("Policy system auto-initialized during first invoke()")

        # Setup config
        run_config = config or {}
        if "configurable" not in run_config:
            run_config["configurable"] = {}

        # Pass track_tool_calls flag via configurable
        run_config["configurable"]["track_tool_calls"] = track_tool_calls

        # Ensure graph is created (needed for state retrieval)
        _ = self.graph

        # Handle resume case (message is None or action_response is provided)
        if message is None or action_response is not None:
            if not thread_id:
                raise ValueError(
                    "thread_id is required when resuming execution (message=None or action_response provided)"
                )

            run_config["configurable"]["thread_id"] = thread_id

            # Add policy system to config if available
            if self._policy_system:
                run_config["configurable"]["policy_system"] = self._policy_system

            # Add callbacks to config (both top-level and configurable for nodes)

            # If action_response provided, update state with it
            if action_response:
                self.graph.update_state(run_config, {"hitl_response": action_response})
                logger.info(
                    f"Resuming execution after HITL response (action_id: {action_response.action_id})"
                )

            # Resume by invoking with None (LangGraph pattern for resuming)
            result = await self.graph.ainvoke(None, config=run_config)

            # Extract final answer
            final_answer = result.get("final_answer", "")

            error_msg = None
            if not final_answer and result.get("error"):
                error_msg = result['error']
                final_answer = f"Error: {error_msg}"

            # Check if graph interrupted again
            if not final_answer:
                try:
                    state = self.graph.get_state(run_config)
                    if state.next:  # Has pending nodes = interrupted again
                        logger.info("Graph interrupted again for human-in-the-loop interaction")
                        final_answer = (
                            "⏸️ Execution paused for approval. "
                            "Use agent.invoke(None, thread_id=..., action_response=...) to resume."
                        )
                except Exception as e:
                    logger.debug(f"Could not check interrupt state: {e}")

            # Get tool calls from result (only if tracking was enabled)
            tool_calls = result.get("tool_calls", []) if track_tool_calls else []

            return InvokeResult(
                answer=final_answer,
                tool_calls=tool_calls,
                thread_id=thread_id,
                error=error_msg,
            )

        # Normal invocation case
        # Convert message to list of BaseMessage
        if isinstance(message, str):
            new_messages = [HumanMessage(content=message)]
        else:
            new_messages = message

        # Auto-generate thread_id if not provided (required for checkpointer)
        if not thread_id:
            thread_id = f"sdk_{uuid.uuid4().hex[:8]}"
            logger.debug(f"Auto-generated thread_id: {thread_id}")

        # Setup config early to check for existing state
        run_config["configurable"]["thread_id"] = thread_id

        # Try to get existing state for this thread_id
        existing_state = None
        try:
            state_snapshot = self.graph.get_state(run_config)
            if state_snapshot and state_snapshot.values:
                existing_state = AgentState(**state_snapshot.values)
                logger.debug(f"Found existing state for thread_id: {thread_id}")
        except Exception as e:
            logger.debug(f"No existing state found for thread_id {thread_id}: {e}")

        # Build state: use existing state if available, otherwise create new
        if existing_state:
            # Append new messages to existing chat history
            existing_chat_messages = existing_state.chat_messages or []
            updated_chat_messages = existing_chat_messages + new_messages

            # Update existing state with new messages
            initial_state_dict = existing_state.model_dump()
            initial_state_dict["chat_messages"] = updated_chat_messages
            initial_state_dict["input"] = new_messages[-1].content if new_messages else ""

            # Update user_context (pi) if provided
            if user_context:
                initial_state_dict["pi"] = user_context

            # Inject variables if provided (from supervisor delegation)
            if variables:
                from cuga.backend.cuga_graph.state.agent_state import StateVariablesManager

                var_manager = StateVariablesManager(existing_state)
                for var_name, var_value in variables.items():
                    var_manager.add_variable(
                        var_value, name=var_name, description=f"Passed from supervisor: {var_name}"
                    )
                initial_state_dict["variables_storage"] = existing_state.variables_storage

            initial_state_pydantic = AgentState(**initial_state_dict)
            logger.debug(
                f"Appended {len(new_messages)} new message(s) to existing conversation "
                f"({len(existing_chat_messages)} existing messages)"
            )
        else:
            # Create new state for HITL wrapper graph (uses AgentState format)
            # The wrapper will pass this to CugaLiteSubgraph which expects CugaLiteState format
            initial_state = {
                "chat_messages": new_messages,
                "thread_id": thread_id,
                "pi": user_context,
                "input": new_messages[-1].content if new_messages else "",
                "url": "",  # Required by AgentState (used for web navigation, empty for SDK)
            }
            initial_state_pydantic = AgentState(**initial_state)

            # Inject variables if provided (from supervisor delegation)
            if variables:
                from cuga.backend.cuga_graph.state.agent_state import StateVariablesManager

                var_manager = StateVariablesManager(initial_state_pydantic)
                for var_name, var_value in variables.items():
                    var_manager.add_variable(
                        var_value, name=var_name, description=f"Passed from supervisor: {var_name}"
                    )
                logger.debug(f"Injected {len(variables)} variables into new state: {list(variables.keys())}")

            logger.debug(f"Created new state for thread_id: {thread_id}")

        # Add policy system to config if available
        if self._policy_system:
            run_config["configurable"]["policy_system"] = self._policy_system

        # Add callbacks to config (both top-level and configurable for nodes)
        if self._callbacks:
            run_config["callbacks"] = self._callbacks
            run_config["configurable"]["callbacks"] = self._callbacks
            logger.debug(
                f"Added {len(self._callbacks)} callback(s) to config: {[type(cb).__name__ for cb in self._callbacks]}"
            )

        # Invoke the graph
        total_messages = len(initial_state_pydantic.chat_messages or [])
        logger.debug(f"Invoking agent with {total_messages} total message(s) in conversation")
        result = await self.graph.ainvoke(initial_state_pydantic, config=run_config)

        # Extract final answer and error
        final_answer = result.get("final_answer", "")
        error_msg = None

        if not final_answer and result.get("error"):
            error_msg = result['error']
            final_answer = f"Error: {error_msg}"

        # Check if graph interrupted for approval
        if not final_answer:
            try:
                state = self.graph.get_state(run_config)
                if state.next:  # Has pending nodes = interrupted
                    logger.info("Graph interrupted for human-in-the-loop interaction")
                    final_answer = (
                        "⏸️ Execution paused for approval. "
                        "Use agent.graph.get_state() and agent.graph.update_state() to handle the interrupt."
                    )
            except Exception as e:
                logger.debug(f"Could not check interrupt state: {e}")

        # Get tool calls from result (only if tracking was enabled)
        tool_calls = result.get("tool_calls", []) if track_tool_calls else []

        return InvokeResult(
            answer=final_answer,
            tool_calls=tool_calls,
            thread_id=thread_id,
            error=error_msg,
        )

    async def stream(
        self,
        message: Union[str, List[BaseMessage], None] = None,
        thread_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        action_response: Optional[Any] = None,  # ActionResponse for resuming after HITL
    ):
        """
        Stream the agent's execution step by step.

        This method yields state updates as the agent processes the task,
        allowing you to monitor progress in real-time.
        Can also resume execution after a human-in-the-loop interaction.

        Args:
            message: User message (string), list of messages, or None to resume execution
            thread_id: Thread ID (required for resume, auto-generated for new conversations)
            config: Optional LangGraph config
            action_response: Optional ActionResponse for resuming after approval/interruption

        Yields:
            State updates as the agent executes

        Example:
            ```python
            # Stream normal execution
            async for state in agent.stream("Calculate 10 factorial"):
                print(f"Step: {state.get('step_count', 0)}")
                if state.get('script'):
                    print(f"Code: {state['script']}")

            # Stream resume after approval
            approval = ActionResponse(...)
            async for state in agent.stream(None, thread_id="user-123", action_response=approval):
                print(f"Resuming: {state}")
            ```
        """
        await self._ensure_initialized()

        # Initialize policy system if auto_load_policies is enabled and not yet initialized
        # This ensures policies are loaded before first stream
        if self._auto_load_policies and (not hasattr(self, '_policy_system') or self._policy_system is None):
            await self.policies._ensure_policy_system()
            logger.debug("Policy system auto-initialized during first stream()")

        # Setup config
        run_config = config or {}
        if "configurable" not in run_config:
            run_config["configurable"] = {}

        # Handle resume case (message is None or action_response is provided)
        if message is None or action_response is not None:
            if not thread_id:
                raise ValueError(
                    "thread_id is required when resuming execution (message=None or action_response provided)"
                )

            run_config["configurable"]["thread_id"] = thread_id

            # Add policy system to config if available
            if self._policy_system:
                run_config["configurable"]["policy_system"] = self._policy_system

            # Add callbacks to config (both top-level and configurable for nodes)
            if self._callbacks:
                run_config["callbacks"] = self._callbacks
                run_config["configurable"]["callbacks"] = self._callbacks

            # If action_response provided, update state with it
            if action_response:
                self.graph.update_state(run_config, {"hitl_response": action_response})
                logger.info(f"Streaming resume after HITL response (action_id: {action_response.action_id})")

            # Stream resume by invoking with None
            async for state in self.graph.astream(
                None,
                config=run_config,
                stream_mode="updates",
                subgraphs=True,
            ):
                yield state
            return

        # Normal streaming case
        # Convert message to list of BaseMessage
        if isinstance(message, str):
            messages = [HumanMessage(content=message)]
        else:
            messages = message

        # Auto-generate thread_id if not provided (required for checkpointer)
        if not thread_id:
            thread_id = f"sdk_{uuid.uuid4().hex[:8]}"
            logger.debug(f"Auto-generated thread_id: {thread_id}")

        # Create initial state for HITL wrapper graph (uses AgentState format)
        initial_state = {
            "chat_messages": messages,
            "thread_id": thread_id,
            "input": messages[-1].content if messages else "",
            "url": "",  # Required by AgentState (used for web navigation, empty for SDK)
        }

        run_config["configurable"]["thread_id"] = thread_id

        # Add policy system to config if available
        if self._policy_system:
            run_config["configurable"]["policy_system"] = self._policy_system

        # Add callbacks to config (both top-level and configurable for nodes)
        if self._callbacks:
            run_config["callbacks"] = self._callbacks
            run_config["configurable"]["callbacks"] = self._callbacks

        # Stream the graph with subgraph updates enabled
        logger.debug(f"Streaming agent with {len(messages)} messages")
        async for state in self.graph.astream(
            initial_state,
            config=run_config,
            stream_mode="updates",  # Stream node updates (including subgraph internals)
            subgraphs=True,  # Include subgraph updates
        ):
            yield state

    def add_tool(self, tool: BaseTool):
        """
        Add a tool to the agent dynamically.

        Note: This only works if using DirectLangChainToolsProvider.
        The graph will need to be recreated on next invocation.

        Args:
            tool: LangChain tool to add

        Example:
            ```python
            agent = CugaAgent(tools=[tool1])

            @tool
            def new_tool(x: int) -> int:
                '''A new tool'''
                return x * 2

            agent.add_tool(new_tool)
            result = await agent.invoke("Use new_tool with 5")
            ```
        """
        if isinstance(self.tool_provider, DirectLangChainToolsProvider):
            self.tool_provider.add_tool(tool)
            # Reset graph so it gets recreated with new tools
            self._graph = None
            self._compiled_graph = None
            logger.info(f"Added tool '{tool.name}' - graph will be recreated on next invocation")
        else:
            raise ValueError(
                "add_tool() only works with DirectLangChainToolsProvider. "
                "Use a custom tool provider for dynamic tool management."
            )

    def add_tools(self, tools: List[BaseTool]):
        """
        Add multiple tools to the agent dynamically.

        Args:
            tools: List of LangChain tools to add

        Example:
            ```python
            agent = CugaAgent()
            agent.add_tools([tool1, tool2, tool3])
            ```
        """
        for tool in tools:
            self.add_tool(tool)


class CugaSupervisor:
    """
    Supervisor for orchestrating multiple CugaAgent instances.

    Supports both internal CugaAgent instances and external A2A agents.

    Example (Programmatic):
        from cuga import CugaAgent, CugaSupervisor
        from cuga.backend.tools_env.registry import ToolRegistry

        # Create supervisor with model configuration
        supervisor = CugaSupervisor(
            model_provider="groq",
            model_name="openai/gpt-oss-120b",
            description="Supervisor for coordinating multiple agents"
        )

        # Add internal agents
        crm_agent = CugaAgent(
            name="crm_agent",
            description="CRM specialist",
            tool_provider=ToolRegistry.get_tool_provider(),
            apps=["crm"]
        )
        supervisor.add_agent("crm_agent", crm_agent)

        # Add external A2A agent
        supervisor.add_agent("external_agent", {
            "name": "external_agent",
            "type": "external",
            "description": "External agent via A2A",
            "config": {
                "a2a_protocol": {
                    "endpoint": "http://localhost:8000",
                    "transport": "http"
                }
            }
        })

        # Execute task
        result = await supervisor.invoke("Get customer data and send email")
        print(result.answer)

        # Access variables
        variables = supervisor.variables_manager.get_variables_summary()
        print(variables)

    Example (YAML):
        supervisor = await CugaSupervisor.from_yaml("supervisor_config.yaml")
        result = await supervisor.invoke("Complex task requiring multiple agents")
    """

    def __init__(
        self,
        agents: Optional[Dict[str, Union[CugaAgent, Dict[str, Any]]]] = None,
        model: Optional[BaseChatModel] = None,
        description: Optional[str] = None,
        callbacks: Optional[List[BaseCallbackHandler]] = None,
    ):
        """
        Initialize supervisor.

        Args:
            agents: Dict mapping agent names to:
                - CugaAgent instances (internal agents)
                - Dict with A2A config (external agents)
                If None, agents can be added later via add_agent()
            model: Optional supervisor model override (BaseChatModel instance)
            description: Optional supervisor description
            callbacks: Optional callback handlers
        """
        self._agents = agents or {}
        self._model = model
        self._description = description
        self._callbacks = callbacks
        self._graph = None
        self._compiled_graph = None
        self._supervisor_state = None

        # Initialize model from settings if not provided
        if not self._model:
            from cuga.config import settings

            llm_manager = LLMManager()
            self._model = llm_manager.get_model(settings.agent.code.model)
            logger.info(f"Using default model: {self._model.__class__.__name__}")

    @classmethod
    async def from_yaml(cls, yaml_path: str) -> "CugaSupervisor":
        """
        Load supervisor configuration from YAML file.

        Creates internal CugaAgent instances from YAML config.
        External agents are configured for A2A connection.

        Args:
            yaml_path: Path to YAML configuration file

        Returns:
            CugaSupervisor instance
        """
        from cuga.supervisor_utils.supervisor_config import load_supervisor_config

        config = await load_supervisor_config(yaml_path)

        return cls(
            agents=config.agents,
            model=None,
        )

    @property
    def graph(self):
        """
        Get the underlying LangGraph StateGraph (compiled).

        Returns:
            Compiled LangGraph graph
        """
        if self._compiled_graph is None:
            from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import (
                create_cuga_supervisor_graph,
            )
            from langgraph.checkpoint.memory import MemorySaver

            # Create supervisor subgraph
            supervisor_subgraph = create_cuga_supervisor_graph(
                supervisor_model=self._model,
                agents=self._agents,
            )

            # Compile with checkpointer
            checkpointer = MemorySaver()
            self._compiled_graph = supervisor_subgraph.compile(checkpointer=checkpointer)
            logger.debug("Compiled supervisor graph with checkpointer")

        return self._compiled_graph

    async def invoke(
        self,
        message: Optional[str],
        thread_id: Optional[str] = None,
        action_response: Optional[Any] = None,
    ) -> InvokeResult:
        """
        Invoke the supervisor with a message.

        Args:
            message: User message (string) or None to resume execution
            thread_id: Thread ID (required for resume, auto-generated for new conversations)
            action_response: Optional ActionResponse for resuming after approval/interruption

        Returns:
            InvokeResult containing answer and metadata
        """
        import uuid
        from langchain_core.messages import HumanMessage

        # Setup config
        if not thread_id:
            thread_id = f"supervisor_{uuid.uuid4().hex[:8]}"
            logger.debug(f"Auto-generated thread_id: {thread_id}")

        config = {"configurable": {"thread_id": thread_id}}
        if self._callbacks:
            config["callbacks"] = self._callbacks

        # Handle resume case
        if message is None or action_response is not None:
            if not thread_id:
                raise ValueError("thread_id is required when resuming execution")

            if action_response:
                self.graph.update_state(config, {"hitl_response": action_response})
                logger.info(
                    f"Resuming execution after HITL response (action_id: {action_response.action_id})"
                )

            result = await self.graph.ainvoke(None, config=config)
        else:
            # Normal invocation
            from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
                CugaSupervisorState,
            )

            initial_state = CugaSupervisorState(
                supervisor_chat_messages=[HumanMessage(content=message)],
                input=message,
                thread_id=thread_id,
                url="",  # Required by AgentState
            )

            result = await self.graph.ainvoke(initial_state, config=config)

        # Store state for variables_manager access
        self._supervisor_state = result if isinstance(result, dict) else result

        # Extract final answer
        final_answer = (
            result.get("final_answer", "")
            if isinstance(result, dict)
            else getattr(result, "final_answer", "")
        )
        error_msg = None

        if not final_answer and result.get("error"):
            error_msg = result["error"]
            final_answer = f"Error: {error_msg}"

        # Get tool calls if available
        tool_calls = (
            result.get("tool_calls", []) if isinstance(result, dict) else getattr(result, "tool_calls", [])
        )

        return InvokeResult(
            answer=final_answer,
            tool_calls=tool_calls,
            thread_id=thread_id,
            error=error_msg,
        )

    @property
    def variables_manager(self):
        """Get supervisor's variables manager for accessing collected variables."""
        if not self._supervisor_state:
            raise ValueError("No supervisor state available. Invoke the supervisor first.")

        # Use supervisor_variables_manager from state
        if isinstance(self._supervisor_state, dict):
            from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
                CugaSupervisorState,
            )

            state = CugaSupervisorState(**self._supervisor_state)
            return state.supervisor_variables_manager
        else:
            return self._supervisor_state.supervisor_variables_manager

    def add_agent(self, name: str, agent: Union[CugaAgent, Dict[str, Any]]) -> None:
        """
        Add an agent to the supervisor.

        Args:
            name: Agent name/identifier
            agent: CugaAgent instance (internal) or A2A config dict (external)
        """
        self._agents[name] = agent
        # Reset graph so it gets recreated with new agent
        self._graph = None
        self._compiled_graph = None
        logger.info(f"Added agent '{name}' - graph will be recreated on next invocation")

    def remove_agent(self, name: str) -> None:
        """Remove an agent from the supervisor."""
        if name in self._agents:
            del self._agents[name]
            # Reset graph so it gets recreated without removed agent
            self._graph = None
            self._compiled_graph = None
            logger.info(f"Removed agent '{name}' - graph will be recreated on next invocation")


# Convenience function for quick usage
async def run_agent(
    message: str,
    tools: Optional[List[BaseTool]] = None,
    model: Optional[BaseChatModel] = None,
) -> str:
    """
    Convenience function to quickly run an agent with a single message.

    This creates a new agent instance, runs it, and returns the result.
    For multiple invocations, create a CugaAgent instance instead.

    Args:
        message: User message to process
        tools: Optional list of tools
        model: Optional language model

    Returns:
        Agent's response as a string

    Example:
        ```python
        from cuga.sdk import run_agent
        from langchain_core.tools import tool

        @tool
        def calculator(expression: str) -> float:
            '''Evaluate a math expression'''
            return eval(expression)

        result = await run_agent(
            "What's 15 * 23?",
            tools=[calculator]
        )
        print(result)
        ```
    """
    agent = CugaAgent(tools=tools, model=model)
    return await agent.invoke(message)
