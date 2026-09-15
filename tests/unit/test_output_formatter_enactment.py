"""Unit tests for OutputFormatter enactment (direct vs markdown prompt rules)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, SystemMessage


def _policy_match(format_type: str, format_config: str):
    from cuga.backend.cuga_graph.policy.models import OutputFormatter

    policy = OutputFormatter(
        id="test_fmt",
        name="Test Formatter",
        description="test",
        format_type=format_type,
        format_config=format_config,
        triggers=[],
    )
    match = MagicMock()
    match.policy = policy
    match.reasoning = "test"
    match.confidence = 1.0
    return match


def _state_and_context(content: str = "Acme Corporation revenue $1,500,000"):
    state = MagicMock()
    state.chat_messages = [AIMessage(content=content)]
    state.final_answer = content

    context = MagicMock()
    context.agent_response = content
    context.chat_messages = []
    context.user_input = "Get the top account"
    return state, context


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_format_returns_config_without_llm():
    from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment

    block = "You are not allowed to view this sensitive data"
    policy_match = _policy_match("direct", block)
    state, context = _state_and_context()

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock()

    with patch("cuga.backend.llm.models.LLMManager") as mock_mgr:
        mock_mgr.return_value.get_model.return_value = mock_llm
        _cmd, metadata = await PolicyEnactment._enact_format_output(state, policy_match, MagicMock(), context)

    assert metadata is not None
    assert metadata["formatted_response"] == block
    assert metadata["format_type"] == "direct"
    mock_llm.ainvoke.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_markdown_prompt_prioritizes_format_config_over_preserve_rules():
    from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment

    replace_instructions = "Replace the entire response with: You are not allowed to view this sensitive data"
    policy_match = _policy_match("markdown", replace_instructions)
    # Use a JSON-containing response so the plain-text pre-check does not short-circuit
    state, context = _state_and_context(content='{"revenue": 1500000, "company": "Acme"}')

    mock_resp = MagicMock()
    mock_resp.content = "You are not allowed to view this sensitive data"
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

    with patch("cuga.backend.llm.models.LLMManager") as mock_mgr:
        mock_mgr.return_value.get_model.return_value = mock_llm
        await PolicyEnactment._enact_format_output(state, policy_match, MagicMock(), context)

    mock_llm.ainvoke.assert_awaited_once()
    messages = mock_llm.ainvoke.call_args.args[0]
    system = next(m for m in messages if isinstance(m, SystemMessage))
    assert replace_instructions in system.content
    assert "take precedence" in system.content.lower()
    assert "do not remove important details" not in system.content.lower()
    assert "preserve all factual information from the original response" not in system.content.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_markdown_skips_llm_for_clean_plain_text():
    """Clean plain-text responses must not trigger a formatter LLM call (C-1 fix)."""
    from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment

    policy_match = _policy_match("markdown", "Respond in plain English.")
    state, context = _state_and_context(content="The revenue for Acme was $1,500,000.")

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock()

    with patch("cuga.backend.llm.models.LLMManager") as mock_mgr:
        mock_mgr.return_value.get_model.return_value = mock_llm
        result, metadata = await PolicyEnactment._enact_format_output(
            state, policy_match, MagicMock(), context
        )

    mock_llm.ainvoke.assert_not_called()
    assert result is None
    assert metadata is None
