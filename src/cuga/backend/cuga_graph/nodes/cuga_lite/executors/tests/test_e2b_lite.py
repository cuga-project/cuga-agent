"""Unit tests for E2B sandbox integration in CUGA Lite mode."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common import VariableUtils
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.tools_env.code_sandbox.e2b_sandbox import execute_in_e2b_sandbox_lite
from cuga.backend.cuga_graph.state.agent_state import AgentState


class TestFilterNewVariables:
    """Test suite for _filter_new_variables helper function."""

    def test_filter_basic_serializable_types(self):
        """Test filtering with basic serializable types."""
        original_keys = {'existing_var'}
        all_locals = {
            'existing_var': 'old',
            'new_str': 'hello',
            'new_int': 42,
            'new_float': 3.14,
            'new_bool': True,
            'new_none': None,
        }

        result = VariableUtils.filter_new_variables(all_locals, original_keys)

        assert len(result) == 5
        assert result['new_str'] == 'hello'
        assert result['new_int'] == 42
        assert result['new_float'] == 3.14
        assert result['new_bool'] is True
        assert result['new_none'] is None

    def test_filter_collections(self):
        """Test filtering with lists, dicts, and tuples."""
        original_keys = set()
        all_locals = {
            'my_list': [1, 2, 3],
            'my_dict': {'a': 1, 'b': 2},
            'my_tuple': (1, 2, 3),
            'nested': {'list': [1, 2], 'dict': {'x': 10}},
        }

        result = VariableUtils.filter_new_variables(all_locals, original_keys)

        assert len(result) == 4
        assert result['my_list'] == [1, 2, 3]
        assert result['my_dict'] == {'a': 1, 'b': 2}
        assert result['my_tuple'] == (1, 2, 3)
        assert result['nested'] == {'list': [1, 2], 'dict': {'x': 10}}

    def test_filter_excludes_internal_variables(self):
        """Test that internal variables (starting with _) are filtered out."""
        original_keys = set()
        all_locals = {
            'public_var': 'visible',
            '_private_var': 'hidden',
            '__dunder__': 'hidden',
            '_internal': 'hidden',
        }

        result = VariableUtils.filter_new_variables(all_locals, original_keys)

        assert len(result) == 1
        assert result == {'public_var': 'visible'}

    def test_filter_excludes_non_serializable_types(self):
        """Test that non-serializable types (functions, classes, modules) are filtered out."""

        def test_function():
            pass

        class TestClass:
            pass

        import types

        test_module = types.ModuleType('test_module')

        original_keys = set()
        all_locals = {
            'serializable': 'keep',
            'function': test_function,
            'class': TestClass,
            'module': test_module,
        }

        result = VariableUtils.filter_new_variables(all_locals, original_keys)

        assert len(result) == 1
        assert result == {'serializable': 'keep'}

    def test_filter_empty_new_vars(self):
        """Test when there are no new variables."""
        original_keys = {'var1', 'var2'}
        all_locals = {'var1': 'a', 'var2': 'b'}

        result = VariableUtils.filter_new_variables(all_locals, original_keys)

        assert result == {}


class TestExecuteInE2BSandbox:
    """Test suite for execute_in_e2b_sandbox_lite helper function."""

    @pytest.mark.asyncio
    async def test_import_error_handling(self):
        """Test that RuntimeError is raised when e2b-code-interpreter is not available."""
        with patch('cuga.backend.tools_env.code_sandbox.e2b_sandbox.E2B_AVAILABLE', False):
            with pytest.raises(RuntimeError, match="e2b-code-interpreter package not installed"):
                await execute_in_e2b_sandbox_lite("print('hello')")

    @pytest.mark.asyncio
    async def test_successful_execution_with_output(self):
        """Test successful E2B execution with stdout output."""
        # Mock the low-level execute_code_in_e2b function that execute_in_e2b_sandbox_lite calls
        with patch('cuga.backend.tools_env.code_sandbox.e2b_sandbox.execute_code_in_e2b') as mock_execute:
            # Simulate E2B returning output with the delimiter and locals dict
            mock_execute.return_value = "Hello from E2B\nResult: 42\n!!!===!!!\n{'result': 42}"

            result, locals_dict = await execute_in_e2b_sandbox_lite("print('test')")
            assert "Hello from E2B" in result
            assert "Result: 42" in result
            assert locals_dict == {'result': 42}

    @pytest.mark.asyncio
    async def test_execution_error_handling(self):
        """Test E2B execution with error."""
        # Mock execute_code_in_e2b to return an error message
        with patch('cuga.backend.tools_env.code_sandbox.e2b_sandbox.execute_code_in_e2b') as mock_execute:
            mock_execute.return_value = "E2B execution error: NameError - name 'undefined_var' is not defined"

            # Should not raise - execute_in_e2b_sandbox_lite returns the error as a string
            result, locals_dict = await execute_in_e2b_sandbox_lite("print(undefined_var)")

            assert "E2B execution error" in result
            assert locals_dict == {}

    @pytest.mark.asyncio
    async def test_empty_locals_handling(self):
        """Test when E2B returns empty locals."""
        # Mock execute_code_in_e2b to return output without the delimiter (no locals)
        with patch('cuga.backend.tools_env.code_sandbox.e2b_sandbox.execute_code_in_e2b') as mock_execute:
            mock_execute.return_value = "Hello"

            result, locals_dict = await execute_in_e2b_sandbox_lite("print('Hello')")
            assert result.strip() == "Hello"
            assert locals_dict == {}


class TestEvalWithToolsAsyncE2B:
    """Test suite for eval_with_tools_async with E2B integration."""

    @pytest.mark.asyncio
    async def test_eval_with_e2b_enabled(self):
        """Test eval_with_tools_async routes to E2B when enabled."""
        mock_state = MagicMock()
        mock_state.variables_manager = MagicMock()

        with patch(
            'cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor.settings'
        ) as mock_settings:
            with patch.object(CodeExecutor, '_get_e2b_executor') as mock_get_executor:
                mock_settings.advanced_features.e2b_sandbox = True
                mock_settings.advanced_features.code_executor_keep_last_n = -1
                mock_settings.advanced_features.execution_output_max_length = 10000
                mock_settings.advanced_features.sandbox_execution_timeout = 30  # sentinel — any positive number works; value is unused since the test code runs in microseconds

                mock_executor = MagicMock()
                mock_executor.execute_for_cuga_lite = AsyncMock(return_value=("42", {'result': 42}))
                mock_get_executor.return_value = mock_executor

                code = "result = 40 + 2\nprint(result)"
                _locals = {}
                state = state = AgentState(
                    input="test task",
                    url="",
                )

                output, new_vars = await CodeExecutor.eval_with_tools_async(code, _locals, state)

                # Verify mock was called
                mock_executor.execute_for_cuga_lite.assert_called_once()

                # Verify new variables
                assert new_vars == {'result': 42}

                # Verify output contains both stdout and variable summary
                assert "42" in output  # The stdout from print(result)
                assert "## New Variables Created:" in output
                assert "## result" in output
                assert "Value Preview: 42" in output

    @pytest.mark.asyncio
    async def test_eval_with_e2b_disabled(self):
        """Test local execution when E2B is disabled."""
        mock_state = MagicMock()
        mock_state.variables_manager = MagicMock()

        with patch(
            'cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor.settings'
        ) as mock_settings:
            mock_settings.advanced_features.e2b_sandbox = False
            mock_settings.advanced_features.code_executor_keep_last_n = -1
            mock_settings.advanced_features.execution_output_max_length = 10000
            mock_settings.advanced_features.sandbox_execution_timeout = 30  # sentinel — any positive number works; value is unused since the test code runs in microseconds
            # Skills off so `_internal_re` isn't injected — keeps `context_locals`
            # empty for this test's bare `y = 20` code, letting the
            # validate_context_usage short-circuit trigger.
            mock_settings.skills.enabled = False

            code = "y = 20"
            _locals = {}
            state = state = AgentState(
                input="test task",
                url="",
            )

            output, new_vars = await CodeExecutor.eval_with_tools_async(code, _locals, state)

            # Verify new variables
            assert new_vars == {'y': 20}

            # Verify output contains variable summary (no stdout since nothing was printed)
            assert (
                "<code ran, no output printed to stdout>" in output or "## New Variables Created:" in output
            )
            assert "## y" in output
            assert "Value Preview: 20" in output

    @pytest.mark.asyncio
    async def test_eval_filters_internal_variables(self):
        """Test that internal variables are filtered from results."""
        mock_state = MagicMock()
        mock_state.variables_manager = MagicMock()

        # Test with local execution but with code that creates internal variables
        # This tests the filtering logic without needing E2B mocking
        code = """public_var = 'visible'
