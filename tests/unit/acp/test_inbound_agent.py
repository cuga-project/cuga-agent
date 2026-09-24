"""Unit coverage for the inbound ACP v1 CUGA agent adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError, dataclass
from typing import Any

import pytest
from acp import PROTOCOL_VERSION, RequestError
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    ImageContentBlock,
    RequestPermissionResponse,
    TextContentBlock,
)

from cuga.backend.server.agent_protocol.events import AgentStreamEvent

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@dataclass
class _RunCall:
    message: str
    context_id: str | None
    approval: dict[str, Any] | None


class _ScriptedRunner:
    def __init__(self, scripts: list[list[AgentStreamEvent]] | None = None) -> None:
        self.scripts = list(scripts or [])
        self.calls: list[_RunCall] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.cancelled = 0

    async def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        self.calls.append(_RunCall(message, context_id, approval))
        self.started.set()
        try:
            if self.block:
                await self.release.wait()
            script = self.scripts.pop(0) if self.scripts else []
            for event in script:
                yield event
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class _ExplodingRunner:
    async def run(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[AgentStreamEvent]:
        raise RuntimeError("secret runner failure")
        yield  # pragma: no cover


class _FakeClient:
    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.updates: list[tuple[str, Any]] = []
        self.permission_requests: list[tuple[str, Any, list[Any]]] = []
        self.outcomes = list(outcomes or [])
        self.permission_started = asyncio.Event()
        self.permission_release = asyncio.Event()
        self.block_permission = False

    async def session_update(self, session_id: str, update: Any, **_kwargs: Any) -> None:
        self.updates.append((session_id, update))

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[Any],
        **_kwargs: Any,
    ) -> RequestPermissionResponse:
        self.permission_requests.append((session_id, tool_call, options))
        self.permission_started.set()
        if self.block_permission:
            await self.permission_release.wait()
        return self.outcomes.pop(0)


class _BlockingUpdateClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.update_started = asyncio.Event()
        self.update_release = asyncio.Event()
        self.update_cancelled = 0

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.update_started.set()
        try:
            await self.update_release.wait()
        except asyncio.CancelledError:
            self.update_cancelled += 1
            raise
        await super().session_update(session_id, update, **kwargs)


def _text(value: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=value)


async def _new_agent(runner: Any, client: _FakeClient | None = None):
    from cuga.backend.server.acp.agent import CugaAcpAgent

    agent = CugaAcpAgent(runner)
    peer = client or _FakeClient()
    agent.on_connect(peer)
    session = await agent.new_session(
        cwd="/workspace", additional_directories=["/workspace/extra"], mcp_servers=[]
    )
    return agent, peer, session.session_id


@pytest.mark.unit
async def test_initialize_supports_only_stable_v1_with_truthful_capabilities() -> None:
    from cuga.backend.server.acp.agent import CugaAcpAgent

    agent = CugaAcpAgent(_ScriptedRunner())
    response = await agent.initialize(protocol_version=PROTOCOL_VERSION)

    assert response.protocol_version == 1
    assert response.agent_info.name == "cuga"
    assert response.agent_info.title == "CUGA"
    assert response.agent_capabilities.load_session is False
    assert response.agent_capabilities.prompt_capabilities.image is False
    assert response.agent_capabilities.prompt_capabilities.audio is False
    assert response.agent_capabilities.prompt_capabilities.embedded_context is False
    assert response.agent_capabilities.mcp_capabilities.http is False
    assert response.agent_capabilities.mcp_capabilities.sse is False
    assert response.agent_capabilities.mcp_capabilities.acp is False
    assert response.agent_capabilities.session_capabilities.list is None
    assert response.agent_capabilities.session_capabilities.additional_directories is None
    assert response.auth_methods == []

    with pytest.raises(RequestError) as exc_info:
        await agent.initialize(protocol_version=2)
    assert exc_info.value.code == -32602
    assert exc_info.value.to_error_obj()["data"] is None


@pytest.mark.unit
async def test_new_sessions_are_unique_stable_and_isolated_metadata() -> None:
    runner = _ScriptedRunner(
        [
            [AgentStreamEvent("final_answer", {"text": "one"}, final=True)],
            [AgentStreamEvent("final_answer", {"text": "two"}, final=True)],
            [AgentStreamEvent("final_answer", {"text": "again"}, final=True)],
        ]
    )
    from cuga.backend.server.acp.agent import CugaAcpAgent

    agent = CugaAcpAgent(runner)
    peer = _FakeClient()
    agent.on_connect(peer)
    first = await agent.new_session(cwd="/workspace/one", additional_directories=["/extra"], mcp_servers=[])
    second = await agent.new_session(cwd="/workspace/two", mcp_servers=[])

    assert first.session_id != second.session_id
    first_record = agent.session_metadata(first.session_id)
    assert first_record.cwd == "/workspace/one"
    assert first_record.additional_directories == ("/extra",)
    assert first_record.context_id != agent.session_metadata(second.session_id).context_id
    assert set(first_record.__dataclass_fields__) == {
        "session_id",
        "context_id",
        "cwd",
        "additional_directories",
    }
    with pytest.raises(FrozenInstanceError):
        first_record.cwd = "/mutated"

    await agent.prompt(first.session_id, [_text("first")])
    await agent.prompt(second.session_id, [_text("second")])
    await agent.prompt(first.session_id, [_text("third")])

    assert runner.calls[0].context_id == runner.calls[2].context_id
    assert runner.calls[0].context_id != runner.calls[1].context_id


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cwd", "additional"),
    [("relative", None), ("/workspace", ["relative"]), ("", None)],
)
async def test_new_session_rejects_unsafe_directory_metadata(cwd: str, additional: list[str] | None) -> None:
    from cuga.backend.server.acp.agent import CugaAcpAgent

    with pytest.raises(RequestError) as exc_info:
        await CugaAcpAgent(_ScriptedRunner()).new_session(
            cwd=cwd,
            additional_directories=additional,
            mcp_servers=[],
        )
    assert exc_info.value.code == -32602
    if cwd:
        assert cwd not in repr(exc_info.value.to_error_obj())


@pytest.mark.unit
async def test_prompt_joins_ordered_text_blocks_and_emits_final_once() -> None:
    runner = _ScriptedRunner(
        [
            [
                AgentStreamEvent("final_answer", {"text": "answer"}, final=True),
                AgentStreamEvent("final_answer", {"text": "late"}, final=True),
            ]
        ]
    )
    agent, client, session_id = await _new_agent(runner)

    response = await agent.prompt(session_id, [_text("first"), _text("second")])

    assert response.stop_reason == "end_turn"
    assert runner.calls[0].message == "first\n\nsecond"
    assert [update.content.text for _, update in client.updates] == ["answer"]


@pytest.mark.unit
@pytest.mark.parametrize("prompt", [[], [_text("")], [_text(" \n ")]])
async def test_prompt_rejects_empty_effective_input(prompt: list[Any]) -> None:
    agent, _client, session_id = await _new_agent(_ScriptedRunner())

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(session_id, prompt)
    assert exc_info.value.code == -32602


@pytest.mark.unit
async def test_prompt_rejects_rich_content_without_reflecting_payload() -> None:
    agent, _client, session_id = await _new_agent(_ScriptedRunner())
    secret = "secret-binary-payload"
    image = ImageContentBlock(type="image", data=secret, mimeType="image/png")

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(session_id, [image])

    assert exc_info.value.code == -32602
    assert secret not in repr(exc_info.value.to_error_obj())


@pytest.mark.unit
async def test_prompt_filters_reasoning_and_only_streams_intended_output() -> None:
    runner = _ScriptedRunner(
        [
            [
                AgentStreamEvent("thought", {"text": "private reasoning"}),
                AgentStreamEvent("tool_call", {"text": "unsafe internals"}),
                AgentStreamEvent("agent_message", {"text": "safe progress"}),
                AgentStreamEvent("final_answer", {"text": "safe final"}, final=True),
            ]
        ]
    )
    agent, client, session_id = await _new_agent(runner)

    await agent.prompt(session_id, [_text("hello")])

    assert [update.content.text for _, update in client.updates] == ["safe progress", "safe final"]


@pytest.mark.unit
async def test_repeated_progress_and_equal_final_are_distinct_events() -> None:
    runner = _ScriptedRunner(
        [
            [
                AgentStreamEvent("agent_message", {"text": "same answer"}),
                AgentStreamEvent("agent_message", {"text": "same answer"}),
                AgentStreamEvent("final_answer", {"text": "same answer"}, final=True),
            ]
        ]
    )
    agent, client, session_id = await _new_agent(runner)

    await agent.prompt(session_id, [_text("hello")])

    assert [update.content.text for _, update in client.updates] == [
        "same answer",
        "same answer",
        "same answer",
    ]


@pytest.mark.unit
async def test_empty_stream_has_deterministic_fallback() -> None:
    agent, client, session_id = await _new_agent(_ScriptedRunner([[]]))

    response = await agent.prompt(session_id, [_text("hello")])

    assert response.stop_reason == "end_turn"
    assert [update.content.text for _, update in client.updates] == ["Agent completed without a response."]


@pytest.mark.unit
async def test_empty_terminal_answer_has_deterministic_fallback() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("final_answer", {"text": ""}, final=True)]])
    agent, client, session_id = await _new_agent(runner)

    response = await agent.prompt(session_id, [_text("hello")])

    assert response.stop_reason == "end_turn"
    assert [update.content.text for _, update in client.updates] == ["Agent completed without a response."]


@pytest.mark.unit
async def test_runner_error_is_safe_and_raw_exception_is_not_reflected() -> None:
    agent, _client, session_id = await _new_agent(_ExplodingRunner())

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(session_id, [_text("secret prompt")])

    assert exc_info.value.code == -32603
    rendered = repr(exc_info.value.to_error_obj())
    assert "secret" not in rendered
    assert "runner failure" not in rendered


@pytest.mark.unit
async def test_neutral_error_event_is_safe_and_not_streamed() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("error", {"text": "secret backend error"}, final=True)]])
    agent, client, session_id = await _new_agent(runner)

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(session_id, [_text("hello")])

    assert exc_info.value.code == -32603
    assert client.updates == []
    assert "secret backend error" not in repr(exc_info.value.data)


@pytest.mark.unit
async def test_unknown_session_prompt_fails_safely_and_cancel_is_noop() -> None:
    from cuga.backend.server.acp.agent import CugaAcpAgent

    agent = CugaAcpAgent(_ScriptedRunner())
    with pytest.raises(RequestError) as exc_info:
        await agent.prompt("missing-secret-session", [_text("hello")])
    assert exc_info.value.code == -32002
    assert "missing-secret-session" not in repr(exc_info.value.to_error_obj())
    await agent.cancel("missing-secret-session")


@pytest.mark.unit
async def test_same_session_concurrent_prompt_is_rejected() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("final_answer", {"text": "done"}, final=True)]])
    runner.block = True
    agent, _client, session_id = await _new_agent(runner)
    first = asyncio.create_task(agent.prompt(session_id, [_text("first")]))
    await runner.started.wait()

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(session_id, [_text("second")])
    assert exc_info.value.code == -32600

    runner.release.set()
    assert (await first).stop_reason == "end_turn"


@pytest.mark.unit
async def test_different_sessions_run_independently() -> None:
    runner = _ScriptedRunner(
        [
            [AgentStreamEvent("final_answer", {"text": "one"}, final=True)],
            [AgentStreamEvent("final_answer", {"text": "two"}, final=True)],
        ]
    )
    runner.block = True
    from cuga.backend.server.acp.agent import CugaAcpAgent

    agent = CugaAcpAgent(runner)
    client = _FakeClient()
    agent.on_connect(client)
    first_id = (await agent.new_session(cwd="/one", mcp_servers=[])).session_id
    second_id = (await agent.new_session(cwd="/two", mcp_servers=[])).session_id

    first = asyncio.create_task(agent.prompt(first_id, [_text("first")]))
    await runner.started.wait()
    second = asyncio.create_task(agent.prompt(second_id, [_text("second")]))
    await asyncio.sleep(0)
    assert len(runner.calls) == 2
    runner.release.set()
    await asyncio.gather(first, second)


@pytest.mark.unit
async def test_cancel_before_prompt_registration_is_not_lost() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("final_answer", {"text": "must not run"}, final=True)]])
    agent, client, session_id = await _new_agent(runner)
    record = agent._sessions[session_id]
    await record.lock.acquire()
    prompt_task = asyncio.create_task(agent.prompt(session_id, [_text("hello")]))
    while not record.registering_tasks:
        await asyncio.sleep(0)
    cancel_task = asyncio.create_task(agent.cancel(session_id))
    await asyncio.sleep(0)
    record.lock.release()

    await cancel_task

    assert (await prompt_task).stop_reason == "cancelled"
    assert len(runner.calls) <= 1
    assert client.updates == []


@pytest.mark.unit
async def test_cancel_during_later_turn_registration_is_not_lost() -> None:
    runner = _ScriptedRunner(
        [
            [AgentStreamEvent("final_answer", {"text": "first"}, final=True)],
            [AgentStreamEvent("final_answer", {"text": "must not run"}, final=True)],
        ]
    )
    agent, client, session_id = await _new_agent(runner)
    assert (await agent.prompt(session_id, [_text("one")])).stop_reason == "end_turn"
    record = agent._sessions[session_id]
    await record.lock.acquire()
    second = asyncio.create_task(agent.prompt(session_id, [_text("two")]))
    while not record.registering_tasks:
        await asyncio.sleep(0)
    cancel_task = asyncio.create_task(agent.cancel(session_id))
    await asyncio.sleep(0)
    record.lock.release()

    await cancel_task
    assert (await second).stop_reason == "cancelled"
    assert len(runner.calls) <= 2
    assert [update.content.text for _, update in client.updates] == ["first"]


@pytest.mark.unit
async def test_duplicate_or_late_cancel_does_not_poison_next_turn() -> None:
    runner = _ScriptedRunner(
        [
            [AgentStreamEvent("final_answer", {"text": "first"}, final=True)],
            [AgentStreamEvent("final_answer", {"text": "second"}, final=True)],
        ]
    )
    agent, client, session_id = await _new_agent(runner)

    assert (await agent.prompt(session_id, [_text("one")])).stop_reason == "end_turn"
    await agent.cancel(session_id)
    await agent.cancel(session_id)
    assert (await agent.prompt(session_id, [_text("two")])).stop_reason == "end_turn"

    assert [update.content.text for _, update in client.updates] == ["first", "second"]


@pytest.mark.unit
async def test_terminal_safe_event_is_delivered_once_without_fallback() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("message", {"text": "terminal"}, final=True)]])
    agent, client, session_id = await _new_agent(runner)

    assert (await agent.prompt(session_id, [_text("hello")])).stop_reason == "end_turn"
    assert [update.content.text for _, update in client.updates] == ["terminal"]


@pytest.mark.unit
async def test_cancel_is_idempotent_propagates_and_suppresses_late_updates() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("final_answer", {"text": "too late"}, final=True)]])
    runner.block = True
    agent, client, session_id = await _new_agent(runner)
    prompt_task = asyncio.create_task(agent.prompt(session_id, [_text("hello")]))
    await runner.started.wait()

    await agent.cancel(session_id)
    await agent.cancel(session_id)

    assert (await prompt_task).stop_reason == "cancelled"
    assert runner.cancelled == 1
    assert client.updates == []


@pytest.mark.unit
async def test_cancel_during_final_delivery_suppresses_output_and_cleans_task() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("final_answer", {"text": "done"}, final=True)]])
    client = _BlockingUpdateClient()
    agent, _client, session_id = await _new_agent(runner, client)
    prompt_task = asyncio.create_task(agent.prompt(session_id, [_text("hello")]))
    await client.update_started.wait()

    await agent.cancel(session_id)

    assert (await prompt_task).stop_reason == "cancelled"
    assert client.update_cancelled == 1
    assert client.updates == []
    assert agent._sessions[session_id].active_task is None


@pytest.mark.unit
async def test_shutdown_during_final_delivery_suppresses_output_and_cleans_task() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("final_answer", {"text": "done"}, final=True)]])
    client = _BlockingUpdateClient()
    agent, _client, session_id = await _new_agent(runner, client)
    prompt_task = asyncio.create_task(agent.prompt(session_id, [_text("hello")]))
    await client.update_started.wait()

    await agent.shutdown()

    assert (await prompt_task).stop_reason == "cancelled"
    assert client.update_cancelled == 1
    assert client.updates == []
    assert agent._sessions[session_id].active_task is None


@pytest.mark.unit
async def test_external_prompt_task_cancellation_is_preserved() -> None:
    runner = _ScriptedRunner()
    runner.block = True
    agent, _client, session_id = await _new_agent(runner)
    prompt_task = asyncio.create_task(agent.prompt(session_id, [_text("hello")]))
    await runner.started.wait()

    prompt_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prompt_task
    assert runner.cancelled == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "confirmed"),
    [
        (AllowedOutcome(outcome="selected", optionId="allow-once"), True),
        (AllowedOutcome(outcome="selected", optionId="reject-once"), False),
        (DeniedOutcome(outcome="cancelled"), False),
        (AllowedOutcome(outcome="selected", optionId="unknown"), False),
    ],
)
async def test_hitl_permission_outcomes_resume_same_context(outcome: Any, confirmed: bool) -> None:
    runner = _ScriptedRunner(
        [
            [
                AgentStreamEvent(
                    "input_required", {"text": "Run safe operation?", "action_id": "action-1"}, final=True
                )
            ],
            [AgentStreamEvent("final_answer", {"text": "finished"}, final=True)],
        ]
    )
    client = _FakeClient([RequestPermissionResponse(outcome=outcome)])
    agent, client, session_id = await _new_agent(runner, client)

    response = await agent.prompt(session_id, [_text("do it")])

    assert response.stop_reason == "end_turn"
    assert len(client.permission_requests) == 1
    request_session, tool_call, options = client.permission_requests[0]
    assert request_session == session_id
    assert tool_call.title == "Run safe operation?"
    assert [(option.option_id, option.kind) for option in options] == [
        ("allow-once", "allow_once"),
        ("reject-once", "reject_once"),
    ]
    assert runner.calls[1].context_id == runner.calls[0].context_id
    assert runner.calls[1].approval == {"action_id": "action-1", "confirmed": confirmed}
    assert [update.content.text for _, update in client.updates] == ["finished"]


@pytest.mark.unit
@pytest.mark.parametrize("action_id", [None, "", 7, [], {}])
async def test_malformed_hitl_action_id_fails_before_permission_and_never_resumes(action_id: Any) -> None:
    runner = _ScriptedRunner(
        [[AgentStreamEvent("input_required", {"text": "Continue?", "action_id": action_id}, final=True)]]
    )
    agent, client, session_id = await _new_agent(runner)

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(session_id, [_text("hello")])

    assert exc_info.value.code == -32603
    assert client.permission_requests == []
    assert len(runner.calls) == 1


@pytest.mark.unit
async def test_stale_pending_hitl_action_fails_closed_without_resume() -> None:
    runner = _ScriptedRunner(
        [[AgentStreamEvent("input_required", {"text": "Continue?", "action_id": "action-1"}, final=True)]]
    )
    client = _FakeClient(
        [RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", optionId="allow-once"))]
    )
    client.block_permission = True
    agent, _client, session_id = await _new_agent(runner, client)
    prompt_task = asyncio.create_task(agent.prompt(session_id, [_text("hello")]))
    await client.permission_started.wait()
    record = agent._sessions[session_id]
    async with record.lock:
        record.pending_action_id = "different-action"
    client.permission_release.set()

    with pytest.raises(RequestError) as exc_info:
        await prompt_task
    assert exc_info.value.code == -32603
    assert len(runner.calls) == 1


@pytest.mark.unit
async def test_repeated_hitl_is_bounded_and_fails_closed() -> None:
    from cuga.backend.server.acp.agent import MAX_PERMISSION_RESUMES

    hitl = AgentStreamEvent("input_required", {"text": "Continue?", "action_id": "repeat"}, final=True)
    runner = _ScriptedRunner([[hitl] for _ in range(MAX_PERMISSION_RESUMES + 1)])
    outcomes = [
        RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", optionId="allow-once"))
        for _ in range(MAX_PERMISSION_RESUMES)
    ]
    agent, client, session_id = await _new_agent(runner, _FakeClient(outcomes))

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(session_id, [_text("loop")])

    assert exc_info.value.code == -32603
    assert len(client.permission_requests) == MAX_PERMISSION_RESUMES


@pytest.mark.unit
async def test_cancel_while_permission_pending_fails_closed_and_suppresses_resume() -> None:
    runner = _ScriptedRunner(
        [[AgentStreamEvent("input_required", {"text": "Continue?", "action_id": "action-1"}, final=True)]]
    )
    client = _FakeClient(
        [RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", optionId="allow-once"))]
    )
    client.block_permission = True
    agent, _client, session_id = await _new_agent(runner, client)
    prompt_task = asyncio.create_task(agent.prompt(session_id, [_text("hello")]))
    await client.permission_started.wait()

    await agent.cancel(session_id)
    client.permission_release.set()

    assert (await prompt_task).stop_reason == "cancelled"
    assert len(runner.calls) == 1


@pytest.mark.unit
async def test_unsupported_methods_and_extensions_use_method_not_found() -> None:
    agent, _client, session_id = await _new_agent(_ScriptedRunner())

    calls = [
        agent.load_session(cwd="/workspace", session_id=session_id, mcp_servers=[]),
        agent.set_session_mode(session_id=session_id, mode_id="unsafe"),
        agent.authenticate(method_id="none"),
        agent.ext_method("secret-extension", {"secret": "payload"}),
    ]
    for call in calls:
        with pytest.raises(RequestError) as exc_info:
            await call
        assert exc_info.value.code == -32601
        assert "payload" not in repr(exc_info.value.data)


@pytest.mark.unit
async def test_shutdown_cancels_all_active_prompt_tasks() -> None:
    runner = _ScriptedRunner()
    runner.block = True
    agent, _client, first_id = await _new_agent(runner)
    second_id = (await agent.new_session(cwd="/two", mcp_servers=[])).session_id
    tasks = [
        asyncio.create_task(agent.prompt(first_id, [_text("one")])),
        asyncio.create_task(agent.prompt(second_id, [_text("two")])),
    ]
    while len(runner.calls) < 2:
        await asyncio.sleep(0)

    await agent.shutdown()

    responses = await asyncio.gather(*tasks)
    assert [response.stop_reason for response in responses] == ["cancelled", "cancelled"]
    assert runner.cancelled == 2


@pytest.mark.unit
async def test_shutdown_rejects_new_prompt_without_entering_runner() -> None:
    runner = _ScriptedRunner([[AgentStreamEvent("final_answer", {"text": "late"}, final=True)]])
    agent, client, session_id = await _new_agent(runner)

    await agent.shutdown()
    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(session_id, [_text("hello")])

    assert exc_info.value.code == -32600
    assert runner.calls == []
    assert client.updates == []
