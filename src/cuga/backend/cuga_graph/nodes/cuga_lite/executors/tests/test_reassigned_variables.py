"""Variables changed by a later code block must reach the blocks after it.

The sandbox node rebuilds each block's namespace from the variables manager,
so a value that is not written back is lost: the next block sees the old one.
"""

from unittest.mock import Mock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.variable_utils import VariableUtils
from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager

pytestmark = pytest.mark.unit


@pytest.fixture
def state():
    state = Mock(spec=AgentState)
    state.variables_manager = VariablesManager()
    state.reflection_skills_enabled = False
    return state


async def run_block(state, code: str):
    """Run one block the way the sandbox node does: namespace from saved variables."""
    manager = state.variables_manager
    namespace = {name: manager.get_variable(name) for name in manager.get_variable_names()}
    return await CodeExecutor.eval_with_tools_async(code=code, _locals=namespace, state=state, mode="local")


@pytest.mark.asyncio
async def test_reassigned_variable_reaches_the_next_block(state):
    await run_block(state, 'roommates = {"status": "exception", "message": "401 Unauthorized"}')
    _, new_vars = await run_block(state, 'roommates = ["Chris", "Jose", "Lindsey"]')
    output, _ = await run_block(state, "print(len(roommates), roommates[0])")

    assert new_vars["roommates"] == ["Chris", "Jose", "Lindsey"]
    assert "3 Chris" in output
    assert state.variables_manager.get_variable("roommates") == ["Chris", "Jose", "Lindsey"]


@pytest.mark.asyncio
async def test_variable_changed_in_place_reaches_the_next_block(state):
    # A set is stored as a tagged copy, so an in-place change is lost unless it is
    # written back (a plain list or dict would be shared with the manager).
    await run_block(state, "seen = {'a'}")
    await run_block(state, "seen.add('b')")
    output, _ = await run_block(state, "print(sorted(seen))")

    assert "['a', 'b']" in output


def test_changed_keys_detects_in_place_change_of_a_mixed_key_dict():
    totals = {(2023, 5): 1.0, "note": "x"}
    snapshot = VariableUtils.snapshot_values({"totals": totals}, {"totals"})
    totals[(2023, 5)] += 2.5

    assert VariableUtils.changed_keys({"totals": totals}, snapshot) == {"totals"}


@pytest.mark.asyncio
async def test_existing_variable_mutated_into_a_cycle_does_not_crash_the_block(state):
    await run_block(state, 'node = {"name": "root", "children": []}')
    output, new_vars = await run_block(
        state, 'node["children"].append(node)\ncount = len(node["children"])\nprint("ok")'
    )

    assert "ok" in output
    assert new_vars["count"] == 1


@pytest.mark.asyncio
async def test_new_self_referencing_variable_does_not_crash_the_block(state):
    output, new_vars = await run_block(state, "loop = []\nloop.append(loop)\ntotal = 2\nprint('ok')")

    assert "ok" in output
    assert new_vars["total"] == 2
    assert "loop" not in new_vars


@pytest.mark.asyncio
async def test_reassigned_variable_survives_keep_last_n(state, monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "code_executor_keep_last_n", 1)
    await run_block(state, 'roommates = {"status": "exception"}')
    await run_block(state, 'roommates = ["Chris", "Jose"]\ncount = len(roommates)')

    assert state.variables_manager.get_variable("roommates") == ["Chris", "Jose"]


def test_snapshot_skips_variables_the_block_does_not_name():
    values = {"big": list(range(5)), "used": 1}
    snapshot = VariableUtils.snapshot_values(values, set(values), code="print(used + 1)")

    assert set(snapshot) == {"used"}


@pytest.mark.asyncio
async def test_unchanged_variable_is_not_saved_again(state):
    await run_block(state, "total = 140.0")
    _, new_vars = await run_block(state, "share = round(total / 3, 2)\nprint(share)")

    assert "share" in new_vars
    assert "total" not in new_vars


def test_changed_keys_detects_reassignment_and_in_place_changes():
    before = {"a": 1, "b": [1], "c": {"k": 1}, "d": "same", "tool": len, "_hidden": 1}
    snapshot = VariableUtils.snapshot_values(before, set(before))
    after = dict(before, a=2, _hidden=2)
    after["b"].append(2)

    assert VariableUtils.changed_keys(after, snapshot) == {"a", "b"}


def test_changed_keys_ignores_equal_copies():
    before = {"rows": [{"id": 1}], "n": 3}
    snapshot = VariableUtils.snapshot_values(before, set(before))
    after = {"rows": [{"id": 1}], "n": 3}  # new objects, same values (e.g. parsed back from a sandbox)

    assert VariableUtils.changed_keys(after, snapshot) == set()


@pytest.mark.asyncio
async def test_self_referencing_value_does_not_stop_the_block(state):
    """A value that cannot be serialized (here a list that contains itself) must not
    stop the block from running; it is compared by identity instead."""
    loop = []
    loop.append(loop)
    output, new_vars = await CodeExecutor.eval_with_tools_async(
        code="total = len(loop) + 1\nprint(total)", _locals={"loop": loop}, state=state, mode="local"
    )
    assert "2" in output
    assert new_vars["total"] == 2


def test_changed_keys_handles_self_referencing_values():
    loop = []
    loop.append(loop)
    snapshot = VariableUtils.snapshot_values({"loop": loop}, {"loop"})
    assert VariableUtils.changed_keys({"loop": loop}, snapshot) == set()
    assert VariableUtils.changed_keys({"loop": [1]}, snapshot) == {"loop"}


@pytest.mark.asyncio
async def test_keep_last_n_limits_only_new_variables(state, monkeypatch):
    """Printing the reassigned variable must not let it take the new variable's slot."""
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "code_executor_keep_last_n", 1)
    await run_block(state, 'roommates = {"status": "exception"}')
    await run_block(state, 'roommates = ["Chris", "Jose"]\ncount = len(roommates)\nprint(roommates)')

    assert state.variables_manager.get_variable("roommates") == ["Chris", "Jose"]
    assert state.variables_manager.get_variable("count") == 2


def test_snapshot_finds_names_that_start_with_a_non_ascii_letter():
    values = {"数量": 1}
    snapshot = VariableUtils.snapshot_values(values, set(values), code="数量 = 2")

    assert set(snapshot) == {"数量"}
