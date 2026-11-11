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
    with reg.run("t-1"):
        reg.before_tool_call("send", {"to": "blocked@x.com"})
        events = reg.run_events()
    assert len(seen) == 2 and len(events) == 1
    assert events[0]["hook"] == "before_tool_call" and events[0]["message"] == "not allowed"
    assert events[0]["thread_id"] == "t-1"
    assert reg.run_events() == []  # nothing is kept after the run, nor outside one


@pytest.mark.asyncio
async def test_concurrent_runs_keep_their_own_events_and_notes():
    import asyncio

    class Noter(AgentHooks):
        def after_tool_call(self, tool, args, result):
            return f"note for {args['to']}"

    reg = HookRegistry([Noter()])

    async def one(thread_id):
        with reg.run(thread_id):
            reg.on_user_message(f"hello from {thread_id}", thread_id)
            await asyncio.sleep(0)  # let the other run go in between
            reg.attach_note([], reg.after_tool_call("send", {"to": thread_id}, []))
            await asyncio.sleep(0)
            return reg.run_events(), reg.before_llm_call(thread_id)

    (events_a, carried_a), (events_b, carried_b) = await asyncio.gather(one("t-a"), one("t-b"))
    assert {e["thread_id"] for e in events_a} == {"t-a"} and len(events_a) == 2
    assert {e["thread_id"] for e in events_b} == {"t-b"} and len(events_b) == 2
    assert carried_a == "note for t-a" and carried_b == "note for t-b"


def test_no_hooks_is_falsy_and_returns_nothing():
    reg = HookRegistry()
    assert not reg
    assert reg.before_tool_call("send", {"to": "a@x.com"}) is None
    assert reg.before_llm_call() is None


class Audited(Broken):
    def __init__(self, report_raises=False):
        self.reports, self.report_raises = [], report_raises

    def _report_failure(self, name, args, exc):
        if self.report_raises:
            raise RuntimeError("bug in the audit")
        self.reports.append((name, args, str(exc)))


@pytest.mark.asyncio
async def test_a_failed_hook_is_reported_to_its_audit_and_the_call_goes_on():
    calls.clear()
    audited = Audited()
    (wrapped,) = wrap_tools_with_hooks([send], HookRegistry([audited]))
    assert await wrapped.ainvoke({"to": "ok@x.com"}) == "sent"
    assert calls == ["ok@x.com"]
    assert audited.reports == [("before_tool_call", ("send", {"to": "ok@x.com"}), "bug in a hook")]


@pytest.mark.asyncio
async def test_a_failing_report_is_only_logged():
    calls.clear()
    (wrapped,) = wrap_tools_with_hooks([send], HookRegistry([Audited(report_raises=True), Block()]))
    assert await wrapped.ainvoke({"to": "ok@x.com"}) == "sent"
    with pytest.raises(HookBlocked, match="not allowed"):  # the next hook still runs
        await wrapped.ainvoke({"to": "blocked@x.com"})
    assert calls == ["ok@x.com"]


class BlocksOutsideToolCalls(AgentHooks):
    def before_reply(self, answer):
        raise HookBlocked("not a tool call")

    def on_task_end(self, answer):
        raise HookBlocked("not a tool call")


def test_hook_blocked_from_a_lifecycle_hook_is_logged_and_skipped():
    reg = HookRegistry([BlocksOutsideToolCalls(), Rewrite(" [ok]")])
    assert reg.before_reply("done").answer == "done [ok]"
    reg.on_task_end("done")  # does not raise


@pytest.mark.asyncio
async def test_hook_blocked_raised_by_a_before_tool_call_hook_still_blocks():
    class Raises(AgentHooks):
        def before_tool_call(self, tool, args):
            raise HookBlocked("raised block")

    calls.clear()
    (wrapped,) = wrap_tools_with_hooks([send], HookRegistry([Raises()]))
    with pytest.raises(HookBlocked, match="raised block"):
        await wrapped.ainvoke({"to": "ok@x.com"})
    assert calls == []