_private_var = 'hidden'
_dunder = 'hidden'
result = 42"""
        _locals = {}
        state = AgentState(
            input="test task",
            url="",
        )

        output, new_vars = await CodeExecutor.eval_with_tools_async(code, _locals, state)

        # Only public variables should be in new_vars
        assert 'public_var' in new_vars
        assert 'result' in new_vars
        assert '_private_var' not in new_vars
        assert '_dunder' not in new_vars


class TestE2BWithVariablesAndTools:
    """Test suite for E2B execution with variables and async tool functions."""

    @pytest.mark.asyncio
    async def test_e2b_with_variables_and_tools(self):
        """Test that E2B execution can access variables and call async tools from _locals."""

        # Define dummy async tool functions
        async def get_account_name(account_id: str) -> str:
            """Get account name by ID."""
            accounts = {"acc_1": "Acme Corp", "acc_2": "TechStart Inc"}
            return accounts.get(account_id, "Unknown")

        async def get_account_revenue(account_id: str) -> float:
            """Get account revenue by ID."""
            revenues = {"acc_1": 1500000.0, "acc_2": 850000.0}
            return revenues.get(account_id, 0.0)

        # Set up _locals with variables and tools
        _locals = {
            "get_account_name": get_account_name,
            "get_account_revenue": get_account_revenue,
            "target_account": "acc_1",
            "threshold": 1000000.0,
        }

        # Code that uses variables and calls tools
        code = """
