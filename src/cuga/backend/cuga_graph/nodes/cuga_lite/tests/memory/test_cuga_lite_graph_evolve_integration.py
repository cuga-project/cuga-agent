"""
Integration tests for create_cuga_lite_graph evolve store/retrieve/prompt injection.

Covers the full flow inside prepare_tools_and_apps:
- store_user_facts is called with correct user_id and message
- retrieve_user_facts is called with correct user_id and query
- Retrieved preference categories are injected into special_instructions_final
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
    CugaLiteState,
    create_cuga_lite_graph,
)


def _make_stub_tool_provider():
    """Return a minimal async tool provider stub that satisfies prepare_tools_and_apps."""
    provider = MagicMock()
    provider.get_all_tools = AsyncMock(return_value=[])
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    return provider


def _make_prepare_node():
    """Return the prepare node runnable extracted from a minimal graph."""
    model = MagicMock()
    model.bind_tools = MagicMock(return_value=model)
    graph = create_cuga_lite_graph(model=model, tool_provider=_make_stub_tool_provider(), apps_list=[])
    return graph.nodes["prepare"].runnable


class TestEvolveStoreAndRetrieveCalledDuringPrepare:
    """Verify store/retrieve are called with correct arguments when evolve is enabled."""

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.get_guidelines", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.store_user_facts", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.retrieve_user_facts", new_callable=AsyncMock)
    async def test_store_user_facts_called_with_user_id_and_message(
        self, mock_retrieve, mock_store, mock_guidelines, mock_is_enabled
    ):
        mock_guidelines.return_value = None
        mock_store.return_value = None
        mock_retrieve.return_value = None

        state = CugaLiteState(
            user_id="test-user",
            chat_messages=[HumanMessage(content="I prefer concise answers")],
        )

        prepare_node = _make_prepare_node()
        await prepare_node.ainvoke(state)

        mock_store.assert_called_once()
        call_args = mock_store.call_args
        assert call_args.args[0] == "test-user"
        assert call_args.args[1] == "I prefer concise answers"

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.get_guidelines", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.store_user_facts", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.retrieve_user_facts", new_callable=AsyncMock)
    async def test_retrieve_user_facts_called_with_user_id_and_query(
        self, mock_retrieve, mock_store, mock_guidelines, mock_is_enabled
    ):
        mock_guidelines.return_value = None
        mock_store.return_value = None
        mock_retrieve.return_value = None

        state = CugaLiteState(
            user_id="test-user",
            chat_messages=[HumanMessage(content="fetch all contacts")],
        )

        prepare_node = _make_prepare_node()
        await prepare_node.ainvoke(state)

        mock_retrieve.assert_called_once()
        call_args = mock_retrieve.call_args
        assert call_args.args[0] == "test-user"
        assert call_args.args[1] == "fetch all contacts"

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.get_guidelines", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.store_user_facts", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.retrieve_user_facts", new_callable=AsyncMock)
    async def test_store_and_retrieve_skipped_when_no_user_id(
        self, mock_retrieve, mock_store, mock_guidelines, mock_is_enabled
    ):
        mock_guidelines.return_value = None
        mock_store.return_value = None
        mock_retrieve.return_value = None

        state = CugaLiteState(
            user_id="",
            chat_messages=[HumanMessage(content="fetch all contacts")],
        )

        prepare_node = _make_prepare_node()
        await prepare_node.ainvoke(state)

        mock_store.assert_not_called()
        mock_retrieve.assert_not_called()

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=False)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.store_user_facts", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.retrieve_user_facts", new_callable=AsyncMock)
    async def test_store_and_retrieve_skipped_when_evolve_disabled(
        self, mock_retrieve, mock_store, mock_is_enabled
    ):
        state = CugaLiteState(
            user_id="test-user",
            chat_messages=[HumanMessage(content="fetch all contacts")],
        )

        prepare_node = _make_prepare_node()
        await prepare_node.ainvoke(state)

        mock_store.assert_not_called()
        mock_retrieve.assert_not_called()


class TestEvolvePreferenceInjectedIntoSpecialInstructions:
    """Verify retrieved preferences are injected into prepared_prompt."""

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.get_guidelines", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.store_user_facts", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.retrieve_user_facts", new_callable=AsyncMock)
    async def test_preference_section_appears_in_prepared_prompt(
        self, mock_retrieve, mock_store, mock_guidelines, mock_is_enabled
    ):
        mock_guidelines.return_value = None
        mock_store.return_value = None
        mock_retrieve.return_value = {
            "user_id": "test-user",
            "matched_count": 1,
            "categories": {"style": [{"content": "Prefers concise answers"}]},
        }

        state = CugaLiteState(
            user_id="test-user",
            chat_messages=[HumanMessage(content="fetch all contacts")],
        )

        prepare_node = _make_prepare_node()
        result = await prepare_node.ainvoke(state)

        update = result.update if hasattr(result, "update") else {}
        prepared_prompt = update.get("prepared_prompt", "")
        assert "Evolve User Preference" in prepared_prompt
        assert "Prefers concise answers" in prepared_prompt

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.get_guidelines", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.store_user_facts", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.retrieve_user_facts", new_callable=AsyncMock)
    async def test_no_preference_section_when_retrieve_returns_none(
        self, mock_retrieve, mock_store, mock_guidelines, mock_is_enabled
    ):
        mock_guidelines.return_value = None
        mock_store.return_value = None
        mock_retrieve.return_value = None

        state = CugaLiteState(
            user_id="test-user",
            chat_messages=[HumanMessage(content="fetch all contacts")],
        )

        prepare_node = _make_prepare_node()
        result = await prepare_node.ainvoke(state)

        update = result.update if hasattr(result, "update") else {}
        prepared_prompt = update.get("prepared_prompt", "")
        assert "Evolve User Preference" not in prepared_prompt

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.get_guidelines", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.store_user_facts", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.retrieve_user_facts", new_callable=AsyncMock)
    async def test_no_preference_section_when_categories_empty(
        self, mock_retrieve, mock_store, mock_guidelines, mock_is_enabled
    ):
        mock_guidelines.return_value = None
        mock_store.return_value = None
        mock_retrieve.return_value = {"user_id": "test-user", "matched_count": 0, "categories": {}}

        state = CugaLiteState(
            user_id="test-user",
            chat_messages=[HumanMessage(content="fetch all contacts")],
        )

        prepare_node = _make_prepare_node()
        result = await prepare_node.ainvoke(state)

        update = result.update if hasattr(result, "update") else {}
        prepared_prompt = update.get("prepared_prompt", "")
        assert "Evolve User Preference" not in prepared_prompt


class TestEvolveStoreFireAndForgetCompatibility:
    """store_user_facts may be awaited or fire-and-forget — both must work."""

    @pytest.mark.asyncio
    @patch("cuga.backend.evolve.integration.EvolveIntegration.is_enabled", return_value=True)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.get_guidelines", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.store_user_facts", new_callable=AsyncMock)
    @patch("cuga.backend.evolve.integration.EvolveIntegration.retrieve_user_facts", new_callable=AsyncMock)
    async def test_store_called_as_async_mock(
        self, mock_retrieve, mock_store, mock_guidelines, mock_is_enabled
    ):
        """AsyncMock is compatible whether store is awaited or wrapped in create_task."""
        mock_guidelines.return_value = None
        mock_store.return_value = None
        mock_retrieve.return_value = None

        state = CugaLiteState(
            user_id="test-user",
            chat_messages=[HumanMessage(content="list all accounts")],
        )

        prepare_node = _make_prepare_node()
        await prepare_node.ainvoke(state)

        # Allow any pending tasks to complete (supports fire-and-forget path)
        await asyncio.sleep(0)

        mock_store.assert_called_once()
        assert mock_store.call_args.args[0] == "test-user"
