"""Contract tests for the agent_protocol package.

Covers:
- All valid AgentStreamEvent construction shapes.
- Structural (duck-typing) check that a fake async runner satisfies AgentRunner.
"""

from __future__ import annotations

import asyncio
import inspect
import json as _json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

from cuga.backend.server.agent_protocol import AgentRunner, AgentStreamEvent

pytestmark = pytest.mark.unit


# ── AgentStreamEvent ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_event_name_only() -> None:
    """Minimal event: only name is required."""
    ev = AgentStreamEvent(name="ping")
    assert ev.name == "ping"
    assert ev.data is None
    assert ev.final is False


@pytest.mark.unit
def test_event_with_mapping_data() -> None:
    """data may be a Mapping[str, Any]."""
    ev = AgentStreamEvent(name="final_answer", data={"text": "hello"}, final=True)
    assert ev.name == "final_answer"
    assert ev.data == {"text": "hello"}
    assert ev.final is True


@pytest.mark.unit
def test_event_with_string_data() -> None:
    """data may be a plain str."""
    ev = AgentStreamEvent(name="chunk", data="partial text")
    assert ev.data == "partial text"
    assert ev.final is False


@pytest.mark.unit
def test_event_with_none_data_explicit() -> None:
    """data=None is valid and is the default."""
    ev = AgentStreamEvent(name="heartbeat", data=None)
    assert ev.data is None


@pytest.mark.unit
def test_event_final_default_is_false() -> None:
    """final defaults to False."""
    ev = AgentStreamEvent(name="step")
    assert ev.final is False


@pytest.mark.unit
def test_event_terminal_flag() -> None:
    """final=True marks the terminal event."""
    ev = AgentStreamEvent(name="done", final=True)
    assert ev.final is True


@pytest.mark.unit
def test_event_slots_no_arbitrary_attributes() -> None:
    """AgentStreamEvent uses __slots__; arbitrary attributes must not be allowed."""
    ev = AgentStreamEvent(name="test")
    with pytest.raises(AttributeError):
        ev.unexpected_field = "oops"  # type: ignore[attr-defined]


# ── AgentRunner structural check ──────────────────────────────────────────────


class _FakeRunner:
    """Minimal fake that structurally matches AgentRunner."""

    async def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        yield AgentStreamEvent(name="final_answer", data={"text": message}, final=True)


@pytest.mark.unit
def test_fake_runner_satisfies_agent_runner_protocol() -> None:
    """A class with the correct run() signature is usable as AgentRunner.

    No @runtime_checkable is used in this project, so we verify structural
    compatibility by annotating the fake as AgentRunner and confirming the
    method exists with the expected signature.
    """
    runner: AgentRunner = _FakeRunner()  # type: ignore[assignment]
    assert callable(runner.run)


@pytest.mark.unit
async def test_fake_runner_yields_agent_stream_event() -> None:
    """The fake runner must yield AgentStreamEvent instances."""
    runner = _FakeRunner()
    events = []
    async for ev in runner.run("hello"):
        events.append(ev)
    assert len(events) == 1
    assert isinstance(events[0], AgentStreamEvent)
    assert events[0].name == "final_answer"
    assert events[0].final is True


@pytest.mark.unit
async def test_fake_runner_passes_context_id() -> None:
    """context_id is accepted; here the fake ignores it (valid runner behaviour)."""
    runner = _FakeRunner()
    events = []
    async for ev in runner.run("msg", context_id="ctx-123"):
        events.append(ev)
    assert events[0].data == {"text": "msg"}


@pytest.mark.unit
async def test_fake_runner_passes_approval() -> None:
    """approval kwarg is accepted without error."""
    runner = _FakeRunner()
    events = []
    async for ev in runner.run("q", approval={"action_id": "x", "confirmed": True}):
        events.append(ev)
    assert events[0].final is True


@pytest.mark.unit
def test_agent_runner_run_signature_is_stable() -> None:
    """The shared runner interface must not grow transport-specific parameters."""
    assert list(inspect.signature(AgentRunner.run).parameters) == [
        "self",
        "message",
        "context_id",
        "approval",
    ]


# ── __init__ re-exports ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_package_exports_agent_stream_event() -> None:
    """AgentStreamEvent must be importable from the package root."""
    from cuga.backend.server.agent_protocol import AgentStreamEvent as ASE

    assert ASE is AgentStreamEvent


@pytest.mark.unit
def test_package_exports_agent_runner() -> None:
    """AgentRunner must be importable from the package root."""
    from cuga.backend.server.agent_protocol import AgentRunner as AR

    assert AR is AgentRunner


# ── SimpleAgentRunner caller_user_id ──────────────────────────────────────────


