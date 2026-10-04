"""Variables that hold a native tool result list their fields, not a preview of the data."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import save_native_result
from cuga.backend.cuga_graph.state.agent_state import AgentState

pytestmark = pytest.mark.unit


def test_native_result_variables_list_fields_instead_of_repeating_the_data():
    state = AgentState(input="", url="")
    rows = [{"email_thread_id": i, "subject": "s" * 300, "label": "priority-1"} for i in range(20)]
    save_native_result(state, "gmail_show_inbox_threads", rows)
    state.variables_manager.add_variable([1, 2, 3], name="computed", description="from code")

    summary = state.variables_manager.get_variables_summary()

    assert "Fields (per item): email_thread_id, subject, label" in summary
    assert "s" * 300 not in summary, "the data is already in the transcript"
    assert "- Value Preview: [1, 2, 3]" in summary, "variables made by code keep their preview"
