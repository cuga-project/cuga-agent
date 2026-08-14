"""Only `/automate` (and its explicit siblings) may arm. Plain English answers once and stops.

WHY THIS EXISTS
---------------
Arming is a standing commitment: it runs unattended, on a schedule, against real credentials, and
delivers into a channel. The product rule is that it takes an explicit verb — the events service is
unreachable without one — so nothing schedules itself because a sentence happened to sound like a
schedule.

The code did not enforce that. `concierge.run()` had THREE arming paths and only the first was
gated:

    _slash_parse  -> _arm_propose   confirm card, then arm on "yes"      ✅
    _pre_route    -> _arm_spec      armed on the spot, no card           ❌
    the LLM       -> find_or_create_flow  armed on the spot, no card     ❌

Observed on the deployed instance: plain ``every 3 minutes give me a joke`` returned
``ARMED cron … Runs every 3 min`` with ``state: None`` — nothing to approve, nothing to see. The
same two paths run on Slack, Telegram and Discord, so it was never Studio-specific.

The gate is a ContextVar rather than an argument because those two callers do not share a call
signature — one calls the tool directly, the other is an LLM tool call several frames away.
``run()`` clears it every turn; only the post-approval armer sets it.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend"))

from events import concierge  # noqa: E402
from events.agent_store import AgentStore, AgentSpec  # noqa: E402
from events.runtime import AgentStoreRuntime  # noqa: E402
from events.subscriptions import SubscriptionStore  # noqa: E402

ARM_ARGS = dict(agent="cuga", kind="cron", prompt="give me a joke", every_minutes=5, deliver_to="web")


def _tool(tmp_path):
    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="cuga", prompt="a test agent"), scope="default")
    store = SubscriptionStore(str(tmp_path / "subs.db"))
    tools = concierge.make_concierge_tools(rt, store=store, engine=None, users=None)
    return next(t for t in tools if t.name == "find_or_create_flow"), store


async def _call(tool, **over):
    args = {**ARM_ARGS, **over}
    return await tool.coroutine(**args) if getattr(tool, "coroutine", None) else await tool.ainvoke(args)


@pytest.mark.asyncio
async def test_plain_english_cannot_arm(tmp_path):
    """The default. This is the state every LLM tool call and the pre-router arrive in."""
    concierge._arm_allowed.set(False)
    tool, store = _tool(tmp_path)

    reply = await _call(tool)

    assert "ARMED" not in reply.upper(), reply[:200]
    assert not [s for s in store.list() if s.status != "deleted"], "a flow was created without a verb"


@pytest.mark.asyncio
async def test_the_refusal_names_the_phrasing_that_works(tmp_path):
    """The LLM reads this string. A bare "no" makes it try a different tool; naming the verb makes
    it relay the instruction to the human."""
    concierge._arm_allowed.set(False)
    tool, _ = _tool(tmp_path)

    reply = await _call(tool)

    assert "/automate" in reply, reply[:200]
    assert "give me a joke" in reply, "the refusal should echo back what they asked for"


@pytest.mark.asyncio
async def test_an_approved_arming_still_works(tmp_path):
    """The one path that may arm: a human typed the verb, saw the card, and replied yes —
    which is what run() reflects by setting this flag before calling _arm_slash."""
    concierge._arm_allowed.set(True)
    tool, store = _tool(tmp_path)

    reply = await _call(tool)

    assert "ARMED" in reply.upper(), reply[:200]
    assert len([s for s in store.list() if s.status != "deleted"]) == 1


@pytest.mark.asyncio
async def test_the_grant_does_not_leak_into_the_next_turn(tmp_path):
    """run() clears the flag at the top of every turn. Without that, one approved arming would
    leave the door open for every plain-English message that followed on the same thread."""
    concierge._arm_allowed.set(True)
    tool, store = _tool(tmp_path)
    await _call(tool)

    concierge._arm_allowed.set(False)  # what run() does on the next message
    reply = await _call(tool, prompt="tell me the weather")

    assert "ARMED" not in reply.upper(), reply[:200]
    assert len([s for s in store.list() if s.status != "deleted"]) == 1, "a second flow slipped in"
