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
    await run_block(state, "items = [1]\nprofile = {'name': 'Paul'}")
    await run_block(state, "items.append(2)\nprofile['city'] = 'Seattle'")
    output, _ = await run_block(state, "print(items, profile)")

    assert "[1, 2]" in output
    assert "'city': 'Seattle'" in output


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
