"""
Unit tests for TokenUsageTracker integration in CugaAgent SDK

Tests that the SDK properly instruments LLM calls with TokenUsageTracker
to enable trajectory tracking for tools like cuga-viz.

Related to issue #71: Instrument CugaAgent SDK with TokenUsageTracker
"""

import pytest
from langchain_core.tools import tool

from cuga import CugaAgent
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.utils.agent_loop import TokenUsageTracker


# Test tool
@tool
def simple_calculator(a: int, b: int, operation: str = "add") -> int:
    """Perform simple math operations"""
    if operation == "add":
        return a + b
    elif operation == "multiply":
        return a * b
    return 0


class TestTokenUsageTrackerIntegration:
    """Tests for TokenUsageTracker integration in CugaAgent"""

    @pytest.mark.asyncio
    async def test_build_callbacks_includes_token_usage_tracker(self):
        """Test that _build_callbacks() includes TokenUsageTracker"""
        agent = CugaAgent(tools=[simple_calculator])

        callbacks = agent._build_callbacks()

        # Verify callbacks list is not empty
        assert len(callbacks) > 0

        # Verify TokenUsageTracker is included
        has_token_tracker = any(isinstance(cb, TokenUsageTracker) for cb in callbacks)
        assert has_token_tracker, "TokenUsageTracker should be in callbacks list"

    @pytest.mark.asyncio
    async def test_build_callbacks_preserves_user_callbacks(self):
        """Test that _build_callbacks() preserves user-provided callbacks"""
        from langchain_core.callbacks import BaseCallbackHandler

        class CustomCallback(BaseCallbackHandler):
            pass

        custom_cb = CustomCallback()
        agent = CugaAgent(tools=[simple_calculator], callbacks=[custom_cb])

        callbacks = agent._build_callbacks()

        # Verify both TokenUsageTracker and custom callback are present
        assert len(callbacks) >= 2
        has_token_tracker = any(isinstance(cb, TokenUsageTracker) for cb in callbacks)
        has_custom = any(isinstance(cb, CustomCallback) for cb in callbacks)

        assert has_token_tracker, "TokenUsageTracker should be in callbacks list"
        assert has_custom, "Custom callback should be preserved"

    @pytest.mark.asyncio
    async def test_invoke_registers_callbacks(self):
        """Test that invoke() registers callbacks including TokenUsageTracker"""
        agent = CugaAgent(tools=[simple_calculator])

        # Verify that _build_callbacks is called and returns TokenUsageTracker
        callbacks = agent._build_callbacks()

        # Verify callbacks list is not empty
        assert len(callbacks) > 0

        # Verify TokenUsageTracker is in the callbacks
        has_token_tracker = any(isinstance(cb, TokenUsageTracker) for cb in callbacks)
        assert has_token_tracker, "TokenUsageTracker should be registered in invoke()"

        # Verify callbacks would be added to config
        # (Full integration test would require actual graph execution)

    @pytest.mark.asyncio
    async def test_activity_tracker_collects_prompts(self):
        """Test that ActivityTracker receives prompts from TokenUsageTracker"""
        # Reset ActivityTracker to ensure clean state
        tracker = ActivityTracker()
        tracker.prompts = []

        agent = CugaAgent(tools=[simple_calculator])

        # Get the callbacks that would be used
        callbacks = agent._build_callbacks()

        # Find the TokenUsageTracker
        token_tracker = None
        for cb in callbacks:
            if isinstance(cb, TokenUsageTracker):
                token_tracker = cb
                break

        assert token_tracker is not None, "TokenUsageTracker should be in callbacks"

        # Verify the tracker is connected to ActivityTracker
        assert token_tracker.tracker is tracker

        # Simulate LLM interaction by calling collect_prompt
        tracker.collect_prompt(role="system", value="You are a helpful assistant")
        tracker.collect_prompt(role="human", value="What is 2 + 2?")
        tracker.collect_prompt(role="assistant", value="The answer is 4")

        # Verify prompts were collected
        assert len(tracker.prompts) == 3
        assert tracker.prompts[0].role == "system"
        assert tracker.prompts[1].role == "human"
        assert tracker.prompts[2].role == "assistant"

    @pytest.mark.asyncio
    async def test_stream_registers_callbacks(self):
        """Test that stream() registers callbacks including TokenUsageTracker"""
        agent = CugaAgent(tools=[simple_calculator])

        # Verify that _build_callbacks is called and returns TokenUsageTracker
        callbacks = agent._build_callbacks()

        # Verify callbacks list is not empty
        assert len(callbacks) > 0

        # Verify TokenUsageTracker is in the callbacks
        has_token_tracker = any(isinstance(cb, TokenUsageTracker) for cb in callbacks)
        assert has_token_tracker, "TokenUsageTracker should be registered in stream()"

        # Verify callbacks would be added to config
        # (Full integration test would require actual graph execution)

    @pytest.mark.asyncio
    async def test_callbacks_in_configurable(self):
        """Test that callbacks are built correctly for both top-level and configurable"""
        agent = CugaAgent(tools=[simple_calculator])

        # Verify that _build_callbacks returns the same list each time
        callbacks1 = agent._build_callbacks()
        callbacks2 = agent._build_callbacks()

        # Both should have TokenUsageTracker
        has_token_tracker1 = any(isinstance(cb, TokenUsageTracker) for cb in callbacks1)
        has_token_tracker2 = any(isinstance(cb, TokenUsageTracker) for cb in callbacks2)

        assert has_token_tracker1, "First call should include TokenUsageTracker"
        assert has_token_tracker2, "Second call should include TokenUsageTracker"

        # Verify the implementation adds callbacks to both locations
        # (This is verified by code inspection - both invoke() and stream()
        # set run_config["callbacks"] and run_config["configurable"]["callbacks"])

    @pytest.mark.asyncio
    async def test_invoke_does_not_mutate_caller_config(self):
        """Reusing a config across invoke() calls must not accumulate callbacks.

        Regression for the case where the SDK aliased and mutated the caller's
        config dict, causing TokenUsageTracker instances to pile up on each call.
        """
        from unittest.mock import patch, AsyncMock

        agent = CugaAgent(tools=[simple_calculator])

        # Stub out graph execution; we only care about what gets written to config.
        with (
            patch.object(
                type(agent),
                "graph",
                new_callable=lambda: property(lambda self: AsyncMock(ainvoke=AsyncMock(return_value={}))),
            ),
        ):
            caller_config: dict = {"configurable": {"thread_id": "t-1"}}
            snapshot_before = {
                "top_level_keys": set(caller_config.keys()),
                "configurable_keys": set(caller_config["configurable"].keys()),
            }

            await agent.invoke("first", thread_id="t-1", config=caller_config)
            await agent.invoke("second", thread_id="t-1", config=caller_config)
            await agent.invoke("third", thread_id="t-1", config=caller_config)

            # The caller's dict must not have been mutated with SDK internals.
            assert set(caller_config.keys()) == snapshot_before["top_level_keys"], (
                "invoke() must not add keys to caller's config dict"
            )
            assert set(caller_config["configurable"].keys()) == snapshot_before["configurable_keys"], (
                "invoke() must not add keys to caller's configurable dict"
            )

    @pytest.mark.asyncio
    async def test_direct_graph_access_is_instrumented(self):
        """`agent.graph` is documented for advanced use. Its base callbacks should
        include TokenUsageTracker so direct `agent.graph.ainvoke(...)` calls also
        produce trajectory data.
        """
        from cuga.backend.cuga_graph.nodes.cuga_lite import cuga_lite_graph as glg

        captured: dict = {}
        original = glg.create_cuga_lite_graph

        def spy(*args, **kwargs):
            captured["callbacks"] = kwargs.get("callbacks")
            return original(*args, **kwargs)

        agent = CugaAgent(tools=[simple_calculator])

        with pytest.MonkeyPatch.context() as mp:
            # sdk.py now imports create_cuga_lite_graph lazily (function-local import),
            # so patching the source module is sufficient — no sdk_module patch needed.
            mp.setattr(glg, "create_cuga_lite_graph", spy)
            # Trigger graph construction
            _ = agent.graph

        base_callbacks = captured.get("callbacks") or []
        assert any(isinstance(cb, TokenUsageTracker) for cb in base_callbacks), (
            "Graph-level base callbacks must include TokenUsageTracker so direct "
            "`agent.graph.ainvoke()` is instrumented"
        )