# Use variable from previous execution
account_id = target_account

# Call async tools
name = await get_account_name(account_id)
revenue = await get_account_revenue(account_id)

# Compute result using another variable
is_high_value = revenue > threshold

print(f"Account: {name}")
print(f"Revenue: ${revenue:,.0f}")
print(f"High value: {is_high_value}")

result = {
    "account_id": account_id,
    "name": name,
    "revenue": revenue,
    "is_high_value": is_high_value
}
print("Done")  # Prevent auto-print of last line
"""

        mock_state = MagicMock()
        mock_state.variables_manager = MagicMock()
        mock_state.variables_manager.get_variables_formatted.return_value = (
            "# Variables from state\ntarget_account = 'acc_1'\nthreshold = 1000000.0\n"
        )
        mock_state.variables_manager.get_variable_count.return_value = 2

        with patch(
            'cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor.settings'
        ) as mock_settings:
            with patch.object(CodeExecutor, '_get_e2b_executor') as mock_get_executor:
                mock_settings.advanced_features.e2b_sandbox = True
                mock_settings.advanced_features.code_executor_keep_last_n = -1
                mock_settings.advanced_features.execution_output_max_length = 10000
                mock_settings.advanced_features.sandbox_execution_timeout = 30  # sentinel — any positive number works; value is unused since the test code runs in microseconds

                # Mock E2B executor
                mock_executor = MagicMock()
                mock_output = """Account: Acme Corp
Revenue: $1,500,000
High value: True
Done"""
                mock_result = {
                    'account_id': 'acc_1',
                    'name': 'Acme Corp',
                    'revenue': 1500000.0,
                    'is_high_value': True,
                    'result': {
                        'account_id': 'acc_1',
                        'name': 'Acme Corp',
                        'revenue': 1500000.0,
                        'is_high_value': True,
                    },
                }
                mock_executor.execute_for_cuga_lite = AsyncMock(return_value=(mock_output, mock_result))
                mock_get_executor.return_value = mock_executor

                state = AgentState(
                    input="test task",
                    url="",
                )

                # Execute in E2B
                output, new_vars = await CodeExecutor.eval_with_tools_async(code, _locals, state)

                # Verify output
                assert "Account: Acme Corp" in output
                assert "Revenue: $1,500,000" in output
                assert "High value: True" in output

                # Verify new variables
                assert 'result' in new_vars
                assert new_vars['result']['name'] == "Acme Corp"
                assert new_vars['result']['revenue'] == 1500000.0
                assert new_vars['result']['is_high_value'] is True
