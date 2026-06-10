"""Policy enactment helpers for applying policy actions in graph nodes."""

from typing import Any, Dict, List, Optional
from copy import deepcopy

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger

from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.models import (
    OutputFormatter,
    PolicyActionType,
    PolicyMatch,
    PolicyType,
    Playbook,
)
from cuga.config import settings


class PolicyEnactment:
    """Static helper class for enacting policy decisions in graph nodes."""

    @staticmethod
    async def check_and_enact(
        state: AgentState,
        config: Optional[RunnableConfig] = None,
        policy_types: Optional[List[PolicyType]] = None,
        adapter: Any = None,
    ) -> tuple[Optional[Command], Optional[Dict[str, Any]]]:
        """
        Check for applicable policies and return enactment command or metadata.

        This method checks for:
        1. Intent Guards and Playbooks (mutually exclusive - highest priority match wins)
        2. Tool Guide policies (applied independently, can have multiple matches)
        3. OutputFormatter policies (when policy_types includes PolicyType.OUTPUT_FORMATTER)

        The target is automatically inferred from policy_types:
        - If OUTPUT_FORMATTER is in policy_types → target = "agent_response"
        - If INTENT_GUARD or PLAYBOOK is in policy_types → target = "intent"
        - If policy_types is None → target = "intent" (default)

        Args:
            state: Current graph state (CugaLiteState or AgentState)
            config: LangGraph RunnableConfig
            policy_types: Optional list of policy types to filter by (e.g., [PolicyType.OUTPUT_FORMATTER]).
                         If None, defaults to [INTENT_GUARD, PLAYBOOK] with target="intent".

        Returns:
            Tuple of (command, metadata):
            - command: Command to execute (e.g., goto END for blocking), or None to continue
            - metadata: Metadata to merge into state.cuga_lite_metadata, or None
        """
        try:
            # Infer target from policy_types
            if policy_types and PolicyType.OUTPUT_FORMATTER in policy_types:
                target = "agent_response"
            elif policy_types and (
                PolicyType.INTENT_GUARD in policy_types or PolicyType.PLAYBOOK in policy_types
            ):
                target = "intent"
            else:
                # Default case: intent matching
                target = "intent"
                if policy_types is None:
                    policy_types = [PolicyType.INTENT_GUARD, PolicyType.PLAYBOOK]

            # Get policy system from config
            policy_system = PolicyConfigurable.from_config(config or {})
            logger.debug(f"PolicyEnactment: Got policy system: {policy_system}")

            # Create context from state
            # Note: create_context_from_state already extracts user_input without execution output
            # and get_target_text() automatically combines user_input + agent_response for "agent_response" target
            context = PolicyConfigurable.create_context_from_state(state, config or {})

            logger.debug(
                f"PolicyEnactment: Created context with user_input='{context.user_input}', inferred target='{target}'"
            )

            # Check for policies (Intent Guards/Playbooks or OutputFormatter based on policy_types)
            policy_match = await policy_system.match_policy(context, target=target, policy_types=policy_types)
            logger.debug(
                f"PolicyEnactment: Policy match result: matched={policy_match.matched}, reasoning={policy_match.reasoning}"
            )

            # Check for Tool Guide policies only if explicitly requested in policy_types
            # Guides are independent and can have multiple matches
            guide_matches = []
            if policy_types and PolicyType.TOOL_GUIDE in policy_types:
                guide_matches = await policy_system.agent.check_tool_guide_policies(context)
                logger.debug(f"PolicyEnactment: Found {len(guide_matches)} Tool Guide policies")

            # If a policy matched, enact it (may return a blocking command)
            command = None
            metadata = None

            if policy_match.matched:
                logger.info(
                    f"Policy matched: {policy_match.policy.name} (action: {policy_match.action.action_type})"
                )
                command, metadata = await PolicyEnactment._enact_policy_action(
                    state, policy_match, policy_system, context, adapter
                )

            # ALWAYS apply Tool Guide policies (merge metadata from all matches)
            # This should happen regardless of whether a playbook/intent guard matched
            # Skip for OutputFormatter checks
            if guide_matches:
                guide_metadata = PolicyEnactment._merge_guide_metadata(guide_matches)

                if metadata:
                    # Merge guide metadata with existing metadata
                    # IMPORTANT: Don't overwrite the main policy's type/name/id
                    # Guides are additive and should be stored separately
                    metadata["guides"] = guide_metadata.get("guides", [])
                    metadata["guide_policies"] = [
                        {
                            "policy_id": e.get("policy_id"),
                            "policy_name": e.get("policy_name"),
                        }
                        for e in guide_metadata.get("guides", [])
                    ]
                    # Ensure policy_type indicates guides are present
                    if metadata.get("policy_type") != "tool_guide":
                        metadata["has_guides"] = True
                else:
                    # No main policy matched, only guides apply
                    metadata = guide_metadata

                logger.info(f"Applied {len(guide_matches)} Tool Guide policies")
            elif metadata:
                # Even if no guides matched, ensure metadata structure is correct
                metadata["guides"] = []
                metadata["guide_policies"] = []

            # Return command (if any) and merged metadata
            return command, metadata

        except Exception as e:
            logger.warning(f"Policy check failed (continuing without policies): {e}", exc_info=True)
            return None, None

    @staticmethod
    def _merge_guide_metadata(guide_matches: List[PolicyMatch]) -> Dict[str, Any]:
        """
        Merge metadata from multiple Tool Guide policy matches.

        Args:
            guide_matches: List of PolicyMatch objects for Tool Guide policies

        Returns:
            Merged metadata dictionary with all guide information
        """
        if not guide_matches:
            return {}

        # Collect all guide configurations
        guides = []
        for match in guide_matches:
            guide_info = {
                "policy_id": match.policy.id,
                "policy_name": match.policy.name,
                "guide_content": match.action.content,
                "target_tools": match.action.modifications.get("target_tools", []),
                "target_apps": match.action.modifications.get("target_apps"),
                "prepend": match.action.modifications.get("prepend", False),
                "priority": match.policy.priority,
            }
            guides.append(guide_info)

        # Sort by priority (higher priority first)
        guides.sort(key=lambda x: x["priority"], reverse=True)

        return {
            "policy_type": "tool_guide",
            "guides": guides,  # List of all guide configurations
            "guide_count": len(guides),
        }

    @staticmethod
    async def _enact_policy_action(
        state: Any,
        policy_match: PolicyMatch,
        policy_system: PolicyConfigurable,
        context: Any,
        adapter: Any = None,
    ) -> tuple[Optional[Command], Optional[Dict[str, Any]]]:
        """
        Enact a specific policy action.

        Args:
            state: Current graph state
            policy_match: Matched policy with action
            policy_system: Policy system instance (for playbook refinement)
            context: PolicyContext (for playbook refinement)

        Returns:
            Tuple of (command, metadata)
        """
        action_type = policy_match.action.action_type

        if action_type == PolicyActionType.BLOCK_INTENT:
            return PolicyEnactment._enact_block_intent(state, policy_match, adapter)

        elif action_type == PolicyActionType.GUIDE_PROMPT:
            return await PolicyEnactment._enact_guide_prompt(state, policy_match, policy_system, context)

        elif action_type == PolicyActionType.MODIFY_TOOLS:
            return PolicyEnactment._enact_modify_tools(state, policy_match)

        elif action_type == PolicyActionType.INJECT_CONTEXT:
            return PolicyEnactment._enact_inject_context(state, policy_match)

        elif action_type == PolicyActionType.LOG_ONLY:
            return PolicyEnactment._enact_log_only(state, policy_match)

        elif action_type == PolicyActionType.TOOL_INJECT_DESCRIPTION:
            return PolicyEnactment._enact_tool_guide(state, policy_match)

        elif action_type == PolicyActionType.TOOL_REQUIRE_APPROVAL:
            return PolicyEnactment._enact_tool_approval(state, policy_match)

        elif action_type == PolicyActionType.FORMAT_OUTPUT:
            return await PolicyEnactment._enact_format_output(state, policy_match, policy_system, context)

        else:
            logger.warning(f"Unknown policy action type: {action_type}")
            return None, None

    @staticmethod
    def _enact_block_intent(
        state: Any, policy_match: PolicyMatch, adapter: Any = None
    ) -> tuple[Command, None]:
        """
        Block the intent and return immediately with guard response.

        Args:
            state: Current graph state
            policy_match: Matched policy

        Returns:
            Command to END with blocked response
        """
        logger.warning(f"Intent blocked by policy: {policy_match.policy.id}")

        blocked_message = AIMessage(content=policy_match.action.content)

        # adapter=None preserves the exact legacy Lite literals (back-compat
        # for the output-formatter caller and any non-adapter path).
        if adapter is not None:
            base_messages = adapter.get_messages(state)
            messages_key = adapter.messages_key
            metadata_key = adapter.metadata_key
        else:
            base_messages = state.chat_messages
            messages_key = "chat_messages"
            metadata_key = "cuga_lite_metadata"

        return (
            Command(
                goto=END,
                update={
                    messages_key: base_messages + [blocked_message],
                    "final_answer": policy_match.action.content,
                    "execution_complete": True,
                    metadata_key: {
                        "policy_blocked": True,
                        "policy_id": policy_match.policy.id,
                        "policy_name": policy_match.policy.name,
                        "policy_type": "intent_guard",
                        "policy_reasoning": policy_match.reasoning,
                        "policy_confidence": policy_match.confidence,
                        "response_content": policy_match.action.content,
                    },
                    "step_count": 0,
                },
            ),
            None,
        )

    @staticmethod
    async def _enact_guide_prompt(
        state: Any, policy_match: PolicyMatch, policy_system: PolicyConfigurable, context: Any
    ) -> tuple[None, Dict[str, Any]]:
        """
        Store playbook guidance for injection into prompt.

        If playbook_refine is enabled, refines the playbook based on user progress.

        Args:
            state: Current graph state
            policy_match: Matched policy
            policy_system: Policy system instance (for accessing agent)
            context: PolicyContext for refinement

        Returns:
            Metadata to store in state
        """
        playbook_guidance = policy_match.action.content
        playbook_content = policy_match.action.content

        # If playbook refinement is enabled and this is a playbook policy, refine it
        if (
            settings.policy.enabled
            and getattr(settings.policy, 'playbook_refine', False)
            and isinstance(policy_match.policy, Playbook)
            and policy_system.agent
        ):
            try:
                logger.info(f"Refining playbook based on user progress: {policy_match.policy.name}")
                enactment = await policy_system.agent.enact_playbook(policy_match.policy, context)
                playbook_guidance = enactment.refined_plan
                playbook_content = enactment.original_plan
                logger.info(
                    f"Playbook refined: {len(enactment.refined_plan)} chars (original: {len(enactment.original_plan)} chars)"
                )
            except Exception as e:
                logger.warning(f"Failed to refine playbook, using original: {e}")
                # Fall back to original content on error

        logger.info(f"Playbook guidance will be injected: {policy_match.policy.name}")

        metadata = {
            "policy_matched": True,
            "policy_id": policy_match.policy.id,
            "policy_name": policy_match.policy.name,
            "policy_type": "playbook",
            "policy_confidence": policy_match.confidence,
            "policy_reasoning": policy_match.reasoning,
            "playbook_guidance": playbook_guidance,
            "playbook_content": playbook_content,
            "playbook_steps": policy_match.action.modifications.get("steps", []),
        }

        return None, metadata

    @staticmethod
    def _enact_modify_tools(state: Any, policy_match: PolicyMatch) -> tuple[None, Dict[str, Any]]:
        """
        Store tool modifications for application during tool preparation.

        Args:
            state: Current graph state
            policy_match: Matched policy

        Returns:
            Metadata to store in state
        """
        logger.info(f"Tools will be modified by policy: {policy_match.policy.name}")

        modifications = policy_match.action.modifications
        metadata = {
            "policy_matched": True,
            "policy_id": policy_match.policy.id,
            "policy_name": policy_match.policy.name,
            "policy_type": "tool_restriction",
            "policy_confidence": policy_match.confidence,
            "remove_tools": modifications.get("remove_tools", []),
            "add_tools": modifications.get("add_tools", []),
            "restriction_message": modifications.get("add_message", ""),
        }

        return None, metadata

    @staticmethod
    def _enact_inject_context(state: Any, policy_match: PolicyMatch) -> tuple[None, Dict[str, Any]]:
        """
        Store context to inject into state.

        Args:
            state: Current graph state
            policy_match: Matched policy

        Returns:
            Metadata to store in state
        """
        logger.info(f"Context will be injected by policy: {policy_match.policy.name}")

        metadata = {
            "policy_matched": True,
            "policy_id": policy_match.policy.id,
            "policy_name": policy_match.policy.name,
            "policy_type": "context_injection",
            "policy_confidence": policy_match.confidence,
            "injected_context": policy_match.action.content,
            "context_modifications": policy_match.action.modifications,
        }

        return None, metadata

    @staticmethod
    def _enact_log_only(state: Any, policy_match: PolicyMatch) -> tuple[None, Dict[str, Any]]:
        """
        Log the policy match without taking action.

        Args:
            state: Current graph state
            policy_match: Matched policy

        Returns:
            Metadata to store in state
        """
        logger.info(f"Policy matched (log only): {policy_match.policy.name} - {policy_match.reasoning}")

        metadata = {
            "policy_matched": True,
            "policy_id": policy_match.policy.id,
            "policy_name": policy_match.policy.name,
            "policy_type": "log_only",
            "policy_confidence": policy_match.confidence,
            "policy_reasoning": policy_match.reasoning,
        }

        return None, metadata

    @staticmethod
    def inject_playbook_into_prompt(base_prompt: str, metadata: Optional[Dict[str, Any]]) -> str:
        """
        Inject playbook guidance into a system prompt.

        Args:
            base_prompt: Original system prompt
            metadata: State metadata containing playbook guidance

        Returns:
            Enhanced prompt with playbook guidance
        """
        if not metadata:
            return base_prompt

        playbook_guidance = metadata.get("playbook_guidance")
        if not playbook_guidance:
            return base_prompt

        logger.info("Injecting playbook guidance into system prompt")

        playbook_steps = metadata.get("playbook_steps", [])

        guidance_section = f"""

## Task Guidance (Playbook)

You have been provided with a step-by-step playbook for this task. Follow these steps carefully:

{playbook_guidance}

"""
        if playbook_steps:
            guidance_section += f"\nThis playbook contains {len(playbook_steps)} steps. Please follow them in order and inform the user of your progress.\n"

        return base_prompt + guidance_section

    @staticmethod
    def apply_tool_restrictions(tools: list, metadata: Optional[Dict[str, Any]]) -> list:
        """
        Apply tool restrictions from policy metadata.

        Args:
            tools: List of available tools
            metadata: State metadata containing tool restrictions

        Returns:
            Filtered list of tools
        """
        if not metadata:
            return tools

        remove_tools = metadata.get("remove_tools", [])
        if not remove_tools:
            return tools

        logger.info(f"Removing {len(remove_tools)} tools based on policy: {remove_tools}")

        filtered_tools = [tool for tool in tools if tool.name not in remove_tools]

        return filtered_tools

    @staticmethod
    def get_restriction_message(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        Get restriction message from policy metadata.

        Args:
            metadata: State metadata

        Returns:
            Restriction message or None
        """
        if not metadata:
            return None

        return metadata.get("restriction_message")

    @staticmethod
    def _enact_tool_guide(state: Any, policy_match: PolicyMatch) -> tuple[None, Dict[str, Any]]:
        """
        Store tool guide metadata for application during tool preparation.

        Args:
            state: Current graph state
            policy_match: Matched policy

        Returns:
            Metadata to store in state
        """
        logger.info(f"Tool descriptions will be enriched by policy: {policy_match.policy.name}")

        modifications = policy_match.action.modifications
        metadata = {
            "policy_matched": True,
            "policy_id": policy_match.policy.id,
            "policy_name": policy_match.policy.name,
            "policy_type": "tool_guide",
            "policy_confidence": policy_match.confidence,
            "policy_reasoning": policy_match.reasoning,
            "guide_content": policy_match.action.content,
            "target_tools": modifications.get("target_tools", []),
            "target_apps": modifications.get("target_apps"),
            "prepend": modifications.get("prepend", False),
        }

        return None, metadata

    @staticmethod
    def _enact_tool_approval(state: Any, policy_match: PolicyMatch) -> tuple[None, Dict[str, Any]]:
        """
        Store tool approval requirements for application during tool execution.

        Args:
            state: Current graph state
            policy_match: Matched policy

        Returns:
            Metadata to store in state
        """
        logger.info(f"Tool approval will be required by policy: {policy_match.policy.name}")

        modifications = policy_match.action.modifications
        metadata = {
            "policy_matched": True,
            "policy_id": policy_match.policy.id,
            "policy_name": policy_match.policy.name,
            "policy_type": "tool_approval",
            "policy_confidence": policy_match.confidence,
            "policy_reasoning": policy_match.reasoning,
            "approval_message": policy_match.action.content,
            "required_tools": modifications.get("required_tools", []),
            "required_apps": modifications.get("required_apps"),
            "show_code_preview": modifications.get("show_code_preview", True),
            "auto_approve_after": modifications.get("auto_approve_after"),
        }

        return None, metadata

    @staticmethod
    def apply_tool_guide(tools: list, metadata: Optional[Dict[str, Any]]) -> list:
        """
        Apply tool description guide from policy metadata.

        Supports both single guide (legacy) and multiple guides (new).
        Guides can exist alongside playbooks or other policy types.

        IMPORTANT: This method creates copies of tools before enriching to avoid
        modifying cached tool objects that might be reused across turns.

        Args:
            tools: List of available tools
            metadata: State metadata containing guide info

        Returns:
            List of tools with enriched descriptions (copies, not originals)
        """
        logger.debug(f"Applying tool guide from policy: {metadata}")
        if not metadata:
            logger.debug("No metadata provided")
            return tools

        # Check if we have multiple guides (new format)
        # Guides can exist in metadata even when main policy_type is "playbook" or other
        guides = metadata.get("guides")

        # Also check for legacy single guide format
        has_legacy_guide = metadata.get("policy_type") == "tool_guide" or metadata.get("guide_content")

        # If no guides found in either format, return early
        if not guides and not has_legacy_guide:
            logger.debug("No tool guide metadata found in metadata")
            logger.debug(f"metadata keys: {list(metadata.keys()) if metadata else 'None'}")
            return tools

        # Create copies of tools to avoid modifying cached originals
        # This prevents guides from accumulating when tools are reused across turns
        enriched_tools = [deepcopy(tool) for tool in tools]

        if guides:
            # New format: multiple guides
            logger.info(f"Applying {len(guides)} tool guide policies")

            for tool in enriched_tools:
                # Apply each guide that matches this tool
                for guide_config in guides:
                    guide_content = guide_config.get("guide_content")
                    target_tools = guide_config.get("target_tools", [])
                    target_apps = guide_config.get("target_apps")
                    prepend = guide_config.get("prepend", False)

                    if not guide_content:
                        continue

                    # Check if this tool should be enriched
                    should_enrich = False

                    # Check if tool name matches
                    if "*" in target_tools or tool.name in target_tools:
                        should_enrich = True

                    # Check if tool's app matches (if tool has app metadata)
                    if target_apps and hasattr(tool, "metadata") and tool.metadata:
                        tool_app = tool.metadata.get("app_name")
                        if tool_app and tool_app in target_apps:
                            should_enrich = True

                    if should_enrich:
                        # Apply guide to tool description
                        original_desc = tool.description or ""

                        if prepend:
                            new_desc = f"{guide_content}\n\n{original_desc}"
                        else:
                            new_desc = f"{original_desc}\n\n{guide_content}"

                        tool.description = new_desc
                        logger.debug(
                            f"Enriched description for tool '{tool.name}' with policy '{guide_config.get('policy_name')}'"
                        )

            return enriched_tools

        else:
            # Legacy format: single guide (for backward compatibility)
            guide_content = metadata.get("guide_content")
            target_tools = metadata.get("target_tools", [])
            target_apps = metadata.get("target_apps")
            prepend = metadata.get("prepend", False)

            if not guide_content:
                return enriched_tools

            logger.info("Enriching tool descriptions (legacy format)")

            for tool in enriched_tools:
                # Check if this tool should be enriched
                should_enrich = False

                # Check if tool name matches
                if "*" in target_tools or tool.name in target_tools:
                    should_enrich = True

                # Check if tool's app matches (if tool has app metadata)
                if target_apps and hasattr(tool, "metadata"):
                    tool_app = tool.metadata.get("app_name")
                    if tool_app and tool_app in target_apps:
                        should_enrich = True

                if should_enrich:
                    # Create a copy of the tool with enriched description
                    original_desc = tool.description or ""

                    if prepend:
                        new_desc = f"{guide_content}\n\n{original_desc}"
                    else:
                        new_desc = f"{original_desc}\n\n{guide_content}"

                    # Update tool description
                    tool.description = new_desc
                    logger.debug(f"Enriched description for tool: {tool.name}")

            return tools

    @staticmethod
    def check_tool_approval_required(
        tool_name: str, app_name: Optional[str], metadata: Optional[Dict[str, Any]]
    ) -> bool:
        """
        Check if a tool requires approval based on policy metadata.

        Args:
            tool_name: Name of the tool to check
            app_name: Name of the app the tool belongs to (optional)
            metadata: State metadata containing approval requirements

        Returns:
            True if approval is required, False otherwise
        """
        if not metadata or metadata.get("policy_type") != "tool_approval":
            return False

        required_tools = metadata.get("required_tools", [])
        required_apps = metadata.get("required_apps")

        # Check if tool name matches
        if "*" in required_tools or tool_name in required_tools:
            return True

        # Check if tool's app matches
        if required_apps and app_name and app_name in required_apps:
            return True

        return False

    @staticmethod
    def check_code_for_tool_approval(code: str, metadata: Optional[Dict[str, Any]]) -> tuple[bool, List[str]]:
        """
        Check if generated code contains tools that require approval.

        Args:
            code: Generated Python code to check
            metadata: State metadata containing approval requirements

        Returns:
            Tuple of (approval_required, preview_lines):
            - approval_required: True if any required tools are found in code
            - preview_lines: List of code lines that use required tools
        """
        if not metadata or metadata.get("policy_type") != "tool_approval":
            return False, []

        required_tools = metadata.get("required_tools", [])
        required_apps = metadata.get("required_apps")

        if not required_tools and not required_apps:
            return False, []

        # Split code into lines for analysis
        code_lines = code.split("\n")
        preview_lines = []
        approval_required = False

        # Check each line for tool usage
        for i, line in enumerate(code_lines, 1):
            line_stripped = line.strip()

            # Skip comments and empty lines
            if not line_stripped or line_stripped.startswith("#"):
                continue

            # Check if line contains any required tools
            for tool_name in required_tools:
                if tool_name == "*":
                    # Wildcard - check for any function call
                    if "(" in line_stripped and not line_stripped.startswith("def "):
                        approval_required = True
                        preview_lines.append(f"Line {i}: {line}")
                        break
                elif tool_name in line_stripped:
                    # Check if it's actually a function call (not just in a string/comment)
                    if f"{tool_name}(" in line_stripped:
                        approval_required = True
                        preview_lines.append(f"Line {i}: {line}")
                        break

        logger.info(f"Tool approval check: required={approval_required}, found {len(preview_lines)} lines")

        return approval_required, preview_lines

    @staticmethod
    async def _enact_format_output(
        state: Any, policy_match: PolicyMatch, policy_system: PolicyConfigurable, context: Any
    ) -> tuple[None, Dict[str, Any]]:
        """
        Format the final AI message output using LLM.

        Args:
            state: Current graph state
            policy_match: Matched OutputFormatter policy
            policy_system: Policy system instance (for accessing LLM)
            context: PolicyContext with chat history

        Returns:
            Metadata with formatted response
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from cuga.backend.llm.models import LLMManager
        from cuga.backend.cuga_graph.utils.langfuse_tracing import get_langfuse_invoke_config

        lf_invoke_config = get_langfuse_invoke_config()

        logger.info(f"Formatting output using policy: {policy_match.policy.name}")

        policy = policy_match.policy
        if not isinstance(policy, OutputFormatter):
            logger.error("Policy is not an OutputFormatter")
            return None, None

        format_type = policy.format_type
        format_config = policy.format_config

        # Get the last AI message content
        last_ai_message = None
        if hasattr(state, "chat_messages") and state.chat_messages:
            # Find the last AI message
            for msg in reversed(state.chat_messages):
                if hasattr(msg, "content") and hasattr(msg, "__class__"):
                    from langchain_core.messages import AIMessage

                    if isinstance(msg, AIMessage):
                        last_ai_message = msg.content
                        break

        # Fallback to agent_response from context (for OutputFormatter)
        if not last_ai_message and hasattr(context, "agent_response") and context.agent_response:
            last_ai_message = context.agent_response
            logger.debug("Using agent_response from context as last AI message")

        # Fallback to final_answer from state
        if not last_ai_message and hasattr(state, "final_answer") and state.final_answer:
            last_ai_message = state.final_answer
            logger.debug("Using final_answer from state as last AI message")

        if not last_ai_message:
            logger.warning("No AI message found to format")
            return None, None

        # Get LLM for formatting
        llm_manager = LLMManager()
        llm = llm_manager.get_model(settings.agent.code.model)

        # Build chat history for context
        chat_history = []
        if context.chat_messages:
            # Include recent chat messages (last 10)
            for msg in context.chat_messages[-10:]:
                if isinstance(msg, str):
                    chat_history.append(HumanMessage(content=msg))
                elif hasattr(msg, "content"):
                    chat_history.append(msg)

        # Create formatting prompt based on format_type
        if format_type == "direct":
            # Direct answer: just return the format_config string as-is
            logger.info(
                f"Using direct answer format: returning format_config directly (length: {len(format_config)} chars)"
            )
            formatted_content = format_config

        elif format_type == "markdown":
            system_prompt = f"""You are an output formatter. Your task is to reformat the AI's response according to the following instructions:

{format_config}

Important:
- Preserve all factual information from the original response
- Only change formatting, structure, and presentation
- Do not add new information that wasn't in the original
- Do not remove important details
- Follow the formatting instructions exactly"""

            # Include user input if available
            user_input_section = ""
            if hasattr(context, "user_input") and context.user_input:
                user_input_section = f"User Input: {context.user_input}\n\n"

            user_prompt = f"""{user_input_section}Agent Response:
{last_ai_message}

Please reformat this response according to the instructions above."""

            messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            if chat_history:
                # Insert chat history before the user prompt
                messages = (
                    [SystemMessage(content=system_prompt)]
                    + chat_history
                    + [HumanMessage(content=user_prompt)]
                )

            formatted_response = await llm.ainvoke(messages, config=lf_invoke_config)
            formatted_content = formatted_response.content

        else:  # json_schema
            import json

            try:
                schema = json.loads(format_config)
                logger.debug(
                    f"Using JSON schema for structured output: {json.dumps(schema, indent=2)[:200]}..."
                )

                system_prompt = """You are an output formatter. Your task is to extract information from the AI's response and format it as JSON according to the provided schema.

Important:
- Extract all factual information from the original response
- Map it to the JSON schema structure
- Preserve all factual information
- Return valid JSON matching the schema exactly"""

                # Include user input if available
                user_input_section = ""
                if hasattr(context, "user_input") and context.user_input:
                    user_input_section = f"User Input: {context.user_input}\n\n"

                user_prompt = f"""{user_input_section}Agent Response:
{last_ai_message}

Extract and format the information from this response as JSON according to the schema."""

                messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
                if chat_history:
                    # Insert chat history before the user prompt
                    messages = (
                        [SystemMessage(content=system_prompt)]
                        + chat_history
                        + [HumanMessage(content=user_prompt)]
                    )

                # Use structured output with JSON schema
                try:
                    # Use with_structured_output with json_schema method
                    structured_llm = llm.with_structured_output(schema, method="json_schema")
                    formatted_response = await structured_llm.ainvoke(messages, config=lf_invoke_config)

                    # Structured output returns a dict, convert to JSON string
                    if isinstance(formatted_response, dict):
                        formatted_content = json.dumps(formatted_response, indent=2)
                    else:
                        formatted_content = str(formatted_response)

                    logger.info(
                        f"Formatted output using structured output with JSON schema (length: {len(formatted_content)} chars)"
                    )
                except Exception as e:
                    logger.warning(f"Structured output failed, falling back to regular LLM call: {e}")
                    # Fallback to regular prompt-based approach
                    system_prompt_fallback = f"""You are an output formatter. Your task is to reformat the AI's response as JSON according to this schema:

{json.dumps(schema, indent=2)}

Important:
- Extract all information from the original response
- Map it to the JSON schema structure
- Preserve all factual information
- Return ONLY valid JSON matching the schema"""

                    messages_fallback = [
                        SystemMessage(content=system_prompt_fallback),
                        HumanMessage(content=user_prompt),
                    ]
                    if chat_history:
                        messages_fallback = (
                            [SystemMessage(content=system_prompt_fallback)]
                            + chat_history
                            + [HumanMessage(content=user_prompt)]
                        )

                    formatted_response = await llm.ainvoke(messages_fallback, config=lf_invoke_config)
                    formatted_content = formatted_response.content

            except json.JSONDecodeError:
                logger.error(f"Invalid JSON schema in format_config: {format_config}")
                return None, None

        try:
            logger.info(
                f"Formatted output using policy '{policy.name}' (length: {len(formatted_content)} chars)"
            )

            metadata = {
                "policy_matched": True,
                "policy_id": policy.id,
                "policy_name": policy.name,
                "policy_type": "output_formatter",
                "policy_reasoning": policy_match.reasoning,
                "policy_confidence": policy_match.confidence,
                "formatted_response": formatted_content,
                "original_response": last_ai_message,
                "format_type": format_type,
            }

            return None, metadata

        except Exception as e:
            logger.error(f"Error formatting output: {e}", exc_info=True)
            return None, None
