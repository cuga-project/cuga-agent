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
    # Arming is slash-only; stand in for the approval run() would have granted.
    concierge._arm_allowed.set(True)
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


# ── PUSH flows: the same watch armed twice must not orphan the first (Sami, PR 603 #2, P1) ──────
#
# The AP push path names each flow. APEngine._new_flow() DELETES any flow whose name already
# exists before creating its replacement, so two subscriptions that share a name cannot both have a
# live flow — arming the second silently deletes the first's, leaving its subscription pointing at
# nothing. With reuse OFF (the default) two identical arms are INTENTIONALLY two subscriptions, so
# their flows must be distinct.
#
# The old test_flow_name_uniqueness.py re-implemented the naming function and asserted on the copy
# (Sami: "inspect source strings instead of exercising production behavior… pass despite the
# repeated-watch failure"). This drives the REAL find_or_create_flow tool against a fake engine
# that reproduces _new_flow's delete-same-named semantics, and checks the outcome that actually
# matters: every subscription still owns a live flow.
class _FakeAP:
    """Just enough of APEngine to catch the collision. `create_push_flow` mirrors `_new_flow`:
    creating a flow whose NAME already exists deletes the prior flow first."""

    project_grain = "tenant"

    def __init__(self):
        self.by_name: dict[str, str] = {}  # flow name → the LIVE flow id for that name
        self.deleted: list[str] = []  # flow ids a same-named create destroyed
        self._n = 0

    async def reachable(self, *_a, **_k):
        return True

    async def connection_exists(self, *_a, **_k):
        return True  # the source's credential is connected — let arming proceed to flow creation

    async def create_push_flow(self, *, name, **_kw):
        if name in self.by_name:  # _new_flow: delete the existing same-named flow, then recreate
            self.deleted.append(self.by_name[name])
        self._n += 1
        fid = f"flow-{self._n}"
        self.by_name[name] = fid
        return fid


def _push_arm_tool(tmp_path, engine):
    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="cuga", prompt="a test agent"), scope="default")
    store = SubscriptionStore(str(tmp_path / "subs.db"))
    tools = concierge.make_concierge_tools(rt, store=store, engine=engine, users=None)
    concierge._arm_allowed.set(True)
    return next(t for t in tools if t.name == "find_or_create_flow"), store


@pytest.mark.asyncio
async def test_same_push_watch_armed_twice_keeps_both_flows(tmp_path, monkeypatch):
    monkeypatch.delenv("EVENTS_FLOW_REUSE", raising=False)  # reuse OFF — the default
    engine = _FakeAP()
    tool, store = _push_arm_tool(tmp_path, engine)

    push = dict(
        agent="cuga",
        kind="push",
        source="github",
        event="new_pr",
        repo="acme/app",
        prompt="watch PRs on acme/app",
        deliver_to="slack",
    )

    async def arm():
        return await tool.coroutine(**push) if getattr(tool, "coroutine", None) else await tool.ainvoke(push)

    r1 = await arm()
    r2 = await arm()
    for r in (r1, r2):
        assert "REUSING" not in r, f"reuse is off; got: {r[:160]}"
        assert "error" not in r.lower(), f"push arm failed: {r[:200]}"

    live = [s for s in store.list() if s.status != "deleted"]
    assert len(live) == 2, f"expected two subscriptions, got {len(live)}: {[s.id for s in live]}"

    # 1) distinct names — the fix's direct effect
    names = {s.flow_name for s in live}
    assert len(names) == 2, f"both arms produced the SAME flow name: {names}"

    # 2) behaviour that actually matters — no subscription's flow was deleted by its sibling
    assert engine.deleted == [], f"a same-named create deleted a sibling flow: {engine.deleted}"
    live_ids = set(engine.by_name.values())
    assert all(s.ap_flow_id in live_ids for s in live), (
        f"a subscription references a deleted flow: {[(s.id, s.ap_flow_id) for s in live]} live={live_ids}"
    )


@pytest.mark.asyncio
async def test_two_DIFFERENT_push_watches_also_stay_distinct(tmp_path, monkeypatch):
    """The pre-existing different-repo case must keep working alongside the repeated-watch fix."""
    monkeypatch.delenv("EVENTS_FLOW_REUSE", raising=False)
    engine = _FakeAP()
    tool, store = _push_arm_tool(tmp_path, engine)

    async def arm(repo):
        a = dict(
            agent="cuga",
            kind="push",
            source="github",
            event="new_pr",
            repo=repo,
            prompt=f"watch {repo}",
            deliver_to="slack",
        )
        return await tool.coroutine(**a) if getattr(tool, "coroutine", None) else await tool.ainvoke(a)

    await arm("acme/one")
    await arm("acme/two")
    live = [s for s in store.list() if s.status != "deleted"]
    assert len(live) == 2 and engine.deleted == [], engine.deleted
