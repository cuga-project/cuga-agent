"""Execute pauses must stop the compiled graph before another model call."""

import pytest
from langgraph.graph import END
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_graph import build_agent_graph
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
    create_execute_agent_tool_node,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("result_kind", ["update", "command", "approval"])
async def test_execute_routes_updates_but_honors_pause_commands(result_kind):
    adapter = SupervisorGraphAdapter(agents={"weatherbot": object()}, plan_approval=True)
    approval_node = create_execute_agent_tool_node(adapter)
    model_calls = []

    async def prepare(state):
        return Command(goto=adapter.execute_node_name)

    async def execute(state, config):
        assert config["configurable"]["test_marker"] == "forwarded"
        if result_kind == "approval":
            return await approval_node(state, config)
        if result_kind == "command":
            return Command(goto=END, update={"final_answer": "Paused"})
        return {"final_answer": "Executed"}

    async def call_model(state):
        model_calls.append(state.final_answer)
        return Command(goto=END, update={"final_answer": "Completed"})

    graph = build_agent_graph(
        adapter=adapter,
        state_class=CugaSupervisorState,
        prepare_node=prepare,
        call_model_node=call_model,
        execute_node=execute,
    ).compile()
    result = await graph.ainvoke(
        CugaSupervisorState(input="Test approval", script="await delegate_to_weatherbot(task='lookup')"),
        {"configurable": {"test_marker": "forwarded"}},
    )

    assert model_calls == (["Executed"] if result_kind == "update" else [])
    if result_kind == "approval":
        assert result["hitl_action"] is not None
        assert result["supervisor_metadata"]["approval_required"] is True
