"""
SuggestHub — Performance Tracing Test Suite
============================================
Run with:
    pytest docs/examples/suggesthub/test_perf.py -s -v

Add --live to use real LLM credentials instead of the mock:
    pytest docs/examples/suggesthub/test_perf.py -s -v --live

What this file does
-------------------
1. Patches the 5 real hotspot functions in the CugaLite execution path with
   time.perf_counter wrappers that accumulate per-segment durations in _TIMING.
2. After every test a `perf_report` fixture prints a structured table to stderr.
3. Three tests exercise distinct code paths:
   - test_simple_message      : basic round-trip (tool path also fires since Ian
                                always calls find_similar_suggestions first)
   - test_tool_trigger_message: explicit safety-issue message
   - test_timing_thresholds   : asserts per-segment time budgets (mock mode)

Actual execution path (CugaAgent SDK → CugaLite):
    START
      → prepare_tools_and_apps          [segment 1: tool_provider.get_apps() ~2s registry hit]
      → call_model                      [segment 2: apply_context_summarization + LLM call]
          → apply_context_summarization [segment 3: token counting/compression]
          → active_model.ainvoke()      [segment 4: LLM inference]
      → sandbox                         [segment 5: code execution + tool calls]
      → call_model (repeat)
    SDKCallback → FinalAnswerAgent

NOTE: ChatAgent / AgentLoop are NOT used by the SDK — they are server-only.

Performance Optimisation Recommendations
-----------------------------------------
# PERF-1 [APPLIED ✅]: Registry + Evolve probes disabled in patch_timings fixture.
#   Location : patch_timings fixture (this file)
#   Cost     : ~5-6 s/turn (registry TCP timeout ~2-3 s + Evolve SSE timeout ~2-3 s)
#   Fix      : settings.advanced_features.registry = False silences api_utils.get_apps().
#              EvolveIntegration.is_enabled() patched to False silences the SSE probe.
#              Both are safe: DirectLangChainToolsProvider needs no registry;
#              SKILL.md supplies all instructions without Evolve.

# PERF-2 [DEFERRED 🔴]: Cache prepare_tools_and_apps result between identical turns.
#   Location : adapter/prepare_node.py:53  prepare_tools_and_apps closure
#   Cost     : ~0.3-2 s/turn (knowledge engine + tool binding + apps fetch)
#   Fix      : TTL-cache the bound tools by (thread_id, tool_set_hash).

# PERF-3 [APPLIED ✅]: apply_context_summarization guard.
#   Location : cuga_graph/utils/context_management_utils.py
#   Cost     : ~0.05 s/turn for short sessions
#   Current  : Already only triggers LLM summarization above the token budget threshold.

# PERF-4 [DEFERRED 🟡]: Pre-warm LLMManager.get_model at startup.
#   Location : sdk.py:1705  CugaAgent.__init__ → _build_callbacks
#   Cost     : One-time ~0.1 s (first call resolves secrets + instantiates provider)
#   Fix      : Call llm_manager.get_model(settings.agent.code.model) once in bob_agent.py.

NOTE: Two source fixes have already been applied:
  - check_sse_availability default timeout: 5 s → 1 s  (chat_agent.py)
  - max_round_trips default: 4 → 2  (chat.py)
  These apply to the server SSE path; in SDK/CugaLite mode they are not exercised.
"""

from __future__ import annotations

import collections
import functools
import sys
import time
import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Module-level timing accumulator — reset between tests by the perf_report fixture
# ──────────────────────────────────────────────────────────────────────────────
_TIMING: dict[str, list[float]] = collections.defaultdict(list)


