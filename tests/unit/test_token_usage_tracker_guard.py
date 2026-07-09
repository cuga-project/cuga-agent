"""TokenUsageTracker must not crash when providers omit llm_output token usage.

LiteLLM/watsonx/streaming responses return llm_output=None (or without a
token_usage entry); usage must then be read from the message's usage_metadata.
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from cuga.backend.cuga_graph.utils.agent_loop import TokenUsageTracker


def _llm_result(llm_output=None, usage_metadata=None) -> LLMResult:
    message = AIMessage(content="hello", usage_metadata=usage_metadata)
    return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output=llm_output)


@pytest.fixture
def tracker():
    return MagicMock()


@pytest.fixture
def handler(tracker):
    return TokenUsageTracker(tracker)


@pytest.mark.asyncio
async def test_legacy_llm_output_path_preserved(handler, tracker):
    response = _llm_result(llm_output={"token_usage": {"total_tokens": 42}})

    await handler.on_llm_end(response)

    tracker.collect_tokens_usage.assert_called_once_with(42)
    tracker.collect_prompt.assert_called_once_with(role="assistant", value="hello")


@pytest.mark.asyncio
async def test_none_llm_output_falls_back_to_usage_metadata(handler, tracker):
    response = _llm_result(
        llm_output=None,
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )

    await handler.on_llm_end(response)

    tracker.collect_tokens_usage.assert_called_once_with(15)


@pytest.mark.asyncio
async def test_llm_output_without_token_usage_falls_back_to_usage_metadata(handler, tracker):
    response = _llm_result(
        llm_output={"model_name": "some-model"},
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    )

    await handler.on_llm_end(response)

    tracker.collect_tokens_usage.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_no_usage_anywhere_is_a_noop(handler, tracker):
    response = _llm_result(llm_output=None, usage_metadata=None)

    await handler.on_llm_end(response)

    tracker.collect_tokens_usage.assert_not_called()
    tracker.collect_prompt.assert_called_once()
