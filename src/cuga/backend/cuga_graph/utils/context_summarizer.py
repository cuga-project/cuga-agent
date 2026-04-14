"""
Context summarization manager for conversation history.

This module provides intelligent context summarization using LangChain's
SummarizationMiddleware to compress older messages while preserving recent context.
"""

import time
import traceback
from typing import List, Optional, Tuple, Dict, Any

from langchain_core.messages import BaseMessage
from langchain.agents.middleware import SummarizationMiddleware
from loguru import logger

from cuga.config import settings
from cuga.backend.cuga_graph.utils.token_counter import TokenCounter
from cuga.backend.cuga_graph.utils.message_utils import convert_to_proper_message_type

# Optional imports for middleware invocation (may not be available in all LangChain versions)
try:
    from langchain.agents.middleware.types import AgentState as LangChainAgentState
    from langgraph.runtime import Runtime

    LANGCHAIN_MIDDLEWARE_AVAILABLE = True
except ImportError:
    LANGCHAIN_MIDDLEWARE_AVAILABLE = False
    LangChainAgentState = None
    Runtime = None


class ContextSummarizer:
    """
    Manages context summarization for conversation history

    This is a wrapper around LangChain's SummarizationMiddleware that provides:
    - Configuration from settings.toml
    - Metrics tracking for demo mode
    - Simplified API for AgentState
    """

    def __init__(
        self,
        model: Any,  # BaseChatModel
        model_name: str = "gpt-4",
        tracker: Optional[Any] = None,  # ActivityTracker (passed to TokenCounter)
    ):
        """
        Initialize context summarizer.

        Args:
            model: LangChain BaseChatModel instance for generating summaries
            model_name: Model name for token counting
            tracker: Optional ActivityTracker instance (passed to TokenCounter for usage tracking)
        """
        self.model = model
        self.model_name = model_name
        self.config = settings.context_summarization
        self.token_counter = TokenCounter(model=model, model_name=model_name, tracker=tracker)

        # Initialize LangChain middleware if enabled
        self.middleware: Optional[SummarizationMiddleware] = None
        if self.config.enabled:
            self._init_middleware()

    def _init_middleware(self):
        """Initialize the LangChain SummarizationMiddleware."""
        try:
            self._setup_model_profile()
            trigger = self._build_trigger_config()
            keep_config = ("messages", self.config.keep_last_n_messages)
            kwargs = self._build_middleware_kwargs(trigger, keep_config)

            self.middleware = SummarizationMiddleware(**kwargs)

            logger.info(
                f"Context summarization initialized: trigger={trigger}, "
                f"keep={keep_config}, model={self.model_name}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize SummarizationMiddleware: {e}")
            self.middleware = None

    def _setup_model_profile(self):
        """Setup model profile with context size for fraction-based triggers."""
        if not (hasattr(self.config, 'trigger_fraction') and self.config.trigger_fraction):
            return

        # Check if profile already exists
        if hasattr(self.model, 'profile') and self.model.profile:
            if isinstance(self.model.profile, dict) and 'max_input_tokens' in self.model.profile:
                logger.debug(f"Model already has profile: {self.model.profile}")
                return

        # Set profile with context size
        try:
            context_size = self.token_counter.get_model_context_size(self.model)
            self.model.profile = {"max_input_tokens": context_size}
            logger.info(f"Set model profile: max_input_tokens={context_size} for {self.model_name}")

            # Verify profile was set
            if not hasattr(self.model, 'profile'):
                logger.error("Failed to set model.profile - attribute doesn't exist after assignment")
        except Exception as e:
            logger.error(f"Failed to set model.profile: {e}")

    def _build_trigger_config(self):
        """Build trigger configuration from config settings.

        Returns:
            Trigger configuration as tuple or list of tuples.
        """
        trigger_config = []

        if hasattr(self.config, 'trigger_fraction') and self.config.trigger_fraction:
            trigger_config.append(("fraction", self.config.trigger_fraction))
        if hasattr(self.config, 'trigger_tokens') and self.config.trigger_tokens:
            trigger_config.append(("tokens", self.config.trigger_tokens))
        if hasattr(self.config, 'trigger_messages') and self.config.trigger_messages:
            trigger_config.append(("messages", self.config.trigger_messages))

        # Return single trigger, list of triggers, or default
        if len(trigger_config) == 1:
            return trigger_config[0]
        elif len(trigger_config) > 1:
            return trigger_config
        else:
            logger.warning("No trigger conditions specified, defaulting to 75% fraction")
            return ("fraction", 0.75)

    def _build_middleware_kwargs(self, trigger, keep_config):
        """Build kwargs dict for SummarizationMiddleware initialization.

        Args:
            trigger: Trigger configuration (from config, but we override it)
            keep_config: Keep configuration tuple

        Returns:
            Dict of kwargs for middleware initialization.
        """
        # IMPORTANT: We do our own trigger checking in should_summarize() which includes
        # tools, system prompt, and overhead. The middleware only counts message tokens.
        # So we pass a very low trigger (1 token) to ensure the middleware always summarizes
        # when we call it (after our trigger check passes).
        middleware_trigger = ("tokens", 1)

        kwargs = {
            "model": self.model,
            "trigger": middleware_trigger,  # Use low trigger, we check ourselves
            "keep": keep_config,
            "token_counter": self.token_counter.token_counter,
            "trim_tokens_to_summarize": self.config.trim_tokens_to_summarize,
        }

        # Add custom prompt if provided
        if hasattr(self.config, 'custom_summary_prompt') and self.config.custom_summary_prompt:
            kwargs["summary_prompt"] = self.config.custom_summary_prompt

        return kwargs

    def should_summarize(
        self,
        messages: List[BaseMessage],
        tools: Optional[List[Any]] = None,
        system_prompt: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if summarization should be triggered.

        Args:
            messages: List of messages to check
            tools: Optional list of tools that will be sent with messages
            system_prompt: Optional system prompt that will be sent with messages

        Returns:
            Tuple of (should_summarize, metrics_dict)
        """
        if not self.config.enabled or not messages or not self.middleware:
            return False, {}

        # Get current metrics (including tools and prompt for accurate context usage)
        metrics = self._calculate_metrics(messages, tools, system_prompt)

        # Check trigger conditions
        should_trigger, trigger_reason = self._check_trigger_conditions(messages, metrics)

        # Only log when summarization is actually triggered
        if should_trigger:
            logger.info(
                f"📊 Context summarization triggered: {metrics['message_count']} messages, "
                f"{metrics.get('total_token_count', 0)} total tokens "
                f"({metrics['usage_percentage']:.1f}% usage), "
                f"reason: {trigger_reason}"
            )

        if trigger_reason:
            metrics["trigger_reason"] = trigger_reason

        return should_trigger, metrics

    def _calculate_metrics(
        self,
        messages: List[BaseMessage],
        tools: Optional[List[Any]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculate current context usage metrics.

        Args:
            messages: List of messages to analyze
            tools: Optional list of tools for accurate token counting
            system_prompt: Optional system prompt for accurate token counting

        Returns:
            Dict containing token_count, context_size, usage_percentage, message_count
        """
        # Count only messages for the metrics (what we'll summarize)
        message_token_count = self.token_counter.count_message_tokens(messages)

        # Count total context including tools and prompt (for accurate trigger calculation)
        total_token_count = self.token_counter.count_total_context_tokens(messages, tools, system_prompt)

        context_size = self.token_counter.get_model_context_size(self.model)

        # Use total tokens for usage percentage (this is what matters for context limits)
        usage_percentage = (total_token_count / context_size) * 100

        metrics = {
            "token_count": message_token_count,  # Messages only (for metrics display)
            "total_token_count": total_token_count,  # Including tools/prompt (for triggering)
            "context_size": context_size,
            "usage_percentage": usage_percentage,  # Based on total tokens
            "message_count": len(messages),
        }

        return metrics

    def _check_trigger_conditions(
        self, messages: List[BaseMessage], metrics: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """Check if any trigger condition is met.

        Args:
            messages: List of messages to check
            metrics: Pre-calculated metrics dict

        Returns:
            Tuple of (should_trigger, trigger_reason)
        """
        # Check fraction trigger
        if hasattr(self.config, 'trigger_fraction') and self.config.trigger_fraction:
            threshold = self.config.trigger_fraction * 100
            if metrics["usage_percentage"] >= threshold:
                return True, f"fraction ({metrics['usage_percentage']:.1f}% >= {threshold}%)"

        # Check token count trigger
        # Use total_token_count (includes tools, prompt, overhead) not just message tokens
        # This ensures we trigger based on actual context usage, not just message content
        if hasattr(self.config, 'trigger_tokens') and self.config.trigger_tokens:
            total_tokens = metrics.get("total_token_count", metrics["token_count"])
            if total_tokens >= self.config.trigger_tokens:
                return True, f"tokens ({total_tokens} >= {self.config.trigger_tokens})"

        # Check message count trigger
        if hasattr(self.config, 'trigger_messages') and self.config.trigger_messages:
            messages_since_summary = self._count_messages_since_last_summary(messages)
            if messages_since_summary >= self.config.trigger_messages:
                return (
                    True,
                    f"messages ({messages_since_summary} since last summary >= {self.config.trigger_messages})",
                )

        return False, None

    def _count_messages_since_last_summary(self, messages: List[BaseMessage]) -> int:
        """Count messages after the last summary message.

        This prevents re-triggering immediately after summarization.

        Args:
            messages: List of messages to analyze

        Returns:
            Number of messages since last summary (or total if no summary found)
        """
        last_summary_idx = -1
        for i, msg in enumerate(messages):
            if self._is_summary_message(msg):
                last_summary_idx = i

        # Count messages after the last summary (or all if no summary found)
        return len(messages) - (last_summary_idx + 1) if last_summary_idx >= 0 else len(messages)

    async def summarize_messages(
        self, messages: List[BaseMessage]
    ) -> Tuple[List[BaseMessage], Dict[str, Any]]:
        """
        Summarize older messages while keeping recent ones.

        This method uses LangChain's SummarizationMiddleware to perform the actual
        summarization, then tracks metrics for monitoring and demo mode display.

        Args:
            messages: List of messages to potentially summarize

        Returns:
            Tuple of (summarized_messages, summary_metrics)
        """
        if not self.config.enabled or not messages or not self.middleware:
            return messages, {"skipped": "summarization disabled or no messages"}

        # Check if we have enough messages to summarize
        keep_n = self.config.keep_last_n_messages
        if len(messages) <= keep_n:
            return messages, {"skipped": "not enough messages to summarize"}

        # Get metrics before summarization (only after confirming summarization will proceed)
        before_metrics = {
            "message_count": len(messages),
            "token_count": self.token_counter.count_message_tokens(messages),
        }

        try:
            typed_messages = self._convert_messages_to_typed(messages)
            result = await self._invoke_middleware(typed_messages, messages, keep_n)

            if result is None:
                return messages, {"skipped": "middleware decided not to summarize"}

            new_messages = self._extract_new_messages(result, messages, keep_n)
            summary_metrics = self._calculate_summary_metrics(before_metrics, new_messages, messages, keep_n)

            return new_messages, summary_metrics

        except Exception as e:
            logger.exception(f"Failed to summarize messages: {e}")
            return messages[-keep_n:], {"error": str(e), "fallback": "kept recent messages only"}

    def _sanitize_content(self, content: Any) -> str:
        """
        Sanitize message content before processing.

        Args:
            content: Raw message content

        Returns:
            Sanitized content string
        """
        # Convert to string if not already
        if not isinstance(content, str):
            content = str(content)

        # Enforce maximum length (100k characters to prevent memory issues)
        max_length = 100000
        if len(content) > max_length:
            logger.warning(f"Message content exceeds max length ({len(content)} > {max_length}), truncating")
            content = content[:max_length]

        # Basic validation - preserve exact empty strings (e.g., for tool calls),
        # but replace whitespace-only or None-like content with placeholder
        stripped_content = content.strip()
        if stripped_content:
            content = stripped_content
        elif content != "":
            content = "[empty message]"

        return content

    def _convert_messages_to_typed(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """
        Convert generic BaseMessage objects to proper typed messages.

        LangChain's middleware requires specific message types (HumanMessage, AIMessage, etc.)

        Args:
            messages: List of messages to convert

        Returns:
            List of typed messages
        """
        typed_messages = []
        for msg in messages:
            # Convert to proper type using shared utility
            converted_msg = convert_to_proper_message_type(msg)
            # Sanitize content before adding to list
            sanitized_content = self._sanitize_content(converted_msg.content)
            # Preserve all existing fields/tool metadata while only normalizing content.
            if hasattr(converted_msg, "model_copy"):
                typed_messages.append(converted_msg.model_copy(update={"content": sanitized_content}))
            else:
                typed_messages.append(converted_msg.copy(update={"content": sanitized_content}))
        return typed_messages

    async def _invoke_middleware(
        self, typed_messages: List[BaseMessage], original_messages: List[BaseMessage], keep_n: int
    ) -> Optional[Dict[str, Any]]:
        """
        Invoke LangChain's SummarizationMiddleware to perform summarization.

        NOTE: This uses LangChain internal APIs (AgentState, Runtime) which may change
        in future versions. Integration tests should catch breaking changes.

        Args:
            typed_messages: List of typed messages for middleware
            original_messages: Original message list for fallback
            keep_n: Number of recent messages to keep

        Returns:
            Middleware result dict or None if no summarization needed
        """
        if not LANGCHAIN_MIDDLEWARE_AVAILABLE:
            raise ImportError(
                "LangChain middleware types not available. "
                "This may be due to an incompatible LangChain version. "
                "Please ensure langchain and langgraph are properly installed."
            )

        if not self.middleware:
            raise RuntimeError("Middleware not initialized")

        try:
            state = LangChainAgentState(messages=typed_messages)  # type: ignore
            runtime = Runtime()  # type: ignore
            # Check if middleware has async method, otherwise use sync
            if hasattr(self.middleware, 'abefore_model'):
                return await self.middleware.abefore_model(state, runtime)
            else:
                # Fallback to sync method if async not available
                return self.middleware.before_model(state, runtime)
        except ImportError as e:
            logger.error(f"Import error during middleware invocation: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise RuntimeError(f"middleware_invocation_failed: Missing required imports - {str(e)}") from e
        except ValueError as e:
            logger.error(f"Value error during middleware invocation: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise RuntimeError(f"middleware_invocation_failed: Invalid value - {str(e)}") from e
        except AttributeError as e:
            logger.error(f"Attribute error during middleware invocation: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise RuntimeError(f"middleware_invocation_failed: Missing attribute - {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error during middleware invocation: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            logger.error("Falling back to keeping recent messages")
            raise RuntimeError(f"middleware_invocation_failed: {str(e)}") from e

    def _extract_new_messages(
        self, result: Dict[str, Any], original_messages: List[BaseMessage], keep_n: int
    ) -> List[BaseMessage]:
        """
        Extract new messages from middleware result.

        The middleware returns RemoveMessage + new summary + preserved messages.

        Args:
            result: Middleware result dict
            original_messages: Original message list for fallback
            keep_n: Number of recent messages to keep

        Returns:
            List of new messages after summarization
        """
        new_messages = []
        for msg in result.get("messages", []):
            if hasattr(msg, '__class__') and msg.__class__.__name__ == 'RemoveMessage':
                continue
            new_messages.append(msg)

        if not new_messages:
            logger.warning("Middleware returned no messages, using fallback")
            return original_messages[-keep_n:]

        return new_messages

    def _calculate_summary_metrics(
        self,
        before_metrics: Dict[str, Any],
        new_messages: List[BaseMessage],
        original_messages: List[BaseMessage],
        keep_n: int,
    ) -> Dict[str, Any]:
        """
        Calculate and log summarization metrics.

        Args:
            before_metrics: Metrics before summarization
            new_messages: Messages after summarization
            original_messages: Original message list
            keep_n: Number of recent messages kept

        Returns:
            Dictionary containing summarization metrics
        """
        after_metrics = {
            "message_count": len(new_messages),
            "token_count": self.token_counter.count_message_tokens(new_messages),
        }

        summary_metrics = {
            "before": before_metrics,
            "after": after_metrics,
            "messages_summarized": len(original_messages) - keep_n,
            "messages_kept": keep_n,
            "tokens_saved": before_metrics["token_count"] - after_metrics["token_count"],
            "compression_ratio": after_metrics["token_count"] / before_metrics["token_count"]
            if before_metrics["token_count"] > 0
            else 1.0,
            "timestamp": time.time(),
        }

        logger.info(
            f"Context summarized: {summary_metrics['messages_summarized']} messages → "
            f"{summary_metrics['tokens_saved']} tokens saved "
            f"({summary_metrics['compression_ratio']:.1%} compression)"
        )

        return summary_metrics

    @staticmethod
    def _is_summary_message(message: BaseMessage) -> bool:
        """
        Check if a message is a summary message created by the middleware.

        Summary messages typically:
        - Have 'summary' in their content
        - Are AIMessage type
        - Contain phrases like "Summary of previous messages"
        """
        if not hasattr(message, 'content'):
            return False

        content = str(message.content).lower()

        # Check for common summary indicators
        summary_indicators = [
            'summary of previous',
            'summary of the previous',
            'summarizing the previous',
            'here is a summary',
            'previous conversation summary',
        ]

        return any(indicator in content for indicator in summary_indicators)
