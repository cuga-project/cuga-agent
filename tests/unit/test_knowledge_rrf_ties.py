"""Equal fused scores in hybrid search are broken by the vector rank, not by file name.

When the vector and keyword legs swap their top two chunks, both score 1/(k+1) + 1/(k+2).
Breaking that tie on file name let the alphabetically first file win: in the synthetic
multi-hop benchmark, a record that only mentions an identifier beat the record named by it.
"""

import pytest

from cuga.backend.knowledge.engine import SearchResult, _rrf_fuse


def _r(filename: str, text: str) -> SearchResult:
    return SearchResult(text=text, filename=filename, page=None, score=0.5)


@pytest.mark.unit
def test_swapped_top_two_keep_the_vector_leg_order():
    # "a_mention.txt" sorts first, so a file-name tie-break would put it on top.
    named = _r("b_record.txt", "Record K1. To continue, consult record K2.")
    mention = _r("a_mention.txt", "Record K0. To continue, consult record K1.")

    fused = _rrf_fuse(dense=[named, mention], lexical=[mention, named])

    assert named.rrf_score == mention.rrf_score
    assert [r.filename for r in fused] == ["b_record.txt", "a_mention.txt"]


@pytest.mark.unit
def test_dense_only_and_keyword_only_tie_prefers_the_dense_hit():
    dense_hit = _r("b.txt", "dense")
    keyword_hit = _r("a.txt", "keyword")

    fused = _rrf_fuse(dense=[dense_hit], lexical=[keyword_hit])

    assert dense_hit.rrf_score == keyword_hit.rrf_score
    assert [r.filename for r in fused] == ["b.txt", "a.txt"]


@pytest.mark.unit
def test_unequal_scores_still_rank_by_fused_score():
    both = _r("z.txt", "in both legs")
    dense_only = _r("a.txt", "dense only")

    fused = _rrf_fuse(dense=[dense_only, both], lexical=[both])

    assert [r.filename for r in fused] == ["z.txt", "a.txt"]
