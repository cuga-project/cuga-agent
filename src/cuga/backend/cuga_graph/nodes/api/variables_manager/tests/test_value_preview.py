import os
import json
import re

from cuga.backend.cuga_graph.state.agent_state import VariablesManager


def extract_preview_for(vm: VariablesManager, name: str, max_length: int = 5000) -> str:
    summary = vm.get_variables_summary(max_length=max_length)
    pattern = rf"## {re.escape(name)}[\s\S]*?\n- Value Preview: (.*)\n"
    match = re.search(pattern, summary)
    assert match, f"Could not find preview for {name}. Summary was: {summary}"
    return match.group(1)


def test_preview_long_string_truncated():
    vm = VariablesManager()
    vm.reset()
    # Create a string long enough to exceed max_length (5000)
    long_str = "x" * 6000
    name = vm.add_variable(long_str, description="very long string")
    preview = extract_preview_for(vm, name, max_length=1000)  # Use smaller max_length to force truncation
    assert "..." in preview
    assert len(preview) <= 1000


def test_preview_long_list_truncated_items():
    vm = VariablesManager()
    vm.reset()
    # Create a list large enough that it will be truncated with smaller max_length
    long_list = [f"item_{i}" * 10 for i in range(100)]  # Much longer items
    name = vm.add_variable(long_list, description="long list")
    preview = extract_preview_for(vm, name, max_length=500)  # Use smaller max_length
    assert "(+" in preview and " more)" in preview


def test_preview_nested_dict_preserves_keys_and_truncates_arrays():
    vm = VariablesManager()
    vm.reset()
    value = {
        "users": [{"id": i, "name": f"User {i}"} for i in range(20)],  # Smaller but still large enough
        "meta": {"page": 1, "page_size": 50},
    }
    name = vm.add_variable(value, description="nested dict with long list")
    preview = extract_preview_for(vm, name, max_length=200)
    # Use smaller max_length to force truncation
    # Should at least preserve some structure and show truncation
    assert "users" in preview  # At least the key should be visible
    assert "..." in preview or "more)" in preview  # Some form of truncation


def test_preview_deep_nesting_shows_full_when_fits():
    vm = VariablesManager()
    vm.reset()
    deep = {"a": {"b": {"c": {"d": {"e": [1, 2, 3]}}}}}
    name = vm.add_variable(deep, description="deep nested")
    preview = extract_preview_for(vm, name)
    # Should show the full structure since it's small enough to fit
    assert "'a': {'b': {'c': {'d': {'e': [1, 2, 3]}}}}" in preview
    assert "..." not in preview


def test_preview_extremely_deep_nesting_capped():
    vm = VariablesManager()
    vm.reset()
    # Create very deep nesting with large content that should be capped
    very_deep = {
        "a": {
            "b": {
                "c": {
                    "d": {
                        "e": {"f": {"g": {"h": {"i": {"j": ["very_long_string_" * 50 for _ in range(10)]}}}}}
                    }
                }
            }
        }
    }
    name = vm.add_variable(very_deep, description="extremely deep nested")
    preview = extract_preview_for(vm, name, max_length=1000)  # Use smaller max_length
    # Should be truncated at some point with ellipsis
    assert "a" in preview
    assert "b" in preview
    assert "..." in preview


def test_playground_scenario_data_json_integration():
    # Test based on playground.py logic
    data_path = os.path.join(os.path.dirname(__file__), "data.json")
    with open(data_path, "r") as file:
        data = json.load(file)

    # now call variables manager to add the data to the variables manager
    data = json.loads(data['data'])
    variable = data['variables']
    vm = VariablesManager()
    vm.add_variable(variable['value'], variable['variable_name'], variable['description'])
    preview = vm.get_variables_summary()
    assert "total_count" in preview
    assert "101" in preview
    assert len(preview) <= 2000
    # Lower bound is a rough "summary is substantial" check; 950 (was 1000) since
    # the per-variable "- Created:" metadata line is no longer rendered (#705).
    assert len(preview) >= 950
