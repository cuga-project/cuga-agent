"""Lone UTF-16 surrogates (half an emoji) must not break a turn (#963).

A lone surrogate is a valid Python ``str`` that cannot be encoded as UTF-8. These
tests cover a model reply that holds one: it breaks ``compile()``, the next request
body and every serializer after it, unless the reply is repaired.

The end-to-end tests run ``CugaAgent.invoke()`` with a scripted model that
encodes each request the way httpx does (``json.dumps(..., ensure_ascii=False)
.encode("utf-8")``), so any surrogate that reaches a model request fails the run.
"""

from __future__ import annotations

import json
from typing import Any, List

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution import code_extraction
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
    extract_code_from_model_response,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
from cuga.backend.cuga_graph.utils.surrogates import (
    repair_message,
    repair_surrogates,
)

pytestmark = pytest.mark.unit

LONE = "\ud83d"  # the high half of 😀
REPLACED = "�"


def _encode_like_httpx(payload: Any) -> bytes:
    """Encode a request body as httpx does; a lone surrogate raises UnicodeEncodeError."""
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def _request_payload(messages: List[BaseMessage]) -> list:
    return [
        {
            "type": m.type,
            "content": m.content,
            "additional_kwargs": m.additional_kwargs,
            "tool_calls": getattr(m, "tool_calls", None),
        }
        for m in messages
    ]


class _ScriptedModel(BaseChatModel):
    """Returns ``replies`` in order (an exception is raised), then "Done.". Requests must encode as UTF-8."""

    replies: List[Any] = Field(default_factory=list)
    requests: List[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-utf8"

    def bind_tools(self, tools: Any, **kwargs: Any):
        # A copy, as providers return: function-calling mode checks that tools were bound.
        # The copy is shallow, so it shares ``replies`` and ``requests``.
        return self.model_copy()

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        body = _encode_like_httpx(_request_payload(messages))
        self.requests.append(body.decode("utf-8"))
        reply = self.replies.pop(0) if self.replies else AIMessage(content="Done.")
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])


class _KeyArgs(BaseModel):
    key: str


class _ScopeArgs(BaseModel):
    scope: str


class _Tools:
    """Tools that record their calls."""

    def __init__(self) -> None:
        self.lookups: list[str] = []
        self.deleted: list[str] = []

    def build(self) -> list[StructuredTool]:
        async def lookup(key: str) -> str:
            self.lookups.append(key)
            return f"value for {key}"

        async def delete_records(scope: str) -> str:
            self.deleted.append(scope)
            return "deleted"

        return [
            StructuredTool(
                name="lookup", description="Look up a key.", args_schema=_KeyArgs, coroutine=lookup
            ),
            StructuredTool(
                name="delete_records",
                description="Delete records.",
                args_schema=_ScopeArgs,
                coroutine=delete_records,
            ),
        ]


_CONFIGURABLE = {
    "enable_todos": False,
    "reflection_enabled": False,
    "pre_execute_verify_enabled": False,
}


@pytest.fixture
def _graph_run(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False, raising=False)
    yield
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)


async def _invoke(monkeypatch, replies: list[AIMessage], message: Any = "Do the task.", **kwargs: Any):
    import cuga
    from cuga.backend.llm.models import LLMManager

    model = _ScriptedModel(replies=list(replies))
    # Start-up also builds agents CugaLite never calls; the fake model keeps them free of provider keys.
    monkeypatch.setattr(LLMManager(), "_pre_instantiated_model", model)
    tools = _Tools()
    agent = cuga.CugaAgent(tools=tools.build(), model=model, auto_load_policies=False)
    configurable = {**_CONFIGURABLE, **kwargs.pop("configurable", {})}
    result = await agent.invoke(message, config={"configurable": configurable}, **kwargs)
    return result, model, tools


