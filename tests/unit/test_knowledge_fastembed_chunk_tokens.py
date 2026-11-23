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


def test_every_chunker_for_one_model_shares_one_counter():
    # The engine builds a chunker per file, and semchunk keeps every tokenizer it is given for the
    # life of the process: a copy per file would keep ~300 MB per file of the E5 tokenizer alive.
    te = _text_embedding()
    first, second = _chunker_tokenizer(te), _chunker_tokenizer(te, passage_prefix="passage: ")
    assert first.get_tokenizer() is second.get_tokenizer()


def test_a_paragraph_longer_than_the_limit_is_split_into_chunks_that_fit():
    # The real HybridChunker and semchunk, with the prefix the embedder adds to each passage.
    from docling_core.transforms.chunker import HybridChunker
    from docling_core.types.doc import DocItemLabel, DoclingDocument

    doc = DoclingDocument(name="long")
    doc.add_text(label=DocItemLabel.TEXT, text=TWENTY_WORDS)
    tok = _chunker_tokenizer(_text_embedding(), passage_prefix="passage: ")
    chunker = HybridChunker(tokenizer=tok)

    texts = [chunker.contextualize(chunk=c) for c in chunker.chunk(doc)]

    # One token per word in this vocabulary, plus one for "passage:".
    assert max(len(t.split()) + 1 for t in texts) <= LIMIT
    assert " ".join(texts).split() == TWENTY_WORDS.split()


def test_the_engine_gives_the_chunker_its_embedders_passage_prefix(tmp_path):
    from unittest.mock import MagicMock, patch

    from cuga.backend.knowledge.config import KnowledgeConfig
    from cuga.backend.knowledge.engine import KnowledgeEngine, _FastEmbedEmbeddings

    eng = KnowledgeEngine(KnowledgeConfig(enabled=True, persist_dir=tmp_path, embedding_provider="fastembed"))
    embedder = MagicMock(spec=_FastEmbedEmbeddings)
    embedder._model = _text_embedding()
    embedder._model.model_name = "intfloat/multilingual-e5-small"
    embedder._passage_prefix = "passage: "
    eng._default_embeddings = embedder
    eng._ensure_embeddings = MagicMock()

    with patch("docling_core.transforms.chunker.HybridChunker") as hybrid_chunker:
        eng._build_docling_chunker(chunk_size=1000)

    assert hybrid_chunker.call_args.kwargs["tokenizer"].passage_prefix == "passage: "


def test_semchunks_counters_are_dropped_after_a_document():
    # Pins the semchunk internals _forget_semchunk_counters clears.
    from semchunk import semchunk

    from cuga.backend.knowledge.engine import _forget_semchunk_counters

    semchunk.chunkerify(_text_embedding().model.tokenizer, chunk_size=4)(TWENTY_WORDS)
    assert isinstance(semchunk._memoized_token_counters, dict) and semchunk._memoized_token_counters

    _forget_semchunk_counters()

    assert semchunk._memoized_token_counters == {}
