"""Issue #471 D1 follow-up: unparseable native tool-call args must not silently
execute the tool with empty args.

On the multi (native FC) path an unparseable args payload is skipped with a
visible marker so the model sees it and retries. The legacy single-call path
stays byte-identical to main (unparseable args → empty-arg call)."""

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.response_utils import (
    extract_code_from_response_tool_calls,
)


class _Resp:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.additional_kwargs: dict = {}


def test_multi_path_skips_unparseable_args_with_marker():
    resp = _Resp([{"name": "notify", "args": "{bad json"}])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert "skipped tool call with unparseable args: notify" in code
    assert "await notify(" not in code  # NOT invoked with empty args


def test_multi_path_valid_args_still_execute():
    resp = _Resp([{"name": "notify", "args": '{"customer": "acme"}'}])
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert "await notify(" in code and "skipped" not in code


def test_multi_path_mixed_valid_and_unparseable():
    resp = _Resp(
        [
            {"name": "good", "args": '{"x": 1}'},
            {"name": "bad", "args": "not-json"},
        ]
    )
    code = extract_code_from_response_tool_calls(resp, multi=True)
    assert "await good(" in code  # valid call still runs
    assert "skipped tool call with unparseable args: bad" in code  # bad one skipped
    assert "await bad(" not in code


def test_legacy_path_unchanged_for_unparseable_args():
    resp = _Resp([{"name": "notify", "args": "{bad json"}])
    code = extract_code_from_response_tool_calls(resp, multi=False)
    # byte-identical to main: empty-arg call (legacy path ignores args_ok)
    assert code == "```python\nresult = await notify()\nprint(result)\n```"
