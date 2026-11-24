"""Docling sizes fastembed chunks with fastembed's own tokenizer, which fastembed loads with
truncation at the model's limit. Counted with it, no text is longer than that limit, so a chunk
of any size looked like it fit and the embedder cut it off (#1166)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import WhitespaceSplit
from tokenizers.processors import TemplateProcessing

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import (
    KnowledgeEngine,
    _FastEmbedEmbeddings,
    _fastembed_docling_tokenizer_cls,
)

pytestmark = pytest.mark.unit

LIMIT = 8
TWENTY_WORDS = " ".join(f"w{i}" for i in range(20))


def _tokenizer(limit=LIMIT, special_tokens=False):
    """A stand-in for the tokenizer fastembed loads: it truncates at ``limit``, as fastembed's does.
    One token per word, plus one for "passage:", plus [CLS] and [SEP] with ``special_tokens``."""
    vocab = {"[UNK]": 0, "[CLS]": 1, "[SEP]": 2, "passage:": 3, **{f"w{i}": i + 4 for i in range(20)}}
    tok = Tokenizer(WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = WhitespaceSplit()
    if special_tokens:
        tok.post_processor = TemplateProcessing(
            single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)]
        )
    if limit:
        tok.enable_truncation(max_length=limit)
    return tok


def _embedder(passage_prefix="", limit=LIMIT, special_tokens=False):
    """A real _FastEmbedEmbeddings around the stand-in tokenizer, without loading a model."""
    emb = object.__new__(_FastEmbedEmbeddings)
    emb._model = SimpleNamespace(model=SimpleNamespace(tokenizer=_tokenizer(limit, special_tokens)))
    emb._passage_prefix = passage_prefix
    emb._query_prefix = ""
    return emb


def _chunker_tokenizer(embedder):
    return _fastembed_docling_tokenizer_cls()(embedder=embedder, max_tokens=LIMIT)


def _engine(tmp_path, embedder, **config):
    eng = KnowledgeEngine(
        KnowledgeConfig(enabled=True, persist_dir=tmp_path, embedding_provider="fastembed", **config)
    )
    eng._default_embeddings = embedder
    eng._ensure_embeddings = MagicMock()
    return eng


def test_a_text_longer_than_the_model_limit_counts_in_full():
    assert _chunker_tokenizer(_embedder()).count_tokens(TWENTY_WORDS) == 20


def test_the_count_includes_the_prefix_and_special_tokens_the_embedder_adds():
    plain = _chunker_tokenizer(_embedder()).count_tokens("w1 w2 w3")
    prefixed = _chunker_tokenizer(_embedder("passage: ", special_tokens=True)).count_tokens("w1 w2 w3")
    assert (plain, prefixed) == (3, 6)


def test_the_embedders_own_tokenizer_still_truncates():
    # The ONNX model needs inputs within its limit: only the chunker's copy counts past it.
    emb = _embedder()
    _chunker_tokenizer(emb).count_tokens(TWENTY_WORDS)
    assert len(emb._model.model.tokenizer.encode(TWENTY_WORDS).ids) == LIMIT


def test_every_chunker_for_one_embedder_shares_one_counter():
    # The engine builds a chunker per file, and semchunk keeps every tokenizer it is given for the
    # life of the process: a copy per file would keep ~300 MB per file of the E5 tokenizer alive.
    emb = _embedder()
    _chunker_tokenizer(emb).count_tokens("w1")
    first = emb.passage_token_counter()
    _chunker_tokenizer(emb).get_tokenizer().encode("w1")
    assert emb.passage_token_counter() is first


@pytest.mark.parametrize("limit", [128, 256, 512])
def test_the_chunk_limit_is_where_the_model_truncates(tmp_path, limit):
    # fastembed truncates all-MiniLM-L6-v2 at 256 and paraphrase-multilingual-MiniLM-L12-v2 at 128.
    eng = _engine(tmp_path, _embedder(limit=limit), embedding_model="sentence-transformers/all-MiniLM-L6-v2")
    tok = eng.get_chunking_tokenizer()
    assert (tok.kind, tok.safe_max_tokens) == ("fastembed", limit)


def test_a_tokenizer_without_truncation_keeps_the_default_limit():
    assert _embedder(limit=None).chunk_token_limit(default=512) == 512


def test_a_paragraph_longer_than_the_limit_is_split_into_chunks_that_fit():
    # The real HybridChunker and semchunk, with the prefix and special tokens the embedder adds.
    from docling_core.transforms.chunker import HybridChunker
    from docling_core.types.doc import DocItemLabel, DoclingDocument

    doc = DoclingDocument(name="long")
    doc.add_text(label=DocItemLabel.TEXT, text=TWENTY_WORDS)
    emb = _embedder("passage: ", special_tokens=True)
    chunker = HybridChunker(tokenizer=_chunker_tokenizer(emb))

    texts = [chunker.contextualize(chunk=c) for c in chunker.chunk(doc)]

    assert max(emb.count_passage_tokens(t) for t in texts) <= LIMIT
    assert " ".join(texts).split() == TWENTY_WORDS.split()


def test_a_long_paragraph_is_split_once(monkeypatch):
    # semchunk must count the prefix and special tokens too: else its pieces are each a few tokens
    # over the limit and HybridChunker splits the paragraph again with a smaller budget.
    from docling_core.transforms.chunker import HybridChunker, hybrid_chunker
    from docling_core.types.doc import DocItemLabel, DoclingDocument

    budgets: list[int] = []
    chunkerify = hybrid_chunker.semchunk.chunkerify

    def spy(tokenizer, chunk_size, **kwargs):
        budgets.append(chunk_size)
        return chunkerify(tokenizer, chunk_size=chunk_size, **kwargs)

    monkeypatch.setattr(hybrid_chunker.semchunk, "chunkerify", spy)
    doc = DoclingDocument(name="long")
    doc.add_text(label=DocItemLabel.TEXT, text=TWENTY_WORDS)
    chunker = HybridChunker(tokenizer=_chunker_tokenizer(_embedder("passage: ", special_tokens=True)))

    list(chunker.chunk(doc))

    assert budgets == [LIMIT]


def test_the_chunker_counts_with_the_collections_own_embedder(tmp_path):
    # A collection pinned to an E5 model is chunked with its prefix, not the default embedder's.
    eng = _engine(tmp_path, _embedder())
    e5 = _embedder("passage: ")
    pinned = (KnowledgeConfig(enabled=True, persist_dir=tmp_path, embedding_provider="fastembed"), e5)

    with patch("docling_core.transforms.chunker.HybridChunker") as hybrid_chunker:
        eng._build_docling_chunker(chunk_size=1000, pinned=pinned)

    assert hybrid_chunker.call_args.kwargs["tokenizer"].count_tokens("w1 w2") == 3


def test_semchunks_counters_are_dropped_after_a_document():
    # Pins the semchunk internals _forget_semchunk_counters clears.
    from semchunk import semchunk

    from cuga.backend.knowledge.engine import _forget_semchunk_counters

    semchunk.chunkerify(_tokenizer(), chunk_size=4)(TWENTY_WORDS)
    assert isinstance(semchunk._memoized_token_counters, dict) and semchunk._memoized_token_counters

    _forget_semchunk_counters()

    assert semchunk._memoized_token_counters == {}


def _load_with_docling(tmp_path, eng, load):
    """Run _load_document on a PDF with DoclingLoader replaced by ``load``."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    loader_cls = MagicMock()
    loader_cls.return_value.load.side_effect = load
    with (
        patch("langchain_docling.DoclingLoader", loader_cls),
        patch.object(KnowledgeEngine, "_get_docling_converter", MagicMock()),
    ):
        return eng._load_document(pdf)


