"""Unit coverage for the lazy direct CUGA runner used by ACP stdio."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class _FakeAgent:
    instances: list["_FakeAgent"] = []
    result = SimpleNamespace(answer="answer", error=None)
    snapshot = SimpleNamespace(next=(), values={})

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = 0
        self.initialize_calls = 0
        self.invoke_started = asyncio.Event()
        self.invoke_release = asyncio.Event()
        self.block_invoke = False
        self.graph = SimpleNamespace(get_state=lambda _config: type(self).snapshot)
        type(self).instances.append(self)

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def invoke(self, message: str | None, **kwargs: Any) -> Any:
        self.calls.append({"message": message, **kwargs})
        self.invoke_started.set()
        if self.block_invoke:
            await self.invoke_release.wait()
        if kwargs.get("action_response") is not None:
            type(self).snapshot = SimpleNamespace(next=(), values={})
        return type(self).result

    async def aclose(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    _FakeAgent.instances.clear()
    _FakeAgent.result = SimpleNamespace(answer="answer", error=None)
    _FakeAgent.snapshot = SimpleNamespace(next=(), values={})


@pytest.mark.unit
async def test_runner_constructs_direct_agent_lazily_and_reuses_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cuga.sdk
    from cuga.backend.server.acp.runner import StdioCugaRunner

    monkeypatch.setattr(cuga.sdk, "CugaAgent", _FakeAgent)
    runner = StdioCugaRunner()
    assert _FakeAgent.instances == []

    first = [event async for event in runner.run("one", context_id="ctx")]
    second = [event async for event in runner.run("two", context_id="ctx")]

    assert len(_FakeAgent.instances) == 1
    assert [call["thread_id"] for call in _FakeAgent.instances[0].calls] == ["ctx", "ctx"]
    assert [first[0].name, second[0].name] == ["final_answer", "final_answer"]


@pytest.mark.unit
async def test_runner_initializes_once_before_concurrent_distinct_context_invocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cuga.sdk
    from cuga.backend.server.acp.runner import StdioCugaRunner

    monkeypatch.setattr(cuga.sdk, "CugaAgent", _FakeAgent)
    runner = StdioCugaRunner()
    agent = runner._get_agent()
    agent.block_invoke = True

    async def consume(context_id: str) -> list[Any]:
        return [event async for event in runner.run("work", context_id=context_id)]

    first = asyncio.create_task(consume("ctx-one"))
    await agent.invoke_started.wait()
    second = asyncio.create_task(consume("ctx-two"))
    while len(agent.calls) < 2:
        await asyncio.sleep(0)
    agent.invoke_release.set()
    await asyncio.gather(first, second)

    assert agent.initialize_calls == 1
    assert [call["thread_id"] for call in agent.calls] == ["ctx-one", "ctx-two"]


@pytest.mark.unit
async def test_runner_maps_pending_hitl_and_structured_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    import cuga.sdk
    from cuga.backend.server.acp.runner import StdioCugaRunner

    monkeypatch.setattr(cuga.sdk, "CugaAgent", _FakeAgent)
    _FakeAgent.snapshot = SimpleNamespace(
        next=("WaitForResponse",),
        values={"hitl_action": {"action_id": "action-1", "description": "Approve operation"}},
    )
    runner = StdioCugaRunner()

    events = [event async for event in runner.run("do it", context_id="ctx")]
    assert events[0].name == "input_required"
    assert events[0].data == {"text": "Approve operation", "action_id": "action-1"}

    resumed = [
        event
        async for event in runner.run(
            "do it",
            context_id="ctx",
            approval={"action_id": "action-1", "confirmed": True},
        )
    ]
    call = _FakeAgent.instances[0].calls[-1]
    assert call["message"] is None
    assert call["action_response"].action_id == "action-1"
    assert call["action_response"].confirmed is True
    assert resumed[0].name == "final_answer"


@pytest.mark.unit
@pytest.mark.parametrize("action_id", [None, "", 7, [], {}])
async def test_runner_rejects_malformed_structured_resume_without_invoke(
    monkeypatch: pytest.MonkeyPatch, action_id: Any
) -> None:
    import cuga.sdk
    from cuga.backend.server.acp.runner import StdioCugaRunner

    monkeypatch.setattr(cuga.sdk, "CugaAgent", _FakeAgent)
    _FakeAgent.snapshot = SimpleNamespace(
        next=("WaitForResponse",),
        values={"hitl_action": {"action_id": "action-1", "description": "Approve"}},
    )
    runner = StdioCugaRunner()
    events = [
        event
        async for event in runner.run(
            "do it", context_id="ctx", approval={"action_id": action_id, "confirmed": True}
        )
    ]

    assert events[0].name == "error"
    assert _FakeAgent.instances[0].calls == []


@pytest.mark.unit
async def test_runner_rejects_stale_or_mismatched_structured_resume_without_invoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cuga.sdk
    from cuga.backend.server.acp.runner import StdioCugaRunner

    monkeypatch.setattr(cuga.sdk, "CugaAgent", _FakeAgent)
    _FakeAgent.snapshot = SimpleNamespace(
        next=("WaitForResponse",),
        values={"hitl_action": {"action_id": "current-action", "description": "Approve"}},
    )
    runner = StdioCugaRunner()
    events = [
        event
        async for event in runner.run(
            "do it", context_id="ctx", approval={"action_id": "stale-action", "confirmed": True}
        )
    ]

    assert events[0].name == "error"
    assert _FakeAgent.instances[0].calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "snapshot",
    [
        SimpleNamespace(next=("WaitForResponse",), values={}),
        SimpleNamespace(next=("WaitForResponse",), values={"hitl_action": object()}),
        SimpleNamespace(next=("WaitForResponse",), values={"hitl_action": {"description": "missing id"}}),
        SimpleNamespace(next=("WaitForResponse",), values={"hitl_action": {"action_id": 7}}),
    ],
)
async def test_runner_fails_closed_for_malformed_or_missing_hitl(
    monkeypatch: pytest.MonkeyPatch, snapshot: Any
) -> None:
    import cuga.sdk
    from cuga.backend.server.acp.runner import StdioCugaRunner

    monkeypatch.setattr(cuga.sdk, "CugaAgent", _FakeAgent)
    _FakeAgent.snapshot = snapshot

    events = [event async for event in StdioCugaRunner().run("do it", context_id="ctx")]
    assert len(events) == 1
    assert events[0].name == "error"
    assert events[0].data is None


@pytest.mark.unit
async def test_runner_fails_closed_when_state_inspection_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import cuga.sdk
    from cuga.backend.server.acp.runner import StdioCugaRunner

    class _BrokenAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self.graph = SimpleNamespace(
                get_state=lambda _config: (_ for _ in ()).throw(RuntimeError("secret"))
            )

    monkeypatch.setattr(cuga.sdk, "CugaAgent", _BrokenAgent)
    events = [event async for event in StdioCugaRunner().run("do it", context_id="ctx")]
    assert events[0].name == "error"


@pytest.mark.unit
async def test_runner_shutdown_closes_once_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    import cuga.sdk
    from cuga.backend.server.acp.runner import StdioCugaRunner

    monkeypatch.setattr(cuga.sdk, "CugaAgent", _FakeAgent)
    runner = StdioCugaRunner()
    await anext(runner.run("do it", context_id="ctx"))
    agent = _FakeAgent.instances[0]

    await runner.shutdown()
    await runner.shutdown()

    assert agent.closed == 1
