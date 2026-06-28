"""Tests for pre-flight argument coercion (Wave-1 Change #4).

The cases mirror the live parity + shadow-coercion audit run against the M3
disney registry: a string-param tool (``director``) and an integer-param tool
(``year``). Each coercion that fired against the live server produced a
server-accepted call with the correct answer; these tests lock in that behavior
at the unit level without needing the live stack.
"""

from __future__ import annotations

from typing import Optional

import pytest
from pydantic import Field, create_model

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.arg_coercion import (
    coerce_kwargs,
    coerce_value,
    jsonschema_type,
    make_coercing_callable,
)

# Models built exactly as create_tool_from_api_dict would: required scalar field.
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


# ── coerce_value: the safe rules (live with master flag on) ─────────────────
def test_R1_dict_key_eq_param():
    r = coerce_value("director", "string", {"director": "Wolfgang Reitherman"}, enable_dict_scalar=False)
    assert r.rule == "R1_dict_key_eq_param" and r.applied and r.value == "Wolfgang Reitherman"


def test_R3_list_unwrap():
    r = coerce_value("director", "string", ["Wolfgang Reitherman"], enable_dict_scalar=False)
    assert r.rule == "R3_list_unwrap" and r.applied and r.value == "Wolfgang Reitherman"


def test_R5_str_to_int():
    r = coerce_value("year", "integer", "2010", enable_dict_scalar=False)
    assert r.rule == "R5_str_to_num" and r.applied and r.value == 2010 and isinstance(r.value, int)


def test_R5_str_to_number():
    r = coerce_value("pct", "number", "3.5", enable_dict_scalar=False)
    assert r.applied and r.value == 3.5 and isinstance(r.value, float)


def test_R1_takes_precedence_over_R2():
    # key matches param -> R1 (safe), never the R2 guesser.
    r = coerce_value("year", "integer", {"year": 2010}, enable_dict_scalar=False)
    assert r.rule == "R1_dict_key_eq_param" and r.applied and r.value == 2010


# ── coerce_value: R2 the guesser (gated + shadowed) ─────────────────────────
def test_R2_shadow_when_disabled():
    r = coerce_value("director", "string", {"name": "Wolfgang Reitherman"}, enable_dict_scalar=False)
    assert r.rule == "R2_dict_single_scalar"
    assert r.shadow and not r.applied
    assert r.value == "Wolfgang Reitherman"  # would-be value, surfaced for logging


def test_R2_applied_when_enabled():
    r = coerce_value("director", "string", {"name": "Wolfgang Reitherman"}, enable_dict_scalar=True)
    assert r.rule == "R2_dict_single_scalar" and r.applied and not r.shadow
    assert r.value == "Wolfgang Reitherman"


def test_R2_does_not_fire_on_list_value():
    # {'director': []} from get_director_by_song -> list value, not a scalar.
    r = coerce_value("director", "string", {"director": []}, enable_dict_scalar=True)
    # key == param -> R1 path; value is the empty list (unwrapped), no R2 guess.
    assert r.rule == "R1_dict_key_eq_param" and r.value == []


def test_R2_multi_key_dict_no_fire():
    r = coerce_value("director", "string", {"a": 1, "b": 2}, enable_dict_scalar=True)
    assert r.rule is None and not r.applied


# ── uncoercible cases left untouched ─────────────────────────────────────────
def test_none_not_coerced():
    r = coerce_value("director", "string", None, enable_dict_scalar=True)
    assert r.rule is None and not r.applied and r.value is None


def test_valid_scalar_not_coerced():
    r = coerce_value("director", "string", "Wolfgang Reitherman", enable_dict_scalar=True)
    assert r.rule is None and not r.applied


# ── coerce_kwargs ────────────────────────────────────────────────────────────
def test_coerce_kwargs_applies_safe_rules():
    out = coerce_kwargs({"director": {"director": "X"}}, {"director": "string"}, enable_dict_scalar=False)
    assert out == {"director": "X"}


