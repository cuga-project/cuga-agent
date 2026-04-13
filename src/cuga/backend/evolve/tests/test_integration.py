"""
Unit tests for EvolveIntegration module.

Tests the self-contained Evolve MCP client wrapper:
- is_enabled() logic with various config combinations
- _convert_messages() format conversion
- get_guidelines() / save_trajectory() behavior when disabled
- Graceful error handling on connection failures
"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from cuga.backend.evolve.integration import EvolveIntegration


class TestIsEnabled:
    """Test EvolveIntegration.is_enabled() with various config combinations."""

    @patch("cuga.backend.evolve.integration.settings")
    def test_disabled_when_evolve_not_enabled(self, mock_settings):
        mock_settings.evolve.enabled = False
        mock_settings.evolve.lite_mode_only = True
        mock_settings.advanced_features.lite_mode = True
        assert EvolveIntegration.is_enabled() is False

    @patch("cuga.backend.evolve.integration.settings")
    def test_enabled_when_evolve_enabled_and_lite_mode(self, mock_settings):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = True
        mock_settings.advanced_features.lite_mode = True
        assert EvolveIntegration.is_enabled() is True

    @patch("cuga.backend.evolve.integration.settings")
    def test_disabled_when_lite_mode_only_but_not_in_lite_mode(self, mock_settings):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = True
        mock_settings.advanced_features.lite_mode = False
        assert EvolveIntegration.is_enabled() is False

    @patch("cuga.backend.evolve.integration.settings")
    def test_enabled_when_lite_mode_only_is_false(self, mock_settings):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.advanced_features.lite_mode = False
        assert EvolveIntegration.is_enabled() is True


class TestConvertMessages:
    """Test message conversion from LangChain to OpenAI format."""

    def test_converts_human_and_ai_messages(self):
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there!"),
            HumanMessage(content="How are you?"),
        ]
        result = EvolveIntegration._convert_messages(messages)
        assert result == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]

    def test_skips_system_messages(self):
        messages = [
            SystemMessage(content="You are a helpful assistant"),
            HumanMessage(content="Hello"),
        ]
        result = EvolveIntegration._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_skips_empty_content(self):
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content=""),
            HumanMessage(content="World"),
        ]
        result = EvolveIntegration._convert_messages(messages)
        assert len(result) == 2

    def test_empty_message_list(self):
        result = EvolveIntegration._convert_messages([])
        assert result == []

    def test_handles_non_string_content(self):
        messages = [
            HumanMessage(content=[{"type": "text", "text": "Hello"}]),
        ]
        result = EvolveIntegration._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert isinstance(result[0]["content"], str)


class TestGetGuidelines:
    """Test get_guidelines behavior when disabled or on error."""

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.settings")
    async def test_returns_none_when_disabled(self, mock_settings):
        mock_settings.evolve.enabled = False
        result = await EvolveIntegration.get_guidelines("test task")
        assert result is None

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_returns_guidelines_when_available(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = "Use pagination when fetching data"
        result = await EvolveIntegration.get_guidelines("fetch all users")
        assert result == "Use pagination when fetching data"
        mock_call_tool.assert_called_once_with("get_guidelines", {"task": "fetch all users"})

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_returns_none_on_empty_result(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = None
        result = await EvolveIntegration.get_guidelines("test task")
        assert result is None

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_returns_none_on_error_gracefully(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.side_effect = ConnectionError("Unable to connect")
        result = await EvolveIntegration.get_guidelines("test task")
        assert result is None

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_returns_none_on_timeout(self, mock_settings, mock_call_tool):
        import asyncio

        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.side_effect = asyncio.TimeoutError("Operation timed out")
        result = await EvolveIntegration.get_guidelines("test task")
        assert result is None


class TestToolDispatch:
    """Test transport selection and fallback behavior."""

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool_direct", new_callable=AsyncMock)
    @patch.object(EvolveIntegration, "_call_tool_via_registry", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_auto_mode_prefers_registry(self, mock_settings, mock_registry_call, mock_direct_call):
        mock_settings.advanced_features.registry = True
        mock_settings.evolve.mode = "auto"
        mock_settings.evolve.timeout = 30.0
        mock_registry_call.return_value = "guideline"

        result = await EvolveIntegration._call_tool("get_guidelines", {"task": "demo"})

        assert result == "guideline"
        mock_registry_call.assert_called_once_with("get_guidelines", {"task": "demo"})
        mock_direct_call.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool_direct", new_callable=AsyncMock)
    @patch.object(EvolveIntegration, "_call_tool_via_registry", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_auto_mode_falls_back_to_direct(self, mock_settings, mock_registry_call, mock_direct_call):
        mock_settings.advanced_features.registry = True
        mock_settings.evolve.mode = "auto"
        mock_settings.evolve.timeout = 30.0
        mock_registry_call.side_effect = RuntimeError("registry unavailable")
        mock_direct_call.return_value = "guideline"

        result = await EvolveIntegration._call_tool("get_guidelines", {"task": "demo"})

        assert result == "guideline"
        mock_direct_call.assert_called_once_with("get_guidelines", {"task": "demo"})

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool_direct", new_callable=AsyncMock)
    @patch.object(EvolveIntegration, "_call_tool_via_registry", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_registry_mode_does_not_fallback(self, mock_settings, mock_registry_call, mock_direct_call):
        mock_settings.advanced_features.registry = True
        mock_settings.evolve.mode = "registry"
        mock_settings.evolve.timeout = 30.0
        mock_registry_call.side_effect = RuntimeError("registry unavailable")

        with pytest.raises(RuntimeError):
            await EvolveIntegration._call_tool("get_guidelines", {"task": "demo"})

        mock_direct_call.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_registry_has_app", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_registry_call_skips_when_app_missing(self, mock_settings, mock_registry_has_app):
        mock_settings.evolve.app_name = "evolve"
        mock_registry_has_app.return_value = False

        with pytest.raises(RuntimeError, match="not configured in the registry"):
            await EvolveIntegration._call_tool_via_registry("get_guidelines", {"task": "demo"})


class TestSaveTrajectory:
    """Test save_trajectory behavior with various config combinations."""

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.settings")
    async def test_skips_when_disabled(self, mock_settings):
        mock_settings.evolve.enabled = False
        await EvolveIntegration.save_trajectory([HumanMessage(content="test")], "task_1", True)

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_skips_when_save_on_success_false_and_success(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.evolve.save_on_success = False
        mock_settings.evolve.save_on_failure = True
        await EvolveIntegration.save_trajectory([HumanMessage(content="test")], "task_1", success=True)
        mock_call_tool.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_skips_when_save_on_failure_false_and_failure(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.evolve.save_on_success = True
        mock_settings.evolve.save_on_failure = False
        await EvolveIntegration.save_trajectory([HumanMessage(content="test")], "task_1", success=False)
        mock_call_tool.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_saves_on_success(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.evolve.save_on_success = True
        mock_settings.evolve.save_on_failure = True
        messages = [
            HumanMessage(content="Get users"),
            AIMessage(content="Here are the users"),
        ]
        await EvolveIntegration.save_trajectory(messages, "task_1", success=True)
        mock_call_tool.assert_called_once()
        call_args = mock_call_tool.call_args[0]
        assert call_args[0] == "save_trajectory"
        payload = json.loads(call_args[1]["trajectory_data"])
        assert len(payload) == 2
        assert payload[0]["role"] == "user"
        assert payload[1]["role"] == "assistant"

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_skips_empty_messages(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.evolve.save_on_success = True
        mock_settings.evolve.save_on_failure = True
        await EvolveIntegration.save_trajectory([SystemMessage(content="system")], "task_1", success=True)
        mock_call_tool.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_handles_error_gracefully(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.evolve.save_on_success = True
        mock_settings.evolve.save_on_failure = True
        mock_call_tool.side_effect = ConnectionError("Unable to connect")
        await EvolveIntegration.save_trajectory([HumanMessage(content="test")], "task_1", success=True)

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_handles_timeout_gracefully(self, mock_settings, mock_call_tool):
        import asyncio

        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.evolve.save_on_success = True
        mock_settings.evolve.save_on_failure = True
        mock_call_tool.side_effect = asyncio.TimeoutError("Operation timed out")
        await EvolveIntegration.save_trajectory([HumanMessage(content="test")], "task_1", success=True)
