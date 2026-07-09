"""
Unit tests for TokenCounter utility.
"""

import pytest
from unittest.mock import Mock
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from cuga.backend.cuga_graph.utils.token_counter import (
    TokenCounter,
    clamp_completion_tokens,
    clamp_watsonx_completion_for_messages,
    ensure_model_context_profile,
    lookup_model_context_size,
    resolve_model_identifier,
)
from cuga.backend.cuga_graph.utils.message_utils import convert_to_proper_message_type


class TestTokenCounter:
    """Test suite for TokenCounter class."""

    def test_initialization(self):
        """Test TokenCounter initialization."""
        counter = TokenCounter(model_name="gpt-4")
        assert counter.model_name == "gpt-4"
        assert counter.tracker is None

    def test_count_message_tokens_empty(self):
        """Test token counting with empty message list."""
        counter = TokenCounter(model_name="gpt-4")
        messages = []
        count = counter.count_message_tokens(messages)
        assert count == 0

    def test_count_message_tokens_single_message(self):
        """Test token counting with a single message."""
        counter = TokenCounter(model_name="gpt-4")
        messages = [HumanMessage(content="Hello, how are you?")]
        count = counter.count_message_tokens(messages)
        # Should be > 0 (exact count depends on tiktoken)
        assert count > 0
        # Rough estimate: ~5 words = ~7 tokens (including overhead)
        assert 5 < count < 20

    def test_count_message_tokens_multiple_messages(self):
        """Test token counting with multiple messages."""
        counter = TokenCounter(model_name="gpt-4")
        messages = [
            HumanMessage(content="Hello, how are you?"),
            AIMessage(content="I'm doing well, thank you!"),
            HumanMessage(content="What's the weather like?"),
        ]
        count = counter.count_message_tokens(messages)
        # Should be > 0 and more than single message
        assert count > 10

    def test_get_model_context_size_known_models(self):
        """Test context size retrieval for known models."""
        # Test with different model names
        counter_gpt4 = TokenCounter(model_name="gpt-4")
        assert counter_gpt4.get_model_context_size() == 8192

        counter_gpt4o = TokenCounter(model_name="gpt-4o")
        assert counter_gpt4o.get_model_context_size() == 128000

        counter_gpt4o_mini = TokenCounter(model_name="gpt-4o-mini")
        assert counter_gpt4o_mini.get_model_context_size() == 128000

        counter_claude_opus = TokenCounter(model_name="claude-3-opus")
        assert counter_claude_opus.get_model_context_size() == 200000

        counter_claude_sonnet = TokenCounter(model_name="claude-3-sonnet")
        assert counter_claude_sonnet.get_model_context_size() == 200000

        counter_gpt_oss = TokenCounter(model_name="openai/gpt-oss-120b")
        assert counter_gpt_oss.get_model_context_size() == 131072

    def test_get_model_context_size_prefers_known_size_over_stale_profile(self):
        """ChatWatsonx-style stale 8K profiles must not override gpt-oss-120b."""
        model = Mock()
        model.model_id = "openai/gpt-oss-120b"
        model.profile = {"max_input_tokens": 8192}

        counter = TokenCounter(model=model, model_name="gpt-4")
        assert counter.get_model_context_size(model) == 131072

    def test_ensure_model_context_profile_overrides_stale_watsonx_profile(self):
        model = Mock()
        model.model_id = "openai/gpt-oss-120b"
        model.profile = {"max_input_tokens": 8192, "tool_calling": True}

        size = ensure_model_context_profile(model, "openai/gpt-oss-120b")
        assert size == 131072
        assert model.profile == {"max_input_tokens": 131072, "tool_calling": True}

    def test_lookup_model_context_size_with_provider_prefix(self):
        assert lookup_model_context_size("openai/gpt-oss-120b") == 131072

    def test_resolve_model_identifier_prefers_model_id(self):
        model = Mock()
        model.model_id = "openai/gpt-oss-120b"
        model.model_name = "gpt-4"

        assert resolve_model_identifier(model, fallback_name="gpt-4") == "openai/gpt-oss-120b"

    def test_get_model_context_size_unknown_model(self):
        """Test context size retrieval for unknown model (should default to 131K based on gpt-oss-120b)."""
        counter = TokenCounter(model_name="unknown-model")
        size = counter.get_model_context_size()
        assert size == 131072  # Default fallback (based on gpt-oss-120b)

    def test_get_model_context_size_partial_match(self):
        """Test context size retrieval with partial model name match."""
        # Should match "gpt-4" prefix
        counter_gpt4_dated = TokenCounter(model_name="gpt-4-0613")
        assert counter_gpt4_dated.get_model_context_size() == 8192

        counter_gpt4o_dated = TokenCounter(model_name="gpt-4o-2024-05-13")
        assert counter_gpt4o_dated.get_model_context_size() == 128000

    def test_calculate_usage_percentage_empty(self):
        """Test usage percentage calculation with empty messages."""
        counter = TokenCounter(model_name="gpt-4")
        messages = []
        usage = counter.calculate_usage_percentage(messages)
        assert usage == 0.0

    def test_calculate_usage_percentage_low_usage(self):
        """Test usage percentage calculation with low token usage."""
        counter = TokenCounter(model_name="gpt-4")
        messages = [HumanMessage(content="Hello")]
        usage = counter.calculate_usage_percentage(messages)
        # Should be very low percentage for gpt-4 (8K context)
        assert 0 < usage < 1.0

    def test_calculate_usage_percentage_different_models(self):
        """Test usage percentage varies by model context size."""
        messages = [HumanMessage(content="Hello " * 100)]  # ~100 tokens

        # Same messages, different models = different percentages
        counter_gpt4 = TokenCounter(model_name="gpt-4")
        usage_gpt4 = counter_gpt4.calculate_usage_percentage(messages, "gpt-4")

        counter_gpt4o = TokenCounter(model_name="gpt-4o")
        usage_gpt4o = counter_gpt4o.calculate_usage_percentage(messages, "gpt-4o")

        # gpt-4 (8K) should have higher percentage than gpt-4o (128K)
        assert usage_gpt4 > usage_gpt4o

    def test_get_cumulative_usage_no_tracker(self):
        """Test cumulative usage returns 0 when no tracker provided."""
        counter = TokenCounter(model_name="gpt-4")
        usage = counter.get_cumulative_usage()
        assert usage == 0

    def test_estimate_tokens(self):
        """Test token estimation for text strings."""
        counter = TokenCounter(model_name="gpt-4")

        # Short text
        text = "Hello world"
        tokens = counter.estimate_tokens(text)
        assert tokens > 0
        assert tokens < 10

        # Longer text
        long_text = "Hello world " * 100
        long_tokens = counter.estimate_tokens(long_text)
        assert long_tokens > tokens

    def test_anthropic_model_char_per_token(self):
        """Test that Anthropic models use different char-per-token ratio."""
        counter_claude = TokenCounter(model_name="claude-3-opus")
        counter_gpt = TokenCounter(model_name="gpt-4")

        text = "A" * 100  # 100 characters

        # Claude uses 3.3 chars/token, GPT uses 3.8
        # So Claude should estimate more tokens for same text
        claude_tokens = counter_claude.estimate_tokens(text)
        gpt_tokens = counter_gpt.estimate_tokens(text)

        assert claude_tokens > gpt_tokens

    def test_count_message_tokens_with_generic_base_message(self):
        """Test token counting with generic BaseMessage instances (should convert properly)."""
        counter = TokenCounter(model_name="gpt-4")

        # Create a generic BaseMessage instance (simulating the error case)
        generic_message = BaseMessage(content="Hello, this is a test message", type="human")
        messages = [generic_message]

        # Should not raise an error and should return a valid token count
        count = counter.count_message_tokens(messages)
        assert count > 0
        assert count < 50  # Reasonable range for this message

    def test_count_message_tokens_mixed_message_types(self):
        """Test token counting with mix of proper and generic BaseMessage instances."""
        counter = TokenCounter(model_name="gpt-4")

        # Mix of proper message types and generic BaseMessage
        messages = [
            HumanMessage(content="Hello"),
            BaseMessage(content="I am a generic message", type="ai"),
            AIMessage(content="I am a proper AI message"),
            BaseMessage(content="Another generic one", type="human"),
        ]

        # Should handle all messages without error
        count = counter.count_message_tokens(messages)
        assert count > 0

    def test_convert_to_proper_message_type(self):
        """Test the convert_to_proper_message_type utility function."""
        # Test conversion of generic BaseMessage with type='human'
        generic_human = BaseMessage(content="Hello", type="human")
        converted = convert_to_proper_message_type(generic_human)
        assert isinstance(converted, HumanMessage)
        assert converted.content == "Hello"

        # Test conversion of generic BaseMessage with type='ai'
        generic_ai = BaseMessage(content="Hi there", type="ai")
        converted = convert_to_proper_message_type(generic_ai)
        assert isinstance(converted, AIMessage)
        assert converted.content == "Hi there"

        # Test that proper message types are returned as-is
        proper_human = HumanMessage(content="Already proper")
        converted = convert_to_proper_message_type(proper_human)
        assert converted is proper_human  # Should be the same object
        assert isinstance(converted, HumanMessage)