def test_coerce_kwargs_shadow_does_not_mutate():
    out = coerce_kwargs({"director": {"name": "X"}}, {"director": "string"}, enable_dict_scalar=False)
    assert out == {"director": {"name": "X"}}  # R2 withheld -> unchanged


def test_coerce_kwargs_ignores_unknown_keys():
    out = coerce_kwargs({"zzz": {"a": 1}}, {"director": "string"}, enable_dict_scalar=True)
    assert out == {"zzz": {"a": 1}}


# ── make_coercing_callable: end-to-end wrapper ──────────────────────────────
def _recorder():
    received = {}

    async def tool_func(*args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs
        return {"ok": True}

    return tool_func, received


@pytest.mark.asyncio
async def test_wrapper_disabled_is_identity():
    tf, rec = _recorder()
    wrapped = make_coercing_callable(tf, DirectorModel, enable=False, enable_dict_scalar=False)
    assert wrapped is tf


@pytest.mark.asyncio
async def test_wrapper_fixes_dict_as_string():
    tf, rec = _recorder()
    wrapped = make_coercing_callable(tf, DirectorModel, enable=True, enable_dict_scalar=False)
    await wrapped(director={"director": "Wolfgang Reitherman"})
    assert rec["kwargs"] == {"director": "Wolfgang Reitherman"}


@pytest.mark.asyncio
async def test_wrapper_fixes_stringized_int_via_validation():
    # R5 + pydantic model_dump forward 2010 as an int (closes the parity gap).
    tf, rec = _recorder()
    wrapped = make_coercing_callable(tf, YearModel, enable=True, enable_dict_scalar=False)
    await wrapped(year="2010")
    assert rec["kwargs"] == {"year": 2010} and isinstance(rec["kwargs"]["year"], int)


@pytest.mark.asyncio
async def test_wrapper_shadow_r2_forwards_original():
    # R2 disabled: the bad dict is NOT fixed; forwarded best-effort so the server
    # returns its validation error (no silent guess).
    tf, rec = _recorder()
    wrapped = make_coercing_callable(tf, DirectorModel, enable=True, enable_dict_scalar=False)
    await wrapped(director={"name": "X"})
    assert rec["kwargs"] == {"director": {"name": "X"}}


@pytest.mark.asyncio
async def test_wrapper_r2_enabled_fixes():
    tf, rec = _recorder()
    wrapped = make_coercing_callable(tf, DirectorModel, enable=True, enable_dict_scalar=True)
    await wrapped(director={"name": "X"})
    assert rec["kwargs"] == {"director": "X"}


@pytest.mark.asyncio
async def test_wrapper_uncoercible_forwarded_unchanged():
    tf, rec = _recorder()
    wrapped = make_coercing_callable(tf, DirectorModel, enable=True, enable_dict_scalar=False)
    await wrapped(director=None)  # None -> server rejects, behavior preserved
    assert rec["kwargs"] == {"director": None}


@pytest.mark.asyncio
async def test_wrapper_passes_positional_through_untouched():
    tf, rec = _recorder()
    wrapped = make_coercing_callable(tf, DirectorModel, enable=True, enable_dict_scalar=False)
    await wrapped({"director": "X"})  # positional -> not coerced
    assert rec["args"] == ({"director": "X"},) and rec["kwargs"] == {}


@pytest.mark.asyncio
async def test_wrapper_preserves_extra_and_optional_keys():
    tf, rec = _recorder()
    wrapped = make_coercing_callable(tf, OptionalModel, enable=True, enable_dict_scalar=False)
    await wrapped(a={"a": "hi"}, b="5", extra="keep")
    assert rec["kwargs"]["a"] == "hi"
    assert rec["kwargs"]["b"] == 5  # optional int coerced from "5"
    assert rec["kwargs"]["extra"] == "keep"  # unknown key preserved
