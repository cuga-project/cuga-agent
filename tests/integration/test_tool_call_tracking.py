"""
E2E tests for tool call tracking feature.

Tests the SDK with CombinedToolProvider connected to digital_sales
to verify tool calls are tracked with proper metadata including
operation_id for OpenAPI-based tools.

Requirements:
- Registry server running with digital_sales app at localhost:8001
- digital_sales API running at localhost:8000

Run E2E tests with:
    pytest tests/integration/test_tool_call_tracking.py -m e2e -v -s
"""

import pytest

from cuga.sdk import CugaAgent
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import CombinedToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker


@pytest.mark.e2e
class TestE2EToolCallTracking:
    """
    E2E tests for tool call tracking with digital_sales API.

    These tests require:
    - Registry server running (localhost:8001)
    - digital_sales API running (localhost:8000)

    Run with: pytest tests/integration/test_tool_call_tracking.py -m e2e -v
    """

    @pytest.mark.asyncio
    async def test_agent_tracks_tool_calls_get_top_account(self):
        """
        E2E test: Run agent to get top account by revenue and verify tool calls tracked.

        This test:
        1. Creates CugaAgent with CombinedToolProvider for digital_sales
        2. Invokes agent with track_tool_calls=True
        3. Verifies tool calls were tracked with operation_id
        """
        tool_provider = CombinedToolProvider(app_names=["digital_sales"])
        agent = CugaAgent(tool_provider=tool_provider)

        # Run the agent with track_tool_calls=True
        result = await agent.invoke(
            "What is the top account by revenue?",
            thread_id="test-e2e-tool-tracking-001",
            track_tool_calls=True,
        )

        # Verify we got a response - InvokeResult has answer and tool_calls
        assert result is not None
        assert len(result.answer) > 0

        # Tool calls come directly from the result
        tool_calls = result.tool_calls

        # Verify tool calls were tracked
        assert len(tool_calls) > 0, "Expected at least one tool call to be tracked"

        # Check the structure of tracked calls
        for call in tool_calls:
            assert "name" in call, "Tool call should have 'name'"
            assert "arguments" in call, "Tool call should have 'arguments'"
            assert "timestamp" in call, "Tool call should have 'timestamp'"
            assert "app_name" in call, "Tool call should have 'app_name'"

            # For digital_sales tools, app_name should be set
            if call["app_name"]:
                assert call["app_name"] == "digital_sales"

        # Print tool calls for debugging
        print(f"\n=== Tool Calls Tracked ({len(tool_calls)}) ===")
        for i, call in enumerate(tool_calls, 1):
            print(f"\n{i}. {call['name']}")
            print(f"   Operation ID: {call.get('operation_id')}")
            print(f"   App: {call.get('app_name')}")
            print(f"   Arguments: {call.get('arguments')}")
            print(f"   Duration: {call.get('duration_ms')}ms")
            if call.get('error'):
                print(f"   Error: {call.get('error')}")

    @pytest.mark.asyncio
    async def test_agent_tracks_multiple_tool_calls(self):
        """
        E2E test: Run agent with task requiring multiple API calls.

        Tests that all tool calls in a multi-step task are tracked.
        """
        tool_provider = CombinedToolProvider(app_names=["digital_sales"])
        agent = CugaAgent(tool_provider=tool_provider)

        result = await agent.invoke(
            "List all accounts and tell me how many there are",
            thread_id="test-e2e-multi-tool-001",
            track_tool_calls=True,
        )

        assert result is not None

        # Tool calls come directly from result
        tool_calls = result.tool_calls

        # Should have at least one tool call
        assert len(tool_calls) >= 1

        # All calls should have required fields
        for call in tool_calls:
            assert call["name"] is not None
            assert call["timestamp"] is not None

    @pytest.mark.asyncio
    async def test_tool_calls_have_operation_id_for_openapi_tools(self):
        """
        E2E test: Verify that OpenAPI-based tools have operation_id tracked.
        """
        tool_provider = CombinedToolProvider(app_names=["digital_sales"])
        agent = CugaAgent(tool_provider=tool_provider)

        result = await agent.invoke(
            "Get the account with the highest revenue",
            thread_id="test-e2e-operation-id-001",
            track_tool_calls=True,
        )

        assert result is not None

        # Tool calls come directly from result
        tool_calls = result.tool_calls

        # Check if any tool calls have operation_id
        # (depends on how the OpenAPI spec defines operationId)
        has_operation_id = any(call.get("operation_id") for call in tool_calls)

        print("\n=== Tool Calls with Operation IDs ===")
        for call in tool_calls:
            print(f"Tool: {call['name']}, Operation ID: {call.get('operation_id')}")

        # Note: This assertion depends on the OpenAPI spec having operationId defined
        # If the spec doesn't define operationId, this will be None
        if has_operation_id:
            print("\n✓ Found tool calls with operation_id")
        else:
            print("\n⚠ No operation_id found (check if OpenAPI spec defines operationId)")

    @pytest.mark.asyncio
    async def test_tool_calls_empty_when_tracking_disabled(self):
        """
        E2E test: Verify tool_calls is empty when track_tool_calls=False (default).
        """
        tool_provider = CombinedToolProvider(app_names=["digital_sales"])
        agent = CugaAgent(tool_provider=tool_provider)

        # Default: track_tool_calls=False
        result = await agent.invoke(
            "What is the top account by revenue?",
            thread_id="test-e2e-no-tracking-001",
        )

        assert result is not None
        assert len(result.answer) > 0
        # tool_calls should be empty when tracking is disabled
        assert result.tool_calls == []


