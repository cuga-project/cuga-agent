"""W17.1 row 20: Non-browser client continues a thread over plain HTTP.

Coverage is provided by tests/unit/server/test_stream_prelude.py (W09.6).

Key assertions already in the unit suite:
- test_thread_created_event_emitted_when_no_x_thread_id
    → without X-Thread-ID, first SSE event is thread_created
- test_no_thread_created_event_when_x_thread_id_provided
    → with X-Thread-ID matching an existing record, issued_thread_id is None
- test_thread_created_sse_event_is_first_in_stream
    → thread_created is the very first event in the SSE stream

This file documents that coverage and provides a lightweight property
test confirming the issued_thread_id contract without a live server.
"""

import pytest


@pytest.mark.unit
def test_non_browser_client_thread_continuation():
    """Row 20: /stream with X-Thread-ID continues thread; without X-Thread-ID, first event is thread_created.

    Without X-Thread-ID the server issues a new thread_id and emits a
    thread_created SSE event as the first event so that plain-HTTP clients
    (SDK, CLI) can capture the ID for subsequent turns.

    With X-Thread-ID pointing to a known record the server does not emit
    thread_created and passes issued_thread_id=None to event_stream.

    The authoritative assertions (which exercise the full SSE pipeline) live
    in tests/unit/server/test_stream_prelude.py::test_thread_created_* and
    test_no_thread_created_event_when_x_thread_id_provided.

    This pass-through test validates the scoped_checkpoint_key() property
    that underpins the "continue" case: the same scope + thread_id always
    produces the same checkpoint key, so a second turn resumes correctly.
    """
    from cuga.backend.server.thread_scope_key import scoped_checkpoint_key
    from cuga.backend.server.conversation_threads import Scope

    scope = Scope(
        tenant_id="tenant-1",
        instance_id="inst-1",
        agent_id="cuga-default",
        user_id="sdk-user",
    )
    thread_id = "sdk-thread-001"

    key_first = scoped_checkpoint_key("web", scope, thread_id)
    key_second = scoped_checkpoint_key("web", scope, thread_id)

    # Same inputs must always produce the same checkpoint key so that turn 2
    # resumes the same checkpoint as turn 1.
    assert key_first == key_second, (
        "Checkpoint key must be deterministic for thread continuation to work."
    )
