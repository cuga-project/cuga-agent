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

pytestmark = pytest.mark.unit


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
    async def test_returns_attributed_guidelines(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = json.dumps(
            {
                "text": "Use the preferred account name.",
                "entity_ids": ["guideline-a", "guideline-b"],
                "namespace_id": "tenant-a",
            }
        )

        result = await EvolveIntegration.get_guidelines_with_attribution(
            "draft a reply",
            user_id="user-a",
            namespace_id="tenant-a",
            session_id="thread-a",
        )

        assert result == {
            "text": "Use the preferred account name.",
            "entity_ids": ["guideline-a", "guideline-b"],
            "namespace_id": "tenant-a",
        }
        mock_call_tool.assert_called_once_with(
            "get_guidelines_with_attribution",
            {
                "task": "draft a reply",
                "user_id": "user-a",
                "namespace_id": "tenant-a",
                "session_id": "thread-a",
            },
        )


class TestUserFacts:
    """Test user fact memory behavior when disabled or on error."""

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.settings")
    async def test_store_user_facts_skips_when_disabled(self, mock_settings):
        mock_settings.evolve.enabled = False
        await EvolveIntegration.store_user_facts("user-123", "I prefer concise answers")

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_store_user_facts_calls_tool(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = {"stored_count": 1}

        await EvolveIntegration.store_user_facts(
            "user-123",
            "I prefer concise answers",
            metadata={"source": "cuga-lite"},
        )

        mock_call_tool.assert_called_once()
        call_name, payload = mock_call_tool.call_args.args
        assert call_name == "store_user_facts"
        assert payload["user_id"] == "user-123"
        assert payload["message"] == "I prefer concise answers"
        assert json.loads(payload["metadata"]) == {"source": "cuga-lite"}
        assert payload["enable_conflict_resolution"] is False

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.settings")
    async def test_retrieve_user_facts_returns_none_when_disabled(self, mock_settings):
        mock_settings.evolve.enabled = False
        result = await EvolveIntegration.retrieve_user_facts("user-123", "How should I answer?")
        assert result is None

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_retrieve_user_facts_returns_payload(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = {
            "user_id": "user-123",
            "matched_count": 1,
            "categories": {"style": [{"content": "Prefers concise answers"}]},
        }

        result = await EvolveIntegration.retrieve_user_facts("user-123", "How should I answer?")

        assert result is not None
        assert result["matched_count"] == 1
        mock_call_tool.assert_called_once_with(
            "retrieve_user_facts",
            {"user_id": "user-123", "query": "How should I answer?", "limit": 5},
        )

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_retrieve_user_facts_returns_none_on_error(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.side_effect = ConnectionError("Unable to connect")

        result = await EvolveIntegration.retrieve_user_facts("user-123", "How should I answer?")

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

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_passes_multi_user_params(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = "guideline text"
        result = await EvolveIntegration.get_guidelines(
            "test task", user_id="user-1", namespace_id="tenant-a", session_id="thread-99"
        )
        assert result == "guideline text"
        mock_call_tool.assert_called_once_with(
            "get_guidelines",
            {"task": "test task", "user_id": "user-1", "namespace_id": "tenant-a", "session_id": "thread-99"},
        )

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_omits_none_multi_user_params(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = "guideline text"
        await EvolveIntegration.get_guidelines("test task", user_id=None, namespace_id=None)
        mock_call_tool.assert_called_once_with("get_guidelines", {"task": "test task"})


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


class TestRecentMcpTools:
    """Test wrappers for the expanded altk-evolve MCP contract."""

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_get_entities_passes_retrieval_scope(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = "# Facts\n\n1. Prefers concise answers"

        result = await EvolveIntegration.get_entities(
            "response preferences",
            entity_type="fact",
            include_public=True,
            limit=25,
            user_id="user-1",
            namespace_id="tenant-a",
            session_id="thread-9",
        )

        assert result == "# Facts\n\n1. Prefers concise answers"
        mock_call_tool.assert_awaited_once_with(
            "get_entities",
            {
                "task": "response preferences",
                "entity_type": "fact",
                "include_public": True,
                "limit": 25,
                "user_id": "user-1",
                "namespace_id": "tenant-a",
                "session_id": "thread-9",
            },
        )

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_get_relevant_guidelines_passes_dosage(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = "Selected guidelines"

        result = await EvolveIntegration.get_relevant_guidelines(
            "prepare a report",
            top_k=4,
            core_support=3,
            namespace_id="tenant-a",
        )

        assert result == "Selected guidelines"
        mock_call_tool.assert_awaited_once_with(
            "get_relevant_guidelines",
            {
                "task": "prepare a report",
                "top_k": 4,
                "core_support": 3,
                "namespace_id": "tenant-a",
            },
        )

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_create_entity_returns_structured_result(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        created = {"event": "ADD", "id": "entity-7", "type": "fact"}
        mock_call_tool.return_value = created

        result = await EvolveIntegration.create_entity(
            "Prefers concise answers",
            "fact",
            metadata={"user_id": "user-1", "session_id": "thread-9"},
            owner_id="user-1",
            namespace_id="tenant-a",
        )

        assert result == created
        mock_call_tool.assert_awaited_once_with(
            "create_entity",
            {
                "content": "Prefers concise answers",
                "entity_type": "fact",
                "metadata": json.dumps({"user_id": "user-1", "session_id": "thread-9"}),
                "enable_conflict_resolution": False,
                "visibility": "private",
                "owner_id": "user-1",
                "namespace_id": "tenant-a",
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method_name,tool_name",
        [("publish_entity", "publish_entity"), ("unpublish_entity", "unpublish_entity")],
    )
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_changes_entity_visibility(
        self,
        mock_settings,
        mock_call_tool,
        method_name,
        tool_name,
    ):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.return_value = {"id": "entity-7", "metadata": {"visibility": "public"}}

        result = await getattr(EvolveIntegration, method_name)(
            "entity-7",
            user_id="user-1",
            namespace_id="tenant-a",
        )

        assert result["id"] == "entity-7"
        mock_call_tool.assert_awaited_once_with(
            tool_name,
            {"entity_id": "entity-7", "user_id": "user-1", "namespace_id": "tenant-a"},
        )

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_delete_entity_returns_mcp_result(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        deleted = {"success": True, "message": "Entity deleted"}
        mock_call_tool.return_value = deleted

        result = await EvolveIntegration.delete_entity(
            "entity-7",
            user_id="user-1",
            agent_id="agent-a",
            namespace_id="tenant-a",
        )

        assert result == deleted
        mock_call_tool.assert_awaited_once_with(
            "delete_entity",
            {
                "entity_id": "entity-7",
                "user_id": "user-1",
                "agent_id": "agent-a",
                "namespace_id": "tenant-a",
            },
        )

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_lists_structured_inventory(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        inventory = {"items": [{"id": "entity-7"}], "total": 1}
        mock_call_tool.return_value = inventory

        result = await EvolveIntegration.list_entities(
            entity_types=["fact"],
            user_id="user-1",
            agent_id="agent-a",
            metadata_filters={"legal_hold": True},
            limit=25,
            record_access=True,
            namespace_id="tenant-a",
        )

        assert result == inventory
        mock_call_tool.assert_awaited_once_with(
            "list_entities",
            {
                "limit": 25,
                "include_content": False,
                "record_access": True,
                "entity_types": ["fact"],
                "user_id": "user-1",
                "agent_id": "agent-a",
                "metadata_filters": json.dumps({"legal_hold": True}),
                "namespace_id": "tenant-a",
            },
        )

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_runs_retention_with_structured_policy(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        report = {"run_id": "run-1", "deleted": [], "flagged": []}
        mock_call_tool.return_value = report
        policy = {"rules": [{"name": "stale", "max_age_days": 90, "action": "flag"}]}

        result = await EvolveIntegration.run_retention(
            policy,
            dry_run=True,
            as_of="2026-07-24T12:00:00+00:00",
            run_id="run-1",
            namespace_id="tenant-a",
            metadata_filters={"agent_id": "agent-a"},
        )

        assert result == report
        mock_call_tool.assert_awaited_once_with(
            "run_retention",
            {
                "policy": json.dumps(policy),
                "dry_run": True,
                "as_of": "2026-07-24T12:00:00+00:00",
                "run_id": "run-1",
                "namespace_id": "tenant-a",
                "metadata_filters": json.dumps({"agent_id": "agent-a"}),
            },
        )

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_patches_metadata_and_records_access(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_call_tool.side_effect = [
            {"id": "entity-7", "metadata": {"legal_hold": True}},
            {"updated_ids": ["entity-7"]},
        ]

        patched = await EvolveIntegration.patch_entity_metadata(
            "entity-7",
            {"legal_hold": True},
            user_id="user-1",
            agent_id="agent-a",
            namespace_id="tenant-a",
        )
        accessed = await EvolveIntegration.record_access(
            ["entity-7"],
            user_id="user-1",
            agent_id="agent-a",
            namespace_id="tenant-a",
        )

        assert patched["metadata"]["legal_hold"] is True
        assert accessed["updated_ids"] == ["entity-7"]
        assert mock_call_tool.await_args_list[0].args == (
            "patch_entity_metadata",
            {
                "entity_id": "entity-7",
                "metadata_patch": json.dumps({"legal_hold": True}),
                "user_id": "user-1",
                "agent_id": "agent-a",
                "namespace_id": "tenant-a",
            },
        )
        assert mock_call_tool.await_args_list[1].args == (
            "record_access",
            {
                "entity_ids": ["entity-7"],
                "user_id": "user-1",
                "agent_id": "agent-a",
                "namespace_id": "tenant-a",
            },
        )


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
        saved = [{"id": "trajectory-1", "type": "trajectory"}]
        mock_call_tool.return_value = saved
        result = await EvolveIntegration.save_trajectory(
            messages,
            "task_1",
            success=True,
            owner_id="user-1",
            tools=[{"type": "function", "function": {"name": "get_users"}}],
        )
        assert result == saved
        mock_call_tool.assert_called_once()
        call_args = mock_call_tool.call_args[0]
        assert call_args[0] == "save_trajectory"
        payload = json.loads(call_args[1]["trajectory_data"])
        assert len(payload) == 2
        assert payload[0]["role"] == "user"
        assert payload[1]["role"] == "assistant"
        assert call_args[1]["owner_id"] == "user-1"
        assert json.loads(call_args[1]["tools"]) == [{"type": "function", "function": {"name": "get_users"}}]

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

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_passes_multi_user_params(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.evolve.save_on_success = True
        mock_settings.evolve.save_on_failure = True
        messages = [HumanMessage(content="Hello"), AIMessage(content="Hi")]
        await EvolveIntegration.save_trajectory(
            messages,
            "task_1",
            success=True,
            user_id="user-1",
            namespace_id="tenant-a",
            session_id="thread-99",
        )
        mock_call_tool.assert_called_once()
        call_args = mock_call_tool.call_args[0]
        assert call_args[0] == "save_trajectory"
        payload = call_args[1]
        assert payload["user_id"] == "user-1"
        assert payload["namespace_id"] == "tenant-a"
        assert payload["session_id"] == "thread-99"
        assert "trajectory_data" in payload
        assert payload["task_id"] == "task_1"

    @pytest.mark.asyncio
    @patch.object(EvolveIntegration, "_call_tool", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.settings")
    async def test_omits_none_multi_user_params(self, mock_settings, mock_call_tool):
        mock_settings.evolve.enabled = True
        mock_settings.evolve.lite_mode_only = False
        mock_settings.evolve.save_on_success = True
        mock_settings.evolve.save_on_failure = True
        messages = [HumanMessage(content="Hello"), AIMessage(content="Hi")]
        await EvolveIntegration.save_trajectory(messages, "task_1", success=True)
        mock_call_tool.assert_called_once()
        payload = mock_call_tool.call_args[0][1]
        assert "user_id" not in payload
        assert "namespace_id" not in payload
        assert "session_id" not in payload
