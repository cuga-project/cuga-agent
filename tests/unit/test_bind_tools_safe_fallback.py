"""_safe_bind falls back to the unbound model when bind_tools is unsupported (issue #471 D9)."""

from typing import Any, List, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import _safe_bind

pytestmark = pytest.mark.unit


class _NoBindModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "no-bind"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    # inherits BaseChatModel.bind_tools -> raises NotImplementedError


class _BindModel(_NoBindModel):
    bound: Optional[List[Any]] = None

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self.model_copy(update={"bound": list(tools)})


def test_safe_bind_returns_unbound_model_when_unsupported():
    model = _NoBindModel()
    # sanity: NotImplementedError IS a RuntimeError subclass — without _safe_bind
    # it would hit the cap's deliberate `except RuntimeError: raise` and crash
    # call_model instead of degrading.
    assert issubclass(NotImplementedError, RuntimeError)
    result = _safe_bind(model, ["tool_a"])
    assert result is model  # degraded, not crashed


def test_safe_bind_binds_when_supported():
    model = _BindModel()
    result = _safe_bind(model, ["tool_a", "tool_b"])
    assert result is not model
    assert result.bound == ["tool_a", "tool_b"]
