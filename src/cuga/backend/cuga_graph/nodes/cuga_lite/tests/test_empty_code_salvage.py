"""execute_python calls that arrive without code: where the program can be recovered from."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import (
    call_code,
    has_empty_code,
    salvage_empty_code,
)

pytestmark = pytest.mark.unit


def _call(args, call_id="c1", name="execute_python"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_empty_detection():
    assert has_empty_code([_call({})])
    assert has_empty_code([_call({"code": "   "})])
    assert not has_empty_code([_call({"code": "print(1)"})])
    assert not has_empty_code([_call({}, name="double")]), "only execute_python"
    assert call_code({"code": "x = 1"}) == "x = 1" and call_code(None) == ""


def test_code_under_another_argument_name():
    calls, invalid, how = salvage_empty_code([_call({"script": "print(2)"})], [], "")
    assert calls[0]["args"] == {"code": "print(2)"} and how == ["other_argument"] and invalid == []
    calls, _, how = salvage_empty_code([_call({"anything": "x = await f()\nprint(x)"})], [], "")
    assert calls[0]["args"] == {"code": "x = await f()\nprint(x)"}, "the only string argument, if it compiles"


def test_prose_in_another_argument_is_not_code():
    calls, _, how = salvage_empty_code([_call({"note": "I will now zip the folder."})], [], "")
    assert calls[0]["args"] == {"note": "I will now zip the folder."} and how == []


def test_invalid_call_whose_raw_arguments_are_python():
    raw = "zipped = await compress(path='/a')\nprint(zipped)"
    calls, invalid, how = salvage_empty_code(
        [], [{"name": "execute_python", "args": raw, "id": "r1", "error": "bad"}], ""
    )
    assert calls == [{"name": "execute_python", "args": {"code": raw}, "id": "r1", "type": "tool_call"}]
    assert invalid == [] and how == ["invalid_call_raw_code"]


def test_invalid_call_that_is_not_python_stays_invalid():
    bad = {"name": "execute_python", "args": '{"code": "print(1)', "id": "r1", "error": "bad"}
    calls, invalid, how = salvage_empty_code([], [bad], "")
    assert calls == [] and invalid == [bad] and how == []


def test_fenced_block_in_the_reply_fills_one_empty_call():
    content = "Running this:\n```python\nprint(await double(value=3))\n```"
    calls, _, how = salvage_empty_code([_call({}, "a"), _call({}, "b")], [], content)
    assert calls[0]["args"] == {"code": "print(await double(value=3))"}
    assert calls[1]["args"] == {}, "one block, one call"
    assert how == ["fenced_block_in_reply"]
