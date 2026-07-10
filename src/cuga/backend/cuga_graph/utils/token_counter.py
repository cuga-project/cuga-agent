"""
Token counting utility for context management.

This module provides token counting capabilities using LangChain's built-in
count_tokens_approximately function and integrates with ActivityTracker
for reactive usage tracking.
"""

from typing import TYPE_CHECKING, List, Optional, Mapping, Any
from functools import partial
from loguru import logger
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

from cuga.backend.cuga_graph.utils.message_utils import convert_to_proper_message_type

# Constants for token estimation
CHARS_PER_TOKEN_FALLBACK = 4  # Rough estimate: 1 token ≈ 4 characters
DEFAULT_CONTEXT_SIZE = 131072  # Default context size for unknown models (based on gpt-oss-120b)

# Our approximate counter (count_tokens_approximately + 15% overhead) has been observed to
# undercount IBM's real tokenizer by ~20% on JSON/dict-heavy prompts (e.g. skill/report
# generation). Inflate the estimate and reserve a larger buffer before trusting it to size
# max_completion_tokens for a WatsonX call, so we clamp to a small budget instead of sending
# a request that watsonx.ai rejects with "max_tokens must be at least 1".
WATSONX_PROMPT_SAFETY_MARGIN = 0.20
WATSONX_COMPLETION_BUFFER = 1024

