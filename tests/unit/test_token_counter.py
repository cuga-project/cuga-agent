"""
Unit tests for TokenCounter utility.
"""

import pytest
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from cuga.backend.cuga_graph.utils.token_counter import TokenCounter
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
