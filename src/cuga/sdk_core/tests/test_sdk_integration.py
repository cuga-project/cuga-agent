"""
Integration tests for CUGA SDK

These tests actually run the agent with real LLM calls and tool execution.
They test the full SDK functionality end-to-end.
"""

import pytest
from langchain_core.tools import tool

from cuga import CugaAgent, run_agent
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import (
    ToolProviderInterface,
    AppDefinition,
)
from cuga.backend.llm.models import LLMManager


# Test tools
@tool
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b


@tool
def multiply_numbers(a: int, b: int) -> int:
    """Multiply two numbers together"""
    return a * b


@tool
def get_greeting(name: str) -> str:
    """Get a greeting for a person"""
    return f"Hello, {name}!"


@tool
def get_user_count() -> int:
    """Get the number of users in the system"""
    return 150


class TestToolProvider(ToolProviderInterface):
    """Test tool provider for integration tests"""

    def __init__(self, tools):
        self.tools = tools
        self.initialized = False

    async def initialize(self):
        self.initialized = True

    async def get_apps(self):
        return [
            AppDefinition(
                name="test_app",
                description="Test application with math and greeting tools",
                type="api",
            )
        ]

    async def get_tools(self, app_name: str):
        if app_name == "test_app":
            return self.tools
        return []

    async def get_all_tools(self):
        return self.tools


class TestSDKInvokeIntegration:
    """Integration tests for CugaAgent.invoke()"""

    @pytest.mark.asyncio
    async def test_invoke_with_direct_tools_simple_math(self):
        """Test invoke with direct tools - simple math operation"""
        agent = CugaAgent(tools=[add_numbers, multiply_numbers])

        result = await agent.invoke("What is 10 + 5?")

        # Verify result is InvokeResult with answer
        assert result is not None
        assert len(result.answer) > 0
        # The agent should use the tool and return 15
        assert "15" in result.answer

    @pytest.mark.asyncio
    async def test_invoke_with_direct_tools_greeting(self):
        """Test invoke with direct tools - random hex tool to ensure uniqueness"""
        import os
        import uuid
        from cuga.config import settings

        # Disable policies for this test to avoid output reformatting interference
        original_policy_enabled = os.environ.get("DYNACONF_POLICY__ENABLED")
        os.environ["DYNACONF_POLICY__ENABLED"] = "false"
        settings.reload()

        unique_id = str(uuid.uuid4())[:8]

        @tool
        def get_secret_code(name: str) -> str:
            """Get a unique secret code for a person."""
            return f"CODE-{name}-{unique_id}"

        try:
            agent = CugaAgent(tools=[get_secret_code])
            # Use a more explicit prompt to reduce LLM chattiness
            query = "Call the get_secret_code tool for 'Alice' and return the exact code provided."
            result = await agent.invoke(query, track_tool_calls=True)

            assert result is not None

            # Check if the unique code is in the answer
            expected_code = f"CODE-Alice-{unique_id}"
            has_code = expected_code.lower() in result.answer.lower()

            # Fallback: verify the tool was at least called correctly with the unique data
            tool_called_correctly = any(
                (
                    tc.get("name") == "get_secret_code"
                    or tc.get("function", {}).get("name") == "get_secret_code"
                )
                and "Alice" in str(tc.get("args") or tc.get("function", {}).get("arguments") or "")
                for tc in result.tool_calls
            )

            assert has_code or tool_called_correctly
        finally:
            # Restore policy setting
            if original_policy_enabled is not None:
                os.environ["DYNACONF_POLICY__ENABLED"] = original_policy_enabled
            else:
                os.environ.pop("DYNACONF_POLICY__ENABLED", None)
            settings.reload()

    @pytest.mark.asyncio
    async def test_invoke_with_thread_id(self):
        """Test invoke with thread_id for E2B caching"""
        agent = CugaAgent(tools=[multiply_numbers])

        result = await agent.invoke("What is 7 * 8?", thread_id="test-thread-123")

        # Verify result
        assert result is not None
        assert "56" in result.answer
        assert result.thread_id == "test-thread-123"

    @pytest.mark.asyncio
    async def test_invoke_with_tool_provider(self):
        """Test invoke with custom tool provider"""
        tool_provider = TestToolProvider(tools=[add_numbers, get_user_count])
        agent = CugaAgent(tool_provider=tool_provider)

        result = await agent.invoke("How many users are in the system?")

        # Verify result contains user count
        assert result is not None
        assert "150" in result.answer

    @pytest.mark.asyncio
    async def test_invoke_multi_step_task(self):
        """Test invoke with task requiring multiple tool calls"""
        agent = CugaAgent(tools=[add_numbers, multiply_numbers])

        result = await agent.invoke("Calculate (10 + 5) * 3")

        # Verify result contains 45
        assert result is not None
        assert "45" in result.answer

    @pytest.mark.asyncio
    async def test_invoke_with_tool_tracking(self):
        """Test invoke with track_tool_calls=True returns tool call metadata"""
        agent = CugaAgent(tools=[add_numbers])

        result = await agent.invoke("What is 10 + 5?", track_tool_calls=True)

        # Verify result is InvokeResult with answer and tool_calls
        assert result is not None
        assert "15" in result.answer
        # Tool calls should be tracked when enabled
        assert isinstance(result.tool_calls, list)

    @pytest.mark.asyncio
    async def test_invoke_result_str_compatibility(self):
        """Test that InvokeResult converts to string for backward compatibility"""
        agent = CugaAgent(tools=[add_numbers])

        result = await agent.invoke("What is 3 + 4?")

        # str(result) should return the answer
        assert str(result) == result.answer
        assert "7" in str(result)


