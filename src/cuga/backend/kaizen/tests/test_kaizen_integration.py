"""
Unit tests for KaizenIntegration module.

Tests the self-contained Kaizen MCP client wrapper:
- is_enabled() logic with various config combinations
- _convert_messages() format conversion
- get_guidelines() / save_trajectory() behavior when disabled
- Graceful error handling on connection failures
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from cuga.backend.kaizen.kaizen_integration import KaizenIntegration


# ─────────────────────────────────────────────────────────────
# is_enabled()
# ─────────────────────────────────────────────────────────────


class TestIsEnabled:
    """Test KaizenIntegration.is_enabled() with various config combinations."""

    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    def test_disabled_when_kaizen_not_enabled(self, mock_settings):
        mock_settings.kaizen.enabled = False
        mock_settings.kaizen.lite_mode_only = True
        mock_settings.advanced_features.lite_mode = True
        assert KaizenIntegration.is_enabled() is False

    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    def test_enabled_when_kaizen_enabled_and_lite_mode(self, mock_settings):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = True
        mock_settings.advanced_features.lite_mode = True
        assert KaizenIntegration.is_enabled() is True

    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    def test_disabled_when_lite_mode_only_but_not_in_lite_mode(self, mock_settings):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = True
        mock_settings.advanced_features.lite_mode = False
        assert KaizenIntegration.is_enabled() is False

    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    def test_enabled_when_lite_mode_only_is_false(self, mock_settings):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_settings.advanced_features.lite_mode = False
        assert KaizenIntegration.is_enabled() is True


# ─────────────────────────────────────────────────────────────
# _convert_messages()
# ─────────────────────────────────────────────────────────────


class TestConvertMessages:
    """Test message conversion from LangChain to OpenAI format."""

    def test_converts_human_and_ai_messages(self):
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there!"),
            HumanMessage(content="How are you?"),
        ]
        result = KaizenIntegration._convert_messages(messages)
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
        result = KaizenIntegration._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_skips_empty_content(self):
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content=""),
            HumanMessage(content="World"),
        ]
        result = KaizenIntegration._convert_messages(messages)
        assert len(result) == 2

    def test_empty_message_list(self):
        result = KaizenIntegration._convert_messages([])
        assert result == []

    def test_handles_non_string_content(self):
        """Content that is a list (multi-modal) gets stringified."""
        messages = [
            HumanMessage(content=[{"type": "text", "text": "Hello"}]),
        ]
        result = KaizenIntegration._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert isinstance(result[0]["content"], str)


# ─────────────────────────────────────────────────────────────
# get_guidelines() when disabled
# ─────────────────────────────────────────────────────────────


class TestGetGuidelines:
    """Test get_guidelines behavior when disabled or on error."""

    @pytest.mark.asyncio
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_returns_none_when_disabled(self, mock_settings):
        mock_settings.kaizen.enabled = False
        result = await KaizenIntegration.get_guidelines("test task")
        assert result is None

    @pytest.mark.asyncio
    @patch.object(KaizenIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_returns_guidelines_when_available(self, mock_settings, mock_call_tool):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_call_tool.return_value = "Use pagination when fetching data"
        result = await KaizenIntegration.get_guidelines("fetch all users")
        assert result == "Use pagination when fetching data"
        mock_call_tool.assert_called_once_with("get_guidelines", {"task": "fetch all users"})

    @pytest.mark.asyncio
    @patch.object(KaizenIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_returns_none_on_empty_result(self, mock_settings, mock_call_tool):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_call_tool.return_value = None
        result = await KaizenIntegration.get_guidelines("test task")
        assert result is None

    @pytest.mark.asyncio
    @patch.object(KaizenIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_returns_none_on_error_gracefully(self, mock_settings, mock_call_tool):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_call_tool.side_effect = ConnectionError("Unable to connect")
        result = await KaizenIntegration.get_guidelines("test task")
        assert result is None  # Should not raise


# ─────────────────────────────────────────────────────────────
# save_trajectory() when disabled / conditional
# ─────────────────────────────────────────────────────────────


class TestSaveTrajectory:
    """Test save_trajectory behavior with various config combinations."""

    @pytest.mark.asyncio
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_skips_when_disabled(self, mock_settings):
        mock_settings.kaizen.enabled = False
        # Should not raise or make any calls
        await KaizenIntegration.save_trajectory(
            [HumanMessage(content="test")], "task_1", True
        )

    @pytest.mark.asyncio
    @patch.object(KaizenIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_skips_when_save_on_success_false_and_success(self, mock_settings, mock_call_tool):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_settings.kaizen.save_on_success = False
        mock_settings.kaizen.save_on_failure = True
        await KaizenIntegration.save_trajectory(
            [HumanMessage(content="test")], "task_1", success=True
        )
        mock_call_tool.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(KaizenIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_skips_when_save_on_failure_false_and_failure(self, mock_settings, mock_call_tool):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_settings.kaizen.save_on_success = True
        mock_settings.kaizen.save_on_failure = False
        await KaizenIntegration.save_trajectory(
            [HumanMessage(content="test")], "task_1", success=False
        )
        mock_call_tool.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(KaizenIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_saves_on_success(self, mock_settings, mock_call_tool):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_settings.kaizen.save_on_success = True
        mock_settings.kaizen.save_on_failure = True
        messages = [
            HumanMessage(content="Get users"),
            AIMessage(content="Here are the users"),
        ]
        await KaizenIntegration.save_trajectory(messages, "task_1", success=True)
        mock_call_tool.assert_called_once()
        call_args = mock_call_tool.call_args[0]
        assert call_args[0] == "save_trajectory"
        payload = json.loads(call_args[1]["trajectory_data"])
        assert len(payload) == 2
        assert payload[0]["role"] == "user"
        assert payload[1]["role"] == "assistant"

    @pytest.mark.asyncio
    @patch.object(KaizenIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_skips_empty_messages(self, mock_settings, mock_call_tool):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_settings.kaizen.save_on_success = True
        mock_settings.kaizen.save_on_failure = True
        # Only SystemMessage -> results in empty converted list
        await KaizenIntegration.save_trajectory(
            [SystemMessage(content="system")], "task_1", success=True
        )
        mock_call_tool.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(KaizenIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.kaizen.kaizen_integration.settings")
    async def test_handles_error_gracefully(self, mock_settings, mock_call_tool):
        mock_settings.kaizen.enabled = True
        mock_settings.kaizen.lite_mode_only = False
        mock_settings.kaizen.save_on_success = True
        mock_settings.kaizen.save_on_failure = True
        mock_call_tool.side_effect = ConnectionError("Unable to connect")
        # Should not raise
        await KaizenIntegration.save_trajectory(
            [HumanMessage(content="test")], "task_1", success=True
        )
