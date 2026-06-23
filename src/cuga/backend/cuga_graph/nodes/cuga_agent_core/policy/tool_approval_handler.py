"""Tool approval detection, interruption, and resumption for agent graphs.

Shared by CugaLite and CugaSupervisor via :class:`CoreGraphAdapter` seams.
"""

from typing import Any, List, Optional

from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    CoreGraphAdapter,
    append_chat_messages_with_step_limit as _core_append_with_step_limit,
    create_error_command as _core_create_error_command,
)


class ToolApprovalHandler:
    """Handles tool approval detection, interruption, and resumption logic."""

    @staticmethod
    def should_skip_policy_check(adapter: CoreGraphAdapter, state: Any) -> bool:
        """
        Check if policy checking should be skipped.

        Returns True if we're returning from approval (user_approved=True),
        which preserves the approval state and prevents re-matching the same policy.
        """
        md = adapter.get_metadata(state)
        return bool(md and md.get("user_approved"))

    @staticmethod
    def is_returning_from_approval(adapter: CoreGraphAdapter, state: Any) -> bool:
        """Check if we're returning from tool approval."""
        md = adapter.get_metadata(state)
        return bool(md and md.get("user_approved") is True)

    @staticmethod
    def extract_approved_code(adapter: CoreGraphAdapter, state: Any) -> Optional[str]:
        """Extract the approved code from the last AI message."""
        from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
            extract_and_combine_codeblocks,
        )

        last_ai_message = None
        for msg in reversed(adapter.get_messages(state)):
            if msg.type == "ai":
                last_ai_message = msg
                break

        if not last_ai_message or not last_ai_message.content:
            return None

        code = extract_and_combine_codeblocks(last_ai_message.content)
        if code:
            logger.info(f"Extracted approved code from last AI message: {len(code)} chars")
            return code

        return None

    @staticmethod
    def clean_approval_metadata(metadata: dict) -> dict:
        """Remove temporary approval fields from metadata."""
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
    def handle_approval_resumption(adapter: CoreGraphAdapter, state: Any) -> Optional[Command]:
        """Handle resumption after user approval: run the approved code."""
        logger.info("Returning from tool approval - skipping code generation, executing approved code")

        code = getattr(state, "script", None) or ToolApprovalHandler.extract_approved_code(adapter, state)

        if not code:
            logger.error("Could not extract code from last AI message after approval")
            return _core_create_error_command(
                adapter,
                adapter.get_messages(state),
                AIMessage(content="Failed to retrieve approved code for execution"),
                state.step_count,
            )

        cleaned_metadata = ToolApprovalHandler.clean_approval_metadata(adapter.get_metadata(state))

        return Command(
            goto=adapter.execute_node_name,
            update={
                "script": code,
                adapter.metadata_key: cleaned_metadata,
                "step_count": state.step_count + 1,
            },
        )

    @staticmethod
    async def check_and_create_approval_interrupt(
        adapter: CoreGraphAdapter,
        state: Any,
        code: str,
        content: str,
        config: dict = None,
    ) -> Optional[Command]:
        """Check if code requires approval and create an interrupt if needed."""
        from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable

        try:
            logger.debug(f"Checking if code requires tool approval (code length: {len(code)} chars)")

            policy_system = PolicyConfigurable.from_config(config or {})
            logger.debug(f"Got policy system: {policy_system}")

            context = PolicyConfigurable.create_context_from_state(state, config or {})
            logger.debug(f"Created context with user_input: '{context.user_input}'")

            policy_match = await policy_system.agent.check_tool_approval_for_code(code, context)
            logger.debug(f"Policy match result: {policy_match}")

            if not policy_match or not policy_match.matched:
                logger.debug("No ToolApproval policy matched the generated code")
                return None

            policy = policy_match.policy
            logger.warning(f"Tool approval required by policy '{policy.name}' - routing to HITL")

            code_lines = code.split("\n")
            preview_lines = code_lines

            approval_metadata = {
                **adapter.get_metadata(state),
                "policy_type": "tool_approval",
                "policy_id": policy.id,
                "policy_name": policy.name,
                "required_tools": policy.required_tools,
                "required_apps": policy.required_apps,
                "approval_message": policy.approval_message
                or "This tool requires your approval before execution.",
                "show_code_preview": policy.show_code_preview,
            }

            adapter.set_metadata(state, approval_metadata)

            return ToolApprovalHandler._create_approval_interrupt(
                adapter, state, code, content, preview_lines
            )

        except Exception as e:
            logger.error(f"Error checking tool approval policies: {e}", exc_info=True)
            return None

    @staticmethod
    def _create_approval_interrupt(
        adapter: CoreGraphAdapter,
        state: Any,
        code: str,
        content: str,
        preview_lines: List[str],
    ) -> Command:
        """Create an interrupt Command for tool approval."""
        from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import create_tool_approval_action

        md = adapter.get_metadata(state)

        approval_metadata = {
            **md,
            "approval_required": True,
            "code_preview": preview_lines,
            "full_code": code if md.get("show_code_preview") else None,
        }

        policy_name = md.get("policy_name", "Tool Approval")
        approval_msg = md.get("approval_message", "This tool requires your approval before execution.")
        tools_list = md.get("required_tools", [])
        apps_list = md.get("required_apps", [])

        hitl_action = create_tool_approval_action(
            policy_name=policy_name,
            required_tools=tools_list,
            code_preview=preview_lines,
            full_code=code,
            approval_message=approval_msg,
            return_to=adapter.sender_name,
        )

        final_answer_text = ToolApprovalHandler._generate_approval_message(
            policy_name=policy_name,
            approval_msg=approval_msg,
            tools_list=tools_list,
            apps_list=apps_list,
            preview_lines=preview_lines,
        )

        updated_messages, error_message = _core_append_with_step_limit(
            adapter, state, [AIMessage(content=content)]
        )
        if error_message:
            return _core_create_error_command(adapter, updated_messages, error_message, state.step_count)

        return Command(
            goto=END,
            update={
                adapter.messages_key: updated_messages,
                "script": code,
                "final_answer": final_answer_text,
                adapter.metadata_key: approval_metadata,
                "hitl_action": hitl_action,
                "sender": adapter.sender_name,
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
        """Generate user-friendly markdown message for approval request."""
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
    def handle_denial(adapter: CoreGraphAdapter, state: Any) -> Optional[Command]:
        """Handle user denial of tool approval."""
        if adapter.get_metadata(state).get("user_approved") is False:
            logger.warning("User denied tool approval - skipping execution")
            meta_key = adapter.metadata_key
            cleared_meta = {k: v for k, v in adapter.get_metadata(state).items() if k != "user_approved"}
            return Command(
                goto=END,
                update={
                    "execution_complete": True,
                    "final_answer": "Execution cancelled by user.",
                    "step_count": state.step_count + 1,
                    meta_key: cleared_meta,
                },
            )
        return None
