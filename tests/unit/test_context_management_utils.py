from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.cuga_graph.utils.context_management_utils import (
    messages_to_history_text,
    truncate_text_for_context,
)


def test_truncate_text_for_context_noop_when_short():
    text = "hello"
    assert truncate_text_for_context(text, 100) == "hello"


def test_truncate_text_for_context_adds_marker():
    text = "x" * 100
    result = truncate_text_for_context(text, 20, label="Execution output")
    assert result.startswith("x" * 20)
    assert "[Execution output trimmed to 20 chars]" in result


def test_messages_to_history_text_formats_roles():
    messages = [
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    ]
    text = messages_to_history_text(messages)
    assert "User: hi" in text
    assert "Assistant: hello" in text


def test_log_and_track_metrics_records_failure_even_with_empty_error_message():
    """Empty str(exception) must still be detected as a failure shape (issue #563 review)."""
    from unittest.mock import Mock

    from cuga.backend.cuga_graph.utils.context_management_utils import _log_and_track_metrics

    failure_metrics = {
        "error": "",  # str(Exception()) is empty
        "fallback": "kept recent messages only",
        "hard_truncation": True,
        "messages_dropped": 5,
        "messages_kept": 10,
    }
    temp_state = Mock()
    temp_state.last_summarization_metrics = {"chat_messages": failure_metrics}
    tracker = Mock()

    _log_and_track_metrics([Mock()] * 15, [Mock()] * 10, temp_state, tracker)

    tracker.collect_step.assert_called_once()
    step = tracker.collect_step.call_args[0][0]
    assert step.name == "ContextSummarizationFailure"
    assert '"hard_truncation": true' in step.data
