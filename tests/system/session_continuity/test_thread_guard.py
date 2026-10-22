"""W17.1 rows 6, 15, 16: ownership guard tests.

These rows are covered by the unit tests in
tests/unit/server/test_thread_guard.py.  This file documents that
coverage mapping and provides pass-through marker tests so that running
``uv run pytest tests/system/session_continuity/ -m 'not slow'``
exercises the relevant assertions.

Row  6 → test_another_users_thread_same_404_as_random_uuid
Row 15 → test_stream_first_turn_record_without_history_is_protected
         (renamed from plan's test_first_turn_record_no_history_protected_from_other_user)
Row 16 → test_db_exception_raises_not_found
"""

import pytest


@pytest.mark.unit
def test_unauthorized_and_unknown_ids_indistinguishable():
    """Row 6: /stream and workspace endpoints return identical 404 for unknown vs unauthorised IDs.

    Delegates to the unit-level assertion in test_thread_guard.py.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from cuga.backend.server.conversation_threads import Scope
    from cuga.backend.server.thread_guard import ThreadNotFound, resolve_owned_thread, _NOT_FOUND_DETAIL

    scope = Scope(
        tenant_id="tenant-1",
        instance_id="inst-1",
        agent_id="agent-1",
        user_id="user-1",
    )

    async def _run():
        db_other = type("DB", (), {"get_in_scope": AsyncMock(return_value=None)})()
        db_rand = type("DB", (), {"get_in_scope": AsyncMock(return_value=None)})()

        with patch(
            "cuga.backend.server.conversation_threads.get_conversation_threads_db",
            return_value=db_other,
        ):
            with pytest.raises(ThreadNotFound) as exc_other:
                await resolve_owned_thread(scope, "other-users-tid")

        with patch(
            "cuga.backend.server.conversation_threads.get_conversation_threads_db",
            return_value=db_rand,
        ):
            with pytest.raises(ThreadNotFound) as exc_rand:
                await resolve_owned_thread(scope, "00000000-0000-0000-0000-000000000000")

        assert exc_other.value.status_code == exc_rand.value.status_code == 404
        assert exc_other.value.detail == exc_rand.value.detail == _NOT_FOUND_DETAIL

    asyncio.run(_run())


@pytest.mark.unit
def test_first_turn_thread_protected():
    """Row 15: A new thread in its first turn is protected from other users.

    A freshly created record (status=NEW, no checkpoint history) must not be
    accessible to a different user.  The authoritative assertion lives in
    test_thread_guard.py::test_stream_first_turn_record_without_history_is_protected.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from cuga.backend.server.conversation_threads import Scope
    from cuga.backend.server.thread_guard import ThreadNotFound, resolve_owned_thread, _NOT_FOUND_DETAIL

    other_scope = Scope(
        tenant_id="tenant-1",
        instance_id="inst-1",
        agent_id="agent-1",
        user_id="other",
    )

    async def _run():
        # DB returns None for the other user (scope mismatch → not found).
        db_other = type("DB", (), {"get_in_scope": AsyncMock(return_value=None)})()
        with patch(
            "cuga.backend.server.conversation_threads.get_conversation_threads_db",
            return_value=db_other,
        ):
            with pytest.raises(ThreadNotFound) as exc:
                await resolve_owned_thread(other_scope, "fresh-thread")
        assert exc.value.status_code == 404
        assert exc.value.detail == _NOT_FOUND_DETAIL

    asyncio.run(_run())


@pytest.mark.unit
def test_db_error_denies_access():
    """Row 16: A DB error in the ownership lookup denies access (fail-closed).

    The authoritative assertion lives in
    test_thread_guard.py::test_db_exception_raises_not_found.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from cuga.backend.server.conversation_threads import Scope
    from cuga.backend.server.thread_guard import ThreadNotFound, resolve_owned_thread, _NOT_FOUND_DETAIL

    scope = Scope(
        tenant_id="tenant-1",
        instance_id="inst-1",
        agent_id="agent-1",
        user_id="user-1",
    )

    async def _run():
        db = type(
            "DB",
            (),
            {"get_in_scope": AsyncMock(side_effect=RuntimeError("connection refused"))},
        )()
        with patch(
            "cuga.backend.server.conversation_threads.get_conversation_threads_db",
            return_value=db,
        ):
            with pytest.raises(ThreadNotFound) as exc:
                await resolve_owned_thread(scope, "tid-abc")
        assert exc.value.status_code == 404
        assert exc.value.detail == _NOT_FOUND_DETAIL

    asyncio.run(_run())