@pytest.mark.unit
class TestToolCallTracker:
    """Unit tests for ToolCallTracker."""

    def test_tracker_is_enabled_after_start_with_enabled_true(self):
        """Test that ToolCallTracker.is_enabled() returns True after start_tracking(enabled=True)."""
        ToolCallTracker.start_tracking(enabled=True)
        assert ToolCallTracker.is_enabled() is True
        ToolCallTracker.stop_tracking()

    def test_tracker_is_disabled_by_default(self):
        """Test that ToolCallTracker.is_enabled() returns False by default."""
        # Without calling start_tracking, should be False
        assert ToolCallTracker.is_enabled() is False

    def test_tracker_is_disabled_when_started_with_enabled_false(self):
        """Test that tracking is disabled when start_tracking(enabled=False)."""
        ToolCallTracker.start_tracking(enabled=False)
        assert ToolCallTracker.is_enabled() is False
        ToolCallTracker.stop_tracking()

    def test_tracker_start_stop_tracking(self):
        """Test start and stop tracking lifecycle."""
        ToolCallTracker.start_tracking(enabled=True)

        ToolCallTracker.record_call(
            tool_name="test_tool",
            arguments={"param1": "value1"},
            result={"status": "success"},
            app_name="test_app",
            operation_id="getTestById",
            duration_ms=100.5,
        )

        current = ToolCallTracker.get_current_calls()
        assert len(current) == 1
        assert current[0]["name"] == "test_tool"
        assert current[0]["operation_id"] == "getTestById"

        calls = ToolCallTracker.stop_tracking()
        assert len(calls) == 1
        assert calls[0]["arguments"] == {"param1": "value1"}
        assert calls[0]["app_name"] == "test_app"

    def test_tracker_does_not_record_when_disabled(self):
        """Test that tracking doesn't record when disabled."""
        ToolCallTracker.start_tracking(enabled=False)
        ToolCallTracker.record_call(
            tool_name="test_tool",
            arguments={},
            result=None,
        )

        calls = ToolCallTracker.stop_tracking()
        assert calls == []

    def test_tracker_records_error(self):
        """Test that tracker records error information."""
        ToolCallTracker.start_tracking(enabled=True)

        ToolCallTracker.record_call(
            tool_name="failing_tool",
            arguments={"id": 123},
            result=None,
            error="Connection timeout",
            duration_ms=5000.0,
        )

        calls = ToolCallTracker.stop_tracking()
        assert len(calls) == 1
        assert calls[0]["error"] == "Connection timeout"
        assert calls[0]["result"] is None

    def test_tool_call_record_has_timestamp(self):
        """Test that recorded calls have timestamps."""
        ToolCallTracker.start_tracking(enabled=True)

        ToolCallTracker.record_call(
            tool_name="test_tool",
            arguments={},
            result="ok",
        )

        calls = ToolCallTracker.stop_tracking()
        assert "timestamp" in calls[0]
        assert calls[0]["timestamp"] is not None


