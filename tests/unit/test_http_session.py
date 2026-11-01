import pytest
import asyncio

from cuga.backend.tools_env.registry.utils.http_session import (
    close_http_session,
    get_http_session,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
async def _reset_http_session():
    """The session is module-level state; close it after every test so tests stay independent."""
    yield
    await close_http_session()


async def test_same_loop_returns_same_session():
    first = await get_http_session()
    second = await get_http_session()

    assert first is second
    assert not first.closed


async def test_new_loop_gets_a_new_session():
    async def fetch():
        return await get_http_session()

    first = await get_http_session()
    second = await asyncio.to_thread(asyncio.run, fetch())

    assert second is not first
    # first belongs to this loop; the helper thread replaced our reference to it, so close it here
    await first.close()


async def test_externally_closed_session_is_replaced():
    first = await get_http_session()
    await first.close()
    second = await get_http_session()

    assert second is not first
    assert not second.closed


async def test_session_is_replaced_after_close_http_session():
    first = await get_http_session()
    await close_http_session()
    second = await get_http_session()

    assert second is not first
    assert not second.closed
    assert first.closed


async def test_close_http_session_is_idempotent():
    await close_http_session()  # nothing was ever opened

    session = await get_http_session()
    await close_http_session()
    await close_http_session()  # already closed

    assert session.closed


async def test_concurrent_callers_share_one_session():
    # get_http_session has no await between its check and its assignment, so this passes today.
    # It guards that: if someone adds an await there, callers interleave and each creates a session.
    sessions = await asyncio.gather(*(get_http_session() for _ in range(20)))

    assert len({id(s) for s in sessions}) == 1
