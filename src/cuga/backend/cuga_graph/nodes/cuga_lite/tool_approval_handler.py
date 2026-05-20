"""
Tool Approval Handler for CugaLite subgraph.

Handles the detection, interruption, and resumption of tool approval flows.
"""

from typing import TYPE_CHECKING, List, Optional
from loguru import logger

from langchain_core.messages import AIMessage

from langgraph.types import Command
from langgraph.graph import END


if TYPE_CHECKING:
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState


class ToolApprovalHandler:
    """Handles tool approval detection, interruption, and resumption logic."""

    @staticmethod
    def should_skip_policy_check(state: "CugaLiteState") -> bool:
        """
        Check if policy checking should be skipped.

        Returns True if we're returning from approval (user_approved=True),
        which preserves the approval state and prevents re-matching the same policy.

        Args:
            state: Current CugaLiteState

        Returns:
            True if policy check should be skipped, False otherwise
        """
        return bool(state.cuga_lite_metadata and state.cuga_lite_metadata.get("user_approved"))

    @staticmethod
    def is_returning_from_approval(state: "CugaLiteState") -> bool:
        """
        Check if we're returning from tool approval.

        Args:
            state: Current CugaLiteState

        Returns:
            True if returning from approval, False otherwise
        """
        return bool(state.cuga_lite_metadata and state.cuga_lite_metadata.get("user_approved") is True)

    @staticmethod
    def extract_approved_code(state: "CugaLiteState") -> Optional[str]:
        """
        Extract the approved code from the last AI message.

        Args:
            state: Current CugaLiteState with chat_messages

        Returns:
            Extracted code string, or None if not found
        """
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            extract_and_combine_codeblocks,
        )

        # Find the last AI message
        last_ai_message = None
        for msg in reversed(state.chat_messages):
            if msg.type == "ai":
                last_ai_message = msg
                break

        if not last_ai_message or not last_ai_message.content:
            return None

        # Extract code from the message
        code = extract_and_combine_codeblocks(last_ai_message.content)
        if code:
            logger.info(f"Extracted approved code from last AI message: {len(code)} chars")
            return code

        return None

    @staticmethod
    def clean_approval_metadata(metadata: dict) -> dict:
        """
        Clean approval-related fields from metadata.

        Removes temporary approval fields to avoid interference with future executions.

        Args:
            metadata: Current metadata dictionary

        Returns:
            Cleaned metadata dictionary
        """
        fields_to_remove = [
            "approval_required",
            "user_approved",
            "required_tools",
            "required_apps",
            "full_code",
            "code_preview",
        ]

        return {k: v for k, v in metadata.items() if k not in fields_to_remove}

    @staticmethod
    def handle_approval_resumption(state: "CugaLiteState") -> Optional[Command]:
        """
        Handle resumption after user approval.

        Extracts the approved code and routes to sandbox for execution.

        Args:
            state: Current CugaLiteState

        Returns:
            Command to route to sandbox, or error Command if code extraction fails
        """
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            create_error_command,
        )

        logger.info("Returning from tool approval - skipping code generation, executing approved code")

        # Extract code from last AI message
        code = ToolApprovalHandler.extract_approved_code(state)

        if not code:
            logger.error("Could not extract code from last AI message after approval")
            return create_error_command(
                state.chat_messages,
                AIMessage(content="Failed to retrieve approved code for execution"),
                state.step_count,
            )

        # Clean approval metadata
        cleaned_metadata = ToolApprovalHandler.clean_approval_metadata(state.cuga_lite_metadata)

        # Route to sandbox with approved code
        return Command(
            goto="sandbox",
            update={
                "script": code,
                "cuga_lite_metadata": cleaned_metadata,
                "step_count": state.step_count + 1,
            },
        )

    @staticmethod
    async def check_and_create_approval_interrupt(
        state: "CugaLiteState",
        code: str,
        content: str,
        config: dict = None,
    ) -> Optional[Command]:
        """
        Check if code requires approval and create interrupt if needed.

        This method checks ToolApproval policies directly against the generated code,
        independent of the initial policy matching phase.

        Args:
            state: Current CugaLiteState
            code: Generated code to check
            content: Full AI response content
            config: Optional config containing policy system

        Returns:
            Command to interrupt for approval, or None if no approval needed
        """
        from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable

        try:
            logger.debug(f"Checking if code requires tool approval (code length: {len(code)} chars)")

            # Get policy system from config
            policy_system = PolicyConfigurable.from_config(config or {})
            logger.debug(f"Got policy system: {policy_system}")

            # Create context from state
            context = PolicyConfigurable.create_context_from_state(state, config or {})
            logger.debug(f"Created context with user_input: '{context.user_input}'")

            # Check if any ToolApproval policies apply to this code
            policy_match = await policy_system.agent.check_tool_approval_for_code(code, context)
            logger.debug(f"Policy match result: {policy_match}")

            if not policy_match or not policy_match.matched:
                logger.debug("No ToolApproval policy matched the generated code")
                return None

            policy = policy_match.policy
            logger.warning(f"Tool approval required by policy '{policy.name}' - routing to HITL")

            code_lines = code.split("\n")
            preview_lines = code_lines

            # Store policy metadata for the approval flow
            approval_metadata = {
                **state.cuga_lite_metadata,
                "policy_type": "tool_approval",
                "policy_id": policy.id,
                "policy_name": policy.name,
                "required_tools": policy.required_tools,
                "required_apps": policy.required_apps,
                "approval_message": policy.approval_message
                or "This tool requires your approval before execution.",
                "show_code_preview": policy.show_code_preview,
            }

            # Update state metadata temporarily for the interrupt creation
            state.cuga_lite_metadata = approval_metadata

            # Create the approval interrupt
            return ToolApprovalHandler._create_approval_interrupt(state, code, content, preview_lines)

        except Exception as e:
            logger.error(f"Error checking tool approval policies: {e}", exc_info=True)
            return None

    @staticmethod
    def _create_approval_interrupt(
        state: "CugaLiteState",
        code: str,
        content: str,
        preview_lines: List[str],
    ) -> Command:
        """
        Create an interrupt Command for tool approval.

        Args:
            state: Current CugaLiteState
            code: Generated code
            content: Full AI response content
            preview_lines: Code preview lines to show user

        Returns:
            Command to exit subgraph and route to HITL
        """
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            append_chat_messages_with_step_limit,
            create_error_command,
        )
        from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import create_tool_approval_action

        # Create approval request metadata
        approval_metadata = {
            **state.cuga_lite_metadata,
            "approval_required": True,
            "code_preview": preview_lines,
            "full_code": code if state.cuga_lite_metadata.get("show_code_preview") else None,
        }

        # Extract policy details
        policy_name = state.cuga_lite_metadata.get("policy_name", "Tool Approval")
        approval_msg = state.cuga_lite_metadata.get(
            "approval_message", "This tool requires your approval before execution."
        )
        tools_list = state.cuga_lite_metadata.get("required_tools", [])
        apps_list = state.cuga_lite_metadata.get("required_apps", [])

        # Create HITL action for tool approval
        hitl_action = create_tool_approval_action(
            policy_name=policy_name,
            required_tools=tools_list,
            code_preview=preview_lines,
            full_code=code,
            approval_message=approval_msg,
        )

        # Generate user-friendly markdown message
        final_answer_text = ToolApprovalHandler._generate_approval_message(
            policy_name=policy_name,
            approval_msg=approval_msg,
            tools_list=tools_list,
            apps_list=apps_list,
            preview_lines=preview_lines,
        )

        # Update messages
        updated_messages, error_message = append_chat_messages_with_step_limit(
            state, [AIMessage(content=content)]
        )
        if error_message:
            return create_error_command(updated_messages, error_message, state.step_count)

        # Return command to exit subgraph and route to parent's SuggestHumanActions -> WaitForResponse
        return Command(
            goto=END,  # Exit subgraph to parent CugaLiteNode.callback_node
            update={
                "chat_messages": updated_messages,
                "script": code,
                "final_answer": final_answer_text,
                "cuga_lite_metadata": approval_metadata,
                "hitl_action": hitl_action,  # Set HITL action for parent to detect
                "sender": "CugaLite",  # Mark sender for return routing
                "step_count": state.step_count + 1,
            },
        )

    @staticmethod
    def _generate_approval_message(
        policy_name: str,
        approval_msg: str,
        tools_list: List[str],
        apps_list: List[str],
        preview_lines: List[str],
    ) -> str:
        """
        Generate user-friendly markdown message for approval request.

        Args:
            policy_name: Name of the policy
            approval_msg: Approval message from policy
            tools_list: List of tools requiring approval
            apps_list: List of apps requiring approval
            preview_lines: Code preview lines

        Returns:
            Formatted markdown string
        """
        content_lines = [f"## ✋ {policy_name}", "", approval_msg, ""]

        if tools_list:
            if tools_list == ["*"]:
                content_lines.append("**Tools requiring approval:** All tools")
            else:
                content_lines.append(f"**Tools requiring approval:** {', '.join(tools_list)}")
            content_lines.append("")

        if apps_list:
            content_lines.append(f"**Apps requiring approval:** {', '.join(apps_list)}")
            content_lines.append("")

        if preview_lines:
            content_lines.append("### Code Preview")
            content_lines.append("")
            content_lines.append("```python")
            content_lines.extend(preview_lines)
            content_lines.append("```")
            content_lines.append("")

        content_lines.append("---")
        content_lines.append("*Please review and approve to continue execution.*")

        return "\n".join(content_lines)

    @staticmethod
    def handle_denial(state: "CugaLiteState") -> Optional[Command]:
        """
        Handle user denial of tool approval.

        Args:
            state: Current CugaLiteState

        Returns:
            Command to end execution, or None if not denied
        """
        if state.cuga_lite_metadata.get("user_approved") is False:
            logger.warning("User denied tool approval - skipping execution")
            return Command(
                goto=END,
                update={
                    "execution_complete": True,
                    "final_answer": "Execution cancelled by user.",
                    "step_count": state.step_count + 1,
                },
            )
        return None