# Model context size constants (in tokens)
MODEL_CONTEXT_SIZES = {
    # ============================================================================
    # OpenAI GPT-5 Series (Latest - 2026)
    # ============================================================================
    "gpt-5.4": 1050000,
    "gpt-5.4-pro": 1050000,
    "gpt-5.2": 400000,
    "gpt-5.2-pro": 400000,
    "gpt-5.1": 400000,
    "gpt-5-pro": 400000,
    "gpt-5": 400000,
    "gpt-5-mini": 400000,
    "gpt-5-nano": 400000,
    "openai/gpt-5.4": 1050000,
    "openai/gpt-5.4-pro": 1050000,
    "openai/gpt-5.2": 400000,
    "openai/gpt-5.2-pro": 400000,
    "openai/gpt-5.1": 400000,
    "openai/gpt-5-pro": 400000,
    "openai/gpt-5": 400000,
    "openai/gpt-5-mini": 400000,
    "openai/gpt-5-nano": 400000,
    # ============================================================================
    # OpenAI GPT-4.1 Series
    # ============================================================================
    "gpt-4.1": 1047576,
    "gpt-4.1-mini": 1047576,
    "gpt-4.1-nano": 1047576,
    "openai/gpt-4.1": 1047576,
    "openai/gpt-4.1-mini": 1047576,
    "openai/gpt-4.1-nano": 1047576,
    # ============================================================================
    # OpenAI GPT-4o Series
    # ============================================================================
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4o-2024-11-20": 128000,
    "gpt-4o-2024-08-06": 128000,
    "gpt-4o-2024-05-13": 128000,
    "gpt-4o-mini-2024-07-18": 128000,
    "openai/gpt-4o": 128000,
    "openai/gpt-4o-mini": 128000,
    # ============================================================================
    # OpenAI GPT-4 Turbo Series
    # ============================================================================
    "gpt-4-turbo": 128000,
    "gpt-4-turbo-preview": 128000,
    "gpt-4-turbo-2024-04-09": 128000,
    "gpt-4-0125-preview": 128000,
    "gpt-4-1106-preview": 128000,
    "openai/gpt-4-turbo": 128000,
    "openai/gpt-4-turbo-preview": 128000,
    # ============================================================================
    # OpenAI GPT-4 Base Series
    # ============================================================================
    "gpt-4": 8192,
    "gpt-4-0613": 8192,
    "gpt-4-0314": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-32k-0613": 32768,
    "gpt-4-32k-0314": 32768,
    "openai/gpt-4": 8192,
    "openai/gpt-4-32k": 32768,
    # ============================================================================
    # OpenAI O-Series (Reasoning Models)
    # ============================================================================
    "o3-pro": 200000,
    "o3": 200000,
    "o4-mini": 200000,
    "o3-mini": 200000,
    "o1": 200000,
    "o1-preview": 128000,
    "o1-mini": 128000,
    "o1-2024-12-17": 200000,
    "openai/o3-pro": 200000,
    "openai/o3": 200000,
    "openai/o4-mini": 200000,
    "openai/o3-mini": 200000,
    "openai/o1": 200000,
    "openai/o1-preview": 128000,
    "openai/o1-mini": 128000,
    # ============================================================================
    # OpenAI GPT-3.5 Series
    # ============================================================================
    "gpt-3.5-turbo": 16385,
    "gpt-3.5-turbo-16k": 16385,
    "gpt-3.5-turbo-0125": 16385,
    "gpt-3.5-turbo-1106": 16385,
    "gpt-3.5-turbo-0613": 4096,
    "openai/gpt-3.5-turbo": 16385,
    "openai/gpt-3.5-turbo-16k": 16385,
    # ============================================================================
    # Open-Source Models (Default Reference)
    # ============================================================================
    "gpt-oss-120b": 131072,
    "gpt-oss-20b": 131072,
    "openai/gpt-oss-120b": 131072,
    "openai/gpt-oss-20b": 131072,
    "rits/openai/gpt-oss-120b": 131072,
    "rits/openai/gpt-oss-20b": 131072,
    # ============================================================================
    # Anthropic Claude 4 Series (Latest - 2025/2026)
    # ============================================================================
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-opus-4-5-20251101": 200000,
    "claude-haiku-4-5-20251001": 200000,
    "claude-sonnet-4-5-20250929": 200000,
    "claude-opus-4-1-20250805": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-sonnet-4-20250514": 200000,
    "anthropic/claude-opus-4-6": 200000,
    "anthropic/claude-sonnet-4-6": 200000,
    "anthropic/claude-opus-4-5-20251101": 200000,
    "anthropic/claude-haiku-4-5-20251001": 200000,
    "anthropic/claude-sonnet-4-5-20250929": 200000,
    "anthropic/claude-opus-4-1-20250805": 200000,
    "anthropic/claude-opus-4-20250514": 200000,
    "anthropic/claude-sonnet-4-20250514": 200000,
    # ============================================================================
    # Anthropic Claude 3.5 Series
    # ============================================================================
    "claude-3-5-opus": 200000,
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku": 200000,
    "claude-3-5-sonnet-20241022": 200000,
    "claude-3-5-sonnet-20240620": 200000,
    "claude-3-5-haiku-20241022": 200000,
    "anthropic/claude-3-5-opus": 200000,
    "anthropic/claude-3-5-sonnet": 200000,
    "anthropic/claude-3-5-haiku": 200000,
    # ============================================================================
    # Anthropic Claude 3 Series
    # ============================================================================
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3-opus-20240229": 200000,
    "claude-3-sonnet-20240229": 200000,
    "claude-3-haiku-20240307": 200000,
    "anthropic/claude-3-opus": 200000,
    "anthropic/claude-3-sonnet": 200000,
    "anthropic/claude-3-haiku": 200000,
    # ============================================================================
    # Anthropic Claude 2 Series
    # ============================================================================
    "claude-2": 100000,
    "claude-2.1": 200000,
    "claude-2.0": 100000,
    "claude-instant": 100000,
    "claude-instant-1": 100000,
    "claude-instant-1.2": 100000,
    "anthropic/claude-2": 100000,
    "anthropic/claude-2.1": 200000,
    "anthropic/claude-instant": 100000,
    # ============================================================================
    # Google Gemini Models
    # ============================================================================
    "gemini-2.0-flash": 1000000,
    "gemini-2.0-flash-exp": 1000000,
    "gemini-1.5-pro": 2000000,
    "gemini-1.5-pro-latest": 2000000,
    "gemini-1.5-flash": 1000000,
    "gemini-1.5-flash-latest": 1000000,
    "gemini-pro": 32768,
    "gemini-exp": 2000000,
    "google/gemini-2.0-flash": 1000000,
    "google/gemini-1.5-pro": 2000000,
    "google/gemini-1.5-flash": 1000000,
    "google/gemini-pro": 32768,
    "google/gemini-exp": 2000000,
    # ============================================================================
    # Meta Llama Models
    # ============================================================================
    "llama-4-maverick-17b-128e-instruct-fp8": 128000,
    "meta-llama/llama-4-maverick-17b-128e-instruct-fp8": 128000,
    "llama-3.3-70b": 128000,
    "llama-3.3-70b-instruct": 128000,
    "meta-llama/llama-3.3-70b-instruct": 128000,
    "llama-3.2": 128000,
    "llama-3.2-1b": 128000,
    "llama-3.2-3b": 128000,
    "llama-3.2-11b": 128000,
    "llama-3.2-90b": 128000,
    "meta-llama/llama-3.2-1b-instruct": 128000,
    "meta-llama/llama-3.2-3b-instruct": 128000,
    "llama-3.1-8b": 128000,
    "llama-3.1-70b": 128000,
    "llama-3.1-405b": 128000,
    "meta-llama/llama-3.1-8b-instruct": 128000,
    "meta-llama/llama-3.1-70b-instruct": 128000,
    "meta-llama/llama-3.1-405b-instruct": 128000,
    "meta-llama/llama-3-405b-instruct": 128000,
    "llama-3-70b": 8192,
    "llama-3-8b": 8192,
    "meta-llama/llama-3-70b-instruct": 8192,
    "meta-llama/llama-3-8b-instruct": 8192,
    "llama-2-70b": 4096,
    "llama-2-13b": 4096,
    "llama-2-7b": 4096,
    "meta-llama/llama-2-70b-chat": 4096,
    "meta-llama/llama-2-13b-chat": 4096,
    "meta-llama/llama-2-7b-chat": 4096,
    # ============================================================================
    # Mistral AI Models
    # ============================================================================
    "mistral-large": 128000,
    "mistral-large-latest": 128000,
    "mistral-large-2411": 128000,
    "mistral-medium": 32000,
    "mistral-small": 32000,
    "mistral-7b": 32000,
    "mistral-7b-instruct": 32000,
    "mixtral-8x7b": 32000,
    "mixtral-8x7b-instruct": 32000,
    "mixtral-8x22b": 64000,
    "mixtral-8x22b-instruct": 64000,
    "codestral": 32000,
    "codestral-latest": 32000,
    "mistralai/mistral-large": 128000,
    "mistralai/mistral-medium": 32000,
    "mistralai/mistral-small": 32000,
    "mistralai/mixtral-8x7b-instruct": 32000,
    "mistralai/mixtral-8x22b-instruct": 64000,
    "mistralai/codestral": 32000,
    # ============================================================================
    # DeepSeek Models
    # ============================================================================
    "deepseek-v3": 64000,
    "deepseek-chat": 64000,
    "deepseek-chat-v3": 64000,
    "deepseek-coder": 64000,
    "deepseek-coder-33b-instruct": 64000,
    "deepseek/deepseek-v3": 64000,
    "deepseek/deepseek-chat": 64000,
    "deepseek/deepseek-coder": 64000,
    # ============================================================================
    # Qwen Models (Alibaba)
    # ============================================================================
    "qwen-2.5": 32000,
    "qwen-2.5-72b-instruct": 32000,
    "qwen-2.5-coder": 32000,
    "qwen-2.5-coder-32b-instruct": 32000,
    "qwen-max": 32000,
    "qwen/qwen-2.5-72b-instruct": 32000,
    "qwen/qwen-2.5-coder-32b-instruct": 32000,
    "qwen/qwen-max": 32000,
    # ============================================================================
    # Cohere Models
    # ============================================================================
    "command-r": 128000,
    "command-r-plus": 128000,
    "command-r-08-2024": 128000,
    "command-r-plus-08-2024": 128000,
    "cohere/command-r": 128000,
    "cohere/command-r-plus": 128000,
    # ============================================================================
    # xAI Grok Models
    # ============================================================================
    "grok-2": 128000,
    "grok-2-latest": 128000,
    "grok-beta": 128000,
    "xai/grok-2": 128000,
    "xai/grok-beta": 128000,
}