class _Snap:
    def __init__(self, next_, values):
        self.next = next_
        self.values = values


class _Graph:
    def __init__(self, snaps):
        self._snaps = snaps
        self.calls = 0

    def get_state(self, _config):
        snap = self._snaps[min(self.calls, len(self._snaps) - 1)]
        self.calls += 1
        return snap


class _Agent:
    def __init__(self, graph):
        self.graph = graph


class _AppState:
    def __init__(self, graph):
        self.agent = _Agent(graph)
        self.output_format = None


def _answer_frame(text: str) -> bytes:
    payload = _json.dumps({"data": text, "variables": {}, "active_policies": []})
    return f"event: Answer\ndata: {payload}\n\n".encode()


def _pending(action_id: str = "approval") -> dict[str, Any]:
    return {
        "action_id": action_id,
        "type": "confirmation",
        "description": "Approve operation?",
    }


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_passes_configured_caller_id() -> None:
    """SimpleAgentRunner should pass its configured caller_user_id to event_stream."""
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    captured_user_ids: list[str] = []
    graph = _Graph([_Snap((), {})])

    async def event_stream(**kwargs):
        captured_user_ids.append(kwargs.get("user_id", ""))
        yield _answer_frame("response")

    runner = SimpleAgentRunner(
        _AppState(graph),
        event_stream,
        auto_approve=False,
        caller_user_id="custom_caller",
    )
    events = [ev async for ev in runner.run("hello", "ctx-1")]

    assert events[-1].name == "final_answer"
    assert captured_user_ids == ["custom_caller"]


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_default_caller_id() -> None:
    """SimpleAgentRunner defaults to 'agent_protocol_user' when no caller_user_id given."""
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    captured_user_ids: list[str] = []
    graph = _Graph([_Snap((), {})])

    async def event_stream(**kwargs):
        captured_user_ids.append(kwargs.get("user_id", ""))
        yield _answer_frame("response")

    runner = SimpleAgentRunner(_AppState(graph), event_stream)
    events = [ev async for ev in runner.run("hello", "ctx-2")]

    assert events[-1].name == "final_answer"
    assert captured_user_ids == ["agent_protocol_user"]


@pytest.mark.anyio
@pytest.mark.unit
async def test_a2a_wrapper_passes_a2a_user() -> None:
    """SimpleA2ARunner compatibility wrapper must always pass caller_user_id='a2a_user'."""
    from cuga.backend.server.a2a.simple_runner import SimpleA2ARunner

    captured_user_ids: list[str] = []
    graph = _Graph([_Snap((), {})])

    async def event_stream(**kwargs):
        captured_user_ids.append(kwargs.get("user_id", ""))
        yield _answer_frame("a2a response")

    runner = SimpleA2ARunner(_AppState(graph), event_stream)
    events = [ev async for ev in runner.run("hello", "ctx-3")]

    assert events[-1].name == "final_answer"
    assert captured_user_ids == ["a2a_user"]


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_generates_context_and_preserves_event_order() -> None:
    """An omitted context is generated once and progress precedes the terminal event."""
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    captured_thread_ids: list[str] = []

    async def event_stream(**kwargs):
        captured_thread_ids.append(kwargs["thread_id"])
        yield b"event: AgentThinking\ndata: working\n\n"
        yield _answer_frame("done")

    runner = SimpleAgentRunner(_AppState(_Graph([_Snap((), {})])), event_stream)
    events = [event async for event in runner.run("hello")]

    assert captured_thread_ids == [captured_thread_ids[0]]
    assert captured_thread_ids[0]
    assert [(event.name, event.data, event.final) for event in events] == [
        ("AgentThinking", {"text": "working"}, False),
        ("final_answer", {"text": "done"}, True),
    ]


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_converts_normal_exception_to_sanitized_error() -> None:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    async def event_stream(**kwargs):
        raise RuntimeError("secret detail")
        yield  # pragma: no cover

    runner = SimpleAgentRunner(_AppState(_Graph([_Snap((), {})])), event_stream)
    events = [event async for event in runner.run("hello", "ctx")]

    assert [(event.name, event.data, event.final) for event in events] == [
        ("error", {"text": "Agent error: RuntimeError"}, True)
    ]
    assert "secret detail" not in str(events[0].data)


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_propagates_cancellation_when_nested_close_fails() -> None:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    entered = asyncio.Event()
    closed = asyncio.Event()
    events = []

    class BlockingFailingCloseStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            entered.set()
            await asyncio.Future()

        async def aclose(self):
            closed.set()
            raise RuntimeError("unsafe close detail")

    runner = SimpleAgentRunner(
        _AppState(_Graph([_Snap((), {})])),
        lambda **kwargs: BlockingFailingCloseStream(),
    )

    async def consume() -> None:
        async for event in runner.run("hello", "ctx"):
            events.append(event)

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert closed.is_set()
    assert events == []


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_does_not_append_error_when_terminal_stream_close_fails() -> None:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    class TerminalFailingCloseStream:
        def __init__(self):
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return _answer_frame("safe answer")

        async def aclose(self):
            raise RuntimeError("unsafe close detail")

    runner = SimpleAgentRunner(
        _AppState(_Graph([_Snap((), {})])),
        lambda **kwargs: TerminalFailingCloseStream(),
    )
    events = [event async for event in runner.run("hello", "ctx")]

    assert events == [AgentStreamEvent("final_answer", {"text": "safe answer"}, final=True)]
    assert "unsafe close detail" not in str(events)


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_early_close_ignores_nested_close_failure() -> None:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    class ProgressFailingCloseStream:
        def __init__(self):
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                await asyncio.Future()
            self._sent = True
            return b"event: AgentThinking\ndata: first\n\n"

        async def aclose(self):
            raise RuntimeError("unsafe close detail")

    runner = SimpleAgentRunner(
        _AppState(_Graph([_Snap((), {})])),
        lambda **kwargs: ProgressFailingCloseStream(),
    )
    stream = runner.run("hello", "ctx")

    assert (await anext(stream)).data == {"text": "first"}
    await stream.aclose()


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_closes_nested_stream_when_consumer_stops() -> None:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    closed = asyncio.Event()

    async def event_stream(**kwargs):
        try:
            yield b"event: AgentThinking\ndata: first\n\n"
            yield b"event: AgentThinking\ndata: second\n\n"
        finally:
            closed.set()

    runner = SimpleAgentRunner(_AppState(_Graph([_Snap((), {})])), event_stream)
    stream = runner.run("hello", "ctx")
    assert (await anext(stream)).data == {"text": "first"}
    await stream.aclose()
    assert closed.is_set()


