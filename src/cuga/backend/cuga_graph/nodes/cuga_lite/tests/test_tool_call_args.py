from cuga.backend.cuga_graph.nodes.cuga_lite.tool_call_args import merge_tool_call_args


def test_single_dict_unpacks_to_named_params():
    param_names = ["product_id", "quantity", "clear_cart_first"]
    d = {"product_id": 820, "quantity": 2, "clear_cart_first": True}
    assert merge_tool_call_args((d,), {}, param_names) == d


def test_single_dict_extra_keys_stripped():
    param_names = ["product_id", "quantity"]
    d = {"product_id": 820, "quantity": 1, "extra": "x"}
    assert merge_tool_call_args((d,), {}, param_names) == {"product_id": 820, "quantity": 1}


def test_positional_mapping_unchanged():
    param_names = ["a", "b"]
    assert merge_tool_call_args((1, 2), {}, param_names) == {"a": 1, "b": 2}


def test_unknown_dict_assigned_to_first_param():
    param_names = ["product_id"]
    d = {"not_a_schema_field": 1}
    assert merge_tool_call_args((d,), {}, param_names) == {"product_id": d}


def test_kwargs_merged():
    param_names = ["product_id"]
    assert merge_tool_call_args((), {"product_id": 5}, param_names) == {"product_id": 5}
