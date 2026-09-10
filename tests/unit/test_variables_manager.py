import pytest

from cuga.backend.cuga_graph.state.agent_state import VariablesManager

pytestmark = pytest.mark.unit


class TestVariablesManager:
    """Test suite for VariablesManager functionality."""

    def test_independent_instances(self):
        """Test that VariablesManager instances are independent (not singletons)."""
        vm1 = VariablesManager()
        vm2 = VariablesManager()
        assert vm1 is not vm2, "VariablesManager should create independent instances"

        vm1.add_variable("value1", name="var1")
        vm2.add_variable("value2", name="var2")

        assert vm1.get_variable_count() == 1
        assert vm2.get_variable_count() == 1
        assert vm1.get_variable("var1") == "value1"
        assert vm2.get_variable("var2") == "value2"
        assert vm1.get_variable("var2") is None
        assert vm2.get_variable("var1") is None

    def test_add_variable_with_description(self):
        """Test adding variables with descriptions."""
        vm = VariablesManager()
        vm.reset()

        var1_name = vm.add_variable("Hello World", description="A simple greeting message")
        var2_name = vm.add_variable([1, 2, 3, 4, True, False], description="List with booleans")
        var3_name = vm.add_variable(
            {"key": "value", "active": True, "disabled": False}, "custom_var", "Dict with booleans"
        )
        var4_name = vm.add_variable(True, description="A standalone boolean")
        var5_name = vm.add_variable(
            {"nested": {"flag": True, "items": [False, True]}}, description="Nested structure with booleans"
        )
        var6_name = vm.add_variable(123, description="An integer variable")
        var7_name = vm.add_variable(3.14, description="A float variable")

        assert vm.get_variable_count() == 7
        assert vm.get_variable(var1_name) == "Hello World"
        assert vm.get_variable(var2_name) == [1, 2, 3, 4, True, False]
        assert vm.get_variable(var3_name) == {"key": "value", "active": True, "disabled": False}
        assert vm.get_variable(var4_name) is True
        assert vm.get_variable(var5_name) == {"nested": {"flag": True, "items": [False, True]}}
        assert vm.get_variable(var6_name) == 123
        assert vm.get_variable(var7_name) == 3.14

    def test_last_n_variables_functionality(self):
        """Test the last_n functionality for variables summary and names."""
        vm = VariablesManager()
        vm.reset()

        vars_added = []
        for i in range(7):
            var_name = vm.add_variable(f"value_{i}", description=f"Variable {i}")
            vars_added.append(var_name)

        summary_last_3 = vm.get_variables_summary(last_n=3)
        assert "value_4" in summary_last_3
        assert "value_5" in summary_last_3
        assert "value_6" in summary_last_3
        assert "value_0" not in summary_last_3
        assert "value_1" not in summary_last_3

        summary_last_2 = vm.get_variables_summary(last_n=2)
        assert "value_5" in summary_last_2
        assert "value_6" in summary_last_2
        assert "value_4" not in summary_last_2

        summary_last_10 = vm.get_variables_summary(last_n=10)
        for i in range(7):
            assert f"value_{i}" in summary_last_10

        summary_invalid = vm.get_variables_summary(last_n=0)
        assert "Invalid last_n value" in summary_invalid

        last_3_names = vm.get_last_n_variable_names(3)
        expected_names = [vars_added[-3], vars_added[-2], vars_added[-1]]
        assert last_3_names == expected_names

    def test_variable_formatting(self):
        """Test variable formatting methods."""
        vm = VariablesManager()
        vm.reset()

        vm.add_variable("test_string", description="Test string")
        vm.add_variable({"key": "value"}, description="Test dict")

        formatted = vm.get_variables_formatted()
        assert "test_string" in formatted
        assert "'key': 'value'" in formatted

        json_str = vm.get_variables_as_json()
        assert "test_string" in json_str
        assert '"test_string"' in json_str
        assert '"key": "value"' in json_str

    def test_variables_summary_with_metadata(self):
        """Test variables summary with metadata."""
        vm = VariablesManager()
        vm.reset()

        vm.add_variable("test_value", description="Test description")

        summary = vm.get_variables_summary()
        assert "test_value" in summary
        assert "Test description" in summary

    def test_boolean_handling(self):
        """Test specific boolean value handling."""
        vm = VariablesManager()
        vm.reset()

        var_bool = vm.add_variable(True, description="Standalone boolean")
        var_list = vm.add_variable([1, 2, True, False], description="List with booleans")
        var_dict = vm.add_variable({"active": True, "disabled": False}, description="Dict with booleans")

        assert vm.get_variable(var_bool) is True
        assert vm.get_variable(var_list) == [1, 2, True, False]
        assert vm.get_variable(var_dict) == {"active": True, "disabled": False}

    def test_reset_keep_last_n_functionality(self):
        """Test the reset_keep_last_n functionality."""
        vm = VariablesManager()
        vm.reset()

        initial_vars = []
        for i in range(7):
            var_name = vm.add_variable(f"initial_{i}", description=f"Initial variable {i}")
            initial_vars.append(var_name)

        assert vm.get_variable_count() == 7

        vm.reset_keep_last_n(3)

        assert vm.get_variable_count() == 3

        remaining_vars = vm.get_last_n_variable_names(3)
        expected_remaining = initial_vars[-3:]
        assert remaining_vars == expected_remaining
        assert len(vm._creation_order) == 3
        assert vm._creation_order == expected_remaining

        new_var1 = vm.add_variable("new_value_1", description="New variable after reset")
        new_var2 = vm.add_variable("new_value_2", description="Another new variable")

        assert vm.get_variable_count() == 5
        assert vm.get_variable(new_var1) == "new_value_1"
        assert vm.get_variable(new_var2) == "new_value_2"
        assert len(vm._creation_order) == 5
        assert vm._creation_order[-2:] == [new_var1, new_var2]

    def test_reset_keep_last_n_edge_cases(self):
        """Test edge cases for reset_keep_last_n."""
        vm = VariablesManager()
        vm.reset()

        vm.add_variable("a")
        vm.add_variable("b")
        vm.add_variable("c")

        vm.reset_keep_last_n(0)
        assert vm.get_variable_count() == 0
        assert len(vm._creation_order) == 0

        new_var = vm.add_variable("after_zero_reset")
        assert vm.get_variable_count() == 1
        assert vm.get_variable(new_var) == "after_zero_reset"

    def test_reset_functionality(self):
        """Test the reset functionality."""
        vm = VariablesManager()
        vm.reset()

        vm.add_variable("test1")
        vm.add_variable("test2")

        assert vm.get_variable_count() == 2

        vm.reset()
        assert vm.get_variable_count() == 0
