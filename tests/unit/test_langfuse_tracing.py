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
    nested_langgraph_invoke_config,
    set_langfuse_callbacks,
    set_langfuse_trace_id,
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
    from cuga.backend.cuga_graph.utils import langfuse_tracing as mod

    set_langfuse_callbacks(None)
    set_langfuse_trace_id(None)
    mod._langfuse_primary_handler.set(None)
    mod._langgraph_run_config.set(None)
    yield
    set_langfuse_callbacks(None)
    set_langfuse_trace_id(None)
    mod._langfuse_primary_handler.set(None)
    mod._langgraph_run_config.set(None)


class TestLangfuseTracingHelpers:
    def test_invoke_config_empty_when_callbacks_not_set(self):
        assert get_langfuse_invoke_config() == {}

    def test_set_callbacks_visible_in_invoke_config(self):
        cb = _fake_langfuse_handler()
        set_langfuse_callbacks([cb])
        assert get_langfuse_invoke_config() == {"callbacks": [cb]}

    def test_non_langfuse_callbacks_are_not_propagated(self):
        set_langfuse_callbacks([_RecordingCallback("token-tracker")])
        assert get_langfuse_invoke_config() == {}

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

    def test_sync_trace_id_builds_handler_when_no_callbacks(self):
        handler = _fake_langfuse_handler()
        with patch(
            "cuga.backend.cuga_graph.utils.langfuse_tracing.create_trace_langfuse_handler",
            return_value=handler,
        ):
            sync_langfuse_callbacks_from_config({"configurable": {"langfuse_trace_id": "abc123"}})
            assert get_langfuse_invoke_config() == {"callbacks": [handler]}

    def test_sync_clears_stale_trace_state_when_next_config_has_no_trace(self):
        """A later invocation in the same async context with no trace id must not
        reattach nested LLM calls to the previous invocation's trace."""
        handler = _fake_langfuse_handler()
        with patch(
            "cuga.backend.cuga_graph.utils.langfuse_tracing.create_trace_langfuse_handler",
            return_value=handler,
        ):
            sync_langfuse_callbacks_from_config({"configurable": {"langfuse_trace_id": "trace-A"}})
            assert get_langfuse_invoke_config() == {"callbacks": [handler]}

            sync_langfuse_callbacks_from_config({"configurable": {}})
            assert get_langfuse_invoke_config() == {}

    def test_get_invoke_config_ignores_non_langfuse_callbacks(self):
        set_langfuse_callbacks([_RecordingCallback("stale")])
        set_langfuse_trace_id("trace-99")
        fresh = _fake_langfuse_handler()
        with patch(
            "cuga.backend.cuga_graph.utils.langfuse_tracing.create_trace_langfuse_handler",
            return_value=fresh,
        ):
            assert get_langfuse_invoke_config() == {"callbacks": [fresh]}

    def test_is_langfuse_callback_handler_detects_langfuse_types(self):
        assert is_langfuse_callback_handler(_fake_langfuse_handler())
        assert not is_langfuse_callback_handler(_RecordingCallback())

    def test_nested_langgraph_invoke_config_prefers_run_config(self):
        node_config = {"callbacks": [_fake_langfuse_handler()], "configurable": {"thread_id": "t1"}}
        set_langfuse_callbacks([])
        assert nested_langgraph_invoke_config(node_config) is node_config

    def test_nested_langgraph_invoke_config_falls_back_to_contextvar(self):
        cb = _fake_langfuse_handler()
        set_langfuse_callbacks([cb])
        assert nested_langgraph_invoke_config(None) == {"callbacks": [cb]}

    def test_sync_stores_run_config_in_contextvar(self):
        from cuga.backend.cuga_graph.utils import langfuse_tracing as mod

        cfg = {"callbacks": [_fake_langfuse_handler()], "configurable": {"thread_id": "t1"}}
        mod._langgraph_run_config.set(None)
        sync_langfuse_callbacks_from_config(cfg)
        assert nested_langgraph_invoke_config(None) is cfg

    def test_get_invoke_config_reuses_primary_handler_for_trace_id(self):
        from cuga.backend.cuga_graph.utils import langfuse_tracing as mod

        first = _fake_langfuse_handler()
        first._trace_context = {"trace_id": "trace-reuse"}
        mod._langfuse_primary_handler.set(first)
        set_langfuse_trace_id("trace-reuse")
        with patch(
            "cuga.backend.cuga_graph.utils.langfuse_tracing.create_trace_langfuse_handler",
        ) as create_mock:
            assert get_langfuse_invoke_config() == {"callbacks": [first]}
            create_mock.assert_not_called()

    def test_sync_reuses_primary_handler_instead_of_creating_duplicates(self):
        from cuga.backend.cuga_graph.utils import langfuse_tracing as mod

        existing = _fake_langfuse_handler()
        existing._trace_context = {"trace_id": "abc123"}
        mod._langfuse_primary_handler.set(existing)
        with patch(
            "cuga.backend.cuga_graph.utils.langfuse_tracing.create_trace_langfuse_handler",
        ) as create_mock:
            sync_langfuse_callbacks_from_config({"configurable": {"langfuse_trace_id": "abc123"}})
            create_mock.assert_not_called()
            assert get_langfuse_invoke_config() == {"callbacks": [existing]}


