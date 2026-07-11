"""Unit tests for the native tool_calls -> Python transpiler (issue #471 D1)."""

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.response_utils import (
    extract_code_from_response_tool_calls,
)


class _Resp:
    """Minimal stand-in exposing the attributes the transpiler reads, so tests
    control the exact tool_call shape (AIMessage would re-validate/normalize)."""

    def __init__(self, tool_calls=None, additional_kwargs=None):
        self.content = ""
        self.tool_calls = tool_calls
        self.additional_kwargs = additional_kwargs or {}


def _tc(name, args):
    return {"name": name, "args": args, "id": f"id_{name}", "type": "tool_call"}


def test_no_tool_calls_returns_none():
    assert extract_code_from_response_tool_calls(_Resp()) is None


def test_legacy_single_call_unchanged():
    # multi=False must reproduce today's output byte for byte
    code = extract_code_from_response_tool_calls(_Resp([_tc("notify", {"customer": "acme"})]), multi=False)
    assert code == '```python\nresult = await notify(customer="acme")\nprint(result)\n```'


def test_legacy_drops_extra_calls():
    # legacy behavior only sees the first call (the D1 bug, preserved when FC off)
    resp = _Resp([_tc("notify", {"customer": "acme"}), _tc("notify", {"customer": "globex"})])
    code = extract_code_from_response_tool_calls(resp, multi=False)
    assert code.count("await notify") == 1


def test_multi_transpiles_all_calls_sequentially():
    resp = _Resp([_tc("notify", {"customer": "acme"}), _tc("notify", {"customer": "globex"})])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert 'result_0 = await notify(customer="acme")' in code
    assert 'result_1 = await notify(customer="globex")' in code
    assert code.count("await notify") == 2
    assert code.index("result_0") < code.index("result_1")


def test_multi_string_args_json_decoded():
    # provider legacy shape: args live in function.arguments as a JSON string
    resp = _Resp([{"function": {"name": "notify", "arguments": '{"customer": "acme"}'}}])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert 'await notify(customer="acme")' in code


def test_multi_malformed_string_args_become_empty():
    resp = _Resp([{"function": {"name": "notify", "arguments": "not json"}}])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert "await notify()" in code


def test_multi_skips_non_identifier_names_without_splicing():
    resp = _Resp([_tc("send-email", {"to": "x"}), _tc("notify", {"customer": "acme"})])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert "await send-email" not in code  # never spliced (would be a syntax error)
    assert "skipped tool with non-identifier name" in code
    assert 'await notify(customer="acme")' in code  # the valid call still runs


def test_multi_nameless_call_skipped():
    resp = _Resp([{"args": {"a": 1}, "id": "x", "type": "tool_call"}, _tc("notify", {"customer": "acme"})])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert code.count("await ") == 1  # only the named call


def test_legacy_form_from_additional_kwargs():
    resp = _Resp(tool_calls=None, additional_kwargs={"tool_calls": [_tc("notify", {"customer": "acme"})]})
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert 'await notify(customer="acme")' in code