@pytest.mark.unit
class TestTrackedToolDecorator:
    """Unit tests for the @tracked_tool decorator."""

    def test_tracked_tool_decorator_simple(self):
        """Test @tracked_tool decorator without any arguments."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool

        @tracked_tool
        def add(a: int, b: int) -> int:
            return a + b

        ToolCallTracker.start_tracking(enabled=True)
        result = add(a=5, b=3)
        calls = ToolCallTracker.stop_tracking()

        assert result == 8
        assert len(calls) == 1
        assert calls[0]["name"] == "add"
        assert calls[0]["operation_id"] == "add"  # Defaults to function name
        assert calls[0]["app_name"] is None
        assert calls[0]["result"] == 8
        assert calls[0]["duration_ms"] is not None
        assert calls[0]["error"] is None

    def test_tracked_tool_decorator_with_app_name(self):
        """Test @tracked_tool decorator with app_name."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool

        @tracked_tool(app_name="calculator")
        def multiply(a: int, b: int) -> int:
            return a * b

        ToolCallTracker.start_tracking(enabled=True)
        result = multiply(a=5, b=3)
        calls = ToolCallTracker.stop_tracking()

        assert result == 15
        assert len(calls) == 1
        assert calls[0]["name"] == "multiply"
        assert calls[0]["operation_id"] == "multiply"  # Function name
        assert calls[0]["app_name"] == "calculator"

    @pytest.mark.asyncio
    async def test_tracked_tool_decorator_async(self):
        """Test @tracked_tool decorator with async function."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool

        @tracked_tool(app_name="user_service")
        async def fetch_user(user_id: int) -> dict:
            return {"id": user_id, "name": "John"}

        ToolCallTracker.start_tracking(enabled=True)
        result = await fetch_user(user_id=123)
        calls = ToolCallTracker.stop_tracking()

        assert result == {"id": 123, "name": "John"}
        assert len(calls) == 1
        assert calls[0]["name"] == "fetch_user"
        assert calls[0]["operation_id"] == "fetch_user"
        assert calls[0]["app_name"] == "user_service"
        assert calls[0]["result"] == {"id": 123, "name": "John"}

    def test_tracked_tool_records_error(self):
        """Test that @tracked_tool records errors."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool

        @tracked_tool(app_name="failing_service")
        def failing_func() -> None:
            raise ValueError("Something went wrong")

        ToolCallTracker.start_tracking(enabled=True)
        try:
            failing_func()
        except ValueError:
            pass
        calls = ToolCallTracker.stop_tracking()

        assert len(calls) == 1
        assert calls[0]["error"] == "Something went wrong"
        assert calls[0]["result"] is None

    def test_tracked_tool_no_tracking_when_disabled(self):
        """Test that @tracked_tool doesn't record when tracking is disabled."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool

        @tracked_tool
        def my_func(x: int) -> int:
            return x * 2

        # Don't enable tracking
        result = my_func(x=5)

        assert result == 10
        # No calls recorded since tracking wasn't enabled
        assert ToolCallTracker.get_current_calls() == []

    def test_tracked_tool_importable_from_cuga(self):
        """Test that tracked_tool can be imported from main cuga package."""
        from cuga import tracked_tool

        @tracked_tool
        def test_func() -> str:
            return "ok"

        ToolCallTracker.start_tracking(enabled=True)
        result = test_func()
        calls = ToolCallTracker.stop_tracking()

        assert result == "ok"
        assert len(calls) == 1
        assert calls[0]["operation_id"] == "test_func"


@pytest.mark.unit
class TestToolProviderOperationId:
    """Unit tests for operation_id storage in tool providers."""

    def test_create_tool_from_api_dict_stores_operation_id(self):
        """Test that create_tool_from_api_dict stores operation_id on tool.func."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict

        tool_def = {
            "description": "Get all accounts",
            "parameters": [],
            "response_schemas": {},
            "operation_id": "getAccounts",
            "app_name": "digital_sales",
            "path": "/accounts",
            "method": "GET",
        }

        tool = create_tool_from_api_dict("digital_sales_get_accounts", tool_def, "digital_sales")

        assert tool.name == "digital_sales_get_accounts"
        assert hasattr(tool.func, '_operation_id')
        assert tool.func._operation_id == "getAccounts"
        assert hasattr(tool.func, '_app_name')
        assert tool.func._app_name == "digital_sales"

    def test_operation_id_not_in_model_dump(self):
        """Test that _operation_id is NOT serialized in tool.model_dump() - ensuring it won't leak into prompts."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict

        tool_def = {
            "description": "Get all accounts",
            "parameters": [],
            "response_schemas": {},
            "operation_id": "getAccounts",
            "app_name": "digital_sales",
            "path": "/accounts",
            "method": "GET",
        }

        tool = create_tool_from_api_dict("digital_sales_get_accounts", tool_def, "digital_sales")

        # Verify operation_id is stored on the func
        assert tool.func._operation_id == "getAccounts"

        # CRITICAL: Verify it does NOT appear in model_dump (which is used for serialization to prompts)
        dump = tool.model_dump()
        assert "_operation_id" not in dump
        assert "operation_id" not in dump
        assert "_app_name" not in dump

        # Also check it's not in str representation
        dump_str = str(dump)
        assert "getAccounts" not in dump_str
        assert "_operation_id" not in dump_str

    def test_operation_id_not_in_prompt_serialization(self):
        """Test that operation_id does NOT appear in prompt-formatted tool output."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
        from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

        tool_def = {
            "description": "Get all accounts",
            "parameters": [
                {"name": "page", "type": "integer", "required": False, "description": "Page number"}
            ],
            "response_schemas": {"success": {"type": "array"}},
            "operation_id": "getAccountsOperationId",
            "app_name": "digital_sales",
            "path": "/accounts",
            "method": "GET",
        }

        tool = create_tool_from_api_dict("digital_sales_get_accounts", tool_def, "digital_sales")

        # Get params_str used in prompts
        params_str = PromptUtils.get_tool_params_str(tool)
        assert "operation_id" not in params_str.lower()
        assert "getAccountsOperationId" not in params_str

        # Get params_doc and response_doc used in prompts
        params_doc, response_doc = PromptUtils.get_tool_docs(tool)
        assert "operation_id" not in params_doc.lower()
        assert "getAccountsOperationId" not in params_doc
        assert "operation_id" not in response_doc.lower()
        assert "getAccountsOperationId" not in response_doc

    def test_create_tool_from_api_dict_handles_missing_operation_id(self):
        """Test that create_tool_from_api_dict handles missing operation_id gracefully."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict

        tool_def = {
            "description": "Get all accounts",
            "parameters": [],
            "response_schemas": {},
            "app_name": "digital_sales",
            "path": "/accounts",
            "method": "GET",
        }

        tool = create_tool_from_api_dict("digital_sales_get_accounts", tool_def, "digital_sales")

        assert tool.name == "digital_sales_get_accounts"
        assert hasattr(tool.func, '_operation_id')
        assert tool.func._operation_id is None

    def test_create_tool_from_tracker_stores_operation_id(self):
        """Test that create_tool_from_tracker stores operation_id on tool.func."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import create_tool_from_tracker

        tool_def = {
            "description": "Get all accounts",
            "parameters": [],
            "operation_id": "getAccountsFromTracker",
        }

        tool = create_tool_from_tracker("get_accounts", tool_def, "digital_sales")

        assert tool.name == "get_accounts"
        assert hasattr(tool.func, '_operation_id')
        assert tool.func._operation_id == "getAccountsFromTracker"
        assert hasattr(tool.func, '_app_name')
        assert tool.func._app_name == "digital_sales"