class TestTokenCounterWithMockTracker:
    """Test TokenCounter with mocked ActivityTracker."""

    def test_get_cumulative_usage_with_tracker(self):
        """Test cumulative usage with mocked tracker."""

        # Create a mock tracker
        class MockTracker:
            token_usage = 1500

        counter = TokenCounter(model_name="gpt-4", tracker=MockTracker())
        usage = counter.get_cumulative_usage()
        assert usage == 1500


def test_clamp_completion_tokens_keeps_requested_when_room():
    assert clamp_completion_tokens(131072, 70000, 16000) == 16000


def test_clamp_completion_tokens_never_returns_negative():
    assert clamp_completion_tokens(8192, 12764, 16000) == 1


def test_clamp_watsonx_completion_for_messages_updates_params():
    pytest.importorskip("langchain_ibm")
    from langchain_ibm import ChatWatsonx

    model = ChatWatsonx.model_construct(
        params={"max_completion_tokens": 16000, "temperature": 0.1},
        max_completion_tokens=16000,
        model_id="openai/gpt-oss-120b",
        profile={"max_input_tokens": 131072},
    )
    huge_messages = [{"role": "user", "content": "word " * 200_000}]

    clamp_watsonx_completion_for_messages(model, huge_messages)

    assert model.params["max_completion_tokens"] >= 1
    assert model.params["max_completion_tokens"] < 16000

    small_messages = [{"role": "user", "content": "hello"}]
    clamp_watsonx_completion_for_messages(model, small_messages)

    assert model.params["max_completion_tokens"] == 16000


