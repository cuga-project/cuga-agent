"""_safe_bind falls back to the unbound model when bind_tools is unsupported (issue #471 D9)."""

from typing import Any, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools import BindToolsUnsupportedError
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import (
    _safe_bind,
    resolve_model_with_bind_tools,
)

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


# ── Over-cap path: the shortlister runs on the same unsupported model ────────
# _safe_bind alone does not cover this — the cap's shortlister calls
# with_structured_output *before* any bind, and its failure used to be rewrapped
# as RuntimeError, hitting the deliberate `except RuntimeError: raise` and
# killing call_model. These exercise resolve_model_with_bind_tools end to end.


def _stub_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda: None, name=name, description="d")


def _provider(tools: List[StructuredTool]) -> AsyncMock:
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=tools)
    return provider


@pytest.mark.asyncio
async def test_over_cap_degrades_when_model_cannot_bind():
    """200 tools / cap 128 on a no-bind model must degrade, not raise."""
    model = _NoBindModel()
    result = await resolve_model_with_bind_tools(
        model,
        configurable={"cuga_lite_bind_tools_mode": "all", "cuga_lite_bind_tools_max_count": 128},
        tools_context_ref={},
        tool_provider=_provider([_stub_tool(f"t{i}") for i in range(200)]),
        query="do a task",
    )
    assert result is model  # degraded to unbound (code-act), run survives


@pytest.mark.asyncio
async def test_over_cap_genuine_shortlister_failure_still_raises():
    """The loud-failure policy is preserved: a real shortlister error still raises
    RuntimeError, so benchmark runs never silently truncate the tool list."""
    model = _BindModel()  # supports bind_tools — so this is NOT a capability gap
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
        new=AsyncMock(side_effect=ValueError("shortlister exploded")),
    ):
        with pytest.raises(RuntimeError, match="shortlister failed"):
            await resolve_model_with_bind_tools(
                model,
                configurable={
                    "cuga_lite_bind_tools_mode": "all",
                    "cuga_lite_bind_tools_max_count": 128,
                },
                tools_context_ref={},
                tool_provider=_provider([_stub_tool(f"t{i}") for i in range(200)]),
                query="do a task",
            )


@pytest.mark.asyncio
async def test_degradation_leaves_a_machine_readable_trace():
    """A log line is not actionable downstream; an eval harness must be able to
    tell that native binding was requested and didn't happen."""
    from cuga.backend.activity_tracker.tracker import ActivityTracker

    tracker = ActivityTracker()
    before = len(tracker.steps or [])
    model = _NoBindModel()
    await resolve_model_with_bind_tools(
        model,
        configurable={"cuga_lite_bind_tools_mode": "all", "cuga_lite_bind_tools_max_count": 128},
        tools_context_ref={},
        tool_provider=_provider([_stub_tool(f"t{i}") for i in range(200)]),
        query="do a task",
    )
    new_steps = [s for s in (tracker.steps or [])[before:] if s.name == "bind_tools_degraded"]
    assert new_steps, "expected a bind_tools_degraded step recording the fallback"


@pytest.mark.asyncio
async def test_capability_error_is_not_a_runtimeerror_subclass():
    """The routing depends on this: BindToolsUnsupportedError must bypass the
    `except RuntimeError: raise` lane that intentional cap failures travel."""
    assert not issubclass(BindToolsUnsupportedError, RuntimeError)
