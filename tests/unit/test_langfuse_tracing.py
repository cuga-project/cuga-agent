"""
Regression tests for Langfuse callback propagation to nested LLM calls.

Without propagation, nested paths (reflection, find_tools, NL auto-continue,
output formatter, context summarization) invoke LLMs with empty callbacks and
Langfuse creates separate root traces per call.

These tests do not require a Langfuse server or API keys.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.cuga_graph.utils.langfuse_tracing import (
    collect_langfuse_callbacks_from_config,
    get_langfuse_invoke_config,
    is_langfuse_callback_handler,
    set_langfuse_callbacks,
    sync_langfuse_callbacks_from_config,
)


class _RecordingCallback:
    """Minimal LangChain-style callback stand-in."""

    def __init__(self, name: str = "recording"):
        self.name = name


def _fake_langfuse_handler():
    """Object that matches is_langfuse_callback_handler heuristics."""
    return type("CallbackHandler", (), {"__module__": "langfuse.langchain"})()


@pytest.fixture(autouse=True)
def _clear_langfuse_context():
    set_langfuse_callbacks(None)
    yield
    set_langfuse_callbacks(None)


class TestLangfuseTracingHelpers:
    def test_invoke_config_empty_when_callbacks_not_set(self):
        assert get_langfuse_invoke_config() == {}

    def test_set_callbacks_visible_in_invoke_config(self):
        cb = _RecordingCallback("trace-scoped")
        set_langfuse_callbacks([cb])
        assert get_langfuse_invoke_config() == {"callbacks": [cb]}

    def test_collect_merges_top_level_and_configurable(self):
        a, b = _fake_langfuse_handler(), _fake_langfuse_handler()
        config = {
            "callbacks": [a],
            "configurable": {"callbacks": [b]},
        }
        merged = collect_langfuse_callbacks_from_config(config)
        assert merged == [a, b]

    def test_collect_ignores_non_langfuse_handlers(self):
        lf = _fake_langfuse_handler()
        config = {"callbacks": [_RecordingCallback("other"), lf]}
        assert collect_langfuse_callbacks_from_config(config) == [lf]

    def test_collect_flattens_callback_manager(self):
        lf = _fake_langfuse_handler()
        manager = type("AsyncCallbackManager", (), {"handlers": [lf, _RecordingCallback("x")]})()
        assert collect_langfuse_callbacks_from_config({"callbacks": manager}) == [lf]

    def test_sync_from_config_populates_context(self):
        cb = _fake_langfuse_handler()
        sync_langfuse_callbacks_from_config({"configurable": {"callbacks": [cb]}})
        assert get_langfuse_invoke_config() == {"callbacks": [cb]}

    def test_is_langfuse_callback_handler_detects_langfuse_types(self):
        assert is_langfuse_callback_handler(_fake_langfuse_handler())
        assert not is_langfuse_callback_handler(_RecordingCallback())


class TestSdkCallbackDedup:
    def test_apply_callbacks_drops_agent_langfuse_when_trace_id_set(self):
        from cuga.sdk import CugaAgent

        agent_level = _fake_langfuse_handler()
        per_invoke = _RecordingCallback("per-invoke")

        agent = CugaAgent.__new__(CugaAgent)
        agent._callbacks = [agent_level]

        run_config = {
            "callbacks": [per_invoke],
            "configurable": {"langfuse_trace_id": "trace-abc", "thread_id": "t1"},
        }
        agent._apply_callbacks(run_config)

        callback_ids = [id(c) for c in run_config["callbacks"]]
        assert id(per_invoke) in callback_ids
        assert id(agent_level) not in callback_ids
        assert run_config["configurable"]["callbacks"] == run_config["callbacks"]


class TestNestedCallSites:
    @pytest.mark.asyncio
    async def test_nl_auto_continue_passes_invoke_config(self):
        from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as mod
        from cuga.config import settings

        cb = _RecordingCallback("nl")
        set_langfuse_callbacks([cb])

        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"auto_continue": false}'
        mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

        with patch.object(settings.advanced_features, "cuga_lite_nl_auto_continue", True, create=True):
            result = await mod.classify_nl_auto_continue(
                mock_llm,
                assistant_visible="done with the task",
                reasoning_excerpt="finished reasoning",
            )
        assert result is False
        mock_llm.ainvoke.assert_awaited_once()
        _args, kwargs = mock_llm.ainvoke.call_args
        assert kwargs.get("config") == {"callbacks": [cb]}

    @pytest.mark.asyncio
    async def test_nl_auto_continue_without_sync_fails_propagation(self):
        """Documents pre-fix behaviour: no callbacks in nested ainvoke config."""
        from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as mod
        from cuga.config import settings

        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"auto_continue": false}'
        mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

        with patch.object(settings.advanced_features, "cuga_lite_nl_auto_continue", True, create=True):
            await mod.classify_nl_auto_continue(
                mock_llm,
                assistant_visible="partial answer here",
                reasoning_excerpt="still working",
            )
        _args, kwargs = mock_llm.ainvoke.call_args
        assert kwargs.get("config") == {}

    @pytest.mark.asyncio
    async def test_output_formatter_ainvoke_receives_callbacks(self):
        from langchain_core.messages import AIMessage

        from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
        from cuga.backend.cuga_graph.policy.models import OutputFormatter

        cb = _RecordingCallback("formatter")
        set_langfuse_callbacks([cb])

        policy = OutputFormatter(
            id="test_fmt",
            name="Test Formatter",
            description="test",
            format_type="markdown",
            format_config="Use bullet points.",
            triggers=[],
        )
        policy_match = MagicMock()
        policy_match.policy = policy
        policy_match.reasoning = "test"
        policy_match.confidence = 1.0

        state = MagicMock()
        state.chat_messages = [AIMessage(content="answer is 42")]
        state.final_answer = "answer is 42"

        context = MagicMock()
        context.agent_response = "answer is 42"
        context.chat_messages = []
        context.user_input = None

        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "- answer is 42"
        mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

        with patch("cuga.backend.llm.models.LLMManager") as mock_mgr:
            mock_mgr.return_value.get_model.return_value = mock_llm
            _cmd, metadata = await PolicyEnactment._enact_format_output(
                state, policy_match, MagicMock(), context
            )

        assert metadata is not None
        mock_llm.ainvoke.assert_awaited_once()
        _args, kwargs = mock_llm.ainvoke.call_args
        assert kwargs.get("config") == {"callbacks": [cb]}

    @pytest.mark.asyncio
    async def test_context_summarization_does_not_wrap_model_with_config(self):
        """with_config(callbacks) breaks ContextSummarizer profile setup; use contextvar instead."""
        from langchain_core.messages import HumanMessage

        from cuga.backend.cuga_graph.utils.context_management_utils import (
            apply_context_summarization,
        )

        set_langfuse_callbacks([_RecordingCallback("summarize")])

        base_model = MagicMock()
        base_model.model_name = "gpt-4"
        messages = [HumanMessage(content="hello")]

        with patch("cuga.backend.cuga_graph.utils.context_management_utils.AgentState") as mock_state_cls:
            instance = MagicMock()
            instance.chat_messages = messages
            mock_state_cls.return_value = instance

            with patch.object(
                instance,
                "manage_message_context",
                new_callable=AsyncMock,
            ) as mock_manage:
                result = await apply_context_summarization(
                    messages,
                    base_model,
                    message_list_name="chat_messages",
                )

        base_model.with_config.assert_not_called()
        mock_manage.assert_awaited_once()
        assert result == messages