def test_clamp_watsonx_completion_applies_safety_margin_for_undercounted_prompts():
    """Our approximate counter undercounts real WatsonX tokenization on dense prompts.

    A prompt whose *raw* estimate leaves just enough room for the requested completion
    must still be clamped once the safety margin/buffer is applied, so we never send a
    request that watsonx.ai would reject with a negative max_tokens.
    """
    pytest.importorskip("langchain_ibm")
    from unittest.mock import patch
    from langchain_ibm import ChatWatsonx

    model = ChatWatsonx.model_construct(
        params={"max_completion_tokens": 16000, "temperature": 0.1},
        max_completion_tokens=16000,
        model_id="openai/gpt-oss-120b",
        profile={"max_input_tokens": 131072},
    )
    # Raw estimate leaves exactly enough headroom for the old buffer (256) but not for
    # the safety-margin-inflated estimate plus the larger WatsonX buffer.
    messages = [{"role": "user", "content": "placeholder"}]

    with patch(
        "cuga.backend.cuga_graph.utils.token_counter.TokenCounter.count_total_context_tokens",
        return_value=114_500,
    ):
        clamp_watsonx_completion_for_messages(model, messages)

    assert model.params["max_completion_tokens"] < 16000
    assert model.params["max_completion_tokens"] >= 1


def test_update_model_parameters_updates_chat_watsonx_params():
    pytest.importorskip("langchain_ibm")
    from langchain_ibm import ChatWatsonx

    from cuga.backend.llm.models import LLMManager

    model = ChatWatsonx.model_construct(
        params={"max_completion_tokens": 1000, "temperature": 0.5},
        max_tokens=1000,
        max_completion_tokens=1000,
        model_id="openai/gpt-oss-120b",
    )
    updated = LLMManager()._update_model_parameters(model, temperature=0.2, max_tokens=8000)

    assert updated.params["max_completion_tokens"] == 8000
    assert updated.params["temperature"] == 0.2
    assert updated.max_completion_tokens == 8000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
