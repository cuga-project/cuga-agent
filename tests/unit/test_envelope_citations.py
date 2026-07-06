# tests/unit/test_envelope_citations.py
from types import SimpleNamespace

from cuga.backend.knowledge.sources import (
    annotate_envelope_with_citations,
    CITATION_DIRECTIVE,
    _reset_all_ledgers_for_tests,
    get_ledger,
)


def _result(text, filename="a.pdf", page=1, scope="agent"):
    return SimpleNamespace(text=text, filename=filename, page=page, scope=scope,
                           score=0.8, section_path="")


def _envelope(results):
    chunks = [{"source": r.scope, "text": r.text, "filename": r.filename, "page": r.page}
              for r in results]
    env = {"scope": "agent", "results": chunks,
           "retrieval": {"reading_directive": "base directive."}}
    # emulate envelope.py by_source sharing the SAME dict objects
    env["by_source"] = {"agent": [c for c in chunks]}
    return env


def setup_function():
    _reset_all_ledgers_for_tests()


def test_stamps_cite_ids_and_extends_directive():
    results = [_result("one"), _result("two")]
    env = _envelope(results)
    annotate_envelope_with_citations(env, results, thread_id="t-1", query="q")
    assert env["results"][0]["cite_id"] == "s1"
    assert env["results"][1]["cite_id"] == "s2"
    # by_source shares dicts -> stamped too
    assert env["by_source"]["agent"][0]["cite_id"] == "s1"
    assert CITATION_DIRECTIVE.strip() in env["retrieval"]["reading_directive"]


def test_same_chunk_next_call_keeps_cite_id():
    results = [_result("stable")]
    env1 = _envelope(results)
    annotate_envelope_with_citations(env1, results, thread_id="t-1", query="q1")
    env2 = _envelope(results)
    annotate_envelope_with_citations(env2, results, thread_id="t-1", query="q2")
    assert env1["results"][0]["cite_id"] == env2["results"][0]["cite_id"] == "s1"
    assert len(get_ledger("t-1")) == 1


def test_no_thread_id_is_a_noop():
    results = [_result("x")]
    env = _envelope(results)
    annotate_envelope_with_citations(env, results, thread_id="", query="q")
    assert "cite_id" not in env["results"][0]
    assert env["retrieval"]["reading_directive"] == "base directive."


def test_result_count_mismatch_is_safe():
    results = [_result("x")]
    env = _envelope(results)
    env["results"].append({"source": "agent", "text": "phantom"})
    annotate_envelope_with_citations(env, results, thread_id="t-1", query="q")
    assert env["results"][0]["cite_id"] == "s1"
    assert "cite_id" not in env["results"][1]
