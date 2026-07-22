"""End-to-end integration test for the knowledge CITATION pipeline.

Covers the seam every existing unit test skips: a REAL search stamps a REAL
cite_id into the per-thread SourceLedger, then FinalAnswerNode.apply_citation_resolution
resolves markers the model emitted (in BOTH ASCII [sN] and lenticular 【sN】) into
per-message display numbers [n] and attaches self-contained source snapshots.

This is the net that would have caught the "【sN】 emitted but no chips / no
sources" regression: before _MARKER_RE learned the lenticular family, 【s1】 fell
through, never rewrote to [1], and state.sources stayed empty — silently. The
existing unit tests either stamp cite_ids but never run an answer against them
(test_envelope_citations), or hand-seed the ledger with a raw register() (not the
search seam) and only test ASCII [s1] (test_final_answer_citations).

Deterministic: real KnowledgeEngine over a tmp dir + fastembed (already
installed); no network, no API key, no live LLM. Run with the default suite:

    pytest tests/integration/test_knowledge_citation_e2e.py -v
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.knowledge.client import KnowledgeClient
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine
from cuga.backend.knowledge.sources import (
    _reset_all_ledgers_for_tests,
    get_ledger,
    set_agent_citations_lookup,
    set_session_override_lookup,
)

pytestmark = pytest.mark.slow


# --- fixtures (copied from test_knowledge_integration.py; the integration
# --- conftest only ships pgvector helpers, so these are not shared) -----------


@pytest_asyncio.fixture
async def engine(monkeypatch):
    """A temporary, isolated real KnowledgeEngine (fastembed, no network)."""
    tmpdir = tempfile.mkdtemp()
    isolated_db = str(Path(tmpdir) / "cuga_storage.db")
    monkeypatch.setattr(
        "cuga.backend.knowledge.engine.get_storage_connection_params",
        lambda: ("local", isolated_db, ""),
    )
    config = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tmpdir),
        embedding_provider="fastembed",
        embedding_model="",
        chunk_size=200,
        chunk_overlap=50,
        max_ingest_workers=1,
        max_pending_tasks=5,
    )
    eng = KnowledgeEngine(config)
    await eng.warmup()
    yield eng
    await eng.aclose()
    eng.shutdown()


@pytest.fixture
def sample_txt(tmp_path):
    """A small doc whose content is trivially retrievable by the query below."""
    p = tmp_path / "sample.txt"
    p.write_text(
        "The knowledge engine uses LangChain vector search for documents. "
        "It supports PDF, DOCX, XLSX, PPTX, HTML, and many other formats. "
        "Documents are chunked, embedded, and stored in a local vector database. "
        "Users can search using natural language queries."
    )
    return p


def setup_function():
    # Isolate the module-global ledger AND the enablement hooks between tests —
    # both are process-global state shared with every other citation test.
    _reset_all_ledgers_for_tests()
    set_session_override_lookup(None)
    set_agent_citations_lookup(None)


def teardown_function():
    set_session_override_lookup(None)
    set_agent_citations_lookup(None)
    _reset_all_ledgers_for_tests()


async def _ingest_and_wait(engine: KnowledgeEngine, collection: str, path: Path) -> None:
    task = await engine.ingest(collection, path)
    task_id = task["task_id"]
    for _ in range(60):
        t = await engine.get_task(task_id)
        if t and t["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.5)
    t = await engine.get_task(task_id)
    assert t and t["status"] == "completed", f"ingest did not complete: {t}"
    await asyncio.sleep(0.2)  # let the vector store settle (mirrors the ingest suite)


def _client(engine: KnowledgeEngine) -> KnowledgeClient:
    # No agent_collection_hash → _resolve_collection('agent') == 'kb_agent_test',
    # and the SAME resolution is used for ingest and search below.
    return KnowledgeClient(engine, default_agent_id="test")


@pytest.mark.asyncio
async def test_real_search_stamps_ledger_then_answer_resolves_both_bracket_styles(engine, sample_txt):
    """The full net: real ingest → real search stamps a real cite_id into the
    thread ledger → an answer citing that id in ASCII [s1] AND lenticular 【s1】
    resolves to ASCII [n] with a populated source snapshot.

    One real cite_id cited twice (ASCII once, lenticular once) → both must resolve
    to the SAME [1]. If the lenticular branch of _MARKER_RE regresses, the second
    marker survives verbatim and this test fails — the exact bug we guard.
    """
    thread = "e2e-cite-thread"
    client = _client(engine)
    collection = client._resolve_collection("agent")
    await _ingest_and_wait(engine, collection, sample_txt)

    # REAL search seam — this is what stamps + registers into get_ledger(thread).
    envelope = await client.search_envelope("LangChain vector search", scope="agent", thread_id=thread)
    results = envelope["results"]
    assert results, "expected at least one real search hit"
    cid = results[0]["cite_id"]
    assert cid == "s1", f"first real chunk should be stamped s1, got {cid!r}"

    # The SAME module-global ledger keyed by this thread now holds the record.
    ledger = get_ledger(thread, create=False)
    assert ledger is not None and ledger.get(cid) is not None

    # REAL resolution. Model answer cites the real id in BOTH bracket styles.
    state = SimpleNamespace(
        final_answer=(f"LangChain powers document search [{cid}]. It supports many formats 【{cid}】."),
        thread_id=thread,
        sources=[],
    )
    FinalAnswerNode.apply_citation_resolution(state)

    # BOTH markers rewrote to the SAME ASCII display number; none survived.
    assert "[1]" in state.final_answer
    assert f"[{cid}]" not in state.final_answer  # ASCII marker consumed
    assert f"【{cid}】" not in state.final_answer  # lenticular marker consumed (the bug)
    assert "【" not in state.final_answer and "】" not in state.final_answer
    assert state.final_answer.count("[1]") == 2  # one id, cited twice -> same number

    # Exactly one source snapshot, carrying the real doc's identity.
    assert len(state.sources) == 1
    snap = state.sources[0]
    assert snap["n"] == 1
    assert snap["cite_id"] == cid
    assert snap["filename"] == "sample.txt"
    assert snap["scope"] == "agent"
    assert snap["snippet"]  # non-empty chunk text from the real document


@pytest.mark.asyncio
async def test_two_real_chunks_resolve_to_ordered_sources(engine, sample_txt):
    """Two distinct real chunks (s1, s2) cited in mixed bracket styles must
    resolve to [1] and [2] in first-appearance order with two ordered snapshots.
    Guards that the lenticular id is registered AND resolvable, not just ASCII.
    """
    thread = "e2e-cite-thread-2"
    client = _client(engine)
    collection = client._resolve_collection("agent")
    await _ingest_and_wait(engine, collection, sample_txt)

    envelope = await client.search_envelope(
        "LangChain vector search formats database", scope="agent", thread_id=thread, limit=5
    )
    results = envelope["results"]
    if len(results) < 2:
        pytest.skip("small doc chunked into <2 pieces; ordering variant needs 2 chunks")
    id1, id2 = results[0]["cite_id"], results[1]["cite_id"]
    assert {id1, id2} == {"s1", "s2"}

    # Cite s2 FIRST (lenticular) then s1 (ASCII): display numbers follow first
    # appearance, so 【s2】 -> [1] and [s1] -> [2].
    state = SimpleNamespace(
        final_answer=f"Formats first 【{id2}】, then the engine [{id1}].",
        thread_id=thread,
        sources=[],
    )
    FinalAnswerNode.apply_citation_resolution(state)

    assert state.final_answer == "Formats first [1], then the engine [2]."
    assert [s["n"] for s in state.sources] == [1, 2]
    assert [s["cite_id"] for s in state.sources] == [id2, id1]
    assert all(s["filename"] == "sample.txt" for s in state.sources)


@pytest.mark.asyncio
async def test_disabled_session_override_strips_real_markers(engine, sample_txt):
    """When the session override disables citations, real ledger-backed markers
    are STRIPPED (not resolved) and sources stay empty — the strip path.
    """
    thread = "e2e-cite-thread-off"
    client = _client(engine)
    collection = client._resolve_collection("agent")
    await _ingest_and_wait(engine, collection, sample_txt)
    envelope = await client.search_envelope("LangChain vector search", scope="agent", thread_id=thread)
    cid = envelope["results"][0]["cite_id"]

    set_session_override_lookup(lambda tid: {"citations_enabled": False})
    try:
        state = SimpleNamespace(
            final_answer=f"answer [{cid}] and 【{cid}】 done",
            thread_id=thread,
            sources=[],
        )
        FinalAnswerNode.apply_citation_resolution(state)
        assert f"[{cid}]" not in state.final_answer
        assert f"【{cid}】" not in state.final_answer
        assert "[1]" not in state.final_answer
        assert state.sources == []
    finally:
        set_session_override_lookup(None)


@pytest.mark.asyncio
async def test_unsupported_bracket_left_visible_not_silent(engine, sample_txt):
    """Canary: a real cite_id in an UNSUPPORTED bracket style — (s1) — is left
    byte-identical and produces no source (it's logged, not rendered). Pins the
    intended-VISIBLE failure mode, distinct from 【sN】 which IS resolved, so no one
    "fixes" it by silently swallowing the marker.
    """
    thread = "e2e-cite-thread-unsupported"
    client = _client(engine)
    collection = client._resolve_collection("agent")
    await _ingest_and_wait(engine, collection, sample_txt)
    envelope = await client.search_envelope("LangChain vector search", scope="agent", thread_id=thread)
    cid = envelope["results"][0]["cite_id"]

    state = SimpleNamespace(final_answer=f"paren cite ({cid}) here", thread_id=thread, sources=[])
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == f"paren cite ({cid}) here"  # unchanged
    assert state.sources == []
