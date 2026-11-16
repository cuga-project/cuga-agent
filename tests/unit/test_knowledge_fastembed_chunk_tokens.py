"""Docling sizes fastembed chunks with fastembed's own tokenizer, which fastembed loads with
truncation at the model's limit. Counted with it, no text is longer than 512 tokens, so a chunk
of any size looked like it fit and the embedder cut it off (#1166)."""

from types import SimpleNamespace

import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import WhitespaceSplit

from cuga.backend.knowledge.engine import _fastembed_docling_tokenizer_cls

pytestmark = pytest.mark.unit

LIMIT = 8
TWENTY_WORDS = " ".join(f"w{i}" for i in range(20))


def _text_embedding():
    """A stand-in for fastembed's TextEmbedding: its tokenizer truncates at LIMIT, as fastembed's does."""
    vocab = {"[UNK]": 0, "passage:": 1, **{f"w{i}": i + 2 for i in range(20)}}
    tok = Tokenizer(WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = WhitespaceSplit()
    tok.enable_truncation(max_length=LIMIT)
    return SimpleNamespace(model=SimpleNamespace(tokenizer=tok))


def _chunker_tokenizer(text_embedding, passage_prefix=""):
    return _fastembed_docling_tokenizer_cls()(
        text_embedding=text_embedding, max_tokens=LIMIT, passage_prefix=passage_prefix
    )


def test_a_text_longer_than_the_model_limit_counts_in_full():
    assert _chunker_tokenizer(_text_embedding()).count_tokens(TWENTY_WORDS) == 20


def test_the_count_includes_the_prefix_the_embedder_adds():
    te = _text_embedding()
    plain = _chunker_tokenizer(te).count_tokens("w1 w2 w3")
    prefixed = _chunker_tokenizer(te, passage_prefix="passage: ").count_tokens("w1 w2 w3")
    assert (plain, prefixed) == (3, 4)


def test_the_splitter_gets_a_tokenizer_that_does_not_truncate():
    # HybridChunker hands get_tokenizer() to semchunk to split long text.
    tok = _chunker_tokenizer(_text_embedding()).get_tokenizer()
    assert len(tok.encode(TWENTY_WORDS).ids) == 20


def test_the_embedders_own_tokenizer_still_truncates():
    # The ONNX model needs inputs within its limit: only the chunker's copy counts past it.
    te = _text_embedding()
    _chunker_tokenizer(te).count_tokens(TWENTY_WORDS)
    assert len(te.model.tokenizer.encode(TWENTY_WORDS).ids) == LIMIT
