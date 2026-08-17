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
