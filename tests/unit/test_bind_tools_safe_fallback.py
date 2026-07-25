"""_safe_bind falls back to the unbound model when bind_tools is unsupported (issue #471 D9)."""

from typing import Any, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import _safe_bind


class _NoBindModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "no-bind"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    # inherits BaseChatModel.bind_tools -> raises NotImplementedError


class _BindModel(_NoBindModel):
    bound: Optional[List[Any]] = None
    got_tool_choice: Any = None

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self.model_copy(update={"bound": list(tools), "got_tool_choice": kwargs.get("tool_choice")})


class _NoToolChoiceModel(_NoBindModel):
    """Supports bind_tools but rejects the tool_choice kwarg (older providers)."""

    plain_bound: bool = False

    def bind_tools(self, tools: Any, **kwargs: Any):
        if "tool_choice" in kwargs:
            raise TypeError("tool_choice not supported")
        return self.model_copy(update={"plain_bound": True})


def test_safe_bind_returns_unbound_model_when_unsupported():
    model = _NoBindModel()
    # sanity: the raw call raises the NotImplementedError (a RuntimeError subclass)
    assert issubclass(NotImplementedError, RuntimeError)
    result = _safe_bind(model, ["tool_a"])
    assert result is model  # degraded, not crashed


def test_safe_bind_binds_when_supported():
    model = _BindModel()
    result = _safe_bind(model, ["tool_a", "tool_b"])
    assert result is not model
    assert result.bound == ["tool_a", "tool_b"]


def test_safe_bind_passes_tool_choice_when_supported():
    result = _safe_bind(_BindModel(), ["tool_a"], tool_choice="required")
    assert result.bound == ["tool_a"]
    assert result.got_tool_choice == "required"


def test_safe_bind_degrades_when_tool_choice_unsupported():
    # provider rejects tool_choice -> fall back to a plain bind, don't crash
    result = _safe_bind(_NoToolChoiceModel(), ["tool_a"], tool_choice="required")
    assert result.plain_bound is True
