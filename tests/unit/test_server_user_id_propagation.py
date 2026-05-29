"""Tests for user_id / service_scope propagation in server main.py.

These exercise ``apply_request_user_context`` — the real production helper that
``event_stream`` calls — so regressions in the assignment logic are caught,
rather than re-implementing the assignment inside the test.
"""

from unittest.mock import patch

from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.server.main import apply_request_user_context


def test_apply_request_user_context_sets_user_id_and_service_scope():
    local_state = AgentState(input="test query", url="https://example.com")

    with (
        patch("cuga.config.get_tenant_id", return_value="tenant-456"),
        patch("cuga.config.get_service_instance_id", return_value="instance-789"),
    ):
        apply_request_user_context(local_state, "authenticated-user-123")

    assert local_state.user_id == "authenticated-user-123"
    assert local_state.service_scope["tenant_id"] == "tenant-456"
    assert local_state.service_scope["instance_id"] == "instance-789"


def test_apply_request_user_context_handles_none_user_id():
    local_state = AgentState(input="test query", url="https://example.com")

    with (
        patch("cuga.config.get_tenant_id", return_value="tenant-456"),
        patch("cuga.config.get_service_instance_id", return_value="instance-789"),
    ):
        apply_request_user_context(local_state, None)

    assert local_state.user_id is None
    assert local_state.service_scope["tenant_id"] == "tenant-456"
    assert local_state.service_scope["instance_id"] == "instance-789"


def test_apply_request_user_context_overwrites_existing_values():
    local_state = AgentState(
        input="test query",
        url="https://example.com",
        thread_id="thread-999",
        user_id="stale-user",
    )

    with (
        patch("cuga.config.get_tenant_id", return_value="tenant-456"),
        patch("cuga.config.get_service_instance_id", return_value="instance-789"),
    ):
        apply_request_user_context(local_state, "authenticated-user-123")

    # Values that flow on to EvolveIntegration
    assert local_state.user_id == "authenticated-user-123"
    assert local_state.thread_id == "thread-999"
    assert local_state.service_scope["tenant_id"] == "tenant-456"
    assert local_state.service_scope["instance_id"] == "instance-789"