class TestSDKStreamIntegration:
    """Integration tests for CugaAgent.stream()"""

    @pytest.mark.asyncio
    async def test_stream_with_direct_tools(self):
        """Test streaming with direct tools"""
        agent = CugaAgent(tools=[add_numbers])

        states = []
        async for state in agent.stream("What is 20 + 22?"):
            states.append(state)

        # Verify we got multiple state updates
        assert len(states) > 0

        # Check that we have different node outputs (handle new tuple format)
        node_names = set()
        for state in states:
            if isinstance(state, tuple) and len(state) == 2:
                _, updates_dict = state
                node_names.update(updates_dict.keys())
            elif isinstance(state, dict):
                node_names.update(state.keys())

        # Should have prepare, call_model, and possibly sandbox nodes
        assert len(node_names) > 0

        # Find the final state with answer
        final_answer = None
        for state in states:
            if isinstance(state, tuple) and len(state) == 2:
                _, updates_dict = state
                for node_name, node_state in updates_dict.items():
                    if isinstance(node_state, dict) and "final_answer" in node_state:
                        final_answer = node_state["final_answer"]
            elif isinstance(state, dict):
                for node_state in state.values():
                    if isinstance(node_state, dict) and "final_answer" in node_state:
                        final_answer = node_state["final_answer"]

        # Verify final answer contains result
        assert final_answer is not None
        assert "42" in final_answer

    @pytest.mark.asyncio
    async def test_stream_observes_code_execution(self):
        """Test that streaming allows observing code execution"""
        agent = CugaAgent(tools=[multiply_numbers])

        code_blocks = []
        async for state in agent.stream("Calculate 6 * 7"):
            # With stream_mode="updates" and subgraphs=True, format is (namespace_tuple, updates_dict)
            if isinstance(state, tuple) and len(state) == 2:
                _, updates_dict = state
                for node_name, node_state in updates_dict.items():
                    if isinstance(node_state, dict) and "script" in node_state:
                        if node_state["script"]:
                            code_blocks.append(node_state["script"])
            # Fallback for old format (dict)
            elif isinstance(state, dict):
                for node_state in state.values():
                    if isinstance(node_state, dict) and "script" in node_state:
                        if node_state["script"]:
                            code_blocks.append(node_state["script"])

        # Verify we observed code being generated
        assert len(code_blocks) > 0
        # Code should contain print statement (CUGA requirement)
        assert any("print" in code.lower() for code in code_blocks)


