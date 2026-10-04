"""Size failures in the shared ``call_model`` node: context overflow and truncated output.

Overflow: the provider rejects the request as too long, the history is compacted and
the call retried once per step; a second overflow at the same step raises as before.
Truncation: a reply cut off at the output-token limit is discarded (never persisted)
and the request sent again with a note, up to ``cuga_lite_output_truncation_retries``
times; a complete code block that was cut only after its closing fence still runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.model_call_recovery import (
    OUTPUT_TRUNCATED_KEY,
    OUTPUT_TRUNCATED_NOTE,
    REACTIVE_COMPACT_KEY,
    code_parses,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import create_call_model_node
from cuga.backend.cuga_graph.utils.context_management_utils import (
    REACTIVE_COMPACT_TRUNCATION_NOTE,
    cap_oversized_messages,
    force_context_compaction,
)
from cuga.backend.llm.errors import is_context_overflow_error, is_output_truncated

pytestmark = pytest.mark.unit

_SUMMARIZE = "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization"
_COMPACT = "cuga.backend.cuga_graph.utils.context_management_utils.force_context_compaction"
_SUMMARIZER = "cuga.backend.cuga_graph.utils.context_summarizer.ContextSummarizer"


class _Adapter(CoreGraphAdapter):
    messages_key = "chat_messages"
    execute_node_name = "sandbox"
    metadata_key = "cuga_lite_metadata"

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return override if override is not None else 50


def _state(messages=None, step_count=0, metadata=None):
    vm = MagicMock()
    vm.get_variable_names.return_value = []
    return SimpleNamespace(
        chat_messages=messages or [HumanMessage(content="do task")],
        step_count=step_count,
        prepared_prompt="SYS",
        cuga_lite_metadata=metadata if metadata is not None else {},
        variables_storage=None,
        variable_counter_state=None,
        variable_creation_order=None,
        variables_manager=vm,
        tool_budget_exhausted=False,
    )


def _settings(retries=2):
    return SimpleNamespace(
        advanced_features=SimpleNamespace(
            cuga_lite_max_steps=50, cuga_lite_output_truncation_retries=retries
        ),
        policy=SimpleNamespace(enabled=False),
    )


def _reply(content: str, finish_reason: Optional[str] = None):
    meta = {"finish_reason": finish_reason} if finish_reason else {}
    return SimpleNamespace(content=content, additional_kwargs={}, response_metadata=meta)


class _Model:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.seen: List[list] = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _no_summarization():
    with patch(_SUMMARIZE, new_callable=AsyncMock) as summarize:
        summarize.side_effect = lambda messages, *a, **kw: messages
        yield


# ── detectors ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Error code: 400 - {'error': {'code': 'context_length_exceeded'}}",
        "This model's maximum context length is 32768 tokens. However, you requested 40000 tokens.",
        "prompt is too long: 210000 tokens > 200000 maximum",
        "Input is too long for requested model.",
        "Invalid input: the number of input tokens cannot exceed the total tokens limit 131072 for this model",
        "The input token count (1200000) exceeds the maximum number of tokens allowed (1048576).",
    ],
)
def test_context_overflow_is_recognised_across_providers(text):
    assert is_context_overflow_error(RuntimeError(text))


def test_context_overflow_is_found_through_the_cause_chain_and_the_body():
    try:
        try:
            raise ValueError("prompt is too long")
        except ValueError as inner:
            raise RuntimeError("model call failed") from inner
    except RuntimeError as outer:
        assert is_context_overflow_error(outer)

    with_body = RuntimeError("400")
    with_body.body = {"error": {"code": "context_length_exceeded"}}
    assert is_context_overflow_error(with_body)


def test_rate_limits_and_other_errors_are_not_overflow():
    assert not is_context_overflow_error(
        RuntimeError("Request too large for gpt-4o: Limit 30000, Requested 45000 tokens per min (TPM)")
    )
    assert not is_context_overflow_error(RuntimeError("connection reset by peer"))


@pytest.mark.parametrize(
    "meta",
    [
        {"finish_reason": "length"},
        {"stop_reason": "max_tokens"},
        {"finish_reason": "MAX_TOKENS"},
        {"done_reason": "length"},
        {"generation_info": {"finish_reason": "length"}},
    ],
)
def test_output_truncation_is_recognised(meta):
    assert is_output_truncated(SimpleNamespace(response_metadata=meta))


@pytest.mark.parametrize("meta", [{}, {"finish_reason": "stop"}, {"stop_reason": "end_turn"}, None])
def test_normal_stops_are_not_truncation(meta):
    assert not is_output_truncated(SimpleNamespace(response_metadata=meta))
    assert not is_output_truncated(object())


def test_code_parses_allows_top_level_await_and_rejects_cut_code():
    assert code_parses("r = await get_x(a=1)\nprint(r)")
    assert not code_parses("r = await get_x(a=1)\nprint(r[")
    assert not code_parses("")
    assert not code_parses(None)


# ── compaction ──────────────────────────────────────────────────────────────


def test_cap_oversized_messages_keeps_tool_ids_and_short_messages():
    call = AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1", "type": "tool_call"}])
    big = ToolMessage(content="x" * 500, tool_call_id="c1", name="t")
    small = HumanMessage(content="short")

    capped = cap_oversized_messages([call, big, small], max_chars=100)

    assert capped[0] is call and capped[2] is small
    assert capped[1].tool_call_id == "c1" and capped[1].name == "t"
    assert capped[1].content.startswith("x" * 100) and REACTIVE_COMPACT_TRUNCATION_NOTE in capped[1].content
    assert big.content == "x" * 500, "the input is never mutated"


@pytest.mark.asyncio
async def test_force_compaction_caps_when_summarization_is_unavailable():
    with patch(_SUMMARIZER) as summarizer_cls:
        summarizer_cls.return_value.middleware = None
        out = await force_context_compaction(
            [HumanMessage(content="q"), HumanMessage(content="y" * 1000)], object(), max_message_chars=50
        )
    assert out is not None and len(out[1].content) < 1000


@pytest.mark.asyncio
async def test_force_compaction_uses_the_summary_bypassing_the_trigger():
    summary = [HumanMessage(content="summary of previous conversation"), HumanMessage(content="last")]
    with patch(_SUMMARIZER) as summarizer_cls:
        summarizer_cls.return_value.middleware = object()
        summarizer_cls.return_value.summarize_messages = AsyncMock(return_value=(summary, {}))
        out = await force_context_compaction(
            [HumanMessage(content="a" * 300) for _ in range(5)] + [HumanMessage(content="last")],
            object(),
            max_message_chars=20000,
        )
    assert out == summary
    summarizer_cls.return_value.summarize_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_compaction_returns_none_when_nothing_shrinks():
    with patch(_SUMMARIZER) as summarizer_cls:
        summarizer_cls.return_value.middleware = None
        assert (
            await force_context_compaction([HumanMessage(content="q")], object(), max_message_chars=50)
            is None
        )


# ── call_model: overflow ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overflow_compacts_and_retries_call_model():
    compacted = [HumanMessage(content="compacted")]
    model = _Model(RuntimeError("prompt is too long"))
    state = _state(step_count=4)

    with patch(_COMPACT, new_callable=AsyncMock, return_value=compacted) as compact:
        cmd = await create_call_model_node(_Adapter(), model, _settings())(state, {})

    assert cmd.goto == "call_model"
    assert cmd.update["chat_messages"] == compacted
    assert cmd.update["cuga_lite_metadata"][REACTIVE_COMPACT_KEY] == 4
    assert "step_count" not in cmd.update, "a compaction retry is not a model step"
    compact.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_overflow_at_the_same_step_raises_the_provider_error():
    error = RuntimeError("prompt is too long")
    state = _state(step_count=4, metadata={REACTIVE_COMPACT_KEY: 4})

    with patch(_COMPACT, new_callable=AsyncMock) as compact, pytest.raises(RuntimeError) as raised:
        await create_call_model_node(_Adapter(), _Model(error), _settings())(state, {})

    assert raised.value is error
    compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_overflow_that_cannot_be_compacted_raises():
    error = RuntimeError("maximum context length is 8192 tokens")
    with patch(_COMPACT, new_callable=AsyncMock, return_value=None), pytest.raises(RuntimeError) as raised:
        await create_call_model_node(_Adapter(), _Model(error), _settings())(_state(), {})
    assert raised.value is error


@pytest.mark.asyncio
async def test_other_errors_are_not_caught():
    with patch(_COMPACT, new_callable=AsyncMock) as compact, pytest.raises(ConnectionError):
        await create_call_model_node(_Adapter(), _Model(ConnectionError("reset")), _settings())(_state(), {})
    compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_successful_call_clears_the_compaction_marker():
    state = _state(step_count=4, metadata={REACTIVE_COMPACT_KEY: 4})
    cmd = await create_call_model_node(_Adapter(), _Model(_reply("All done.")), _settings())(state, {})
    assert cmd.goto == END
    assert REACTIVE_COMPACT_KEY not in cmd.update["cuga_lite_metadata"]


# ── call_model: truncated output ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_truncated_code_is_discarded_and_retried_with_a_note():
    state = _state()
    model = _Model(_reply("```python\nr = await get_x(\n", finish_reason="length"))

    cmd = await create_call_model_node(_Adapter(), model, _settings())(state, {})

    assert cmd.goto == "call_model"
    assert cmd.update["chat_messages"] == state.chat_messages, "the cut reply is never persisted"
    assert cmd.update["step_count"] == 1
    assert cmd.update["cuga_lite_metadata"][OUTPUT_TRUNCATED_KEY] == 1
    assert cmd.update["script"] is None


@pytest.mark.asyncio
async def test_the_retry_sends_the_note_outbound_only():
    state = _state(step_count=1, metadata={OUTPUT_TRUNCATED_KEY: 1})
    model = _Model(_reply("Short answer."))

    cmd = await create_call_model_node(_Adapter(), model, _settings())(state, {})

    (outbound,) = model.seen
    assert outbound[-1] == {"role": "user", "content": OUTPUT_TRUNCATED_NOTE}
    assert cmd.goto == END
    assert OUTPUT_TRUNCATED_KEY not in cmd.update["cuga_lite_metadata"]
    assert all(OUTPUT_TRUNCATED_NOTE not in str(m.content) for m in cmd.update["chat_messages"])


@pytest.mark.asyncio
async def test_truncated_text_answer_is_retried():
    cmd = await create_call_model_node(
        _Adapter(), _Model(_reply("The three orders are: first, sec", finish_reason="length")), _settings()
    )(_state(), {})
    assert cmd.goto == "call_model" and cmd.update["cuga_lite_metadata"][OUTPUT_TRUNCATED_KEY] == 1


@pytest.mark.asyncio
async def test_a_complete_code_block_cut_after_its_fence_still_runs():
    content = "```python\nr = await get_x(a=1)\nprint(r)\n```\nThis prints the res"
    cmd = await create_call_model_node(
        _Adapter(), _Model(_reply(content, finish_reason="length")), _settings()
    )(_state(), {})
    assert cmd.goto == "sandbox"
    assert "print(r)" in cmd.update["script"]


@pytest.mark.asyncio
async def test_retries_stop_at_the_configured_count():
    state = _state(step_count=2, metadata={OUTPUT_TRUNCATED_KEY: 2})
    cmd = await create_call_model_node(
        _Adapter(), _Model(_reply("Half an answ", finish_reason="length")), _settings(retries=2)
    )(state, {})
    assert cmd.goto == END
    assert cmd.update["final_answer"] == "Half an answ"


@pytest.mark.asyncio
async def test_zero_retries_turns_the_feature_off():
    cmd = await create_call_model_node(
        _Adapter(), _Model(_reply("Half an answ", finish_reason="length")), _settings(retries=0)
    )(_state(), {})
    assert cmd.goto == END


@pytest.mark.asyncio
async def test_untruncated_replies_are_untouched():
    cmd = await create_call_model_node(
        _Adapter(), _Model(_reply("Done.", finish_reason="stop")), _settings()
    )(_state(), {})
    assert cmd.goto == END and cmd.update["final_answer"] == "Done."
