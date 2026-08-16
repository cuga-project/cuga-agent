import pytest
from langchain_core.messages import AIMessage

pytestmark = pytest.mark.unit

RAW = "<|channel|>final<|message|>The total is 42<|return|>"


def test_lite_boundary_sanitizes_content():
    """CugaLite decode boundary: the value that becomes final_answer, is streamed
    as a CodeAgent event, and lands in state.messages is clean at source."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

    adapter = object.__new__(AgentGraphAdapter)
    content, _ = AgentGraphAdapter.normalize_response(adapter, AIMessage(content=RAW))
    assert "<|" not in content
    assert "The total is 42" in content


def test_base_boundary_sanitizes_content():
    """Supervisor/base adapter takes the same path."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter

    content, _ = CoreGraphAdapter.normalize_response(object(), AIMessage(content=RAW))
    assert "<|" not in content
    assert "The total is 42" in content


def test_legitimate_custom_markers_survive():
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter

    text = "Use <|custom|> and <|im_end|> as delimiters."
    content, _ = CoreGraphAdapter.normalize_response(object(), AIMessage(content=text))
    assert content == text


# ── channel-aware stripping ────────────────────────────────────────────────
# Removing tokens alone is only correct for loose/trailing framing. On
# channel-structured output it welds channel names onto the text and promotes
# the model's private analysis channel into the answer.


def test_trailing_control_token_is_removed():
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    assert strip_harmony_tokens("The total is 42<|return|>") == "The total is 42"


def test_channel_structured_output_keeps_only_the_final_channel():
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    raw = "<|channel|>final<|message|>The total is 42<|return|>"
    # Plain token removal would yield "finalThe total is 42".
    assert strip_harmony_tokens(raw) == "The total is 42"


def test_analysis_channel_is_never_promoted_into_the_answer():
    """The important one: the analysis channel is the model's private reasoning.
    Plain token removal turned this into 'analysisLet me thinkfinal42' — both
    mangled and a chain-of-thought leak."""
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    raw = "<|channel|>analysis<|message|>Let me think<|end|><|channel|>final<|message|>42"
    out = strip_harmony_tokens(raw)
    assert out == "42"
    assert "Let me think" not in out


def test_indentation_survives_channel_extraction():
    """A token directly before an indented block must not take the block's
    leading whitespace with it (would corrupt Markdown code blocks)."""
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    assert strip_harmony_tokens("<|message|>    def foo():\n        return 1") == (
        "    def foo():\n        return 1"
    )


def test_non_harmony_text_is_untouched():
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    for text in ("No framing here at all.", "Use <|custom|> and <|im_end|> as delimiters."):
        assert strip_harmony_tokens(text) == text
