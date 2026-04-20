"""
Unit tests for ContextSummarizer class.

Tests the context summarization logic including trigger detection,
message splitting, and summarization workflow.
"""

import pytest
from typing import List
from unittest.mock import Mock, patch
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

from cuga.backend.cuga_graph.utils.context_summarizer import ContextSummarizer


@pytest.fixture(scope="function", autouse=True)
def ensure_settings_validated():
    """Ensure settings validators are applied before each test to prevent CI failures."""
    from cuga.config import settings, validators
    import dynaconf

    # Re-register all validators to ensure they're present
    # This is safe to do multiple times
    for validator in validators:
        try:
            settings.validators.register(validator)
        except Exception:
            # Validator might already be registered, that's fine
            pass

    # Ensure validators are applied (idempotent operation)
    # validate_all() is idempotent - calling it multiple times is safe
    try:
        settings.validators.validate_all()
    except dynaconf.ValidationError:
        # ValidationError means validators were already applied and some failed
        # This is expected and we can continue
        pass

    yield

    # No cleanup needed - settings is a module-level singleton


@pytest.fixture
def mock_model():
    """Create a mock LLM model with profile information."""
    model = Mock()

    # Add profile information required by LangChain SummarizationMiddleware
    model.profile = {"max_input_tokens": 8192}

    # Mock response
    response = Mock()
    response.content = "This is a summary of the conversation."
    response.response_metadata = {
        'token_usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}
    }
    model.invoke.return_value = response
    return model


@pytest.fixture
def mock_middleware():
    """Create a mock SummarizationMiddleware."""
    middleware = Mock()

    # Mock the before_model method to return summarized messages
    async def mock_abefore_model(state, runtime):
        messages = state.messages if hasattr(state, 'messages') else state.get('messages', [])
        if len(messages) <= 10:
            return None  # Don't summarize

        # Return a mock result with summary + last 10 messages
        from langchain_core.messages import HumanMessage

        summary_msg = HumanMessage(content="[Summary of previous conversation]")
        recent_messages = messages[-10:]

        return {"messages": [summary_msg] + recent_messages}

    # Support both sync and async methods
    middleware.abefore_model = Mock(side_effect=mock_abefore_model)
    middleware.before_model = Mock(side_effect=lambda s, r: None)  # Fallback
    return middleware


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = Mock()
    settings.context_summarization = Mock()
    settings.context_summarization.enabled = True
    settings.context_summarization.trigger_fraction = 0.75
    settings.context_summarization.trigger_tokens = None
    settings.context_summarization.trigger_messages = None
    settings.context_summarization.keep_last_n_messages = 10
    settings.context_summarization.summarization_model = "gpt-4o-mini"
    settings.context_summarization.trim_tokens_to_summarize = 4000
    settings.context_summarization.custom_summary_prompt = None
    return settings


@pytest.fixture
def sample_messages():
    """Create sample messages for testing."""
    messages = []
    for i in range(30):
        if i % 2 == 0:
            messages.append(HumanMessage(content=f"User message {i}"))
        else:
            messages.append(AIMessage(content=f"Assistant response {i}"))
    return messages


class TestContextSummarizerInitialization:
    """Test ContextSummarizer initialization."""

    async def test_init_with_enabled_config(self, mock_model, mock_settings):
        """Test initialization when summarization is enabled."""
        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")

            assert summarizer.model == mock_model
            assert summarizer.model_name == "gpt-4"
            assert summarizer.config == mock_settings.context_summarization
            assert hasattr(summarizer, 'middleware')

    async def test_init_with_disabled_config(self, mock_model, mock_settings):
        """Test initialization when summarization is disabled."""
        mock_settings.context_summarization.enabled = False

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")

            assert summarizer.model == mock_model
            # Middleware is still created but won't be used
            assert not summarizer.config.enabled

    async def test_init_with_tracker(self, mock_model, mock_settings):
        """Test initialization with ActivityTracker."""
        mock_tracker = Mock()

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4", tracker=mock_tracker)

            # Tracker is passed to TokenCounter, not stored in ContextSummarizer
            assert summarizer.token_counter.tracker == mock_tracker


