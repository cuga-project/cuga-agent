"""W17.1 row 14: Non-UI entry points cannot reach web checkpoints.

Coverage is provided by tests/unit/server/test_entry_point_isolation.py.
This file adds a thin system-level smoke test that validates the same
property through the scoped_checkpoint_key() public API, giving the
system/ directory a concrete assertion to run in the extras CI job.
"""

import pytest
import uuid


@pytest.mark.unit
def test_run_entry_point_cannot_read_web_checkpoint():
    """Row 14: A /run call with thread_id equal to a web UUID gets a scoped key.

    The scoped key must be distinct from the bare UUID, must not itself be a
    valid UUID, and must begin with the "run:" entry-point prefix.  This
    ensures that /run checkpoints are isolated from web-session checkpoints
    even when the caller supplies a web-issued thread_id.

    Full coverage of all entry-point prefixes (a2a:, acp:, ext:) lives in
    tests/unit/server/test_entry_point_isolation.py (W07.2–W07.5).
    """
    from cuga.backend.server.thread_scope_key import scoped_checkpoint_key
    from cuga.backend.server.conversation_threads import Scope

    DEFAULT_USER_ID = "default_user"
    scope = Scope(
        tenant_id="t1",
        instance_id="i1",
        agent_id="cuga-default",
        user_id=DEFAULT_USER_ID,
    )
    web_uuid = "550e8400-e29b-41d4-a716-446655440000"

    run_key = scoped_checkpoint_key("run", scope, web_uuid)

    # The scoped key must differ from the raw web UUID.
    assert run_key != web_uuid, "run key must not equal the bare web UUID"

    # The key must carry the entry-point prefix.
    assert run_key.startswith("run:"), f"expected 'run:' prefix, got: {run_key!r}"

    # The key must not itself be parseable as a UUID.
    with pytest.raises(ValueError):
        uuid.UUID(run_key)