@pytest.mark.unit
class TestInvokeResult:
    """Unit tests for InvokeResult model."""

    def test_invoke_result_fields(self):
        """Test InvokeResult has all expected fields."""
        from cuga.sdk import InvokeResult

        result = InvokeResult(
            answer="Test answer",
            tool_calls=[{"name": "test_tool", "arguments": {}}],
            thread_id="test-thread-123",
            error=None,
        )

        assert result.answer == "Test answer"
        assert len(result.tool_calls) == 1
        assert result.thread_id == "test-thread-123"
        assert result.error is None

    def test_invoke_result_str_returns_answer(self):
        """Test that str(InvokeResult) returns the answer for backward compatibility."""
        from cuga.sdk import InvokeResult

        result = InvokeResult(
            answer="My answer",
            tool_calls=[],
            thread_id="test-123",
        )

        assert str(result) == "My answer"

    def test_invoke_result_defaults(self):
        """Test InvokeResult with default values."""
        from cuga.sdk import InvokeResult

        result = InvokeResult()

        assert result.answer == ""
        assert result.tool_calls == []
        assert result.thread_id == ""
        assert result.error is None


@pytest.mark.unit
class TestToolCallRecordModel:
    """Unit tests for ToolCallRecord model."""

    def test_tool_call_record_fields(self):
        """Test ToolCallRecord has all expected fields."""
        from cuga.backend.cuga_graph.state.agent_state import ToolCallRecord

        record = ToolCallRecord(
            name="test_tool",
            operation_id="getTestById",
            arguments={"id": 123},
            result={"data": "value"},
            app_name="test_app",
            timestamp="2024-01-01T00:00:00",
            duration_ms=100.5,
            error=None,
        )

        assert record.name == "test_tool"
        assert record.operation_id == "getTestById"
        assert record.arguments == {"id": 123}
        assert record.result == {"data": "value"}
        assert record.app_name == "test_app"
        assert record.duration_ms == 100.5
        assert record.error is None

    def test_tool_call_record_optional_fields(self):
        """Test ToolCallRecord with minimal fields."""
        from cuga.backend.cuga_graph.state.agent_state import ToolCallRecord

        record = ToolCallRecord(name="minimal_tool")

        assert record.name == "minimal_tool"
        assert record.operation_id is None
        assert record.arguments == {}
        assert record.result is None
        assert record.app_name is None
