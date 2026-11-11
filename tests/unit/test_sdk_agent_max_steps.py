"""CugaAgent(cuga_lite_max_steps=...) reaches the step cap the graph reads from the run config."""

from unittest.mock import AsyncMock

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


class _RecordingGraph:
    """Stands in for the compiled graph: records each run config and runs nothing."""

    def __init__(self):
        self.configs: list[dict] = []

    async def aget_state(self, config):
        return None

    async def ainvoke(self, state, config):
        self.configs.append(config)
        return {"final_answer": "done"}

    async def astream(self, state, config, stream_mode, subgraphs):
        self.configs.append(config)
        yield ((), {"node": {}})


def _agent_with_recording_graph(monkeypatch):
    from cuga.sdk import CugaAgent

    agent = CugaAgent(cuga_lite_max_steps=5, auto_load_policies=False)
    monkeypatch.setattr(agent, "_ensure_initialized", AsyncMock())
    agent._compiled_graph = graph = _RecordingGraph()
    return agent, graph


@pytest.mark.asyncio
async def test_per_invoke_cap_replaces_the_callers_and_the_constructors(monkeypatch):
    agent, graph = _agent_with_recording_graph(monkeypatch)
    caller = {"configurable": {"cuga_lite_max_steps": 9}}

    await agent.invoke("hi", config=caller, cuga_lite_max_steps=3)

    assert graph.configs[-1]["configurable"]["cuga_lite_max_steps"] == 3
    assert caller["configurable"]["cuga_lite_max_steps"] == 9, "the caller's dict is not changed"


@pytest.mark.asyncio
async def test_per_stream_cap_replaces_the_callers_and_the_constructors(monkeypatch):
    agent, graph = _agent_with_recording_graph(monkeypatch)
    caller = {"configurable": {"cuga_lite_max_steps": 9}}

    [_ async for _ in agent.stream("hi", config=caller, cuga_lite_max_steps=3)]

    assert graph.configs[-1]["configurable"]["cuga_lite_max_steps"] == 3
    assert caller["configurable"]["cuga_lite_max_steps"] == 9, "the caller's dict is not changed"
