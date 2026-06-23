"""
Shared utility functions for OutputFormatter policy handling.

This module provides shared functionality for applying OutputFormatter policies
across different parts of the codebase to avoid code duplication.
"""

import logging
from typing import Optional

from langchain_core.runnables import RunnableConfig

from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.config import settings

logger = logging.getLogger(__name__)


async def apply_output_formatter_policies(
    state: AgentState,
    config: Optional[RunnableConfig] = None,
    context: str = "unknown",
    metadata_key: str = "cuga_lite_metadata",
) -> None:
    """
    Apply OutputFormatter policies to the final answer.

    This function provides consistent OutputFormatter policy application across
    different execution contexts (SDK, CugaLite, etc.).

    Args:
        state: Current agent state with final_answer set
        config: Optional LangGraph config (may contain policy_system)
        context: Context identifier for logging ("sdk", "cuga_lite", etc.)
        metadata_key: State attribute for policy metadata. Default matches legacy
            ``AgentState.cuga_lite_metadata``; callers (e.g. supervisor) should pass
            their own key (``supervisor_metadata``).
    """
    if not settings.policy.enabled or not hasattr(state, 'final_answer') or not state.final_answer:
        return

    try:
        from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
        from cuga.backend.cuga_graph.policy.models import PolicyType
        from cuga.backend.cuga_graph.utils.langfuse_tracing import sync_langfuse_callbacks_from_config

        sync_langfuse_callbacks_from_config(config)

        logger.debug(f"{context}: Checking OutputFormatter policies...")
        command, metadata = await PolicyEnactment.check_and_enact(
            state, config, policy_types=[PolicyType.OUTPUT_FORMATTER]
        )

        if metadata and metadata.get("formatted_response"):
            formatted_response = metadata["formatted_response"]
            logger.info(
                f"{context}: OutputFormatter policy matched, formatted response "
                f"(original: {len(state.final_answer)} chars, formatted: {len(formatted_response)} chars)"
            )
            state.final_answer = formatted_response

            # Store consistent metadata structure for UI display
            _update_output_formatter_metadata(state, metadata, metadata_key=metadata_key)

        elif metadata:
            logger.debug(f"{context}: OutputFormatter metadata returned but no formatted_response")
            # Store metadata even if no formatted_response (for UI display)
            _update_output_formatter_metadata(state, metadata, applied=False, metadata_key=metadata_key)

    except Exception as e:
        logger.warning(f"{context}: Error checking OutputFormatter policies: {e}", exc_info=True)


def _update_output_formatter_metadata(
    state: AgentState,
    metadata: dict,
    applied: bool = True,
    metadata_key: str = "cuga_lite_metadata",
) -> None:
    """
    Update state metadata with OutputFormatter policy information.

    Args:
        state: Agent state to update
        metadata: Policy enactment metadata
        applied: Whether the formatter was actually applied (True) or just matched (False)
    """
    if not hasattr(state, metadata_key) or getattr(state, metadata_key) is None:
        setattr(state, metadata_key, {})

    # Base metadata that's always included
    base_metadata = {
        "policy_matched": True,
        "policy_type": metadata.get("policy_type", "output_formatter"),
        "policy_name": metadata.get("policy_name", "Unknown Output Formatter"),
        "policy_id": metadata.get("policy_id"),
        "policy_reasoning": metadata.get("policy_reasoning", ""),
        "policy_confidence": metadata.get("policy_confidence"),
    }

    # Add formatter-specific metadata
    if applied:
        base_metadata.update(
            {
                "output_formatter_applied": True,
                "output_formatter_policy": metadata.get("policy_name", "Unknown"),
                "output_formatter_id": metadata.get("policy_id"),
                "formatted_response": metadata.get("formatted_response"),
                "original_response": metadata.get("original_response"),
                "format_type": metadata.get("format_type"),
            }
        )

    getattr(state, metadata_key).update(base_metadata)
