"""Regression tests for #791: a rebound variable must reach the variables manager.

Before the fix only names absent before a block (plus result/results/output/outputs)
were written back, so a block that rebound an existing name left its first value in
the manager — and in every later pre-execute VERIFY prompt.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common import VariableUtils
from cuga.backend.cuga_graph.state.agent_state import AgentState


def _locals_from_manager(state: AgentState) -> dict:
    return {
        name: state.variables_manager.get_variable(name)
        for name in state.variables_manager.get_variable_names()
    }


@pytest.mark.unit
class TestFilterChangedVariables:
    def test_rebound_key_is_included(self):
        all_locals = {'items': ['old']}
        snapshot = VariableUtils.snapshot_values(all_locals)
        all_locals['items'] = []

        result = VariableUtils.filter_new_variables(all_locals, {'items'}, original_values=snapshot)

        assert result == {'items': []}

    def test_unchanged_key_is_excluded(self):
        all_locals = {'items': ['same'], 'count': 3}
        snapshot = VariableUtils.snapshot_values(all_locals)

        result = VariableUtils.filter_new_variables(all_locals, {'items', 'count'}, original_values=snapshot)

        assert result == {}

    def test_equal_value_new_object_is_excluded(self):
        """E2B replaces every object with a parsed copy; equal values are not changes."""
        all_locals = {'rows': [{'id': 1}], 'name': 'kite'}
        snapshot = VariableUtils.snapshot_values(all_locals)
        all_locals.update({'rows': [{'id': 1}], 'name': 'kite'})

        result = VariableUtils.filter_new_variables(all_locals, {'rows', 'name'}, original_values=snapshot)

        assert result == {}

    def test_in_place_mutation_is_included(self):
        rows = [{'id': 1}]
        all_locals = {'rows': rows}
        snapshot = VariableUtils.snapshot_values(all_locals)
        rows.append({'id': 2})

        result = VariableUtils.filter_new_variables(all_locals, {'rows'}, original_values=snapshot)

        assert result == {'rows': [{'id': 1}, {'id': 2}]}

    def test_type_change_with_equal_value_is_included(self):
        all_locals = {'flag': 1}
        snapshot = VariableUtils.snapshot_values(all_locals)
        all_locals['flag'] = True

        result = VariableUtils.filter_new_variables(all_locals, {'flag'}, original_values=snapshot)

        assert result == {'flag': True}

    def test_snapshot_skips_tools_modules_and_internals(self):
        import types

        async def tool():
            pass

        all_locals = {
            'tool': tool,
            'mod': types.ModuleType('m'),
            '_internal': 1,
            'data': [1],
        }

        assert VariableUtils.snapshot_values(all_locals) == {'data': [1]}


@pytest.mark.unit
@pytest.mark.asyncio
class TestReboundVariablesReachManager:
    async def test_second_block_rebind_updates_summary(self):
        state = AgentState(input="test", url="")

        await CodeExecutor.eval_with_tools_async(
            code="eligible = ['first']\nprint(eligible)", _locals={}, state=state, mode="local"
        )
        assert state.variables_manager.get_variable("eligible") == ['first']

        output, new_vars = await CodeExecutor.eval_with_tools_async(
            code="eligible = ['second']\nprint(eligible)",
            _locals=_locals_from_manager(state),
            state=state,
            mode="local",
        )

        assert new_vars == {'eligible': ['second']}
        assert state.variables_manager.get_variable("eligible") == ['second']
        summary = state.variables_manager.get_variables_summary()
        assert "second" in summary
        assert "first" not in summary
        assert "## Variables Updated:" in output

    async def test_unchanged_variables_are_not_reported(self):
        state = AgentState(input="test", url="")

        await CodeExecutor.eval_with_tools_async(
            code="kept = {'a': 1}\nprint(kept)", _locals={}, state=state, mode="local"
        )
        output, new_vars = await CodeExecutor.eval_with_tools_async(
            code="print(kept['a'])",
            _locals=_locals_from_manager(state),
            state=state,
            mode="local",
        )

        assert new_vars == {}
        assert "Variables Updated" not in output

    async def test_e2b_rebind_updates_manager_and_skips_unchanged(self):
        """E2B returns parsed copies of every local; only the rebound one is written back."""
        state = AgentState(input="test", url="")
        state.variables_manager.add_variable(['old'], name="eligible")
        state.variables_manager.add_variable({'id': 7}, name="kept")

        mock_executor = MagicMock()
        mock_executor.execute_for_cuga_lite = AsyncMock(
            return_value=("done", {'eligible': [], 'kept': {'id': 7}})
        )
        with patch.object(CodeExecutor, '_get_e2b_executor', return_value=mock_executor):
            _, new_vars = await CodeExecutor.eval_with_tools_async(
                code="eligible = []\nprint('done')",
                _locals=_locals_from_manager(state),
                state=state,
                mode="e2b",
            )

        assert new_vars == {'eligible': []}
        assert state.variables_manager.get_variable("eligible") == []

    async def test_eligible_kites_trace(self):
        """Mirrors AppWorld f6936d4_1: a rebind to [] must replace the hiking-socks rows."""
        state = AgentState(input="test", url="")

        socks = {'product_id': 6, 'name': 'Wigwam Merino Comfort Hiker Socks'}
        await CodeExecutor.eval_with_tools_async(
            code=f"eligible_kites = [{socks!r}]\nprint(len(eligible_kites))",
            _locals={},
            state=state,
            mode="local",
        )

        await CodeExecutor.eval_with_tools_async(
            code="eligible_kites = []\nprint(f'Eligible kites: {len(eligible_kites)}')",
            _locals=_locals_from_manager(state),
            state=state,
            mode="local",
        )

        kite = {'product_id': 2389, 'product_type': 'kite', 'name': 'Into The Wind 10-ft. Delta Kite'}
        await CodeExecutor.eval_with_tools_async(
            code=f"fastest_kite = eligible_kites[0] if eligible_kites else {kite!r}\nprint(fastest_kite)",
            _locals=_locals_from_manager(state),
            state=state,
            mode="local",
        )

        assert state.variables_manager.get_variable("eligible_kites") == []
        summary = state.variables_manager.get_variables_summary()
        assert "Hiker Socks" not in summary
        assert "Delta Kite" in summary