@pytest.mark.parametrize("fails", [False, True], ids=["parsed", "parse-failed"])
def test_loading_a_document_frees_semchunks_counters(tmp_path, fails):
    from semchunk import semchunk

    def load():
        semchunk.chunkerify(_tokenizer(), chunk_size=4)(TWENTY_WORDS)  # as HybridChunker does
        assert semchunk._memoized_token_counters
        if fails:
            raise RuntimeError("docling failed")
        return [Document(page_content="w1 w2")]

    eng = _engine(tmp_path, _embedder())
    if fails:
        with pytest.raises(Exception, match="docling failed"):
            _load_with_docling(tmp_path, eng, load)
    else:
        _load_with_docling(tmp_path, eng, load)

    assert semchunk._memoized_token_counters == {}


def test_only_a_chunk_over_the_limit_is_split_again(tmp_path):
    # Some Docling paths do not re-check their output (a table cell over the limit). The guard counts
    # each fastembed chunk as the embedder does and re-splits only the ones that do not fit.
    fits = "w1 w2 w3 w4 w5 w6 w7"  # 7 tokens + "passage:"
    too_long = "\n\n".join(" ".join(f"w{i}" for i in range(j, j + 5)) for j in (0, 5, 10, 15))
    eng = _engine(tmp_path, _embedder("passage: "), chunk_overlap=0)

    docs = _load_with_docling(
        tmp_path, eng, lambda: [Document(page_content=fits), Document(page_content=too_long)]
    )

    texts = [d.page_content for d in docs]
    assert texts[0] == fits
    assert len(texts) > 2
    assert max(eng._default_embeddings.count_passage_tokens(t) for t in texts) <= LIMIT
    assert " ".join(texts[1:]).split() == too_long.split()