# ── End to end: model replies ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_963_reply_completes_the_turn(monkeypatch, _graph_run):
    """The reply from #963: a lone surrogate inside an unclosed fence."""
    result, model, _ = await _invoke(monkeypatch, [AIMessage(content="```python\nx = 'caf\ud800'\nprint(x)")])

    assert result.error is None and result.answer == "Done."
    assert f"caf{REPLACED}" in model.requests[-1]


@pytest.mark.asyncio
async def test_split_emoji_does_not_run_the_rejected_reasoning_draft(monkeypatch, _graph_run):
    """Content with half an emoji must not count as "no code" and fall back to reasoning."""
    reply = AIMessage(
        content=f"```python\nr = await lookup(key='report {LONE}')\nprint(r)",
        additional_kwargs={"reasoning_content": "```python\nr = await delete_records(scope='all')\n```"},
    )
    result, _, tools = await _invoke(monkeypatch, [reply])

    assert tools.deleted == []
    assert tools.lookups == [f"report {REPLACED}"]
    assert result.error is None and result.answer == "Done."


@pytest.mark.asyncio
async def test_lone_surrogate_in_a_closed_fence_runs_with_the_repaired_text(monkeypatch, _graph_run):
    """A closed fence skips extraction's compile() check; the reply is repaired before it reaches the sandbox."""
    reply = AIMessage(content="```python\nr = await lookup(key='caf\ud800')\nprint(r)\n```")
    result, _, tools = await _invoke(monkeypatch, [reply])

    assert tools.lookups == [f"caf{REPLACED}"]
    assert result.error is None and result.answer == "Done."


@pytest.mark.asyncio
async def test_tool_use_failed_generation_with_a_lone_surrogate_runs_repaired(monkeypatch, _graph_run):
    """The provider rejects a tool call; ``ainvoke_model`` runs its ``failed_generation`` as code."""
    error = RuntimeError("tool_use_failed")
    error.body = {
        "error": {
            "code": "tool_use_failed",
            "failed_generation": {
                "name": "python",
                "arguments": "r = await lookup(key='caf\ud800')\nprint(r)",
            },
        }
    }
    result, _, tools = await _invoke(monkeypatch, [error])

    assert tools.lookups == [f"caf{REPLACED}"]
    assert result.error is None and result.answer == "Done."


@pytest.mark.asyncio
async def test_valid_code_before_the_bad_line_runs_once_and_the_turn_completes(monkeypatch, _graph_run):
    reply = AIMessage(content="```python\nr = await lookup(key='a')\nprint(r)\nnote = 'caf\ud800'")
    result, _, tools = await _invoke(monkeypatch, [reply])

    assert tools.lookups == ["a"]
    assert result.error is None and result.answer == "Done."


@pytest.mark.asyncio
async def test_trajectory_stays_encodable_with_the_tracker_on(monkeypatch, _graph_run):
    """With ``tracker_enabled`` every step is written as UTF-8 JSON; a lone surrogate cut the file off."""
    from cuga.backend.activity_tracker import tracker as activity_tracker
    from cuga.backend.activity_tracker.tracker import ActivityTracker

    written: list[bytes] = []

    def _to_file(self):
        written.append(_encode_like_httpx([step.model_dump() for step in self.steps]))

    # The settings object the tracker module reads; other tests can rebind cuga.config.settings.
    monkeypatch.setattr(activity_tracker.settings.advanced_features, "tracker_enabled", True, raising=False)
    monkeypatch.setattr(ActivityTracker, "to_file", _to_file)
    reply = AIMessage(content="```python\nr = await lookup(key='a')\nprint(r)\nnote = 'caf\ud800'")
    result, _, tools = await _invoke(monkeypatch, [reply])

    assert written
    assert tools.lookups == ["a"]
    assert result.error is None and result.answer == "Done."


