import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.arguments import (
    merge_tool_call_args,
    unexpected_tool_arg_names,
)


@pytest.mark.unit
def test_single_dict_unpacks_to_named_params():
    param_names = ["product_id", "quantity", "clear_cart_first"]
    d = {"product_id": 820, "quantity": 2, "clear_cart_first": True}
    assert merge_tool_call_args((d,), {}, param_names) == d


@pytest.mark.unit
def test_single_dict_extra_keys_stripped():
    param_names = ["product_id", "quantity"]
    d = {"product_id": 820, "quantity": 1, "extra": "x"}
    assert merge_tool_call_args((d,), {}, param_names) == {"product_id": 820, "quantity": 1}


@pytest.mark.unit
def test_positional_mapping_unchanged():
    param_names = ["a", "b"]
    assert merge_tool_call_args((1, 2), {}, param_names) == {"a": 1, "b": 2}


@pytest.mark.unit
def test_unknown_dict_assigned_to_first_param():
    param_names = ["product_id"]
    d = {"not_a_schema_field": 1}
    assert merge_tool_call_args((d,), {}, param_names) == {"product_id": d}


@pytest.mark.unit
def test_kwargs_merged():
    param_names = ["product_id"]
    assert merge_tool_call_args((), {"product_id": 5}, param_names) == {"product_id": 5}


@pytest.mark.unit
def test_unexpected_names_from_dict_bag_with_known_keys():
    param_names = ["product_id", "quantity"]
    d = {"product_id": 1, "quantity": 2, "currency": "USD"}
    assert unexpected_tool_arg_names((d,), {}, param_names) == ["currency"]


@pytest.mark.unit
def test_unexpected_names_from_kwargs():
    param_names = ["product_id"]
    assert unexpected_tool_arg_names((), {"product_id": 1, "currency": "USD"}, param_names) == ["currency"]


@pytest.mark.unit
def test_unexpected_names_empty_when_dict_is_nested_payload():
    """Whole-dict-to-first-param case is not treated as unexpected fields."""
    param_names = ["product_id"]
    d = {"not_a_schema_field": 1}
    assert unexpected_tool_arg_names((d,), {}, param_names) == []