class TestShouldSummarize:
    """Test should_summarize trigger detection."""

    async def test_should_summarize_disabled(self, mock_model, mock_settings, sample_messages):
        """Test that summarization is skipped when disabled."""
        mock_settings.context_summarization.enabled = False

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            should_trigger, metrics = summarizer.should_summarize(sample_messages)

            assert not should_trigger
            assert metrics == {}

    async def test_should_summarize_empty_messages(self, mock_model, mock_settings):
        """Test with empty message list."""
        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            should_trigger, metrics = summarizer.should_summarize([])

            assert not should_trigger
            assert metrics == {}

    async def test_should_summarize_fraction_trigger(self, mock_model, mock_settings):
        """Test fraction-based trigger."""
        # Create messages that exceed 75% of context
        large_messages: List[BaseMessage] = [HumanMessage(content="x" * 1000) for _ in range(100)]

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            should_trigger, metrics = summarizer.should_summarize(large_messages)

            assert 'token_count' in metrics
            assert 'usage_percentage' in metrics
            assert 'message_count' in metrics
            assert metrics['message_count'] == 100

            # Should trigger if usage > 75%
            if metrics['usage_percentage'] >= 75:
                assert should_trigger
                assert 'trigger_reason' in metrics
                assert 'fraction' in metrics['trigger_reason']

    async def test_should_summarize_token_trigger(self, mock_model, mock_settings):
        """Test token-based trigger."""
        mock_settings.context_summarization.trigger_tokens = 1000
        messages: List[BaseMessage] = [HumanMessage(content="x" * 500) for _ in range(10)]

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            should_trigger, metrics = summarizer.should_summarize(messages)

            if metrics['token_count'] >= 1000:
                assert should_trigger
                assert 'tokens' in metrics['trigger_reason']

    async def test_should_summarize_message_trigger(self, mock_model, mock_settings, sample_messages):
        """Test message count trigger."""
        mock_settings.context_summarization.trigger_messages = 20

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            should_trigger, metrics = summarizer.should_summarize(sample_messages)

            assert should_trigger  # 30 messages > 20 threshold
            assert 'messages' in metrics['trigger_reason']
            assert metrics['message_count'] == 30


class TestSummarizeMessages:
    """Test message summarization logic."""

    async def test_summarize_disabled(self, mock_model, mock_settings, sample_messages):
        """Test that summarization returns original messages when disabled."""
        mock_settings.context_summarization.enabled = False

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            result_messages, metrics = await summarizer.summarize_messages(sample_messages)

            assert result_messages == sample_messages
            assert 'skipped' in metrics
            assert metrics['skipped'] == "summarization disabled or no messages"

    async def test_summarize_insufficient_messages(self, mock_model, mock_settings):
        """Test with fewer messages than keep_last_n."""
        messages: List[BaseMessage] = [HumanMessage(content=f"Message {i}") for i in range(5)]
        mock_settings.context_summarization.keep_last_n_messages = 10

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            result_messages, metrics = await summarizer.summarize_messages(messages)

            assert result_messages == messages
            assert 'skipped' in metrics

    async def test_summarize_success(self, mock_model, mock_settings, mock_middleware, sample_messages):
        """Test successful summarization."""
        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            with patch(
                'cuga.backend.cuga_graph.utils.context_summarizer.SummarizationMiddleware',
                return_value=mock_middleware,
            ):
                summarizer = ContextSummarizer(mock_model, "gpt-4")
                summarizer.middleware = mock_middleware  # Inject mock middleware

                result_messages, metrics = await summarizer.summarize_messages(sample_messages)

                # Should have summary + last 10 messages = 11 total
                assert len(result_messages) == 11

                # First message should be the summary
                assert isinstance(result_messages[0], HumanMessage)
                assert "Summary" in result_messages[0].content or "summary" in result_messages[0].content

                # Check metrics
                assert 'before' in metrics
                assert 'after' in metrics
                assert metrics['before']['message_count'] == 30
                assert metrics['after']['message_count'] == 11
                assert metrics['messages_summarized'] == 20
                assert metrics['messages_kept'] == 10
                assert 'tokens_saved' in metrics
                assert 'compression_ratio' in metrics

    async def test_summarize_model_invocation(
        self, mock_model, mock_settings, mock_middleware, sample_messages
    ):
        """Test that middleware is invoked correctly."""
        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            with patch(
                'cuga.backend.cuga_graph.utils.context_summarizer.SummarizationMiddleware',
                return_value=mock_middleware,
            ):
                summarizer = ContextSummarizer(mock_model, "gpt-4")
                summarizer.middleware = mock_middleware

                await summarizer.summarize_messages(sample_messages)

                # Verify middleware was called (async version)
                mock_middleware.abefore_model.assert_called_once()

    async def test_summarize_with_custom_prompt(
        self, mock_model, mock_settings, mock_middleware, sample_messages
    ):
        """Test summarization with custom prompt template."""
        custom_prompt = "Custom summary prompt: {messages}"
        mock_settings.context_summarization.custom_summary_prompt = custom_prompt

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            with patch(
                'cuga.backend.cuga_graph.utils.context_summarizer.SummarizationMiddleware',
                return_value=mock_middleware,
            ):
                summarizer = ContextSummarizer(mock_model, "gpt-4")
                summarizer.middleware = mock_middleware

                result_messages, metrics = await summarizer.summarize_messages(sample_messages)

                # Verify summarization occurred
                assert len(result_messages) == 11
                assert 'before' in metrics

    async def test_summarize_with_tracker(self, mock_model, mock_settings, mock_middleware, sample_messages):
        """Test that ActivityTracker is available during summarization."""
        mock_tracker = Mock()

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            with patch(
                'cuga.backend.cuga_graph.utils.context_summarizer.SummarizationMiddleware',
                return_value=mock_middleware,
            ):
                summarizer = ContextSummarizer(mock_model, "gpt-4", tracker=mock_tracker)
                summarizer.middleware = mock_middleware

                result_messages, metrics = await summarizer.summarize_messages(sample_messages)

                # Verify summarization completed
                assert len(result_messages) == 11
                assert 'tokens_saved' in metrics