@pytest.mark.asyncio
async def test_streamed_states_stay_encodable(monkeypatch, _graph_run):
    """``/stream`` encodes each chunk after it is yielded; a raw reply in a state cut the stream."""
    import cuga
    from cuga.backend.llm.models import LLMManager

    model = _ScriptedModel(replies=[AIMessage(content="```python\nx = 'caf\ud800'\nprint(x)")])
    monkeypatch.setattr(LLMManager(), "_pre_instantiated_model", model)
    agent = cuga.CugaAgent(tools=_Tools().build(), model=model, auto_load_policies=False)

    states = [
        state
        async for state in agent.stream("Look up a value.", config={"configurable": dict(_CONFIGURABLE)})
    ]

    assert states
    for state in states:
        _encode_like_httpx(state)


@pytest.mark.asyncio
async def test_final_answer_with_a_lone_surrogate_is_delivered(monkeypatch, _graph_run):
    result, _, _ = await _invoke(monkeypatch, [AIMessage(content=f"All done {LONE}")])

    assert result.error is None
    assert result.answer == f"All done {REPLACED}"


@pytest.mark.asyncio
async def test_function_calling_arguments_with_a_lone_surrogate(monkeypatch, _graph_run):
    reply = AIMessage(
        content="", tool_calls=[{"name": "lookup", "args": {"key": f"a{LONE}"}, "id": "call_1"}]
    )
    result, model, tools = await _invoke(monkeypatch, [reply], execution_mode="function_calling")

    assert tools.lookups == [f"a{REPLACED}"]
    assert result.error is None and result.answer == "Done."


# ── compile() and the helper ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "```python\nx = 'caf\ud800'\nprint(x)",  # #963: UnicodeEncodeError
        "print('caf\ud800')",  # raw-text fallback
        "```python\n" + "-" * 6000 + "1\nprint(1)",  # MemoryError on every version
    ],
)
def test_text_compile_rejects_counts_as_no_code(text: str) -> None:
    """Whatever ``compile()`` raises, the text is "no code", never a crash."""
    assert extract_code_from_model_response(text, None) == ""


@pytest.mark.parametrize("error", [RecursionError, SystemError, MemoryError, ValueError])
def test_any_compile_error_counts_as_no_code(monkeypatch, error) -> None:
    """``RecursionError`` (3.10/3.11) and ``SystemError`` (3.11 f-strings) are version-specific; simulate them."""

    def _raise(*args, **kwargs):
        raise error("simulated")

    monkeypatch.setattr(code_extraction, "compile", _raise, raising=False)
    assert extract_code_from_model_response("```python\nprint(1)", None) == ""
    assert extract_code_from_model_response("print(1)", None) == ""


def test_repair_joins_split_pairs_and_replaces_lone_halves() -> None:
    assert repair_surrogates("Done 😀") == "Done 😀"
    assert repair_surrogates(f"caf{LONE}") == f"caf{REPLACED}"
    clean = "plain text"
    assert repair_surrogates(clean) is clean


def test_repair_message_covers_reasoning_and_tool_arguments() -> None:
    message = AIMessage(
        content="ok",
        additional_kwargs={"reasoning_content": f"r{LONE}"},
        tool_calls=[{"name": "lookup", "args": {"key": f"a{LONE}"}, "id": "c1"}],
    )
    repaired = repair_message(message)

    assert repaired.additional_kwargs["reasoning_content"] == f"r{REPLACED}"
    assert repaired.tool_calls[0]["args"] == {"key": f"a{REPLACED}"}
    assert message.tool_calls[0]["args"] == {"key": f"a{LONE}"}  # the original is not mutated
    clean = AIMessage(content="ok")
    assert repair_message(clean) is clean


def test_repair_message_without_model_copy_returns_a_repaired_copy() -> None:
    """Like ``_FakeResponse`` in ``ainvoke_model``: plain attributes, no ``model_copy``."""

    class _Plain:
        content = f"caf{LONE}"
        additional_kwargs: dict = {}

    original = _Plain()
    repaired = repair_message(original)

    assert repaired.content == f"caf{REPLACED}"
    assert original.content == f"caf{LONE}"