_SORTED_MODEL_CONTEXT_KEYS = sorted(MODEL_CONTEXT_SIZES.keys(), key=len, reverse=True)

if TYPE_CHECKING:
    pass


def resolve_model_identifier(model: Optional[Any] = None, fallback_name: str = "") -> str:
    """Return the best-effort model id/name from a LangChain chat model instance."""
    if model is None:
        return fallback_name

    try:
        from langchain_ibm import ChatWatsonx

        if isinstance(model, ChatWatsonx):
            model_id = getattr(model, "model_id", None)
            if model_id:
                return str(model_id)
    except ImportError:
        pass

    for attr in ("model_id", "model_name", "model"):
        value = getattr(model, attr, None)
        if value:
            return str(value)

    return fallback_name


def lookup_model_context_size(model_name: Optional[str]) -> Optional[int]:
    """Look up a known context window size for *model_name*, if available."""
    if not model_name:
        return None

    normalized_name = model_name.strip()
    if "/" in normalized_name:
        normalized_name = normalized_name.split("/", 1)[1]

    if normalized_name in MODEL_CONTEXT_SIZES:
        return MODEL_CONTEXT_SIZES[normalized_name]

    sorted_keys = _SORTED_MODEL_CONTEXT_KEYS
    for key in sorted_keys:
        if normalized_name.startswith(key):
            return MODEL_CONTEXT_SIZES[key]

    return None


