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


def test_analysis_channel_is_never_promoted_into_the_answer():
    """Channel-structured output: the protocol puts the answer in the final
    channel. Removing tokens alone welded the channel names on and surfaced the
    model's private analysis — 'analysisLet me thinkfinal42'."""
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    raw = "<|channel|>analysis<|message|>Let me think<|end|><|channel|>final<|message|>42"
    out = strip_harmony_tokens(raw)
    assert out == "42"
    assert "Let me think" not in out


def test_loose_framing_and_plain_text_are_unaffected():
    """The cases that already worked must keep working: a trailing control
    token, indentation before a code block, and non-harmony markers."""
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    assert strip_harmony_tokens("The total is 42<|return|>") == "The total is 42"
    assert strip_harmony_tokens("<|message|>    def foo():\n        return 1") == (
        "    def foo():\n        return 1"
    )
    assert strip_harmony_tokens("Use <|custom|> here.") == "Use <|custom|> here."


def test_stray_message_token_does_not_discard_preceding_text():
    """Dropping everything before <|message|> is only correct when real channel
    framing precedes it. Without a <|channel|> header the token is stray, and
    the text before it is content, not a discarded channel."""
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    assert strip_harmony_tokens("Here is the answer<|message|>42") == "Here is the answer42"


# Harmony defines three assistant channels: final (user-facing), analysis
# (chain-of-thought) and commentary (tool calls). No positional rule picks the
# answer out, which is why these are parsed rather than pattern-matched.


def test_tool_call_channel_is_not_surfaced_as_the_answer():
    """commentary carries tool calls. A 'last message wins' rule would show raw
    tool JSON to the user."""
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    raw = (
        "<|channel|>analysis<|message|>I should call a tool<|end|>"
        '<|channel|>commentary<|message|>{"name":"get_secret"}<|call|>'
    )
    out = strip_harmony_tokens(raw)
    assert out == ""
    assert "get_secret" not in out
    assert "I should call a tool" not in out


def test_analysis_only_output_yields_no_answer():
    """No final channel means no user-facing answer. Returning the analysis
    would be the very leak this guards against."""
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    assert strip_harmony_tokens("<|channel|>analysis<|message|>Thinking out loud<|end|>") == ""


def test_final_channel_wins_even_when_it_is_not_last():
    """A trailing commentary message must not displace the answer."""
    from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens

    raw = '<|channel|>final<|message|>42<|end|><|channel|>commentary<|message|>{"t":"x"}<|call|>'
    assert strip_harmony_tokens(raw) == "42"