class TestSDKModelConfiguration:
    """Integration tests for model configuration"""

    @pytest.mark.asyncio
    async def test_default_model_from_llm_manager(self):
        """Test that agent uses default model from LLMManager"""
        agent = CugaAgent(tools=[add_numbers])

        # Verify model is set
        assert agent._model is not None

        # Invoke to ensure it works
        result = await agent.invoke("What is 3 + 4?")
        assert result is not None
        assert "7" in result.answer

    @pytest.mark.asyncio
    async def test_custom_model(self):
        """Test using custom model configuration"""
        from cuga.config import settings

        llm_manager = LLMManager()
        custom_model = llm_manager.get_model(settings.agent.code.model)

        agent = CugaAgent(tools=[add_numbers], model=custom_model)

        # Verify custom model is used
        assert agent._model == custom_model

        # Invoke to ensure it works
        result = await agent.invoke("What is 5 + 6?")
        assert result is not None
        assert "11" in result.answer


class TestSDKToolManagement:
    """Integration tests for dynamic tool management"""

    @pytest.mark.asyncio
    async def test_add_tool_dynamically(self):
        """Test adding tools after agent creation"""
        agent = CugaAgent(tools=[add_numbers])

        # First invocation with only add_numbers
        result1 = await agent.invoke("What is 8 + 9?")
        assert "17" in result1.answer

        # Add multiply tool
        agent.add_tool(multiply_numbers)

        # Second invocation can now use multiply
        result2 = await agent.invoke("What is 4 * 5?")
        assert "20" in result2.answer

    @pytest.mark.asyncio
    async def test_add_multiple_tools_dynamically(self):
        """Test adding multiple tools at once"""
        agent = CugaAgent(tools=[])

        # Add multiple tools
        agent.add_tools([add_numbers, multiply_numbers, get_greeting])

        # Verify all tools work
        result = await agent.invoke("Add 10 and 5")
        assert "15" in result.answer


class TestSDKHelperFunctions:
    """Integration tests for SDK helper functions"""

    @pytest.mark.asyncio
    async def test_run_agent_convenience_function(self):
        """Test run_agent convenience function"""
        result = await run_agent("What is 12 + 13?", tools=[add_numbers])

        # Verify result - run_agent returns InvokeResult
        assert result is not None
        assert "25" in result.answer


class TestSDKToolProvider:
    """Integration tests for custom tool provider"""

    @pytest.mark.asyncio
    async def test_tool_provider_initialization(self):
        """Test that tool provider is properly initialized"""
        tool_provider = TestToolProvider(tools=[add_numbers, get_greeting])
        agent = CugaAgent(tool_provider=tool_provider)

        # Tool provider should be initialized when agent is used
        result = await agent.invoke("What is 2 + 3?")

        assert tool_provider.initialized
        assert result is not None
        assert "5" in result.answer

    @pytest.mark.asyncio
    async def test_tool_provider_with_multiple_tools(self):
        """Test tool provider with multiple tools"""
        tool_provider = TestToolProvider(tools=[add_numbers, multiply_numbers, get_greeting, get_user_count])
        agent = CugaAgent(tool_provider=tool_provider)

        # Test different tools
        result1 = await agent.invoke("How many users?")
        assert "150" in result1.answer

        result2 = await agent.invoke("Greet Bob")
        assert "Bob" in result2.answer or "bob" in result2.answer.lower()


class TestSDKErrorHandling:
    """Integration tests for error handling"""

    @pytest.mark.asyncio
    async def test_agent_with_no_tools(self):
        """Test agent behavior with no tools"""
        agent = CugaAgent(tools=[])

        # Should still work for simple questions
        result = await agent.invoke("What is 2 + 2?")

        # Agent should respond even without tools
        assert result is not None
        assert len(result.answer) > 0

    @pytest.mark.asyncio
    async def test_agent_with_unavailable_tool(self):
        """Test agent when asked to use unavailable tool"""
        agent = CugaAgent(tools=[add_numbers])

        # Ask for something that requires a tool we don't have
        result = await agent.invoke("What is the weather today?")

        # Agent should respond that it can't do this
        assert result is not None
        assert len(result.answer) > 0
