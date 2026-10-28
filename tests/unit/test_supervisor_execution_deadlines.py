"""Delegation deadlines cover child runs without leaking cancelled child tasks."""

import asyncio

import pytest
from langgraph.graph import END
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.code_wrapper import CodeWrapper
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
    create_execute_agent_tool_node,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter
from cuga.config import settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def local_execution(monkeypatch):
    monkeypatch.setattr(settings.execution, "python_backend", "local")
    monkeypatch.setattr(settings.policy, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "sandbox_execution_timeout", 0.02)
    monkeypatch.setattr(settings.supervisor, "delegation_execution_timeout", 1)
    monkeypatch.setattr(CodeExecutor, "_local_executor", None)


def node_and_state(delegate):
    adapter = SupervisorGraphAdapter(agents={"weatherbot": object()})
    adapter._agent_tools_context["delegate_to_weatherbot"] = delegate
    state = CugaSupervisorState(
        input="weather",
        thread_id="deadline-test",
        script="weather = await delegate_to_weatherbot('weather')\nprint(weather)",
    )
    return create_execute_agent_tool_node(adapter), state


@pytest.mark.asyncio
async def test_child_model_can_finish_after_python_block_deadline():
    async def delegate(task):
        await asyncio.sleep(0.06)
        return "WEATHER-RESULT"

    node, state = node_and_state(delegate)
    result = await node(state)

    assert not isinstance(result, Command)
    assert "WEATHER-RESULT" in result["supervisor_chat_messages"][-1].content
    assert "timed out" not in result["supervisor_chat_messages"][-1].content


@pytest.mark.asyncio
async def test_delegation_deadline_rejects_remote_execution():
    with pytest.raises(ValueError, match="requires mode='local'"):
        await CodeExecutor.eval_with_tools_async(
            "print('must not start')", {}, CugaSupervisorState(), mode="e2b", execution_timeout=1
        )


@pytest.mark.asyncio
async def test_supervisor_orchestrates_locally_without_changing_child_backend(monkeypatch):
    monkeypatch.setattr(settings.execution, "python_backend", "e2b")

    async def delegate(task):
        assert settings.execution.python_backend == "e2b"
        return "REMOTE-CHILD-CONFIG-PRESERVED"

    node, state = node_and_state(delegate)
    result = await node(state)

    assert not isinstance(result, Command)
    assert "REMOTE-CHILD-CONFIG-PRESERVED" in result["supervisor_chat_messages"][-1].content
    assert settings.execution.python_backend == "e2b"


@pytest.mark.asyncio
async def test_supervisor_deadline_cancels_child_and_stops_model_retry(monkeypatch):
    cancelled = asyncio.Event()
    monkeypatch.setattr(settings.supervisor, "delegation_execution_timeout", 0.03)

    async def delegate(task):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    node, state = node_and_state(delegate)
    result = await node(state)

    assert cancelled.is_set()
    assert isinstance(result, Command)
    assert result.goto == END
    assert "Supervisor delegation timed out" in result.update["final_answer"]
    assert result.update["error"] == result.update["final_answer"]
    assert "0 tool call(s)" not in result.update["final_answer"]


@pytest.mark.asyncio
async def test_child_python_block_keeps_its_shorter_deadline():
    async def slow_tool():
        await asyncio.sleep(0.08)
        return "UNREACHABLE"

    async def delegate(task):
        output, _ = await CodeExecutor.eval_with_tools_async(
            "child = await slow_tool()\nprint(child)", {"slow_tool": slow_tool}, CugaSupervisorState()
        )
        return output

    node, state = node_and_state(delegate)
    result = await node(state)

    assert "Execution timed out after 0.02 seconds" in result["supervisor_chat_messages"][-1].content
    assert "UNREACHABLE" not in result["supervisor_chat_messages"][-1].content


@pytest.mark.asyncio
async def test_parent_deadline_cancels_nested_executor_task(monkeypatch):
    cancelled = asyncio.Event()
    monkeypatch.setattr(settings.supervisor, "delegation_execution_timeout", 0.03)

    async def slow_tool():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def delegate(task):
        return await LocalExecutor().execute(
            CodeWrapper.wrap_code("result = await slow_tool()\nprint(result)"),
            {"slow_tool": slow_tool},
            timeout=1,
        )

    node, state = node_and_state(delegate)
    result = await node(state)

    assert isinstance(result, Command)
    assert cancelled.is_set(), "nested child execution must end before the parent reports timeout"


@pytest.mark.asyncio
async def test_cancellation_cleanup_error_cannot_replace_expired_parent_deadline(monkeypatch):
    monkeypatch.setattr(settings.supervisor, "delegation_execution_timeout", 0.03)

    async def delegate(task):
        try:
            await asyncio.Event().wait()
        finally:
            raise TimeoutError("child cleanup timeout")

    node, state = node_and_state(delegate)
    result = await node(state)

    assert isinstance(result, Command)
    assert result.goto == END
    assert "Supervisor delegation timed out" in result.update["final_answer"]


@pytest.mark.asyncio
async def test_child_tool_timeout_is_not_a_supervisor_deadline():
    async def delegate(task):
        raise TimeoutError("Weather service request timed out")

    node, state = node_and_state(delegate)
    result = await node(state)

    assert not isinstance(result, Command)
    output = result["supervisor_chat_messages"][-1].content
    assert "Weather service request timed out" in output
    assert "Supervisor delegation timed out" not in output


@pytest.mark.asyncio
async def test_external_cancellation_waits_for_generated_task_cleanup():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow_tool():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    run = asyncio.create_task(
        LocalExecutor().execute(
            CodeWrapper.wrap_code("result = await slow_tool()\nprint(result)"),
            {"slow_tool": slow_tool},
            timeout=1,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert cancelled.is_set()