def clamp_completion_tokens(
    context_size: int,
    prompt_tokens: int,
    requested: int,
    *,
    buffer: int = 256,
) -> int:
    """Keep completion budget positive when prompt size is near the context window."""
    remaining = context_size - prompt_tokens - buffer
    if remaining >= requested:
        return requested
    return max(1, remaining)


def clamp_watsonx_completion_for_messages(model: Any, messages: list) -> None:
    """Prevent negative max_completion_tokens when prompt size nears the context window."""
    try:
        from langchain_ibm import ChatWatsonx
    except ImportError:
        return

    llm = model
    while hasattr(llm, "bound"):
        llm = llm.bound
    if not isinstance(llm, ChatWatsonx):
        return

    context_size = ensure_model_context_profile(llm)
    counter = TokenCounter(model=llm)
    lc_messages = []
    for message in messages:
        if isinstance(message, dict):
            role = (message.get("role") or "user").lower()
            content = str(message.get("content", ""))
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))
        else:
            lc_messages.append(message)
    raw_prompt_tokens = counter.count_total_context_tokens(lc_messages)
    # Inflate: our estimator undercounts vs IBM's real tokenizer on dense/JSON-heavy prompts.
    prompt_tokens = int(raw_prompt_tokens * (1 + WATSONX_PROMPT_SAFETY_MARGIN))

    params = dict(llm.params or {})
    requested = (
        getattr(llm, "max_completion_tokens", None)
        or getattr(llm, "max_tokens", None)
        or params.get("max_completion_tokens")
        or 16000
    )
    requested = int(requested)

    clamped = clamp_completion_tokens(
        context_size, prompt_tokens, requested, buffer=WATSONX_COMPLETION_BUFFER
    )
    if clamped != requested:
        logger.warning(
            "Clamped WatsonX max_completion_tokens {} -> {} "
            "(~{} raw / ~{} safety-inflated prompt tokens, {} context)",
            requested,
            clamped,
            raw_prompt_tokens,
            prompt_tokens,
            context_size,
        )
    params["max_completion_tokens"] = clamped
    llm.params = params


def ensure_model_context_profile(model: Optional[Any] = None, model_name: Optional[str] = None) -> int:
    """Ensure *model.profile* reflects the known context window for the resolved model name."""
    resolved_name = (model_name or "").strip() or resolve_model_identifier(model, fallback_name="")
    context_size = lookup_model_context_size(resolved_name)
    if context_size is None:
        context_size = DEFAULT_CONTEXT_SIZE

    if model is not None:
        try:
            existing = getattr(model, "profile", None)
            if not isinstance(existing, Mapping) or existing.get("max_input_tokens") != context_size:
                merged = dict(existing) if isinstance(existing, Mapping) else {}
                merged["max_input_tokens"] = context_size
                model.profile = merged
                logger.debug(
                    "Set model profile: max_input_tokens={} for {}",
                    context_size,
                    resolved_name or "unknown",
                )
        except Exception as exc:
            logger.warning(f"Failed to set model.profile: {exc}")

    return context_size


