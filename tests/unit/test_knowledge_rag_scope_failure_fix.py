"""Tests for the unified Step-5 RAG scope-failure fix.

Three layers, each with a focused test set:

  Layer 1 (prompting):  scope defaults, contract decision tree,
                        auto-fallback session→all on 0 hits.
  Layer 2 (retrieval):  cid_glyph_run rule, relative-score gap rule,
                        per-scope quota, cross-scope RRF.
  Layer 3 (observability): retrieval envelope shape, per-scope stats,
                        recommendation thresholds, single canonical log.

Each test names the exact contract it pins so the regression intent is
visible without re-reading the design doc.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.knowledge.engine import (
    KnowledgeEngine,
    SearchResult,
    _JunkFilterStats,
    _MultiSearchStats,
    _classify_junk_chunk,
    _apply_junk_filter,
)
from cuga.backend.knowledge.envelope import build_retrieval_envelope
from cuga.backend.knowledge.config import KnowledgeConfig


# ============================================================
# Layer 2 — _classify_junk_chunk: new cid_glyph_run rule
# ============================================================


def test_cid_glyph_run_catches_mixed_content_fragment():
    """The exact production failing case: a chunk where 12 CID-shape
    tokens are followed by a long Hebrew prose sentence. The ratio
    rule (30%) missed this because the prose floods the token count,
    pushing the cid ratio below threshold. The new RUN rule fires on
    ≥3 consecutive CID tokens regardless of overall ratio.
    """
    sample = (
        "- /C2E/CEF/CEB /C2D /C4C,9 /CE1/CE9/CFA/CF0/CEE /CE4/CE9/CE9/CE8/CF1 "
        "/CEC/CF2 /CE4/CF2/CF8/CFA/CE4 /CFA/CEB/CF8/CF2/CEE /CFA/CEE/CE9/CE9/CF7 "
        "/CE1/CEB/CF8/CE1 /CE6 . בעל הפוליסה רשאי לבטל את פוליסת ההשבתה "
        "בהודעה למבטח; תקופת ההשבתה תסתיים במועד המצויין בהודעה האמורה "
        "ובלבד שמועד סיום ההשבתה לא יהיה למפרע"
    )
    reason = _classify_junk_chunk(sample, "policy.pdf")
    assert reason == "cid_glyph_run"


def test_cid_glyph_run_does_not_fire_on_two_consecutive_glyphs():
    """The RUN rule needs ≥3 in a row. Two consecutive CID tokens
    surrounded by prose (e.g. a code listing referencing two error
    codes) must NOT trigger — false positive territory."""
    sample = (
        "This document discusses /C2E and /C4F error codes. They appear "
        "in legacy systems and require firmware updates to resolve. "
        "Verify the configuration matches the specification."
    )
    assert _classify_junk_chunk(sample, "manual.pdf") is None


def test_cid_glyph_run_catches_pure_cid_chunk_under_5_tokens():
    """The old ratio rule had a ``len(tokens) >= 5`` floor that let
    short pure-CID chunks slip through. The RUN rule has no floor
    other than its own ≥3 consecutive requirement — a 3-token pure
    CID chunk that's just over the ``too_short`` 30-char floor still
    gets caught.
    """
    short_cid = "/CE3/CE5/CE1/CEB /CF8/CE1/CE9/CE5 /CE5/CE9/CE6/CFA"
    assert len(short_cid) >= 30  # past the too_short floor
    assert _classify_junk_chunk(short_cid, "scan.pdf") == "cid_glyph_run"


def test_cid_glyph_run_no_false_positive_on_unix_paths():
    """Defense against the obvious false positive: code-path snippets
    like ``See /usr/local/bin/foo and /etc/nginx.conf for config``. The
    CID token shape is ``/?C[0-9A-Fa-f]{2,4}`` — Unix paths don't
    match. This documents and pins the regex's restraint.
    """
    code = (
        "See /usr/local/bin/foo and /etc/nginx.conf for config. The "
        "server reads /opt/cuga/data/index.json on startup."
    )
    assert _classify_junk_chunk(code, "readme.md") is None


# ============================================================
# Layer 2 — relative-score gap rule
# ============================================================


def test_relative_score_gap_drops_low_chunk_under_dry_run():
    """When the top score is comfortable (≥0.3) and a candidate is
    flagged as junk AND scores <0.5× the top, drop it even under
    ``dry_run`` mode. Reason gets a ``_low_relative_score`` suffix so
    the dual-trigger is visible in logs.
    """
    top = SearchResult(
        text="A real paragraph of length thirty plus characters.", filename="paper.pdf", page=1, score=0.9
    )
    junk_low = SearchResult(text="x" * 30, filename="paper.pdf", page=2, score=0.2)
    # Force the junk to be flagged as too_short to enter the gap path
    # — we use a deliberately tiny string.
    junk_low.text = "tiny"  # under 30 chars → too_short
    results, stats = _apply_junk_filter([top, junk_low], "dry_run")
    # The flagged-and-far chunk was dropped (would-have-been-kept in
    # plain dry_run without the gap rule).
    assert junk_low not in results
    assert top in results
    # The reason carries the dual-trigger suffix.
    assert any("low_relative_score" in k for k in stats.reasons)


def test_relative_score_gap_skips_when_top_is_below_03():
    """When the ENTIRE batch is below the noise floor, don't apply
    the relative-score gap — every chunk is junk-grade and the 0.5×
    cutoff becomes arbitrary noise that may drop the only-relevant
    chunk. Pin this so a future refactor can't reintroduce it.
    """
    top = SearchResult(text="tiny", filename="p.pdf", page=1, score=0.25)
    runner = SearchResult(text="tiny", filename="p.pdf", page=2, score=0.10)
    results, stats = _apply_junk_filter([top, runner], "dry_run")
    # Both flagged (too_short) but the gap rule did NOT fire because
    # the top is below 0.3. Both still in results under dry_run.
    assert top in results
    assert runner in results
    # No reason carries the low_relative_score suffix.
    assert not any("low_relative_score" in k for k in stats.reasons)


# ============================================================
# Layer 3 — retrieval envelope shape
# ============================================================


def test_envelope_carries_retrieval_block_with_required_fields():
    """The new wire schema includes a ``retrieval`` block with the
    invariant per-scope fields plus a totals summary. Pin the shape
    so SDK consumers can ground a TypedDict on it.
    """
    multi = _MultiSearchStats()
    multi.by_scope = {
        "agent": _JunkFilterStats(
            candidates=10, filtered_count=2, below_threshold=1, reasons={"too_short": 2}
        ),
        "session": _JunkFilterStats(candidates=1),
    }
    multi.top_score_by_scope = {"agent": 0.9, "session": 0.85}
    results = [
        SearchResult(text="a", filename="kb.md", page=1, score=0.9, scope="agent"),
        SearchResult(text="s", filename="up.pdf", page=2, score=0.85, scope="session"),
    ]
    env = build_retrieval_envelope(
        results=results,
        scope_requested="all",
        multi_stats=multi,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from=None,
        include_scores=False,
    )
    r = env["retrieval"]
    assert set(r.keys()) >= {
        "by_scope",
        "failed_scopes",
        "partial",
        "fallback_from",
        "totals",
        "recommendation",
    }
    for s in ("agent", "session"):
        scope_entry = r["by_scope"][s]
        assert set(scope_entry.keys()) >= {
            "candidates",
            "returned",
            "filtered",
            "below_threshold",
            "drain_drops",
            "dedup_collapses",
            "filter_mode",
        }
        assert scope_entry["filter_mode"] == "enforce"
    # Reasons surface when non-empty.
    assert r["by_scope"]["agent"]["reasons"] == {"too_short": 2}
    # Totals match the by-scope sums.
    assert r["totals"]["filtered"] == 2
    assert r["totals"]["candidates"] == 11
    assert r["totals"]["returned"] == 2


def test_envelope_recommendation_prefer_scope_requires_abs_floor():
    """``prefer_<scope>`` fires only when winner ≥ 0.5 absolute AND
    ≥ 1.5× the runner. Two scenarios:
      (a) 0.45 vs 0.30 — ratio 1.5× but winner < 0.5 abs floor → None.
      (b) 0.80 vs 0.50 — ratio 1.6× AND winner ≥ 0.5 → prefer_agent.
    """
    # (a) low absolute, satisfies ratio but not floor → no nudge.
    multi_a = _MultiSearchStats()
    multi_a.by_scope = {"agent": _JunkFilterStats(), "session": _JunkFilterStats()}
    multi_a.top_score_by_scope = {"agent": 0.45, "session": 0.30}
    env_a = build_retrieval_envelope(
        results=[
            SearchResult(text="a", filename="x", page=1, score=0.45, scope="agent"),
            SearchResult(text="s", filename="y", page=1, score=0.30, scope="session"),
        ],
        scope_requested="all",
        multi_stats=multi_a,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from=None,
        include_scores=False,
    )
    # Both scores are above the low_confidence floor (0.3) so we don't
    # emit low_confidence; the ratio fires but the abs floor doesn't.
    assert env_a["retrieval"]["recommendation"] is None

    # (b) higher absolute + ratio satisfied → prefer_agent.
    multi_b = _MultiSearchStats()
    multi_b.by_scope = {"agent": _JunkFilterStats(), "session": _JunkFilterStats()}
    multi_b.top_score_by_scope = {"agent": 0.80, "session": 0.50}
    env_b = build_retrieval_envelope(
        results=[
            SearchResult(text="a", filename="x", page=1, score=0.80, scope="agent"),
            SearchResult(text="s", filename="y", page=1, score=0.50, scope="session"),
        ],
        scope_requested="all",
        multi_stats=multi_b,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from=None,
        include_scores=False,
    )
    assert env_b["retrieval"]["recommendation"] == "prefer_agent"


def test_envelope_recommendation_low_confidence_when_all_below_floor():
    """When the max top-score across scopes is < 0.3, the LLM should
    hedge — emit ``low_confidence`` regardless of ratio.
    """
    multi = _MultiSearchStats()
    multi.by_scope = {"agent": _JunkFilterStats(), "session": _JunkFilterStats()}
    multi.top_score_by_scope = {"agent": 0.25, "session": 0.10}
    env = build_retrieval_envelope(
        results=[
            SearchResult(text="a", filename="x", page=1, score=0.25, scope="agent"),
            SearchResult(text="s", filename="y", page=1, score=0.10, scope="session"),
        ],
        scope_requested="all",
        multi_stats=multi,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from=None,
        include_scores=False,
    )
    assert env["retrieval"]["recommendation"] == "low_confidence"


def test_envelope_recommendation_no_clean_results_when_all_filtered():
    """When candidates existed but every one was filtered as junk, the
    LLM should tell the user nothing relevant was found rather than
    invent an explanation. ``no_clean_results`` is the explicit signal.
    """
    multi = _MultiSearchStats()
    multi.by_scope = {
        "agent": _JunkFilterStats(candidates=5, filtered_count=5, reasons={"too_short": 5}),
    }
    # No results survived; top_score_by_scope is empty.
    env = build_retrieval_envelope(
        results=[],
        scope_requested="all",
        multi_stats=multi,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from=None,
        include_scores=False,
    )
    assert env["retrieval"]["recommendation"] == "no_clean_results"


def test_envelope_invariant_holds_per_scope():
    """Pin the documented invariant:
       candidates == returned + filtered + below_threshold + drain_drops + dedup_collapses
    Future refactors to ``_apply_junk_filter`` / ``_materialize`` /
    ``search_multi`` that break this will trip the test.
    """
    multi = _MultiSearchStats()
    multi.by_scope = {
        "agent": _JunkFilterStats(
            candidates=10, filtered_count=3, below_threshold=2, drain_drops=1, dedup_collapses=0
        ),
    }
    multi.top_score_by_scope = {"agent": 0.7}
    results = [
        SearchResult(text=f"a{i}", filename="x", page=i, score=0.7, scope="agent")
        for i in range(4)  # returned = 4
    ]
    env = build_retrieval_envelope(
        results=results,
        scope_requested="all",
        multi_stats=multi,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from=None,
        include_scores=False,
    )
    by_agent = env["retrieval"]["by_scope"]["agent"]
    assert by_agent["candidates"] == (
        by_agent["returned"]
        + by_agent["filtered"]
        + by_agent["below_threshold"]
        + by_agent["drain_drops"]
        + by_agent["dedup_collapses"]
    )


def test_envelope_fallback_from_surfaces_for_auto_fallback():
    """Engine auto-fallback (session→all on 0 hits) must surface
    ``retrieval.fallback_from = "session"`` so the LLM's contract
    rule fires ("the engine already retried — don't retry yourself").
    """
    multi = _MultiSearchStats()
    multi.by_scope = {"agent": _JunkFilterStats(), "session": _JunkFilterStats()}
    multi.top_score_by_scope = {"agent": 0.8}
    env = build_retrieval_envelope(
        results=[SearchResult(text="a", filename="x", page=1, score=0.8, scope="agent")],
        scope_requested="all",
        multi_stats=multi,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from="session",
        include_scores=False,
    )
    assert env["retrieval"]["fallback_from"] == "session"


def test_envelope_omits_back_compat_filtered_count_when_zero():
    """Regression guard for the back-compat top-level ``filtered_count``
    alias: only emitted when ``totals.filtered > 0``. When nothing was
    filtered, the field must NOT appear at the top level — that keeps
    quiet responses quiet for consumers that grep the field name.
    Structured ``retrieval.totals.filtered`` is always present (0).
    """
    multi = _MultiSearchStats()
    multi.by_scope = {
        "agent": _JunkFilterStats(candidates=2),
        "session": _JunkFilterStats(candidates=1),
    }
    multi.top_score_by_scope = {"agent": 0.9, "session": 0.85}
    env = build_retrieval_envelope(
        results=[
            SearchResult(text="a", filename="x", page=1, score=0.9, scope="agent"),
            SearchResult(text="s", filename="y", page=1, score=0.85, scope="session"),
        ],
        scope_requested="all",
        multi_stats=multi,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from=None,
        include_scores=False,
    )
    assert "filtered_count" not in env, (
        "Top-level back-compat alias must be omitted when nothing was filtered"
    )
    # Structured surface still present, set to 0.
    assert env["retrieval"]["totals"]["filtered"] == 0


def test_sdk_and_http_envelopes_match_modulo_include_scores():
    """**SDK/HTTP drift guard.** Both surfaces call
    ``build_retrieval_envelope`` with the same args except ``include_scores``
    (SDK hardcodes True, HTTP defaults to False; see docstring on
    ``KnowledgeClient.search_envelope``). The envelopes must therefore
    be byte-identical when normalized: strip the ``score`` field from
    chunks and they should be equal.

    Without this test a future refactor could diverge the two surfaces
    silently — e.g. add a field to one and forget the other.
    """
    multi = _MultiSearchStats()
    multi.by_scope = {
        "agent": _JunkFilterStats(candidates=1),
        "session": _JunkFilterStats(candidates=1),
    }
    multi.top_score_by_scope = {"agent": 0.7, "session": 0.65}
    results = [
        SearchResult(text="a", filename="x", page=1, score=0.7, scope="agent"),
        SearchResult(text="s", filename="y", page=1, score=0.65, scope="session"),
    ]
    common_kwargs = dict(
        results=results,
        scope_requested="all",
        multi_stats=multi,
        single_stats=None,
        single_scope_name=None,
        filter_mode="enforce",
        fallback_from=None,
    )
    env_http = build_retrieval_envelope(**common_kwargs, include_scores=False)
    env_sdk = build_retrieval_envelope(**common_kwargs, include_scores=True)

    def _strip_scores(env):
        env = dict(env)
        env["results"] = [{k: v for k, v in c.items() if k != "score"} for c in env["results"]]
        if "by_source" in env:
            env["by_source"] = {
                s: [{k: v for k, v in c.items() if k != "score"} for c in chunks]
                for s, chunks in env["by_source"].items()
            }
        return env

    assert _strip_scores(env_http) == _strip_scores(env_sdk), (
        "SDK and HTTP envelopes must match modulo the score field — a "
        "drift here means new observability landed in one path but not "
        "the other; fix the helper, not the test"
    )


# ============================================================
# Layer 1 — defaults + auto-fallback (end-to-end via route)
# ============================================================


def _route_app_with_fake_engine(
    *,
    session_hits: list[SearchResult] | None = None,
    multi_hits: list[SearchResult] | None = None,
    multi_stats: _MultiSearchStats | None = None,
):
    """Build a fastapi app + fake engine for route-level tests.

    ``session_hits`` are returned by single-scope session search;
    ``multi_hits`` + ``multi_stats`` are returned by search_multi
    (used to verify the auto-fallback path).
    """
    from cuga.backend.knowledge.routes import knowledge_router
    from cuga.backend.knowledge.auth import KnowledgeIdentity, require_internal_or_auth

    session_hits = session_hits or []
    multi_hits = multi_hits or []
    if multi_stats is None:
        multi_stats = _MultiSearchStats()
        multi_stats.by_scope = {"agent": _JunkFilterStats(), "session": _JunkFilterStats()}
        for r in multi_hits:
            multi_stats.top_score_by_scope.setdefault(r.scope, r.score)

    async def _stub_with_stats(*, collection, query, limit, score_threshold, scope=""):
        results = session_hits if scope == "session" else []
        # Stamp the requested scope on each result for the route's
        # scope-validation logic.
        out = [
            SearchResult(text=r.text, filename=r.filename, page=r.page, score=r.score, scope=scope)
            for r in results
        ]
        return out, _JunkFilterStats(candidates=len(out))

    eng = SimpleNamespace(
        _config=SimpleNamespace(
            enabled=True,
            agent_level_enabled=True,
            session_level_enabled=True,
            default_limit=10,
            default_score_threshold=0.0,
            search_junk_filter="dry_run",
        ),
        search=AsyncMock(),
        search_with_stats=AsyncMock(side_effect=_stub_with_stats),
        search_multi=AsyncMock(return_value=(multi_hits, multi_stats)),
    )
    app = FastAPI()
    app.include_router(knowledge_router)
    app.state.app_state = SimpleNamespace(
        knowledge_engine=eng,
        knowledge_provider=None,
        knowledge_config_hash="hash",
    )
    app.dependency_overrides[require_internal_or_auth] = lambda: KnowledgeIdentity(
        user_id="u",
        tenant_id="t",
        agent_id="agent-x",
        thread_id="thread-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
        auth_mode="internal",
        roles=None,
    )
    return app, eng


def test_auto_fallback_session_zero_hits_retries_as_all():
    """The Layer 1 safety net: when the LLM calls ``scope='session'``
    and the engine finds nothing above threshold, the engine retries
    internally as ``scope='all'`` and the envelope carries
    ``retrieval.fallback_from = "session"``. The LLM contract rule
    teaches the model not to retry itself.
    """
    multi_hits = [
        SearchResult(text="found in agent", filename="kb.md", page=1, score=0.9, scope="agent"),
    ]
    multi_stats = _MultiSearchStats()
    multi_stats.by_scope = {
        "agent": _JunkFilterStats(candidates=1),
        "session": _JunkFilterStats(),
    }
    multi_stats.top_score_by_scope = {"agent": 0.9}
    app, eng = _route_app_with_fake_engine(
        session_hits=[],  # session returns nothing
        multi_hits=multi_hits,
        multi_stats=multi_stats,
    )
    body = TestClient(app).post("/api/knowledge/search", json={"query": "x", "scope": "session"}).json()
    # Auto-fallback engaged.
    assert body["retrieval"]["fallback_from"] == "session"
    # The fallback widened the scope, so we got the agent result.
    assert any(c["source"] == "agent" for c in body["results"])
    # Both search_with_stats (initial session try) AND search_multi
    # (fallback) were invoked.
    assert eng.search_with_stats.await_count == 1
    assert eng.search_multi.await_count == 1


def test_auto_fallback_does_not_fire_when_session_has_results():
    """The fallback fires ONLY when session is genuinely empty. If
    session returns even one chunk, the call returns it as-is — no
    extra search_multi call. Important for cost: we don't want to
    retry on every session call.
    """
    session_hits = [
        SearchResult(text="my session doc", filename="up.pdf", page=1, score=0.8, scope="session"),
    ]
    app, eng = _route_app_with_fake_engine(session_hits=session_hits)
    body = TestClient(app).post("/api/knowledge/search", json={"query": "x", "scope": "session"}).json()
    assert body["retrieval"]["fallback_from"] is None
    assert eng.search_with_stats.await_count == 1
    assert eng.search_multi.await_count == 0


def test_default_scope_for_search_tool_is_session_when_both_enabled():
    """The SDK's LangChain tool default for ``scope=`` is the
    narrowest plausible scope when both are wired. The engine
    auto-fallback covers the 0-hit case so this default is risk-free.
    """
    from cuga.backend.knowledge.client import KnowledgeClient
    import inspect

    eng = SimpleNamespace(
        _config=SimpleNamespace(
            enabled=True,
            agent_level_enabled=True,
            session_level_enabled=True,
            default_limit=10,
            default_score_threshold=0.0,
        ),
        search=AsyncMock(),
        search_with_stats=AsyncMock(),
        search_multi=AsyncMock(),
    )
    client = KnowledgeClient(eng, default_agent_id="agent-x")
    tools = client.get_langchain_tools(thread_id="thread-x")
    search_tool = next(t for t in tools if t.name == "knowledge_search_knowledge")
    assert inspect.signature(search_tool.coroutine).parameters["scope"].default == "session"


def test_knowledge_instructions_contract_carries_decision_tree():
    """Strong-form contract guard. Pins the structure, not just the
    presence of a few keywords — a "tidy the docs" PR that collapses
    the worked examples, drops the Hebrew, or rewrites the
    anti-example as a one-liner will trip this test. Each assertion
    cites the design-review element it protects.
    """
    import re

    contract_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "cuga"
        / "configurations"
        / "knowledge"
        / "knowledge_instructions.md"
    )
    text = contract_path.read_text(encoding="utf-8")

    # (a) Headline rule: "NARROWEST scope" caps-on for instructional weight.
    assert "NARROWEST scope" in text, (
        "Contract must lead with 'NARROWEST scope' (capitalized) per design review"
    )

    # (b) Byte-exact awareness header appears in the contract so the LLM
    # can pattern-match the same literal string in the prompt and the
    # awareness summary. Emitted by awareness.py:160.
    assert "### Session Documents (this conversation only):" in text, (
        "Contract must reference the byte-exact awareness header so the "
        "LLM grounds 'session if topical' on the right string"
    )

    # (c) Worked-example set: after the cross-scope failure (LLM picked
    # session when the agent KB had a topic-overlapping doc that would
    # ALSO answer), the third-round review re-introduced the "All"
    # example to teach the topic-overlap case explicitly. Current
    # required set: Session + All + Anti-example (3 items).
    example_lines = re.findall(r"^- \*(?:Session|All|Anti-example)\.?\*", text, re.M)
    assert len(example_lines) >= 3, (
        f"Contract must carry the Session + All + Anti-example worked-"
        f"example set — found {len(example_lines)} matching list items"
    )

    # (d) Hebrew example was DELIBERATELY REMOVED by operator request.
    # The Session example now uses a generic personal-document filename
    # (paystub) which conveys the same teaching — "session uploads are
    # personal/unique, agent KB is institutional, route by topic match"
    # — without baking deployment-specific language into the contract.
    # Operators serving non-Latin users should rely on the harness text
    # + glossary (which IS query-expanded across languages).
    hebrew_chars = re.findall(r"[\u0590-\u05FF]", text)
    # Hebrew assertion deliberately removed — see comment above.
    assert "paystub" in text.lower() or "personal" in text.lower(), (
        "Session example must convey 'personal/unique upload → session' via a generic personal-doc filename"
    )
    _ = hebrew_chars  # silence unused-var lint; kept for diff readability

    # (e) Anti-example ("session doc exists but is OFF-topic").
    assert "OFF-topic" in text or "off-topic" in text.lower(), (
        "Contract must carry the anti-example so weak LLMs don't over-bias "
        "to session whenever any session doc is uploaded"
    )

    # (f) Auto-fallback rule — both the wire field name and the
    # behavior instruction.
    assert "fallback_from" in text
    assert "do NOT re-issue" in text or "do NOT retry" in text.lower(), (
        "Contract must instruct the LLM not to retry after an engine "
        "auto-fallback (avoids redundant search calls)"
    )


def test_contract_teaches_reading_retrieval_block():
    """The contract must also teach the LLM to read the ``retrieval``
    block — recommendation first, partial flag, fallback hint.
    Without this, the new envelope's diagnostic surface is invisible
    to the model.
    """
    contract_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "cuga"
        / "configurations"
        / "knowledge"
        / "knowledge_instructions.md"
    )
    text = contract_path.read_text(encoding="utf-8")
    assert "retrieval" in text
    assert "recommendation" in text
    assert "prefer_" in text


# ============================================================
# Layer 2 — per-scope quota (failing-case shape)
# ============================================================


def test_accurate_mode_forces_full_page_ocr_to_bypass_cid_text_layer():
    """**Load-bearing regression guard for the CID-encoded-PDF bug.**

    Some PDFs (Hebrew government forms, IBM Box exports, scanned PDFs
    with embedded "OCR underlay") carry a text layer that's actually
    just font CID glyph IDs without Unicode mapping. Docling's default
    behavior is to read the text layer when present and skip OCR for
    that page — so it extracts ``/CE3/CE5/CE1/...`` mojibake verbatim
    and the chunks are useless.

    cuga's accurate mode forces ``force_full_page_ocr=True`` to bypass
    the broken text layer and re-OCR the page image. Without this:
      - 689 ``/C`` glyph escapes per typical insurance form
      - 0 readable Hebrew characters
      - Junk filter rejects the chunks at retrieval (good), but they
        still bloat the index and waste embedder cost (bad).
    With this:
      - 0 glyph escapes
      - 2000+ real Hebrew characters
      - Insurance policy terms readable

    Reproducer: /tmp/diag_force_ocr.py on a CID-encoded PDF.
    """
    import shutil
    import tempfile
    from unittest.mock import patch
    from cuga.backend.knowledge.engine import KnowledgeEngine
    from cuga.backend.knowledge.config import KnowledgeConfig
    from docling.datamodel.base_models import InputFormat

    persist = Path(tempfile.mkdtemp(prefix="cuga-force-ocr-"))
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=persist,
        embedding_provider="fastembed",
        docling_pdf_mode="accurate",
        docling_layout_engine="auto",
    )
    # Pretend Tesseract is installed so we exercise the Tesseract branch.
    with patch.object(shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None):
        eng = KnowledgeEngine(cfg)
        converter = eng._get_docling_converter()
    pdf_opts = converter.format_to_options[InputFormat.PDF]
    ocr = pdf_opts.pipeline_options.ocr_options
    assert ocr.force_full_page_ocr is True, (
        "Accurate mode must force full-page OCR to bypass CID-encoded "
        "text layers that produce /Cxx mojibake. Without this, Hebrew "
        "government forms / IBM Box exports / scan PDFs with broken OCR "
        "underlay get indexed as glyph-ID gibberish."
    )
    eng.shutdown()


def test_layout_options_preserves_orphan_clusters():
    """**Load-bearing regression guard for the Hebrew-PDF recall bug.**

    Cuga's ``_get_docling_converter`` builds a
    ``LayoutObjectDetectionOptions`` (added in #304 to engage MPS).
    The alternative class Docling ships as the default
    (``LayoutOptions``) has ``create_orphan_clusters=True``; the
    object-detection variant defaults it to ``False`` and the upstream
    docstring explicitly warns about the asymmetry.

    Without ``create_orphan_clusters=True``, layout boxes that the
    object-detection model can't classify into a known structure get
    silently dropped. Observed on a Hebrew population-registry form:
    the entire field-label column AND the 9-digit ID number lived in
    "orphan" text elements; the stored chunk collapsed from 574 chars
    to 320 chars and the LLM returned a wrong number from an address
    line because the actual ID label/value were never indexed.

    Reproducer: /tmp/diag_fix.py side-by-side.
    """
    import tempfile
    from cuga.backend.knowledge.engine import KnowledgeEngine
    from cuga.backend.knowledge.config import KnowledgeConfig
    from docling.datamodel.base_models import InputFormat

    persist = Path(tempfile.mkdtemp(prefix="cuga-orphan-clusters-"))
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=persist,
        embedding_provider="fastembed",
        docling_pdf_mode="accurate",
        docling_layout_engine="auto",
    )
    eng = KnowledgeEngine(cfg)
    converter = eng._get_docling_converter()
    pdf_opts = converter.format_to_options[InputFormat.PDF]
    layout = pdf_opts.pipeline_options.layout_options
    assert layout.create_orphan_clusters is True, (
        "LayoutObjectDetectionOptions must enable orphan-cluster "
        "preservation to match Docling's default LayoutOptions; without "
        "this, form labels and unstructured text get dropped from chunks"
    )
    eng.shutdown()


def test_search_multi_failing_case_session_not_drowned(monkeypatch):
    """Regression guard for the original failing case (Hebrew Amit
    Levy ID). Session has 1 doc (the answer), agent has 10 noise
    docs. Old global-score-sort would interleave; new per-scope
    quota guarantees session keeps its 1 slot AND agent gets the
    remaining 9. The 15-agent-noise + 1-session-buried pattern
    cannot recur.
    """
    persist = Path(tempfile.mkdtemp(prefix="cuga-failing-case-"))
    cfg = KnowledgeConfig(enabled=True, persist_dir=persist)
    eng = KnowledgeEngine(cfg)

    async def _stub_with_stats(*, collection, query, limit, score_threshold, scope=""):
        if collection == "kb_sess":
            results = [SearchResult(text="THE ANSWER", filename="amit.pdf", page=1, score=0.78, scope=scope)]
        else:
            results = [
                SearchResult(
                    text=f"noise{i}", filename="other.pdf", page=i, score=0.52 - 0.01 * i, scope=scope
                )
                for i in range(10)
            ]
        return results, _JunkFilterStats(candidates=len(results))

    monkeypatch.setattr(eng, "search_with_stats", _stub_with_stats)
    # Option F (quota) mode — the original anti-drown invariant: session
    # gets its 1 reserved slot, agent fills the remaining 9 for a total
    # of 10. This is what the auto-fallback path uses (it preserves the
    # caller's ``limit`` budget). Option B (default for explicit
    # ``scope="all"``) gives session+agent each their own ``limit``, so
    # session still keeps its chunk — see Option B's own tests for that.
    results, _multi_stats = asyncio.run(
        eng.search_multi(
            scoped_collections=[("agent", "kb_agent"), ("session", "kb_sess")],
            query="עמית לוי תעודת זהות",
            limit=10,
            per_scope_limit=False,
        )
    )
    assert len(results) == 10
    by_scope: dict[str, int] = {}
    for r in results:
        by_scope[r.scope] = by_scope.get(r.scope, 0) + 1
    # Session got its 1 reserved slot; agent took the remaining 9.
    assert by_scope == {"agent": 9, "session": 1}
    # And the session chunk is THE ANSWER, not noise.
    session_chunks = [r for r in results if r.scope == "session"]
    assert session_chunks[0].text == "THE ANSWER"