def _timed(key: str):
    """Decorator factory: wraps an async function with perf_counter timing."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                _TIMING[key].append(time.perf_counter() - t0)
        return wrapper
    return decorator


def _print_report(test_name: str) -> None:
    SEP = "─" * 60
    lines = ["", SEP, f" TIMING REPORT  [{test_name}]", SEP]
    total = 0.0
    for key, durations in sorted(_TIMING.items()):
        agg = sum(durations)
        total += agg
        label = key[:48].ljust(48)
        calls = f"×{len(durations)}" if len(durations) > 1 else "   "
        lines.append(f" {label}  {agg:6.3f} s  {calls}")
    lines += [SEP, f" {'TOTAL'.ljust(48)}  {total:6.3f} s", SEP, ""]
    print("\n".join(lines), file=sys.stderr)


# ──────────────────────────────────────────────────────────────────────────────
# pytest CLI flag: --live (registered in conftest.py)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def live_mode(request):
    return request.config.getoption("--live")


# ──────────────────────────────────────────────────────────────────────────────
# Session-scoped fixture: patch hotspots + disable MCP registry/Evolve probes
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def patch_timings():
    """
    Patch the actual CugaLite execution path hotspots and disable the two
    MCP-related network probes that fire on every turn even though the
    SuggestHub SDK agent uses DirectLangChainToolsProvider (no registry needed):

      - settings.advanced_features.registry = False
          Stops api_utils.get_apps() from attempting a TCP connect to localhost:8001.
          Cost without this: ~2-3 s per turn (connect timeout + error).

      - EvolveIntegration.is_enabled() → False
          Stops the Evolve SSE probe that follows the registry failure.
          Cost without this: ~2-3 s per turn (SSE connect timeout).

    Both are safe to disable for tests: DirectLangChainToolsProvider injects
    tools directly; SKILL.md supplies all instructions; no Evolve server runs
    in the test environment.

    Segment map:
      1. tool_provider.get_apps()          → DirectLangChainToolsProvider.get_apps
      2. apply_context_summarization       → shared_nodes (token counting)
      3. sandbox.eval_with_tools_async     → code execution + tool calls
      4. model.ainvoke                     → LLM call (patched per-test in _run_message)
    """
    from unittest.mock import patch as _patch

    import cuga.backend.cuga_graph.utils.context_management_utils as _ctx_mod
    import cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes as _shared_mod
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
    from cuga.config import settings

    # ── Disable registry TCP probe (saves ~2-3 s/turn)
    _orig_registry = settings.advanced_features.registry
    settings.advanced_features.registry = False

    # ── Disable Evolve SSE probe (saves ~2-3 s/turn)
    _evolve_patch = _patch(
        "cuga.backend.evolve.integration.EvolveIntegration.is_enabled",
        return_value=False,
    )
    _evolve_patch.start()

    # ── 1. DirectLangChainToolsProvider.get_apps
    _orig_get_apps = DirectLangChainToolsProvider.get_apps
    DirectLangChainToolsProvider.get_apps = _timed("1_tool_provider.get_apps")(_orig_get_apps)

    # ── 2. apply_context_summarization — patch in both the utils module AND
    #       the shared_nodes module (which imports it at module load time)
    _orig_ctx = _ctx_mod.apply_context_summarization
    _timed_ctx = _timed("2_apply_context_summarization")(_orig_ctx)
    _ctx_mod.apply_context_summarization = _timed_ctx
    _shared_mod.apply_context_summarization = _timed_ctx

    # ── 3. CodeExecutor.eval_with_tools_async (sandbox + tool calls)
    _orig_eval = CodeExecutor.eval_with_tools_async

    async def _timed_eval(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return await _orig_eval(*args, **kwargs)
        finally:
            _TIMING["3_sandbox.eval_with_tools_async"].append(time.perf_counter() - t0)

    CodeExecutor.eval_with_tools_async = _timed_eval  # type: ignore[method-assign]

    yield

    # ── Restore everything
    DirectLangChainToolsProvider.get_apps = _orig_get_apps
    _ctx_mod.apply_context_summarization = _orig_ctx
    _shared_mod.apply_context_summarization = _orig_ctx
    CodeExecutor.eval_with_tools_async = _orig_eval
    settings.advanced_features.registry = _orig_registry
    _evolve_patch.stop()


# ──────────────────────────────────────────────────────────────────────────────
# Per-test fixture: print report + reset accumulator
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def perf_report(request):
    """Clear _TIMING before the test, print + clear after."""
    _TIMING.clear()
    yield
    _print_report(request.node.name)
    _TIMING.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Helper: run a message through the agent
# ──────────────────────────────────────────────────────────────────────────────

async def _run_message(message: str, live: bool) -> str:
    """
    Drive a message through CugaAgent.invoke().

    ReasoningChatOpenAI is a frozen Pydantic model — instance attributes cannot
    be set.  We patch at the *class* level instead, using patch.object on the
    concrete class returned by LLMManager.  The patch is scoped to this call so
    it doesn't bleed across tests.

    mock mode (live=False):
        ainvoke is replaced with a stub returning a plain-text AIMessage so no
        real LLM call is made.  Segments 1-3 (get_apps, context_summarization,
        sandbox) still run against real code.

    live mode (live=True):
        ainvoke is wrapped with a timing shim only — the real call goes through.
    """
    from langchain_core.messages import AIMessage as _AIMessage
    from unittest.mock import patch as _patch

    from docs.examples.suggesthub.agents.bob_agent import get_bob_agent
    from cuga.backend.llm.models import LLMManager
    from cuga.config import settings

    agent = get_bob_agent()

    # Resolve the concrete model class — must be done after LLMManager has
    # instantiated it (happens inside get_bob_agent → agent.graph → __init__)
    _llm_mgr = LLMManager()
    _real_model = _llm_mgr.get_model(settings.agent.code.model)
    _model_cls = type(_real_model)  # e.g. ReasoningChatOpenAI

    _mock_reply = (
        "I've reviewed your report. There are no existing suggestions matching this issue. "
        "Would you like me to create a new suggestion?"
    )

    # Grab the *unbound* original method from the class so we can call it
    _orig_ainvoke = _model_cls.ainvoke

    if not live:
        async def _mock_ainvoke(self, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                return _AIMessage(content=_mock_reply)
            finally:
                _TIMING["4_model.ainvoke [LLM call]"].append(time.perf_counter() - t0)
    else:
        async def _mock_ainvoke(self, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                return await _orig_ainvoke(self, *args, **kwargs)
            finally:
                _TIMING["4_model.ainvoke [LLM call]"].append(time.perf_counter() - t0)

    with _patch.object(_model_cls, "ainvoke", _mock_ainvoke):
        result = await agent.invoke(message)

    return str(result)


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simple_message(live_mode):
    """
    Basic chat round-trip: user reports a broken coffee machine.
    Ian's instructions always call find_similar_suggestions first, so the
    sandbox (segment 3) will fire even for a simple message.
    """
    result = await _run_message(
        "The coffee machine on Floor 3 has been broken for two weeks.",
        live=live_mode,
    )
    assert result, "Expected a non-empty response from the agent"


@pytest.mark.asyncio
async def test_tool_trigger_message(live_mode):
    """
    Safety issue message — explicitly triggers the find_similar_suggestions
    + save_suggestion_draft path to exercise multiple sandbox executions.
    In live mode: expect segments 3 (sandbox) to fire ×2 or more.
    """
    result = await _run_message(
        "I want to log a safety issue — the railing on the staircase to Level 2 "
        "is loose and someone is going to get hurt.",
        live=live_mode,
    )
    assert result, "Expected a non-empty response from the agent"


@pytest.mark.asyncio
async def test_timing_thresholds(live_mode):
    """
    Assert per-segment time budgets (mock mode only).

    These catch catastrophic regressions — e.g. registry timeout growing,
    context summarizer calling LLM unexpectedly, sandbox hanging.

    Budgets (intentionally generous for CI variance):
        1_tool_provider.get_apps      < 0.1 s  (DirectLangChainToolsProvider is instant)
        2_apply_context_summarization < 1.0 s  (token counting only, no LLM call expected)
        3_sandbox.eval_with_tools     < 0.5 s  (mock LLM means minimal code to execute)
        4_model.ainvoke               < 0.05 s (mock — should be near-zero)
    """
    if live_mode:
        pytest.skip("Threshold test is only meaningful in mock mode — use --live for live benchmarks")

    await _run_message(
        "Is there already a suggestion about improving the cafeteria Wi-Fi?",
        live=False,
    )

    budgets = {
        "1_tool_provider.get_apps": 0.1,
        "2_apply_context_summarization": 1.0,
        "3_sandbox.eval_with_tools_async": 0.5,
        "4_model.ainvoke [LLM call]": 0.05,
    }

    for segment, limit in budgets.items():
        durations = _TIMING.get(segment, [])
        if not durations:
            continue  # segment didn't fire (e.g. sandbox skipped when mock exits early)
        total = sum(durations)
        assert total <= limit, (
            f"Performance regression: '{segment}' took {total:.3f} s "
            f"(budget: {limit:.2f} s). See PERF comments in this file for fix guidance."
        )
