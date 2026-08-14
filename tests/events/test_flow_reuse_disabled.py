"""Arming the same thing twice must create TWO flows, not silently reuse one.

WHY THIS EXISTS
---------------
Observed in a real Slack arming: a human typed a request, was shown a confirmation card, replied
"yes", and got back

    REUSING existing flow "ea:cron-cuga-1m-dcac" (CRON) for cuga → slack … Nothing new created.

They confirmed an arming and nothing was armed. That is the worst shape a failure can take here —
it is indistinguishable from success unless you go and look at the subscription list.

The dedup identity was never sound for cron/poll. It folds a hash of the task text into the key,
and that text comes from the ``_utterance`` ContextVar, which the react-agent does not reliably
propagate into tool execution (the same caveat ``concierge.py`` documents for the poll-tier
picker). When it does not arrive, every arming in that path hashes the same string and collides.

So reuse is OFF unless ``EVENTS_FLOW_REUSE=1``. Both directions are pinned here, because "off by
default" is only half a contract — an operator who turns it back on must still get the old
behaviour.

Nothing offline covered this before; only the live NL→Flow bench ever exercised it, which is why
it could regress unnoticed.
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

ARM_ARGS = dict(
    agent="cuga",
    kind="cron",
    prompt="every 3 minutes give me a joke",
    every_minutes=3,
    deliver_to="slack",
)


def _arm_tool(tmp_path):
    """The real `find_or_create_flow` tool, wired to throwaway stores and no AP engine."""
    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="cuga", prompt="a test agent"), scope="default")
    store = SubscriptionStore(str(tmp_path / "subs.db"))
    tools = concierge.make_concierge_tools(rt, store=store, engine=None, users=None)
    return next(t for t in tools if t.name == "find_or_create_flow"), store


async def _arm(tool, **over):
    args = {**ARM_ARGS, **over}
    return await tool.coroutine(**args) if getattr(tool, "coroutine", None) else await tool.ainvoke(args)


@pytest.mark.asyncio
async def test_arming_the_same_request_twice_creates_two_flows(tmp_path, monkeypatch):
    monkeypatch.delenv("EVENTS_FLOW_REUSE", raising=False)
    tool, store = _arm_tool(tmp_path)

    first = await _arm(tool)
    second = await _arm(tool)

    for reply in (first, second):
        assert "REUSING" not in reply, f"reuse is supposed to be off, got: {reply[:160]}"
    live = [s for s in store.list() if s.status != "deleted"]
    assert len(live) == 2, f"expected two flows, got {len(live)}: {[s.id for s in live]}"
    # The empty dedup_key is the mechanism: the store's UNIQUE index is partial
    # (WHERE dedup_key != ''), so an empty key is what lets the second insert through at all.
    assert all(s.dedup_key == "" for s in live), [s.dedup_key for s in live]


@pytest.mark.asyncio
async def test_reuse_still_works_when_explicitly_re_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_FLOW_REUSE", "1")
    tool, store = _arm_tool(tmp_path)

    first = await _arm(tool)
    second = await _arm(tool)

    assert "REUSING" not in first, first[:160]
    assert "REUSING" in second, f"with the flag on, the second arm should reuse: {second[:160]}"
    live = [s for s in store.list() if s.status != "deleted"]
    assert len(live) == 1, f"expected one flow, got {len(live)}"
