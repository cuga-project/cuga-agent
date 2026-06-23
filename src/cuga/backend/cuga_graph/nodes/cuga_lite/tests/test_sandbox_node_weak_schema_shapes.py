"""Observed-shape capture for weak-schema tools in the sandbox node (issue #272)."""

from __future__ import annotations

from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import (
    _describe_observed_shape,
    _record_weak_schema_shapes,
)


def test_describe_observed_shape_dict():
    assert "dict with keys" in _describe_observed_shape({"a": 1, "b": 2})


def test_describe_observed_shape_list():
    assert "list of 3 items" in _describe_observed_shape(["x", "y", "z"])


def test_describe_observed_shape_empty_list():
    assert _describe_observed_shape([]) == "empty list"


def test_describe_observed_shape_str():
    assert "str of 11 chars" in _describe_observed_shape("hello world")


def test_describe_observed_shape_other_type():
    assert _describe_observed_shape(42) == "int"


def test_record_weak_schema_shapes_stores_first_observation():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["a", "b"], "error": None}])
    assert "file_readfile" in adapter._observed_tool_shapes


def test_record_weak_schema_shapes_skips_non_weak_tools():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "other_tool", "result": "x", "error": None}])
    assert adapter._observed_tool_shapes == {}


def test_record_weak_schema_shapes_first_observation_wins():
    adapter = SimpleNamespace(
        _weak_schema_tool_names=frozenset({"file_readfile"}),
        _observed_tool_shapes={"file_readfile": "old"},
    )
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["z"], "error": None}])
    assert adapter._observed_tool_shapes["file_readfile"] == "old"


def test_record_weak_schema_shapes_skips_errored_calls():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": None, "error": "boom"}])
    assert adapter._observed_tool_shapes == {}


def test_record_weak_schema_shapes_noop_when_no_weak_schema_tools():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset(), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["a"], "error": None}])
    assert adapter._observed_tool_shapes == {}