@pytest.mark.anyio
@pytest.mark.unit
@pytest.mark.parametrize(("reply", "confirmed"), [("approve", True), ("deny", False)])
async def test_simple_agent_runner_resumes_parked_hitl_decision(reply: str, confirmed: bool) -> None:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    resumes = []

    async def event_stream(**kwargs):
        resumes.append(kwargs["resume"])
        yield _answer_frame("resumed")

    graph = _Graph([_Snap(("WaitForResponse",), {"hitl_action": _pending()})])
    runner = SimpleAgentRunner(_AppState(graph), event_stream)
    events = [event async for event in runner.run(reply, "ctx")]

    assert events[-1].name == "final_answer"
    assert len(resumes) == 1
    assert resumes[0].confirmed is confirmed


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_reasks_on_ambiguous_hitl_reply() -> None:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    called = False

    async def event_stream(**kwargs):
        nonlocal called
        called = True
        yield _answer_frame("unexpected")

    graph = _Graph([_Snap(("WaitForResponse",), {"hitl_action": _pending()})])
    runner = SimpleAgentRunner(_AppState(graph), event_stream)
    events = [event async for event in runner.run("maybe", "ctx")]

    assert called is False
    assert [(event.name, event.final) for event in events] == [("input_required", True)]


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_bounds_automatic_hitl_resumes() -> None:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    calls = 0
    pending = _pending()

    async def event_stream(**kwargs):
        nonlocal calls
        calls += 1
        yield b"event: AgentThinking\ndata: retrying\n\n"

    graph = _Graph(
        [_Snap((), {})] + [_Snap(("WaitForResponse",), {"hitl_action": pending}) for _ in range(20)]
    )
    runner = SimpleAgentRunner(_AppState(graph), event_stream, auto_approve=True)
    events = [event async for event in runner.run("hello", "ctx")]

    assert calls == 13
    assert events[-1] == AgentStreamEvent("final_answer", {"text": "Agent completed processing"}, final=True)


@pytest.mark.unit
def test_neutral_package_dir_exposes_lazy_exports_without_loading_them() -> None:
    """Directory introspection advertises lazy runners without importing heavy modules."""
    script = """
import sys
import cuga.backend.server.agent_protocol as package
assert {'AgentRunner', 'AgentStreamEvent', 'SimpleAgentRunner', 'SupervisorAgentRunner'} <= set(dir(package))
assert 'SimpleAgentRunner' not in package.__dict__
assert 'SupervisorAgentRunner' not in package.__dict__
for prefix in ('acp', 'cuga.sdk', 'cuga.backend.cuga_graph'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.unit
def test_neutral_package_import_is_acp_free_and_lightweight() -> None:
    """Importing neutral contracts must not load ACP, graph, or SDK modules."""
    script = """
