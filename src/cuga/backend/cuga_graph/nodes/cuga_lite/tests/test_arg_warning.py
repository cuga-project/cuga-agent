"""Tests for the detection-only tool-arg warning (former Wave-1 Change #4).

Change #4's coercion was removed (WONTFIX — the failure does not occur on the
current M3 corpus; see arg_warning.py). What remains is a pure detector: it
flags suspect argument shapes in the logs but NEVER mutates the call. These
tests lock in that it (a) recognizes the dict-as-string / list-wrap / stringized
-number shapes, (b) stays silent on valid scalars, and (c) forwards every call
unchanged.
"""

from __future__ import annotations

import inspect
from typing import Optional

import pytest
from pydantic import Field, create_model

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.arg_warning import (
    jsonschema_type,
    make_arg_warning_callable,
    suspect_reason,
    warn_suspect_kwargs,
)

DirectorModel = create_model("DirectorInput", director=(str, Field(...)))
YearModel = create_model("YearInput", year=(int, Field(...)))
OptionalModel = create_model("OptInput", a=(str, Field(...)), b=(Optional[int], Field(default=None)))


# ── jsonschema_type ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "ann,expected",
    [
        (str, "string"),
        (int, "integer"),
        (float, "number"),
        (bool, "boolean"),
        (list, "array"),
        (dict, "object"),
        (Optional[int], "integer"),
        (Optional[str], "string"),
    ],
)
def test_jsonschema_type(ann, expected):
    assert jsonschema_type(ann) == expected


# ── suspect_reason: the malformed shapes ─────────────────────────────────────
def test_dict_as_string_is_suspect():
    assert "dict-as-string" in suspect_reason("string", {"director": "X"})


def test_dict_any_shape_on_scalar_is_suspect():
    # multi-key dict on a scalar param is still clearly wrong
    assert suspect_reason("string", {"a": 1, "b": 2}) is not None


def test_list_wrap_is_suspect():
    assert "list" in suspect_reason("string", ["X"])


def test_stringized_int_is_suspect():
    assert suspect_reason("integer", "2010") == "stringized integer"


def test_stringized_number_is_suspect():
    assert suspect_reason("number", "3.5") == "stringized number"


# ── suspect_reason: things that must stay silent ─────────────────────────────
def test_valid_scalar_not_suspect():
    assert suspect_reason("string", "Wolfgang Reitherman") is None


def test_valid_int_not_suspect():
    assert suspect_reason("integer", 2010) is None


def test_non_numeric_string_for_int_not_flagged():
    # a non-numeric string is the model's mistake but not the dict-as-string shape
    assert suspect_reason("integer", "abc") is None


def test_none_not_suspect():
    assert suspect_reason("string", None) is None


def test_non_scalar_param_never_suspect():
    # array/object params can legitimately receive lists/dicts
    assert suspect_reason("array", ["X"]) is None
    assert suspect_reason("object", {"a": 1}) is None


# ── warn_suspect_kwargs: logs, never mutates ─────────────────────────────────
def test_warn_logs_for_suspect_arg(caplog):
    kwargs = {"director": {"director": "X"}}
    field_types = {"director": "string"}
    with caplog.at_level("WARNING"):
        warn_suspect_kwargs(kwargs, field_types, model_name="DirectorInput")
    assert kwargs == {"director": {"director": "X"}}  # unchanged


def test_warn_ignores_unknown_keys():
    kwargs = {"zzz": {"a": 1}}
    warn_suspect_kwargs(kwargs, {"director": "string"}, model_name="DirectorInput")
    assert kwargs == {"zzz": {"a": 1}}


# ── make_arg_warning_callable: end-to-end, forwards UNCHANGED ─────────────────
def _recorder():
    received = {}

    async def tool_func(*args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs
        return {"ok": True}

    return tool_func, received


def _sync_recorder():
    """A *synchronous* tool callable — mirrors StructuredTool.func/_run, which
    prepare_tools_and_apps also routes through make_arg_warning_callable."""
    received = {}

    def tool_func(*args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs
        return {"ok": True}

    return tool_func, received


def test_sync_tool_func_yields_sync_wrapper():
    """A sync ``.func``/``._run`` callable must stay sync through the wrapper, so
    downstream ``make_tool_awaitable`` dispatches it to a worker thread via
    ``run_in_executor`` instead of awaiting it inline on the event loop (which
    a blocking sync tool could otherwise stall). Contract: sync in -> sync out."""
    tf, rec = _sync_recorder()
    wrapped = make_arg_warning_callable(tf, DirectorModel, enable=True)
    assert not inspect.iscoroutinefunction(wrapped)
    result = wrapped(director={"director": "X"})
    assert result == {"ok": True}
    assert rec["kwargs"] == {"director": {"director": "X"}}  # NOT coerced


@pytest.mark.asyncio
async def test_async_tool_func_yields_async_wrapper():
    """An async ``.coroutine`` callable keeps an awaitable wrapper."""
    tf, rec = _recorder()
    wrapped = make_arg_warning_callable(tf, DirectorModel, enable=True)
    assert inspect.iscoroutinefunction(wrapped)
    result = await wrapped(director={"director": "X"})
    assert result == {"ok": True}
    assert rec["kwargs"] == {"director": {"director": "X"}}  # NOT coerced


@pytest.mark.asyncio
async def test_wrapper_disabled_is_identity():
    tf, _ = _recorder()
    assert make_arg_warning_callable(tf, DirectorModel, enable=False) is tf


@pytest.mark.asyncio
async def test_wrapper_no_schema_is_identity():
    tf, _ = _recorder()
    assert make_arg_warning_callable(tf, None, enable=True) is tf


@pytest.mark.asyncio
async def test_wrapper_forwards_dict_as_string_unchanged():
    tf, rec = _recorder()
    wrapped = make_arg_warning_callable(tf, DirectorModel, enable=True)
    await wrapped(director={"director": "X"})
    assert rec["kwargs"] == {"director": {"director": "X"}}  # NOT coerced


@pytest.mark.asyncio
async def test_wrapper_forwards_stringized_int_unchanged():
    tf, rec = _recorder()
    wrapped = make_arg_warning_callable(tf, YearModel, enable=True)
    await wrapped(year="2010")
    assert rec["kwargs"] == {"year": "2010"}  # still a string; no coercion


@pytest.mark.asyncio
async def test_wrapper_forwards_valid_scalar_unchanged():
    tf, rec = _recorder()
    wrapped = make_arg_warning_callable(tf, DirectorModel, enable=True)
    await wrapped(director="Wolfgang Reitherman")
    assert rec["kwargs"] == {"director": "Wolfgang Reitherman"}


@pytest.mark.asyncio
async def test_wrapper_passes_positional_through_untouched():
    tf, rec = _recorder()
    wrapped = make_arg_warning_callable(tf, DirectorModel, enable=True)
    await wrapped({"director": "X"})
    assert rec["args"] == ({"director": "X"},) and rec["kwargs"] == {}


@pytest.mark.asyncio
async def test_wrapper_preserves_extra_and_optional_keys():
    tf, rec = _recorder()
    wrapped = make_arg_warning_callable(tf, OptionalModel, enable=True)
    await wrapped(a={"a": "hi"}, b="5", extra="keep")
    assert rec["kwargs"] == {"a": {"a": "hi"}, "b": "5", "extra": "keep"}  # all unchanged