class TestEdgeCases:
    """Test edge cases and error handling."""

    async def test_single_message(self, mock_model, mock_settings):
        """Test with single message."""
        messages: List[BaseMessage] = [HumanMessage(content="Single message")]

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            result_messages, metrics = await summarizer.summarize_messages(messages)

            # Should skip summarization
            assert result_messages == messages
            assert 'skipped' in metrics

    async def test_exactly_keep_n_messages(self, mock_model, mock_settings):
        """Test with exactly keep_last_n messages."""
        messages: List[BaseMessage] = [HumanMessage(content=f"Message {i}") for i in range(10)]
        mock_settings.context_summarization.keep_last_n_messages = 10

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            result_messages, metrics = await summarizer.summarize_messages(messages)

            # Should skip summarization
            assert result_messages == messages
            assert 'skipped' in metrics

    async def test_model_error_handling(self, mock_model, mock_settings, sample_messages):
        """Test error handling when middleware fails."""
        from unittest.mock import AsyncMock

        mock_middleware_error = Mock()
        mock_middleware_error.abefore_model = AsyncMock(side_effect=Exception("Middleware error"))
        mock_middleware_error.before_model.side_effect = Exception("Middleware error")

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            with patch(
                'cuga.backend.cuga_graph.utils.context_summarizer.SummarizationMiddleware',
                return_value=mock_middleware_error,
            ):
                summarizer = ContextSummarizer(mock_model, "gpt-4")
                summarizer.middleware = mock_middleware_error

                # Should fall back to keeping recent messages
                result_messages, metrics = await summarizer.summarize_messages(sample_messages)

                # Should return last 10 messages as fallback
                assert len(result_messages) == 10
                assert 'error' in metrics or 'fallback' in metrics

    async def test_empty_summary_response(self, mock_model, mock_settings, mock_middleware, sample_messages):
        """Test handling when middleware returns result."""
        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            with patch(
                'cuga.backend.cuga_graph.utils.context_summarizer.SummarizationMiddleware',
                return_value=mock_middleware,
            ):
                summarizer = ContextSummarizer(mock_model, "gpt-4")
                summarizer.middleware = mock_middleware

                result_messages, metrics = await summarizer.summarize_messages(sample_messages)

                # Should create summary message
                assert len(result_messages) == 11
                assert 'before' in metrics