import sys
import cuga.backend.server.agent_protocol as package
assert package.AgentRunner
assert package.AgentStreamEvent
for prefix in ('acp', 'cuga.sdk', 'cuga.backend.cuga_graph'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.unit
def test_package_exports_simple_agent_runner() -> None:
    """SimpleAgentRunner must be importable from the package root."""
    from cuga.backend.server.agent_protocol import SimpleAgentRunner as SAR

    assert SAR.__name__ == "SimpleAgentRunner"


@pytest.mark.unit
def test_package_exports_supervisor_agent_runner() -> None:
    """SupervisorAgentRunner must be importable from the package root."""
    from cuga.backend.server.agent_protocol import SupervisorAgentRunner as SUAR

    assert SUAR.__name__ == "SupervisorAgentRunner"


@pytest.mark.unit
def test_a2a_stream_event_is_agent_stream_event_alias() -> None:
    """A2AStreamEvent in a2a/runner.py must be the same object as AgentStreamEvent."""
    from cuga.backend.server.a2a.runner import A2AStreamEvent
    from cuga.backend.server.agent_protocol import AgentStreamEvent

    assert A2AStreamEvent is AgentStreamEvent


@pytest.mark.anyio
@pytest.mark.unit
async def test_supervisor_runner_is_lazy_forwards_context_and_uses_configured_cache(monkeypatch) -> None:
    from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner

    invoke_calls = []

    class FakeSupervisor:
        async def invoke(self, message, thread_id=None):
            invoke_calls.append((message, thread_id))
            return SimpleNamespace(answer="answer", error=None)

    fake = FakeSupervisor()
    from_yaml_calls = []

    async def from_yaml(path):
        from_yaml_calls.append(path)
        return fake

    import cuga.sdk

    monkeypatch.setattr(cuga.sdk.CugaSupervisor, "from_yaml", from_yaml)
    state = SimpleNamespace()
    runner = SupervisorAgentRunner(state, "supervisor.yaml", cache_attr="isolated_cache")

    assert not hasattr(state, "isolated_cache")
    first = [event async for event in runner.run("one", "ctx-1")]
    second = [event async for event in runner.run("two", "ctx-2")]

    assert from_yaml_calls == ["supervisor.yaml"]
    assert state.isolated_cache is fake
    assert invoke_calls == [("one", "ctx-1"), ("two", "ctx-2")]
    assert [first[-1].data, second[-1].data] == [{"text": "answer"}, {"text": "answer"}]


@pytest.mark.anyio
@pytest.mark.unit
async def test_supervisor_runner_propagates_task_cancellation_without_error_event() -> None:
    from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner

    entered = asyncio.Event()
    events = []

    class BlockingSupervisor:
        async def invoke(self, message, thread_id=None):
            entered.set()
            await asyncio.Future()

    state = SimpleNamespace(supervisor=BlockingSupervisor())
    runner = SupervisorAgentRunner(state, "unused.yaml", cache_attr="supervisor")

    async def consume() -> None:
        async for event in runner.run("hello", "ctx"):
            events.append(event)

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == []


@pytest.mark.anyio
@pytest.mark.unit
async def test_supervisor_runner_converts_normal_exception_to_sanitized_error() -> None:
    from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner

    class FailingSupervisor:
        async def invoke(self, message, thread_id=None):
            raise RuntimeError("secret detail")

    state = SimpleNamespace(supervisor=FailingSupervisor())
    runner = SupervisorAgentRunner(state, "unused.yaml", protocol_name="test", cache_attr="supervisor")
    events = [event async for event in runner.run("hello", "ctx")]

    assert [(event.name, event.data, event.final) for event in events] == [
        ("error", {"text": "test handler error: RuntimeError"}, True)
    ]
    assert "secret detail" not in str(events[0].data)


@pytest.mark.anyio
@pytest.mark.unit
async def test_a2a_supervisor_wrapper_propagates_task_cancellation_without_error_event() -> None:
    from cuga.backend.server.a2a.runner import SupervisorA2ARunner

    entered = asyncio.Event()
    events = []

    class BlockingSupervisor:
        async def invoke(self, message, thread_id=None):
            entered.set()
            await asyncio.Future()

    state = SimpleNamespace(a2a_supervisor=BlockingSupervisor())
    runner = SupervisorA2ARunner(state, "unused.yaml")

    assert runner._delegate._cache_attr == "a2a_supervisor"
    assert runner._delegate._protocol_name == "A2A"

    async def consume() -> None:
        async for event in runner.run("hello", "ctx"):
            events.append(event)

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == []
