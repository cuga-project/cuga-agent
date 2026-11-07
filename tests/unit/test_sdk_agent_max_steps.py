"""CugaAgent(cuga_lite_max_steps=...) reaches the step cap the graph reads from the run config."""

import pytest

pytestmark = pytest.mark.unit


def test_constructor_cap_reaches_the_run_config_and_a_caller_config_wins():
    from cuga.sdk import CugaAgent

    agent = CugaAgent(cuga_lite_max_steps=5, auto_load_policies=False)

    assert agent._prepare_run_config(None)["configurable"]["cuga_lite_max_steps"] == 5
    caller = {"configurable": {"cuga_lite_max_steps": 9}}
    assert agent._prepare_run_config(caller)["configurable"]["cuga_lite_max_steps"] == 9


def test_no_cap_leaves_the_settings_default():
    from cuga.sdk import CugaAgent

    agent = CugaAgent(auto_load_policies=False)

    assert "cuga_lite_max_steps" not in agent._prepare_run_config(None)["configurable"]


@pytest.mark.parametrize("bad", [0, -3, 2.5, True, "10"])
def test_invalid_cap_is_rejected(bad):
    from cuga.sdk import CugaAgent

    with pytest.raises(ValueError, match="cuga_lite_max_steps"):
        CugaAgent(cuga_lite_max_steps=bad, auto_load_policies=False)
