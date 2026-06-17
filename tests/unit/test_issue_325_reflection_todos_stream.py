from __future__ import annotations

import json

from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.utils.agent_loop import (
    StreamEvent,
    _sandbox_stream_events,
    _split_sandbox_execution_output,
)
from cuga.backend.server.manage_routes import _extract_agent_feature_overrides


def test_split_sandbox_execution_output_splits_reflection():
    raw = "tool ran ok\n\n---\n\nSummary:\nReflect on next step"
    execution, reflection = _split_sandbox_execution_output(raw)
    assert execution == "tool ran ok"
    assert reflection == "Reflect on next step"


def test_sandbox_stream_events_emits_reflection_and_task_todos():
    state_data = {
        "chat_messages": [
            HumanMessage(content="Execution output:\nresult=1\n\n---\n\nSummary:\nContinue with step 2")
        ],
        "variables_storage": {"result": 1},
        "task_todos": [
            {"text": "Fetch data", "status": "completed"},
            {"text": "Summarize", "status": "in_progress"},
        ],
    }

    events = _sandbox_stream_events(state_data)
    names = [event.name for event in events]

    assert names == ["CodeAgent", "Reflection", "TaskTodos"]

    code_agent = json.loads(events[0].data)
    assert "Summary:" not in code_agent["execution_output"]
    assert code_agent["execution_output"] == "result=1"
    assert events[1].data == "Continue with step 2"

    todos_payload = json.loads(events[2].data)
    assert len(todos_payload["todos"]) == 2


def test_sandbox_stream_events_emits_task_todos_without_execution_output():
    state_data = {
        "chat_messages": [],
        "task_todos": [{"text": "Plan step", "status": "pending"}],
    }

    events = _sandbox_stream_events(state_data)
    assert [event.name for event in events] == ["TaskTodos"]


def test_extract_agent_feature_overrides_reads_demo_reflection_flag():
    overrides = _extract_agent_feature_overrides(
        {
            "feature_flags": {
                "enable_todos": True,
                "reflection": True,
            }
        }
    )
    assert overrides["enable_todos"] is True
    assert overrides["reflection_enabled"] is True


def test_stream_event_list_formatting():
    events = [
        StreamEvent(name="Reflection", data="summary text"),
        StreamEvent(name="TaskTodos", data='{"todos": []}'),
    ]
    formatted = [event.format() for event in events]
    assert formatted[0].startswith("event: Reflection\n")
    assert formatted[1].startswith("event: TaskTodos\n")