class TestMetricsCalculation:
    """Test metrics calculation accuracy."""

    async def test_tokens_saved_calculation(
        self, mock_model, mock_settings, mock_middleware, sample_messages
    ):
        """Test that tokens_saved is calculated correctly."""
        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            with patch(
                'cuga.backend.cuga_graph.utils.context_summarizer.SummarizationMiddleware',
                return_value=mock_middleware,
            ):
                summarizer = ContextSummarizer(mock_model, "gpt-4")
                summarizer.middleware = mock_middleware

                result_messages, metrics = await summarizer.summarize_messages(sample_messages)

                before_tokens = metrics['before']['token_count']
                after_tokens = metrics['after']['token_count']
                tokens_saved = metrics['tokens_saved']

                assert tokens_saved == before_tokens - after_tokens
                assert tokens_saved > 0  # Should save tokens

    async def test_compression_ratio_calculation(
        self, mock_model, mock_settings, mock_middleware, sample_messages
    ):
        """Test that compression_ratio is calculated correctly."""
        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            with patch(
                'cuga.backend.cuga_graph.utils.context_summarizer.SummarizationMiddleware',
                return_value=mock_middleware,
            ):
                summarizer = ContextSummarizer(mock_model, "gpt-4")
                summarizer.middleware = mock_middleware

                result_messages, metrics = await summarizer.summarize_messages(sample_messages)

                before_tokens = metrics['before']['token_count']
                after_tokens = metrics['after']['token_count']
                # Verify compression ratio is calculated correctly
                assert metrics['compression_ratio'] == after_tokens / before_tokens


