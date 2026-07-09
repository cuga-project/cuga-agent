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
