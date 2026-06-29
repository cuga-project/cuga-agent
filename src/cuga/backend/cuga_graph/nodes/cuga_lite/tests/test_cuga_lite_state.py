"""Tests for CugaLiteState model."""

from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState


def test_cuga_lite_state_user_id_field_exists():
    """Verify that user_id field exists and can be set in CugaLiteState."""
    state = CugaLiteState(
        chat_messages=[HumanMessage(content="test")],
        sub_task="test task",
        user_id="user-123",
    )

    assert state.user_id == "user-123"


def test_cuga_lite_state_user_id_defaults_to_none():
    """Verify that user_id defaults to None when not provided."""
    state = CugaLiteState(
        chat_messages=[HumanMessage(content="test")],
        sub_task="test task",
    )

    assert state.user_id is None


def test_cuga_lite_state_user_id_can_be_updated():
    """Verify that user_id can be updated after state creation."""
    state = CugaLiteState(
        chat_messages=[HumanMessage(content="test")],
        sub_task="test task",
        user_id="user-123",
    )

    state.user_id = "user-456"
    assert state.user_id == "user-456"


def test_cuga_lite_state_multi_user_fields_work_together():
    """Verify that user_id, thread_id, and service_scope work together correctly."""
    state = CugaLiteState(
        chat_messages=[HumanMessage(content="test")],
        sub_task="test task",
        user_id="user-123",
        thread_id="thread-456",
        service_scope={"tenant_id": "tenant-789", "instance_id": "inst-1"},
    )

    assert state.user_id == "user-123"
    assert state.thread_id == "thread-456"
    assert state.service_scope == {"tenant_id": "tenant-789", "instance_id": "inst-1"}


def test_cuga_lite_state_service_scope_tenant_id_extraction():
    """Verify that tenant_id can be extracted from service_scope."""
    state = CugaLiteState(
        chat_messages=[HumanMessage(content="test")],
        sub_task="test task",
        service_scope={"tenant_id": "tenant-789", "instance_id": "inst-1"},
    )

    tenant_id = (state.service_scope or {}).get("tenant_id")
    assert tenant_id == "tenant-789"


def test_cuga_lite_state_handles_empty_service_scope():
    """Verify that empty service_scope is handled gracefully."""
    state = CugaLiteState(
        chat_messages=[HumanMessage(content="test")],
        sub_task="test task",
        service_scope={},
    )

    tenant_id = (state.service_scope or {}).get("tenant_id")
    assert tenant_id is None


# Made with Bob