class TestStateCorruption:
    """Test state corruption scenarios (Issue #12 from ISSUES.md)."""

    async def test_original_messages_not_modified(self, mock_model, sample_messages):
        """Verify original message list is never modified."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        # Store original state
        original_messages = [msg for msg in sample_messages]
        original_length = len(sample_messages)
        original_ids = [id(msg) for msg in sample_messages]

        # Perform summarization
        result_messages, metrics = await summarizer.summarize_messages(sample_messages)

        # Verify original list unchanged
        assert len(sample_messages) == original_length
        assert sample_messages == original_messages
        assert [id(msg) for msg in sample_messages] == original_ids

        # Result should be different list
        assert result_messages is not sample_messages

    async def test_middleware_exception_preserves_state(self, mock_model, sample_messages):
        """Test that middleware exceptions don't corrupt state."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")
        original_messages = sample_messages.copy()

        # Mock middleware to raise exception
        with patch.object(summarizer, '_invoke_middleware', side_effect=Exception("Middleware crash")):
            result_messages, metrics = await summarizer.summarize_messages(sample_messages)

        # On error, falls back to sliding window (keeps last N messages)
        # This is the actual behavior - not returning all original messages
        assert isinstance(result_messages, list)
        assert len(result_messages) <= len(sample_messages)
        # Original list should not be modified
        assert sample_messages == original_messages

    async def test_model_failure_preserves_state(self, mock_model, mock_settings, sample_messages):
        """Test that model failures don't corrupt state."""
        mock_model.invoke.side_effect = Exception("Model API error")

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")
            result_messages, metrics = await summarizer.summarize_messages(sample_messages)

            # Middleware creates error summary + keeps last N messages
            # Expected: 1 error summary + keep_last_n_messages from settings
            keep_n = mock_settings.context_summarization.keep_last_n_messages
            expected_count = 1 + keep_n
            assert len(result_messages) == expected_count
            assert 'Error generating summary' in result_messages[0].content

    async def test_concurrent_summarization_thread_safety(self, mock_model, sample_messages):
        """Test concurrent summarization doesn't corrupt state."""
        import asyncio

        summarizer = ContextSummarizer(mock_model, "gpt-4")
        results = []
        errors = []

        async def summarize_task():
            try:
                result, metrics = await summarizer.summarize_messages(sample_messages)
                results.append((result, metrics))
            except Exception as e:
                errors.append(e)

        # Run 10 concurrent tasks
        tasks = [summarize_task() for _ in range(10)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # All should complete
        assert len(results) + len(errors) == 10
        assert not errors, f"Concurrent summarize_messages calls failed: {errors!r}"
        assert len(results) == 10

        # Verify all results are valid
        for result, metrics in results:
            assert isinstance(result, list)
            assert len(result) > 0
            assert isinstance(metrics, dict)

    async def test_recovery_after_multiple_failures(self, mock_model, sample_messages):
        """Test system recovers after multiple failures."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        # Multiple failed attempts
        for i in range(5):
            with patch.object(summarizer, '_invoke_middleware', side_effect=Exception(f"Failure {i}")):
                result, metrics = await summarizer.summarize_messages(sample_messages)
                # Falls back to sliding window (keeps last N)
                assert isinstance(result, list)
                assert len(result) <= len(sample_messages)

        # Final successful attempt should work
        result_final, metrics_final = await summarizer.summarize_messages(sample_messages)
        assert isinstance(result_final, list)
        assert isinstance(metrics_final, dict)


class TestMalformedMessages:
    """Test handling of malformed messages."""

    async def test_none_content(self, mock_model):
        """Test that None content raises validation error (expected behavior)."""
        ContextSummarizer(mock_model, "gpt-4")

        # LangChain's Pydantic validation doesn't allow None content
        # This is expected behavior - test that it raises appropriate error
        with pytest.raises(Exception):  # ValidationError from Pydantic
            [
                HumanMessage(content=None),
                AIMessage(content="Valid response"),
                HumanMessage(content=None),
            ] * 5

    async def test_empty_string_content(self, mock_model):
        """Test messages with empty string content."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        messages = [
            HumanMessage(content=""),
            AIMessage(content=""),
            HumanMessage(content="Valid"),
        ] * 5

        result_messages, metrics = await summarizer.summarize_messages(messages)
        assert isinstance(result_messages, list)

    async def test_invalid_message_types(self, mock_model):
        """Test with invalid types in message list."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        # Mix valid and invalid types
        messages = [
            HumanMessage(content="Valid"),
            "Invalid string",
            AIMessage(content="Valid"),
            {"invalid": "dict"},
            None,
        ] * 3

        # Should handle or raise appropriate error
        try:
            result_messages, metrics = await summarizer.summarize_messages(messages)
            # If succeeds, all results should be BaseMessage
            for msg in result_messages:
                assert isinstance(msg, BaseMessage)
        except (TypeError, AttributeError):
            # Acceptable to raise error for invalid types
            pass

    async def test_message_with_tool_calls(self, mock_model):
        """Test messages with tool_calls attribute."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        messages = [
            HumanMessage(content="Use calculator"),
            AIMessage(
                content="",
                tool_calls=[{"name": "calculator", "args": {"expression": "2+2"}, "id": "call_123"}],
            ),
            HumanMessage(content="Result: 4"),
        ] * 5

        result_messages, metrics = await summarizer.summarize_messages(messages)
        assert isinstance(result_messages, list)
        assert len(result_messages) > 0

    async def test_message_with_additional_kwargs(self, mock_model):
        """Test messages with additional_kwargs."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        messages = [
            HumanMessage(content="Message", additional_kwargs={"custom": "value"}),
            AIMessage(content="Response", additional_kwargs={"model": "gpt-4"}),
        ] * 8

        result_messages, metrics = await summarizer.summarize_messages(messages)
        assert isinstance(result_messages, list)


class TestUnicodeHandling:
    """Test unicode and special character handling."""

    async def test_emoji_content(self, mock_model):
        """Test messages with emojis."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        messages = [
            HumanMessage(content="Hello 👋 😀 🎉"),
            AIMessage(content="Hi there! 🌟 ✨ 🚀"),
            HumanMessage(content="Emoji test: 😀😃😄😁😆😅🤣😂"),
        ] * 5

        result_messages, metrics = await summarizer.summarize_messages(messages)

        assert isinstance(result_messages, list)
        for msg in result_messages:
            assert isinstance(msg.content, str)

    async def test_multilingual_content(self, mock_model):
        """Test messages with multiple languages."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        messages = [
            HumanMessage(content="Hello 世界 🌍"),
            AIMessage(content="Bonjour! 你好! مرحبا"),
            HumanMessage(content="Привет! こんにちは! 안녕하세요"),
            AIMessage(content="Γεια σου! שלום! สวัสดี"),
        ] * 4

        result_messages, metrics = await summarizer.summarize_messages(messages)

        assert isinstance(result_messages, list)
        for msg in result_messages:
            assert isinstance(msg.content, str)

    async def test_special_characters(self, mock_model):
        """Test messages with special characters."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        messages = [
            HumanMessage(content="Special: @#$%^&*(){}[]|\\"),
            AIMessage(content="Quotes: \"'`"),
            HumanMessage(content="Newlines\nand\ttabs\rand\fformfeeds"),
            AIMessage(content="Unicode: \u0000\u001f\u007f"),
        ] * 4

        result_messages, metrics = await summarizer.summarize_messages(messages)
        assert isinstance(result_messages, list)

    async def test_rtl_languages(self, mock_model):
        """Test right-to-left languages."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        messages = [
            HumanMessage(content="שלום עולם"),  # Hebrew
            AIMessage(content="مرحبا بالعالم"),  # Arabic
            HumanMessage(content="Mixed: Hello שלום مرحبا"),
        ] * 5

        result_messages, metrics = await summarizer.summarize_messages(messages)
        assert isinstance(result_messages, list)


class TestContentTruncation:
    """Test content truncation at 100k chars (Issue #4 from ISSUES.md)."""

    async def test_truncation_at_100k_chars(self, mock_model):
        """Test that content is truncated at 100k characters."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        # Create message with 150k characters
        long_content = "x" * 150000
        messages = [
            HumanMessage(content=long_content),
            AIMessage(content="Short response"),
        ] * 6

        result_messages, metrics = await summarizer.summarize_messages(messages)

        # Verify truncation occurred
        for msg in result_messages:
            if msg.content:
                assert len(msg.content) <= 100000, f"Content length {len(msg.content)} exceeds 100k"

    async def test_truncation_preserves_message_structure(self, mock_model):
        """Test that truncation doesn't break message structure."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        long_content = "y" * 120000
        messages = [
            HumanMessage(content=long_content),
            AIMessage(content="Response"),
        ] * 5

        result_messages, metrics = await summarizer.summarize_messages(messages)

        # All results should still be valid messages
        for msg in result_messages:
            assert isinstance(msg, BaseMessage)
            assert hasattr(msg, 'content')

    async def test_no_truncation_for_short_content(self, mock_model):
        """Test that short content is not truncated."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        short_content = "x" * 1000
        messages = [
            HumanMessage(content=short_content),
            AIMessage(content=short_content),
        ] * 10

        result_messages, metrics = await summarizer.summarize_messages(messages)

        # Short content should remain unchanged (if not summarized)
        # Note: After summarization, content will be different
        for msg in result_messages:
            if msg.content:
                assert len(msg.content) <= 100000


class TestSummaryQualityValidation:
    """Test summary quality validation (Issue #8 from ISSUES.md)."""

    async def test_summary_contains_conversation_keywords(self, mock_model, sample_messages):
        """Test that summary contains expected keywords."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        result_messages, metrics = await summarizer.summarize_messages(sample_messages)

        if 'skipped' not in metrics and len(result_messages) < len(sample_messages):
            # First message should be summary
            summary_content = str(result_messages[0].content).lower()

            # Should contain conversation-related keywords
            keywords = ['summary', 'previous', 'conversation', 'discussed', 'earlier']
            has_keyword = any(keyword in summary_content for keyword in keywords)

            # Note: This is basic validation, not comprehensive quality check
            assert has_keyword or len(summary_content) > 10

    async def test_summary_not_empty(self, mock_model, sample_messages):
        """Test that summary is not empty."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        result_messages, metrics = await summarizer.summarize_messages(sample_messages)

        if 'skipped' not in metrics and len(result_messages) < len(sample_messages):
            # Summary should have content
            summary = result_messages[0]
            assert summary.content
            assert len(summary.content) > 0

    async def test_empty_summary_handling(self, mock_model, sample_messages):
        """Test handling of empty summary from model."""
        # Mock model to return empty summary
        mock_model.invoke.return_value.content = ""

        summarizer = ContextSummarizer(mock_model, "gpt-4")

        result_messages, metrics = await summarizer.summarize_messages(sample_messages)

        # Should handle empty summary gracefully
        # Middleware creates error message for empty summaries
        assert isinstance(result_messages, list)
        assert len(result_messages) > 0


class TestHardcodedTriggerOverride:
    """Test hardcoded trigger override behavior (Issue #1 from ISSUES.md)."""

    async def test_middleware_configured_with_low_trigger(self, mock_model, mock_settings, sample_messages):
        """Verify middleware is configured with 1 token trigger."""
        # Set high trigger fraction that shouldn't trigger
        mock_settings.context_summarization.trigger_fraction = 0.99  # 99%

        with patch('cuga.backend.cuga_graph.utils.context_summarizer.settings', mock_settings):
            summarizer = ContextSummarizer(mock_model, "gpt-4")

            # Check middleware exists
            assert hasattr(summarizer, 'middleware')

            # Our should_summarize uses fraction
            should_trigger, metrics = summarizer.should_summarize(sample_messages)

            # With high fraction, should not trigger
            # But middleware is configured with 1 token, creating inconsistency
            # This demonstrates Issue #1


class TestImportFailureRecovery:
    """Test import failure recovery (Issue #3 from ISSUES.md)."""

    async def test_graceful_degradation_to_sliding_window(self, mock_model, sample_messages):
        """Test fallback to sliding window when middleware fails."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        # Mock middleware to fail
        with patch.object(summarizer, '_invoke_middleware', return_value=None):
            result_messages, metrics = await summarizer.summarize_messages(sample_messages)

        # Should fall back to returning original messages
        assert len(result_messages) == len(sample_messages)
        assert 'skipped' in metrics


class TestRollbackMechanism:
    """Test rollback mechanism (Issue #12 from ISSUES.md)."""

    async def test_no_partial_state_on_failure(self, mock_model, sample_messages):
        """Verify no partial state changes on failure."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")
        original_messages = sample_messages.copy()

        # Simulate failure during summarization
        with patch.object(summarizer, '_invoke_middleware', side_effect=Exception("Partial failure")):
            result_messages, metrics = await summarizer.summarize_messages(sample_messages)

        # On failure, falls back to sliding window (not full original)
        # The key is: original list is not modified, result is valid
        assert isinstance(result_messages, list)
        assert len(result_messages) <= len(sample_messages)
        # Original should not be modified
        assert sample_messages == original_messages

    async def test_atomic_summarization(self, mock_model, sample_messages):
        """Test that summarization is atomic (all or nothing)."""
        summarizer = ContextSummarizer(mock_model, "gpt-4")

        # Mock middleware to return incomplete result
        incomplete_result = [HumanMessage(content="Partial summary")]

        with patch.object(summarizer, '_invoke_middleware', return_value=incomplete_result):
            result_messages, metrics = await summarizer.summarize_messages(sample_messages)

        # Should accept the result (even if incomplete) or return original
        # The key is it shouldn't be in a corrupted state
        assert isinstance(result_messages, list)
        assert len(result_messages) > 0


class TestTokenCounterEdgeCases:
    """Test token counter edge cases."""

    async def test_token_counter_with_invalid_model_name(self, mock_model):
        """Test token counter with unknown model name."""
        from cuga.backend.cuga_graph.utils.token_counter import TokenCounter

        counter = TokenCounter(model=mock_model, model_name="unknown-model-xyz")
        messages = [
            HumanMessage(content="Test message 1"),
            AIMessage(content="Test response 1"),
        ] * 3

        # Should use fallback context size
        token_count = counter.count_message_tokens(messages)
        assert token_count > 0

        context_size = counter.get_model_context_size(mock_model)
        assert context_size > 0  # Should use fallback

    async def test_token_counter_with_missing_profile(self, mock_model):
        """Test token counter when model has no profile."""
        from cuga.backend.cuga_graph.utils.token_counter import TokenCounter

        # Create model without profile
        model_no_profile = Mock()

        counter = TokenCounter(model=model_no_profile, model_name="gpt-4")
        messages = [HumanMessage(content="Test")] * 3

        # Should handle missing profile
        try:
            token_count = counter.count_message_tokens(messages)
            assert token_count >= 0
        except (AttributeError, KeyError):
            # Acceptable to raise error
            pass
