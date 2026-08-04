import json
import re
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.state.agent_state import VariablesManager

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "variables_manager"


def extract_preview_for(vm: VariablesManager, name: str, max_length: int = 5000) -> str:
    summary = vm.get_variables_summary(max_length=max_length)
    pattern = rf"## {re.escape(name)}[\s\S]*?\n- Value Preview: (.*)\n"
    match = re.search(pattern, summary)
    assert match, f"Could not find preview for {name}. Summary was: {summary}"
    return match.group(1)


def test_preview_long_string_truncated():
    vm = VariablesManager()
    vm.reset()
    long_str = "x" * 6000
    name = vm.add_variable(long_str, description="very long string")
    preview = extract_preview_for(vm, name, max_length=1000)
    assert "..." in preview
    assert len(preview) <= 1000


def test_preview_long_list_truncated_items():
    vm = VariablesManager()
    vm.reset()
    long_list = [f"item_{i}" * 10 for i in range(100)]
    name = vm.add_variable(long_list, description="long list")
    preview = extract_preview_for(vm, name, max_length=500)
    assert "(+" in preview and " more)" in preview


def test_preview_nested_dict_preserves_keys_and_truncates_arrays():
    vm = VariablesManager()
    vm.reset()
    value = {
        "users": [{"id": i, "name": f"User {i}"} for i in range(20)],
        "meta": {"page": 1, "page_size": 50},
    }
    name = vm.add_variable(value, description="nested dict with long list")
    preview = extract_preview_for(vm, name, max_length=200)
    assert "users" in preview
    assert "..." in preview or "more)" in preview


def test_preview_deep_nesting_shows_full_when_fits():
    vm = VariablesManager()
    vm.reset()
    deep = {"a": {"b": {"c": {"d": {"e": [1, 2, 3]}}}}}
    name = vm.add_variable(deep, description="deep nested")
    preview = extract_preview_for(vm, name)
    assert "'a': {'b': {'c': {'d': {'e': [1, 2, 3]}}}}" in preview
    assert "..." not in preview


def test_preview_extremely_deep_nesting_capped():
    vm = VariablesManager()
    vm.reset()
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
    preview = extract_preview_for(vm, name, max_length=1000)
    assert "a" in preview
    assert "b" in preview
    assert "..." in preview


def test_playground_scenario_data_json_integration():
    data_path = _FIXTURES_DIR / "data.json"
    with open(data_path, "r") as file:
        data = json.load(file)

    data = json.loads(data["data"])
    variable = data["variables"]
    vm = VariablesManager()
    vm.add_variable(variable["value"], variable["variable_name"], variable["description"])
    preview = vm.get_variables_summary()
    assert "total_count" in preview
    assert "101" in preview
    assert len(preview) <= 2000
    assert len(preview) >= 1000
