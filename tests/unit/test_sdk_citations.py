"""SDK surface for knowledge citations: InvokeResult.sources + enable_citations."""

from cuga.sdk import InvokeResult


def test_invoke_result_sources_default_and_roundtrip():
    assert InvokeResult().sources == []
    r = InvokeResult(answer="x [1]", sources=[{"n": 1, "cite_id": "s1", "filename": "f.pdf"}])
    assert r.sources[0]["filename"] == "f.pdf"
    assert str(r) == "x [1]"


def test_enable_citations_param_stored():
    from cuga.sdk import CugaAgent

    agent = CugaAgent(enable_citations=False)
    assert agent._enable_citations is False


def test_enable_citations_defaults_to_none():
    from cuga.sdk import CugaAgent

    agent = CugaAgent()
    assert agent._enable_citations is None