class TokenCounter:
    """
    Utility for counting tokens in messages.

    Uses LangChain's count_tokens_approximately for proactive counting (before LLM calls)
    and integrates with ActivityTracker for cumulative usage tracking.
    """

    def __init__(
        self,
        model: Optional[Any] = None,  # BaseChatModel
        model_name: str = "gpt-4",
        tracker: Optional[Any] = None,  # ActivityTracker
    ):
        """
        Initialize token counter.

        Args:
            model: Optional BaseChatModel instance for profile-based context size
            model_name: Model name for fallback context size lookup
            tracker: Optional ActivityTracker instance for usage tracking
        """
        self.model = model
        self.model_name = model_name
        self.tracker = tracker

        # Use LangChain's approximate token counter
        # Tune for Anthropic models (3.3 chars per token vs default 3.8)
        if model and hasattr(model, '_llm_type') and model._llm_type == "anthropic-chat":
            self.token_counter = partial(count_tokens_approximately, chars_per_token=3.3)
        elif "claude" in model_name.lower():
            self.token_counter = partial(count_tokens_approximately, chars_per_token=3.3)
        else:
            self.token_counter = count_tokens_approximately

    def count_message_tokens(self, messages: List[BaseMessage]) -> int:
        """
        Count total tokens in a list of messages.

        This is PROACTIVE counting - used to check if we need to summarize
        BEFORE sending to the LLM.

        Args:
            messages: List of LangChain messages

        Returns:
            Total token count (approximate)
        """
        if not messages:
            return 0

        try:
            # Convert any generic BaseMessage instances to proper types
            converted_messages = [convert_to_proper_message_type(msg) for msg in messages]
            return self.token_counter(converted_messages)
        except ValueError as e:
            # Handle case where conversion didn't work
            if "Unknown BaseMessage type" in str(e):
                logger.warning(
                    f"Failed to convert BaseMessage instances properly. "
                    f"Error: {e}. Falling back to character estimation."
                )
            else:
                logger.warning(f"Error counting tokens: {e}, falling back to character estimation")

            # Fallback: rough character-based estimation
            total_chars = sum(len(str(msg.content)) for msg in messages)
            return total_chars // CHARS_PER_TOKEN_FALLBACK
        except Exception as e:
            logger.warning(f"Unexpected error counting tokens: {e}, falling back to character estimation")
            # Fallback: rough character-based estimation
            total_chars = sum(len(str(msg.content)) for msg in messages)
            return total_chars // CHARS_PER_TOKEN_FALLBACK

    def count_tool_tokens(self, tools: Optional[List[Any]]) -> int:
        """
        Estimate token count for tool schemas.

        Tool schemas include name, description, and parameter definitions.
        This provides a rough estimate of the overhead tools add to each request.

        Args:
            tools: List of tool objects (LangChain tools or similar)

        Returns:
            Estimated token count for all tool schemas
        """
        if not tools:
            return 0

        total_tokens = 0
        for tool in tools:
            try:
                # Extract tool information
                tool_name = getattr(tool, 'name', '')
                tool_desc = getattr(tool, 'description', '')

                # Get args schema if available
                args_schema = None
                if hasattr(tool, 'args_schema'):
                    args_schema = tool.args_schema
                elif hasattr(tool, 'args'):
                    args_schema = tool.args

                # Estimate tokens for tool definition
                # Tool name + description
                tool_text = f"{tool_name} {tool_desc}"
                tool_tokens = self.estimate_tokens(tool_text)

                # Add tokens for parameter schema (rough estimate)
                if args_schema:
                    # Convert schema to string representation
                    if hasattr(args_schema, 'schema'):
                        schema_str = str(args_schema.schema())
                    else:
                        schema_str = str(args_schema)

                    schema_tokens = self.estimate_tokens(schema_str)
                    tool_tokens += schema_tokens

                total_tokens += tool_tokens

            except Exception as e:
                logger.debug(f"Error estimating tokens for tool: {e}")
                # Fallback: assume 200 tokens per tool on average
                total_tokens += 200

        logger.debug(f"Estimated {total_tokens} tokens for {len(tools)} tools")
        return total_tokens

    def count_total_context_tokens(
        self,
        messages: List[BaseMessage],
        tools: Optional[List[Any]] = None,
        system_prompt: Optional[str] = None,
    ) -> int:
        """
        Count total tokens including messages, tools, and system prompt.

        This provides a more accurate estimate of actual context usage for
        triggering summarization, matching what Langfuse reports more closely.

        Args:
            messages: List of messages
            tools: Optional list of tools
            system_prompt: Optional system prompt text

        Returns:
            Total estimated token count
        """
        message_tokens = self.count_message_tokens(messages)
        tool_tokens = self.count_tool_tokens(tools) if tools else 0
        prompt_tokens = self.estimate_tokens(system_prompt) if system_prompt else 0

        # Add 15% overhead for message formatting, special tokens, etc.
        overhead = int(message_tokens * 0.15)

        total = message_tokens + tool_tokens + prompt_tokens + overhead

        logger.debug(
            f"Total context tokens: {total} "
            f"(messages: {message_tokens}, tools: {tool_tokens}, "
            f"prompt: {prompt_tokens}, overhead: {overhead})"
        )

        return total

    def get_model_context_size(self, model: Optional[Any] = None) -> int:  # BaseChatModel
        """
        Get the context window size for a given model.

        Prefers known model-name mappings over provider-supplied profiles, because
        some integrations (e.g. ChatWatsonx) ship with generic 8K profiles that
        do not match large-context models like gpt-oss-120b.

        Args:
            model: Optional BaseChatModel instance (uses instance model if not provided)

        Returns:
            Context window size in tokens
        """
        model_to_check = model or self.model
        model_name = resolve_model_identifier(model_to_check, fallback_name=self.model_name)

        known_size = lookup_model_context_size(model_name)
        if known_size is not None:
            return known_size

        if model_to_check:
            profile_limit = self._get_profile_limits(model_to_check)
            if profile_limit is not None:
                return profile_limit

        logger.warning(
            f"Unknown model '{model_name}', defaulting to {DEFAULT_CONTEXT_SIZE} context window. "
            "Consider adding the model to MODEL_CONTEXT_SIZES constant or setting model.profile "
            "with max_input_tokens for accurate tracking."
        )
        return DEFAULT_CONTEXT_SIZE

    @staticmethod
    def _get_profile_limits(model: Any) -> Optional[int]:  # BaseChatModel
        """
        Retrieve max input token limit from the model profile.

        This is the same method used by LangChain's SummarizationMiddleware.

        Args:
            model: BaseChatModel instance

        Returns:
            Max input tokens if available, None otherwise
        """
        try:
            profile = model.profile
        except AttributeError:
            return None

        if not isinstance(profile, Mapping):
            return None

        max_input_tokens = profile.get("max_input_tokens")

        if not isinstance(max_input_tokens, int):
            return None

        return max_input_tokens

    def calculate_usage_percentage(
        self,
        messages: List[BaseMessage],
        model: Optional[Any] = None,  # BaseChatModel
    ) -> float:
        """
        Calculate what percentage of context is being used.

        Args:
            messages: List of messages to count
            model: Optional BaseChatModel instance (uses instance model if not provided)

        Returns:
            Usage percentage (0-100)
        """
        if not messages:
            return 0.0

        token_count = self.count_message_tokens(messages)
        context_size = self.get_model_context_size(model)

        return (token_count / context_size) * 100

    def get_cumulative_usage(self) -> int:
        """
        Get cumulative token usage from ActivityTracker.

        This is REACTIVE tracking - shows total tokens used across all LLM calls.
        Useful for cost tracking and analytics.

        Returns:
            Total tokens used in session (0 if no tracker)
        """
        if self.tracker:
            return self.tracker.token_usage
        return 0

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for a text string.

        Args:
            text: Text to estimate

        Returns:
            Estimated token count
        """
        # Use character-based estimation
        # Default: 3.8 chars per token, Anthropic: 3.3 chars per token
        chars_per_token = 3.3 if "claude" in self.model_name.lower() else 3.8
        return int(len(text) / chars_per_token)


# Made with Bob
