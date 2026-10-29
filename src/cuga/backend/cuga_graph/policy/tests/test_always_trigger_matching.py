"""Unit tests: AlwaysTrigger output formatters must be matched by match_policy (issue #805)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.cuga_graph.policy.agent import PolicyAgent, PolicyConflictResolution, PolicyContext
from cuga.backend.cuga_graph.policy.models import (
    AlwaysTrigger,
    AppTrigger,
    KeywordTrigger,
    NaturalLanguageTrigger,
    OutputFormatter,
    PolicyType,
)
from cuga.backend.cuga_graph.policy.storage import PolicyStorage
from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage


def _formatter(policy_id: str, triggers, priority: int = 50) -> OutputFormatter:
    return OutputFormatter(
        id=policy_id,
        name=f"Formatter {policy_id}",
        description="Format every response",
        triggers=triggers,
        format_type="direct",
        format_config="FORMATTED",
        priority=priority,
        enabled=True,
    )


def _nl_formatter(policy_id: str, priority: int = 10) -> OutputFormatter:
    return _formatter(
        policy_id,
        [NaturalLanguageTrigger(value=["weather report"], target="agent_response", threshold=0.5)],
        priority=priority,
    )


def _conflict_chain(matched_policy_index, confidence: float = 0.9):
    chain = MagicMock()
    chain.ainvoke = AsyncMock(
        return_value=PolicyConflictResolution(
            matched_policy_index=matched_policy_index,
            confidence=confidence,
            reasoning="stubbed conflict resolution",
        )
    )
    return chain


async def _match_formatter(agent: PolicyAgent):
    return await agent.match_policy(
        _context(), target="agent_response", policy_types=[PolicyType.OUTPUT_FORMATTER]
    )


def _context() -> PolicyContext:
    return PolicyContext(
        user_input="what is the weather",
        chat_messages=[],
        sub_task="",
        agent_response="It is sunny today.",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_always_trigger_formatter_is_matched_without_llm():
    storage = PolicyStorage(collection_name="test_always_trigger_match")
    await storage.initialize_async()
    try:
        await storage.add_policy(_formatter("of_always", [AlwaysTrigger()]))
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)

        with patch("cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain") as get_chain:
            match = await agent.match_policy(
                _context(), target="agent_response", policy_types=[PolicyType.OUTPUT_FORMATTER]
            )
            get_chain.assert_not_called()

        assert match.matched is True
        assert match.policy is not None and match.policy.id == "of_always"
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_keyword_formatter_beats_always_formatter():
    """A specific keyword match must take precedence over a catch-all formatter."""
    storage = PolicyStorage(collection_name="test_always_trigger_precedence")
    await storage.initialize_async()
    try:
        await storage.add_policy(_formatter("of_always", [AlwaysTrigger()], priority=100))
        await storage.add_policy(
            _formatter(
                "of_keyword",
                [KeywordTrigger(value=["sunny"], target="agent_response", operator="or")],
                priority=10,
            )
        )
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)

        match = await agent.match_policy(
            _context(), target="agent_response", policy_types=[PolicyType.OUTPUT_FORMATTER]
        )

        assert match.matched is True
        assert match.policy.id == "of_keyword"
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_always_formatter_not_matched_without_policy_types():
    """Default (policy_types=None) matching still excludes output formatters."""
    storage = PolicyStorage(collection_name="test_always_trigger_default_exclusion")
    await storage.initialize_async()
    try:
        await storage.add_policy(_formatter("of_always", [AlwaysTrigger()]))
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)

        match = await agent.match_policy(_context(), target="agent_response")

        assert match.matched is False
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_highest_priority_always_formatter_wins():
    storage = PolicyStorage(collection_name="test_always_trigger_priority")
    await storage.initialize_async()
    try:
        await storage.add_policy(_formatter("of_low", [AlwaysTrigger()], priority=10))
        await storage.add_policy(_formatter("of_high", [AlwaysTrigger()], priority=90))
        await storage.add_policy(_formatter("of_mid", [AlwaysTrigger()], priority=50))
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)

        match = await _match_formatter(agent)

        assert match.matched is True
        assert match.policy.id == "of_high"
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_always_formatter_priority_tie_broken_by_id():
    """Equal priority must not depend on storage order: lowest id wins."""
    storage = PolicyStorage(collection_name="test_always_trigger_tie")
    await storage.initialize_async()
    try:
        await storage.add_policy(_formatter("of_b", [AlwaysTrigger()], priority=50))
        await storage.add_policy(_formatter("of_a", [AlwaysTrigger()], priority=50))
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)

        match = await _match_formatter(agent)

        assert match.matched is True
        assert match.policy.id == "of_a"
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nl_formatter_match_beats_always_formatter():
    """An NL-matched formatter takes precedence over a higher-priority catch-all."""
    storage = PolicyStorage(collection_name="test_always_trigger_nl_hit")
    await storage.initialize_async()
    try:
        await storage.add_policy(_formatter("of_always", [AlwaysTrigger()], priority=100))
        await storage.add_policy(_nl_formatter("of_nl", priority=10))
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=_conflict_chain(matched_policy_index=1),
        ) as get_chain:
            match = await _match_formatter(agent)
            get_chain.assert_called_once()

        assert match.matched is True
        assert match.policy.id == "of_nl"
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nl_formatter_miss_falls_back_to_always_formatter():
    """When the LLM rejects every NL formatter, the catch-all still applies."""
    storage = PolicyStorage(collection_name="test_always_trigger_nl_miss")
    await storage.initialize_async()
    try:
        await storage.add_policy(_formatter("of_always", [AlwaysTrigger()], priority=10))
        await storage.add_policy(_nl_formatter("of_nl", priority=100))
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=_conflict_chain(matched_policy_index=None, confidence=0.1),
        ) as get_chain:
            match = await _match_formatter(agent)
            get_chain.assert_called_once()

        assert match.matched is True
        assert match.policy.id == "of_always"
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_always_formatter_respects_additional_app_trigger():
    """Non-NL triggers alongside AlwaysTrigger are still enforced."""
    storage = PolicyStorage(collection_name="test_always_trigger_app")
    await storage.initialize_async()
    try:
        await storage.add_policy(_formatter("of_app", [AlwaysTrigger(), AppTrigger(value="crm")]))
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)

        miss = await agent.match_policy(
            _context(), target="agent_response", policy_types=[PolicyType.OUTPUT_FORMATTER]
        )
        assert miss.matched is False

        ctx = _context()
        ctx.active_apps = ["crm"]
        hit = await agent.match_policy(
            ctx, target="agent_response", policy_types=[PolicyType.OUTPUT_FORMATTER]
        )
        assert hit.matched is True
        assert hit.policy.id == "of_app"
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_keyword_trigger_output_formatter_normalized_to_always():
    """A new UI formatter starts with a keyword trigger with value=[]; that means 'always'."""
    storage = PolicyStorage(collection_name="test_always_trigger_normalize_kw")
    await storage.initialize_async()
    try:
        result = await apply_policies_data_to_storage(
            storage,
            [
                {
                    "id": "of_empty_kw",
                    "name": "Empty KW",
                    "description": "Format everything",
                    "policy_type": "output_formatter",
                    "triggers": [
                        {
                            "type": "keyword",
                            "value": [],
                            "target": "agent_response",
                            "case_sensitive": False,
                            "operator": "and",
                        }
                    ],
                    "format_type": "direct",
                    "format_config": "FORMATTED",
                }
            ],
        )
        assert not result.get("errors"), result
        stored = await storage.get_policy("of_empty_kw")
        assert [t.type for t in stored.triggers] == ["always"]
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_triggers_output_formatter_normalized_to_always():
    """UI sends triggers=[] when all keywords are cleared; it must be stored as AlwaysTrigger."""
    storage = PolicyStorage(collection_name="test_always_trigger_normalize")
    await storage.initialize_async()
    try:
        result = await apply_policies_data_to_storage(
            storage,
            [
                {
                    "id": "of_empty",
                    "name": "Empty",
                    "description": "Format everything",
                    "policy_type": "output_formatter",
                    "triggers": [],
                    "format_type": "direct",
                    "format_config": "FORMATTED",
                }
            ],
        )
        assert not result.get("errors"), result
        stored = await storage.get_policy("of_empty")
        assert stored is not None
        assert [t.type for t in stored.triggers] == ["always"]
    finally:
        await storage.disconnect()


def _formatter_payload(policy_id: str, triggers) -> dict:
    return {
        "id": policy_id,
        "name": f"Payload {policy_id}",
        "description": "Format everything",
        "policy_type": "output_formatter",
        "triggers": triggers,
        "format_type": "direct",
        "format_config": "FORMATTED",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blank_keyword_values_normalized_to_always():
    """Blank strings would substring-match every response; they must be stripped."""
    storage = PolicyStorage(collection_name="test_always_trigger_blank_kw")
    await storage.initialize_async()
    try:
        result = await apply_policies_data_to_storage(
            storage,
            [
                _formatter_payload(
                    "of_blank_kw",
                    [{"type": "keyword", "value": ["", "  "], "target": "agent_response", "operator": "or"}],
                )
            ],
        )
        assert not result.get("errors"), result
        stored = await storage.get_policy("of_blank_kw")
        assert [t.type for t in stored.triggers] == ["always"]
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_keyword_with_app_trigger_keeps_app_and_adds_always():
    """Dropping an empty keyword must not leave an app-only formatter that can never match."""
    storage = PolicyStorage(collection_name="test_always_trigger_kw_app")
    await storage.initialize_async()
    try:
        result = await apply_policies_data_to_storage(
            storage,
            [
                _formatter_payload(
                    "of_kw_app",
                    [
                        {"type": "keyword", "value": [], "target": "agent_response"},
                        {"type": "app", "value": "crm"},
                    ],
                )
            ],
        )
        assert not result.get("errors"), result
        stored = await storage.get_policy("of_kw_app")
        assert sorted(t.type for t in stored.triggers) == ["always", "app"]

        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)
        miss = await _match_formatter(agent)
        assert miss.matched is False

        ctx = _context()
        ctx.active_apps = ["crm"]
        hit = await agent.match_policy(
            ctx, target="agent_response", policy_types=[PolicyType.OUTPUT_FORMATTER]
        )
        assert hit.matched is True
        assert hit.policy.id == "of_kw_app"
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blank_nl_value_normalized_to_always_without_llm():
    """A scalar NL value of "" is wrapped to [""] by the loader; it must still become always."""
    storage = PolicyStorage(collection_name="test_always_trigger_blank_nl")
    await storage.initialize_async()
    try:
        result = await apply_policies_data_to_storage(
            storage,
            [
                _formatter_payload(
                    "of_blank_nl",
                    [{"type": "natural_language", "value": "", "target": "agent_response"}],
                )
            ],
        )
        assert not result.get("errors"), result
        stored = await storage.get_policy("of_blank_nl")
        assert [t.type for t in stored.triggers] == ["always"]

        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)
        with patch("cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain") as get_chain:
            match = await _match_formatter(agent)
            get_chain.assert_not_called()
        assert match.matched is True
        assert match.policy.id == "of_blank_nl"
    finally:
        await storage.disconnect()
