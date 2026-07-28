"""Unit tests for RunReceipt aggregation, rendering, and the fail-safe collector."""

import uuid

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from cuga.backend.cuga_graph.utils.run_receipt import (
    RunMetricsCollector,
    RunReceipt,
    build_run_receipt,
)


def _llm_result(usage_metadata=None, llm_output=None, content="ok") -> LLMResult:
    message = AIMessage(content=content, usage_metadata=usage_metadata)
    return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output=llm_output)


async def _run_call(collector, usage_metadata=None, llm_output=None, model_name=None):
    run_id = uuid.uuid4()
    metadata = {"ls_model_name": model_name} if model_name else {}
    await collector.on_chat_model_start({}, [], run_id=run_id, metadata=metadata)
    await collector.on_llm_end(
        _llm_result(usage_metadata=usage_metadata, llm_output=llm_output), run_id=run_id
    )


@pytest.mark.asyncio
async def test_collector_accumulates_usage_metadata_per_model():
    collector = RunMetricsCollector()
    usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    await _run_call(collector, usage_metadata=usage, model_name="gpt-4.1")
    await _run_call(collector, usage_metadata=usage, model_name="gpt-4.1")

    assert collector.llm_calls == 2
    assert collector.usage_by_model["gpt-4.1"]["input_tokens"] == 200
    assert collector.usage_by_model["gpt-4.1"]["total_tokens"] == 240
    assert collector.llm_time_s > 0


@pytest.mark.asyncio
async def test_collector_falls_back_to_legacy_llm_output():
    collector = RunMetricsCollector()
    await _run_call(
        collector,
        llm_output={
            "token_usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
            "model_name": "gpt-4o",
        },
    )

    assert collector.usage_by_model["gpt-4o"] == {
        "input_tokens": 50,
        "output_tokens": 10,
        "total_tokens": 60,
        "cache_read_tokens": 0,
        "reasoning_tokens": 0,
    }


@pytest.mark.asyncio
async def test_collector_reads_usage_from_llm_output_when_generations_empty():
    collector = RunMetricsCollector()
    await collector.on_llm_end(
        LLMResult(
            generations=[],
            llm_output={
                "token_usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                "model_name": "gpt-4o",
            },
        ),
        run_id=uuid.uuid4(),
    )

    assert collector.usage_by_model["gpt-4o"]["total_tokens"] == 7


@pytest.mark.asyncio
async def test_collector_accumulates_cache_read_tokens():
    collector = RunMetricsCollector()
    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "total_tokens": 110,
        "input_token_details": {"cache_read": 80},
    }
    await _run_call(collector, usage_metadata=usage, model_name="gpt-4o")
    await _run_call(collector, usage_metadata=usage, model_name="gpt-4o")

    assert collector.usage_by_model["gpt-4o"]["cache_read_tokens"] == 160

    receipt = build_run_receipt(collector, [], wall_time_s=1.0)
    assert receipt.cache_read_tokens == 160
    assert "80% cached" in str(receipt)


@pytest.mark.asyncio
async def test_collector_accumulates_reasoning_tokens():
    collector = RunMetricsCollector()
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "output_token_details": {"reasoning": 30},
    }
    await _run_call(collector, usage_metadata=usage, model_name="gpt-oss-120b")
    await _run_call(collector, usage_metadata=usage, model_name="gpt-oss-120b")

    assert collector.usage_by_model["gpt-oss-120b"]["reasoning_tokens"] == 60

    receipt = build_run_receipt(collector, [], wall_time_s=1.0)
    assert receipt.reasoning_tokens == 60
    assert "60 reasoning" in str(receipt)


@pytest.mark.asyncio
async def test_collector_never_raises_on_malformed_response():
    collector = RunMetricsCollector()
    await collector.on_llm_end(None, run_id=uuid.uuid4())  # type: ignore[arg-type]
    await collector.on_llm_end(LLMResult(generations=[]), run_id=uuid.uuid4())
    await collector.on_llm_error(RuntimeError("boom"), run_id=uuid.uuid4())

    assert collector.usage_by_model == {}


@pytest.mark.asyncio
async def test_build_receipt_aggregates_tools_and_cost():
    collector = RunMetricsCollector()
    await _run_call(
        collector,
        usage_metadata={"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000},
        model_name="openai/gpt-4.1",
    )
    tool_calls = [
        {"name": "crm.search", "duration_ms": 1500.0},
        {"name": "crm.search", "duration_ms": 500.0},
        {"name": "crm.get", "duration_ms": 300.0},
    ]

    receipt = build_run_receipt(collector, tool_calls, wall_time_s=9.42)

    assert receipt is not None
    assert receipt.cost_usd == 10.00  # $2 + $8 per 1M for gpt-4.1
    assert receipt.tool_call_count == 3
    assert receipt.slowest_tool == "crm.search"
    assert receipt.tool_time_s == 2.3
    assert receipt.wall_time_s == 9.42


@pytest.mark.asyncio
async def test_unknown_model_yields_cost_none():
    collector = RunMetricsCollector()
    await _run_call(
        collector,
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        model_name="self-hosted-model",
    )

    receipt = build_run_receipt(collector, [], wall_time_s=1.0)

    assert receipt is not None
    assert receipt.cost_usd is None
    assert receipt.total_tokens == 15


@pytest.mark.asyncio
async def test_mixed_known_and_unknown_models_yields_cost_none():
    collector = RunMetricsCollector()
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    await _run_call(collector, usage_metadata=usage, model_name="gpt-4o")
    await _run_call(collector, usage_metadata=usage, model_name="mystery-model")

    receipt = build_run_receipt(collector, [], wall_time_s=1.0)

    assert receipt is not None
    assert receipt.cost_usd is None
    assert sorted(receipt.models) == ["gpt-4o", "mystery-model"]


def test_empty_receipt_builds_and_renders():
    receipt = build_run_receipt(RunMetricsCollector(), None, wall_time_s=0.0)

    assert receipt is not None
    assert receipt.cost_usd is None
    rendered = str(receipt)
    assert "Run Receipt" in rendered
    assert "n/a (unknown model)" in rendered


def test_render_box_contains_all_key_lines():
    receipt = RunReceipt(
        models=["gpt-4.1"],
        input_tokens=18342,
        output_tokens=2101,
        total_tokens=20443,
        cost_usd=0.0535,
        llm_calls=7,
        tool_call_count=4,
        llm_time_s=6.1,
        tool_time_s=2.8,
        wall_time_s=9.4,
        slowest_tool="crm.search_accounts",
        tool_timings=[{"name": "crm.search_accounts", "calls": 2, "total_ms": 1900.0}],
    )

    rendered = str(receipt)
    assert "18,342 in / 2,101 out (20,443)" in rendered
    assert "$0.0535" in rendered
    assert "crm.search_accounts 1.9s" in rendered
    # every line in the box has equal width
    widths = {len(line) for line in rendered.splitlines()}
    assert len(widths) == 1


def test_build_receipt_returns_none_on_broken_collector():
    assert build_run_receipt(None, [], wall_time_s=1.0) is None  # type: ignore[arg-type]
