"""normalize_response honors native tool_calls under FC opt-in (issue #471 D1/D2)."""

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter


class _Resp:
    def __init__(self, content="", tool_calls=None, reasoning=None):
        self.content = content
        self.tool_calls = tool_calls
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}


def _tc(name, args):
    return {"name": name, "args": args, "id": f"id_{name}", "type": "tool_call"}


def _adapter(allow_native):
    a = AgentGraphAdapter(
        tracker=None,
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )
    a._allow_native_tool_calls = allow_native
    return a


TWO = [_tc("notify", {"customer": "acme"}), _tc("notify", {"customer": "globex"})]


def test_fc_off_ignores_tool_calls_with_preamble():
    # default behavior preserved: a non-empty preamble stays the content, tool_calls ignored
    content, _ = _adapter(allow_native=False).normalize_response(
        _Resp(content="I'll do it now.", tool_calls=TWO)
    )
    assert content == "I'll do it now."


def test_fc_off_empty_content_recovers_single_call():
    content, _ = _adapter(allow_native=False).normalize_response(_Resp(content="", tool_calls=TWO))
    assert content.count("await notify") == 1  # legacy single-call


def test_fc_on_preamble_executes_all_calls_and_preserves_preamble():
    content, reasoning = _adapter(allow_native=True).normalize_response(
        _Resp(content="I'll notify both now.", tool_calls=TWO)
    )
    assert content.count("await notify") == 2
    assert "I'll notify both now." in (reasoning or "")  # preamble kept as reasoning


def test_fc_on_empty_content_executes_all_calls():
    content, _ = _adapter(allow_native=True).normalize_response(_Resp(content="", tool_calls=TWO))
    assert content.count("await notify") == 2


def test_fc_on_explicit_code_block_wins():
    # if the model wrote a code fence, respect code-act intent and don't transpile tool_calls
    code = "```python\nx = 1\nprint(x)\n```"
    content, _ = _adapter(allow_native=True).normalize_response(_Resp(content=code, tool_calls=TWO))
    assert content == code


def test_fc_on_plain_answer_without_tool_calls_unchanged():
    content, _ = _adapter(allow_native=True).normalize_response(_Resp(content="Here is the answer."))
    assert content == "Here is the answer."


@pytest.mark.parametrize("allow_native", [True, False])
def test_never_raises_on_missing_attrs(allow_native):
    class _Bare:
        content = ""

    content, reasoning = _adapter(allow_native).normalize_response(_Bare())
    assert content == ""