class _FakeLangfuseLikeHandler:
    """Mirrors the real langfuse.langchain.CallbackHandler's dispatch shape:
    on_llm_start/on_chat_model_start call a *name-mangled* private method.
    Used to prove TraceScopedLangfuseCallbackHandler's override is actually
    reachable through inheritance, not just present in source.
    """

    def __init__(self, trace_context=None):
        self._trace_context = trace_context
        self._runs: dict = {}
        self.recorded_trace_ids: list = []

    def on_llm_start(self, serialized, prompts, *, run_id=None, parent_run_id=None, **kwargs):
        self.__on_llm_action(serialized, run_id, prompts, parent_run_id, **kwargs)

    def on_chat_model_start(self, serialized, messages, *, run_id=None, parent_run_id=None, **kwargs):
        self.__on_llm_action(serialized, run_id, messages, parent_run_id, **kwargs)

    def __on_llm_action(self, serialized, run_id, prompts, parent_run_id=None, **kwargs):
        ctx = self._trace_context or {}
        self.recorded_trace_ids.append(ctx.get("trace_id"))
        self._runs[run_id] = parent_run_id


class TestTraceScopedHandler:
    @pytest.fixture(autouse=True)
    def _reset_handler_cache(self):
        from cuga.backend.cuga_graph.utils import langfuse_tracing as mod

        mod._TRACE_SCOPED_HANDLER_CLASS = None
        yield
        mod._TRACE_SCOPED_HANDLER_CLASS = None

    def test_attaches_orphan_llm_start_to_eval_trace(self):
        from cuga.backend.cuga_graph.utils import langfuse_tracing as mod

        with patch.object(mod, "_langfuse_handler_classes", return_value=(_FakeLangfuseLikeHandler,)):
            handler_cls = mod._trace_scoped_handler_class()
            handler = handler_cls(trace_context=None)

        set_langfuse_trace_id("eval-trace-7")
        handler.on_llm_start({}, ["hi"], run_id="run-1", parent_run_id=None)

        assert handler.recorded_trace_ids == ["eval-trace-7"]
        assert handler._trace_context is None  # restored after the call

    def test_attaches_orphan_chat_model_start_to_eval_trace(self):
        from cuga.backend.cuga_graph.utils import langfuse_tracing as mod

        with patch.object(mod, "_langfuse_handler_classes", return_value=(_FakeLangfuseLikeHandler,)):
            handler_cls = mod._trace_scoped_handler_class()
            handler = handler_cls(trace_context=None)

        set_langfuse_trace_id("eval-trace-8")
        handler.on_chat_model_start({}, [["hi"]], run_id="run-2", parent_run_id=None)

        assert handler.recorded_trace_ids == ["eval-trace-8"]
        assert handler._trace_context is None

    def test_does_not_override_tracked_nested_calls(self):
        from cuga.backend.cuga_graph.utils import langfuse_tracing as mod

        with patch.object(mod, "_langfuse_handler_classes", return_value=(_FakeLangfuseLikeHandler,)):
            handler_cls = mod._trace_scoped_handler_class()
            handler = handler_cls(trace_context={"trace_id": "fixed-trace"})

        handler._runs["parent-1"] = None
        handler.on_llm_start({}, ["hi"], run_id="run-3", parent_run_id="parent-1")

        assert handler.recorded_trace_ids == ["fixed-trace"]
        assert handler._trace_context == {"trace_id": "fixed-trace"}


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

    def test_apply_callbacks_reads_only_top_level_callbacks(self):
        from cuga.sdk import CugaAgent

        caller = _RecordingCallback("caller")
        config_only = _RecordingCallback("configurable-only")
        agent = CugaAgent.__new__(CugaAgent)
        agent._callbacks = []

        run_config = {
            "callbacks": [caller],
            "configurable": {"callbacks": [config_only]},
        }
        agent._apply_callbacks(run_config)
        assert caller in run_config["callbacks"]
        assert config_only not in run_config["callbacks"]
        assert run_config["configurable"]["callbacks"] == run_config["callbacks"]

    def test_apply_callbacks_detects_langfuse_handler_inside_callback_manager(self):
        """A Langfuse handler nested inside a caller-supplied callback manager (not a
        bare list) must still be detected so the agent-level handler is dropped."""
        from cuga.backend.cuga_graph.utils.langfuse_tracing import _flatten_callbacks
        from cuga.sdk import CugaAgent

        agent_level = _fake_langfuse_handler()
        per_call_lf = _fake_langfuse_handler()
        manager = type("AsyncCallbackManager", (), {"handlers": [per_call_lf]})()

        agent = CugaAgent.__new__(CugaAgent)
        agent._callbacks = [agent_level]

        run_config = {"callbacks": manager, "configurable": {}}
        agent._apply_callbacks(run_config)

        flattened_ids = [id(c) for c in _flatten_callbacks(run_config["callbacks"])]
        assert id(per_call_lf) in flattened_ids
        assert id(agent_level) not in flattened_ids


class TestNestedCallSites:
    @pytest.mark.asyncio
    async def test_nl_auto_continue_passes_invoke_config(self):
        from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as mod
        from cuga.config import settings

        cb = _fake_langfuse_handler()
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

        cb = _fake_langfuse_handler()
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
    async def test_shortlist_passes_explicit_run_config(self):
        from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

        node_config = {
            "callbacks": [_fake_langfuse_handler()],
            "configurable": {"thread_id": "bind-cap"},
        }
        set_langfuse_callbacks([])

        api_detail = type("APIDetails", (), {"name": "tool_a", "reasoning": "r"})()
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=MagicMock(result=[api_detail]))
        tool = MagicMock()
        tool.name = "tool_a"

        with patch(
            "cuga.backend.cuga_graph.nodes.shared.base_agent.BaseAgent.get_chain",
            return_value=mock_chain,
        ):
            with patch("cuga.backend.llm.models.LLMManager"):
                ranked = await PromptUtils.shortlist_tool_names(
                    query="list users",
                    all_tools=[tool],
                    all_apps=[],
                    top_k=1,
                    run_config=node_config,
                )

        assert ranked == ["tool_a"]
        _args, kwargs = mock_chain.ainvoke.call_args
        assert kwargs.get("config") is node_config

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
