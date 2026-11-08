"""HookRegistry: order, blocking, rewritten replies, broken hooks, events and listeners."""

import pytest
from langchain_core.tools import tool

from cuga.backend.hooks import AgentHooks, HookBlocked, HookRegistry, HookResult, wrap_tools_with_hooks

pytestmark = pytest.mark.unit


class Block(AgentHooks):
    def before_tool_call(self, tool, args):
        return "not allowed" if args.get("to") == "blocked@x.com" else None


class Rewrite(AgentHooks):
    def __init__(self, suffix):
        self.suffix, self.seen = suffix, []

    def before_reply(self, answer):
        self.seen.append(answer)
        return HookResult(answer=answer + self.suffix)


class Broken(AgentHooks):
    def before_tool_call(self, tool, args):
        raise RuntimeError("bug in a hook")


calls = []


@tool
async def send(to: str) -> str:
    """Send a message."""
    calls.append(to)
    return "sent"


@pytest.mark.asyncio
async def test_a_blocking_hook_stops_the_tool_before_it_runs():
    calls.clear()
    (wrapped,) = wrap_tools_with_hooks([send], HookRegistry([Block()]))
    with pytest.raises(HookBlocked, match="not allowed"):
        await wrapped.ainvoke({"to": "blocked@x.com"})
    assert await wrapped.ainvoke({"to": "ok@x.com"}) == "sent"
    assert calls == ["ok@x.com"]


def test_reply_hooks_run_in_order_and_see_the_rewritten_answer():
    first, second = Rewrite(" [a]"), Rewrite(" [b]")
    res = HookRegistry([first, second]).before_reply("done")
    assert second.seen == ["done [a]"]
    assert res.answer == "done [a] [b]"


@pytest.mark.asyncio
async def test_a_broken_hook_is_skipped_and_the_others_still_run():
    calls.clear()
    (wrapped,) = wrap_tools_with_hooks([send], HookRegistry([Broken(), Block()]))
    with pytest.raises(HookBlocked):
        await wrapped.ainvoke({"to": "blocked@x.com"})
    assert await wrapped.ainvoke({"to": "ok@x.com"}) == "sent"


def test_events_are_recorded_per_run_and_sent_to_listeners():
    reg = HookRegistry([Block()])
    seen = []
    reg.add_listener(seen.append)
    reg.before_tool_call("send", {"to": "a@x.com"})
    reg.start_run()
    reg.before_tool_call("send", {"to": "blocked@x.com"})
    events = reg.run_events()
    assert len(seen) == 2 and len(events) == 1
    assert events[0]["hook"] == "before_tool_call" and events[0]["message"] == "not allowed"


def test_no_hooks_is_falsy_and_returns_nothing():
    reg = HookRegistry()
    assert not reg
    assert reg.before_tool_call("send", {"to": "a@x.com"}) is None
    assert reg.before_llm_call() is None
