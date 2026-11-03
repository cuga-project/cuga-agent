"""Per-session model, request stamp and host instructions for inbound ACP sessions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class _RecordingAgent:
    instances: list["_RecordingAgent"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.overrides: list[Any] = []
        self.graph = SimpleNamespace(get_state=lambda _config: SimpleNamespace(next=(), values={}))
        type(self).instances.append(self)

    async def initialize(self) -> None:
        return None

    async def invoke(self, message: str | None, **kwargs: Any) -> Any:
        from cuga.backend.llm.models import get_current_llm_override

        self.calls.append({"message": message, **kwargs})
        self.overrides.append(get_current_llm_override())
        return SimpleNamespace(answer="answer", error=None)

    async def aclose(self) -> None:
        return None


class _FakeManager:
    def get_model(self, _settings: Any) -> str:
        from cuga.backend.llm.models import get_current_llm_override

        return f"model-for:{(get_current_llm_override() or {}).get('model')}"


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    import cuga.backend.llm.models as models
    import cuga.sdk

    _RecordingAgent.instances.clear()
    monkeypatch.setattr(cuga.sdk, "CugaAgent", _RecordingAgent)
    monkeypatch.setattr(models, "LLMManager", _FakeManager)
    monkeypatch.delenv("CUGA_SESSION_HEADER", raising=False)


@pytest.mark.unit
async def test_each_session_runs_on_its_own_model_and_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.llm.models import get_current_llm_override
    from cuga.backend.server.acp.runner import StdioCugaRunner

    monkeypatch.setenv("CUGA_SESSION_HEADER", "x-platform-session")
    runner = StdioCugaRunner()
    runner.describe_session("ctx-a", "session-a", "")
    runner.describe_session("ctx-b", "session-b", "")
    runner.set_session_model("ctx-a", "ibm/gpt-5")

    [_ async for _ in runner.run("one", context_id="ctx-a")]
    [_ async for _ in runner.run("two", context_id="ctx-b")]

    agent = _RecordingAgent.instances[0]
    assert agent.overrides == [
        {"model": "ibm/gpt-5", "default_headers": {"x-platform-session": "session-a"}},
        {"default_headers": {"x-platform-session": "session-b"}},
    ]
    assert agent.calls[0]["config"]["configurable"]["llm"] == "model-for:ibm/gpt-5"
    assert agent.calls[1]["config"]["configurable"]["llm"] == "model-for:None"
    assert get_current_llm_override() is None


@pytest.mark.unit
async def test_a_session_without_model_or_stamp_keeps_the_configured_model() -> None:
    from cuga.backend.server.acp.runner import StdioCugaRunner

    runner = StdioCugaRunner()
    runner.describe_session("ctx", "session", "")

    [_ async for _ in runner.run("hello", context_id="ctx")]

    agent = _RecordingAgent.instances[0]
    assert agent.overrides == [None]
    assert "config" not in agent.calls[0]


@pytest.mark.unit
async def test_host_instructions_reach_the_turn() -> None:
    from cuga.backend.server.acp.runner import StdioCugaRunner

    runner = StdioCugaRunner()
    runner.describe_session("ctx", "session", "Follow the workspace rules.")

    [_ async for _ in runner.run("hello", context_id="ctx")]

    configurable = _RecordingAgent.instances[0].calls[0]["config"]["configurable"]
    assert configurable["special_instructions"] == "Follow the workspace rules."


@pytest.mark.unit
async def test_set_config_option_sets_the_session_model_and_refuses_others() -> None:
    from acp import RequestError

    from cuga.backend.server.acp.agent import CugaAcpAgent
    from cuga.backend.server.acp.runner import StdioCugaRunner

    runner = StdioCugaRunner()
    agent = CugaAcpAgent(runner)
    session = await agent.new_session(cwd="/workspace", mcp_servers=[])
    context_id = agent.session_metadata(session.session_id).context_id

    response = await agent.set_config_option(
        config_id="model", session_id=session.session_id, value="cb/glm-5"
    )

    assert response.config_options == []
    assert runner._model_by_context[context_id] == "cb/glm-5"
    with pytest.raises(RequestError):
        await agent.set_config_option(config_id="mode", session_id=session.session_id, value="x")
    with pytest.raises(RequestError):
        await agent.set_config_option(config_id="model", session_id=session.session_id, value=" ")
    with pytest.raises(RequestError):
        await agent.set_config_option(config_id="model", session_id="unknown", value="cb/glm-5")


@pytest.mark.unit
async def test_new_and_loaded_sessions_read_the_instruction_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from cuga.backend.server.acp.agent import CugaAcpAgent
    from cuga.backend.server.acp.runner import StdioCugaRunner

    rules = tmp_path / "AGENTS.md"
    rules.write_text("Use the platform tools.", encoding="utf-8")
    monkeypatch.setenv("CUGA_INSTRUCTION_FILES", str(rules))
    runner = StdioCugaRunner()
    agent = CugaAcpAgent(runner, session_store=_MemoryStore())
    agent.on_connect(SimpleNamespace(session_update=_noop))

    session = await agent.new_session(cwd="/workspace", mcp_servers=[])
    context_id = agent.session_metadata(session.session_id).context_id
    assert runner._instructions_by_context[context_id] == "Use the platform tools."
    assert runner._session_by_context[context_id] == session.session_id

    rules.write_text("Updated rules.", encoding="utf-8")
    await agent.load_session(cwd="/workspace", session_id=session.session_id, mcp_servers=[])
    assert runner._instructions_by_context[context_id] == "Updated rules."


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


class _MemoryStore:
    """The session store a host injects, kept to what a load reads."""

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}

    def add(self, session_id: str, context_id: str, cwd: str, directories: tuple[str, ...]) -> None:
        self._sessions[session_id] = SimpleNamespace(
            session_id=session_id, context_id=context_id, cwd=cwd, additional_directories=directories
        )

    def get(self, session_id: str) -> Any:
        return self._sessions.get(session_id)

    def append(self, session_id: str, role: str, text: str) -> None:
        del session_id, role, text

    def messages(self, session_id: str) -> list[tuple[str, str]]:
        del session_id
        return []

    def close(self) -> None:
        return None
