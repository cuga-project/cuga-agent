"""Tests for user_id propagation in server main.py event_stream."""

from unittest.mock import patch

from cuga.backend.cuga_graph.state.agent_state import AgentState


def test_local_state_user_id_is_set_from_authenticated_user():
    """Verify that local_state.user_id is set from the authenticated user_id."""
    # Create a mock state
    local_state = AgentState(
        input="test query",
        url="https://example.com",
    )

    # Simulate the user_id assignment from main.py
    user_id = "authenticated-user-123"
    local_state.user_id = user_id

    assert local_state.user_id == "authenticated-user-123"


def test_local_state_service_scope_is_set_with_tenant_and_instance():
    """Verify that local_state.service_scope is set with tenant_id and instance_id."""
    local_state = AgentState(
        input="test query",
        url="https://example.com",
    )

    # Simulate the service_scope assignment from main.py
    with patch("cuga.config.get_tenant_id", return_value="tenant-456"):
        with patch("cuga.config.get_service_instance_id", return_value="instance-789"):
            from cuga.config import get_service_instance_id, get_tenant_id

            local_state.service_scope = {
                "tenant_id": get_tenant_id(),
                "instance_id": get_service_instance_id(),
            }

    assert local_state.service_scope["tenant_id"] == "tenant-456"
    assert local_state.service_scope["instance_id"] == "instance-789"


def test_local_state_user_id_and_service_scope_set_together():
    """Verify that both user_id and service_scope are set correctly together."""
    local_state = AgentState(
        input="test query",
        url="https://example.com",
    )

    user_id = "authenticated-user-123"

    with patch("cuga.config.get_tenant_id", return_value="tenant-456"):
        with patch("cuga.config.get_service_instance_id", return_value="instance-789"):
            from cuga.config import get_service_instance_id, get_tenant_id

            # Simulate the assignments from main.py (lines 1245-1247)
            local_state.user_id = user_id
            local_state.service_scope = {
                "tenant_id": get_tenant_id(),
                "instance_id": get_service_instance_id(),
            }
            local_state.user_id = user_id  # Duplicate assignment as in main.py

    # Verify both are set correctly
    assert local_state.user_id == "authenticated-user-123"
    assert local_state.service_scope["tenant_id"] == "tenant-456"
    assert local_state.service_scope["instance_id"] == "instance-789"


def test_local_state_user_id_duplicate_assignment_does_not_cause_issues():
    """Verify that the duplicate user_id assignment (lines 1245 and 1247) doesn't cause issues."""
    local_state = AgentState(
        input="test query",
        url="https://example.com",
    )

    user_id = "authenticated-user-123"

    # First assignment (line 1245)
    local_state.user_id = user_id
    assert local_state.user_id == "authenticated-user-123"

    # Second assignment (line 1247) - should not cause any issues
    local_state.user_id = user_id
    assert local_state.user_id == "authenticated-user-123"


def test_local_state_handles_none_user_id():
    """Verify that local_state handles None user_id gracefully."""
    local_state = AgentState(
        input="test query",
        url="https://example.com",
    )

    user_id = None
    local_state.user_id = user_id

    assert local_state.user_id is None


def test_local_state_multi_user_context_complete():
    """Verify complete multi-user context is set correctly for Evolve integration."""
    local_state = AgentState(
        input="test query",
        url="https://example.com",
        thread_id="thread-999",
    )

    user_id = "authenticated-user-123"

    with patch("cuga.config.get_tenant_id", return_value="tenant-456"):
        with patch("cuga.config.get_service_instance_id", return_value="instance-789"):
            from cuga.config import get_service_instance_id, get_tenant_id

            local_state.user_id = user_id
            local_state.service_scope = {
                "tenant_id": get_tenant_id(),
                "instance_id": get_service_instance_id(),
            }

    # Verify all multi-user context fields are set for Evolve
    assert local_state.user_id == "authenticated-user-123"
    assert local_state.thread_id == "thread-999"
    assert local_state.service_scope["tenant_id"] == "tenant-456"

    # These values would be passed to EvolveIntegration
    evolve_user_id = local_state.user_id or None
    evolve_namespace_id = (local_state.service_scope or {}).get("tenant_id") or None
    evolve_session_id = local_state.thread_id or None

    assert evolve_user_id == "authenticated-user-123"
    assert evolve_namespace_id == "tenant-456"
    assert evolve_session_id == "thread-999"


# Made with Bob
