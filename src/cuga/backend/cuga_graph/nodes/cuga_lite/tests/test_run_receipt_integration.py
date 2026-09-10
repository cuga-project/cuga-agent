"""End-to-end run-receipt test through CugaAgent.invoke() with a fake chat model.

The fake model subclasses BaseChatModel so LangChain actually fires callbacks —
a plain object with an ``ainvoke`` method would bypass the collector entirely.
"""

from typing import Any, List, Optional
from unittest.mock import patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from cuga.sdk import CugaAgent


class _UsageChatModel(BaseChatModel):
    """Always answers with plain NL and reports token usage like a real provider."""

    @property
    def _llm_type(self) -> str:
        return "usage-fake"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = AIMessage(
            content="There are 3 accounts.",
            usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128},
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"model_name": "gpt-4o"},
        )


@tool
def get_accounts() -> list:
    """Return all accounts."""
    return ["acme", "globex", "initech"]


def _make_agent() -> CugaAgent:
    return CugaAgent(
        tools=[get_accounts],
        model=_UsageChatModel(),
        auto_load_policies=False,
        enable_knowledge=False,
        enable_skills=False,
    )


@pytest.mark.asyncio
async def test_receipt_attached_when_flag_enabled():
    agent = _make_agent()
    with patch("cuga.sdk.settings.advanced_features.run_receipt", True):
        result = await agent.invoke("how many accounts are there?")

    assert result.receipt is not None
    assert result.receipt.total_tokens >= 128
    assert result.receipt.input_tokens >= 120
    assert result.receipt.llm_calls >= 1
    assert result.receipt.models == ["gpt-4o"]
    assert result.receipt.output_tokens >= 1
    assert result.receipt.wall_time_s > 0
    # tracking was forced internally for timings, but the caller did not opt in
    assert result.tool_calls == []
    rendered = str(result.receipt)
    assert "Run Receipt" in rendered and "gpt-4o" in rendered


@pytest.mark.asyncio
async def test_receipt_is_none_when_flag_disabled():
    agent = _make_agent()
    with patch("cuga.sdk.settings.advanced_features.run_receipt", False):
        result = await agent.invoke("how many accounts are there?")

    assert result.receipt is None
    assert result.answer
