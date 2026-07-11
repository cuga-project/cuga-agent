"""Unit tests for the native tool_calls -> Python transpiler (issue #471 D1).

multi=True emits args via dict-splat ``await name(**{'k': v})`` (keyword/non-
identifier-safe); multi=False (legacy) keeps the byte-identical ``k=v`` kwargs.
"""

import ast

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.response_utils import (
    extract_code_from_response_tool_calls,
)


def _sandbox_body(code):
    """Strip the ```python fence; the block runs inside an async fn in the sandbox."""
    return code.strip("`").removeprefix("python").strip()


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
    assert 'result_0 = await notify(**{\'customer\': "acme"})' in code
    assert 'result_1 = await notify(**{\'customer\': "globex"})' in code
    assert code.count("await notify") == 2
    assert code.index("result_0") < code.index("result_1")


def test_multi_string_args_json_decoded():
    # provider legacy shape: args live in function.arguments as a JSON string
    resp = _Resp([{"function": {"name": "notify", "arguments": '{"customer": "acme"}'}}])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert 'await notify(**{\'customer\': "acme"})' in code


def test_multi_malformed_string_args_become_empty():
    resp = _Resp([{"function": {"name": "notify", "arguments": "not json"}}])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert "await notify()" in code


def test_multi_skips_non_identifier_names_without_splicing():
    resp = _Resp([_tc("send-email", {"to": "x"}), _tc("notify", {"customer": "acme"})])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert "await send-email" not in code  # never spliced (would be a syntax error)
    assert "skipped tool with non-callable name" in code
    assert 'await notify(**{' in code  # the valid call still runs


def test_multi_nameless_call_skipped():
    resp = _Resp([{"args": {"a": 1}, "id": "x", "type": "tool_call"}, _tc("notify", {"customer": "acme"})])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert code.count("await ") == 1  # only the named call


def test_legacy_form_from_additional_kwargs():
    resp = _Resp(tool_calls=None, additional_kwargs={"tool_calls": [_tc("notify", {"customer": "acme"})]})
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert 'await notify(**{\'customer\': "acme"})' in code


def test_multi_keyword_and_non_identifier_arg_names_are_valid_python():
    # 'from' is a Python keyword; '$filter'/'page-size' are not identifiers.
    # Splicing them as k=v kwargs would be a SyntaxError; dict-splat is safe.
    resp = _Resp([_tc("send_email", {"from": "a@x", "$filter": "x eq 1", "page-size": 5})])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    # the block runs inside an async function in the sandbox; compile with top-level await
    compile(_sandbox_body(code), "<transpiled>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    assert "**{" in code and "'from'" in code and "'$filter'" in code and "'page-size'" in code


def test_multi_keyword_tool_name_is_skipped_not_spliced():
    resp = _Resp([_tc("class", {"a": 1}), _tc("notify", {"customer": "acme"})])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert "await class(" not in code
    assert "non-callable name" in code
    assert 'await notify(**{' in code


def test_legacy_single_call_skips_nothing_matches_main_on_malformed_leading():
    # multi=False (default/legacy) must inspect ONLY tool_calls[0]: a malformed
    # leading entry yields None (byte-identical to main), NOT the later valid one.
    resp = _Resp([{"args": {"a": 1}}, _tc("notify", {"customer": "acme"})])  # first is nameless
    assert extract_code_from_response_tool_calls(resp, multi=False) is None
    # multi=True still recovers the valid one
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert code.count("await ") == 1 and "notify" in code
