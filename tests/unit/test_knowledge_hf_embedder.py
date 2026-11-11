"""The huggingface embedding provider adds the E5 query and passage prefixes."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from cuga.backend.knowledge.engine import _PyTorchEmbeddings

pytestmark = pytest.mark.unit


class _Batch(dict):
    def to(self, device):
        return self


class _Tokenizer:
    model_max_length = 512

    def __init__(self):
        self.seen: list[str] = []

    def __call__(self, texts, **kwargs):
        self.seen.extend(texts)
        return _Batch(attention_mask=torch.ones(len(texts), 3, dtype=torch.long))


class _Model:
    def to(self, device):
        return self

    def eval(self):
        return self

    def __call__(self, attention_mask):
        return SimpleNamespace(last_hidden_state=torch.ones(attention_mask.shape[0], 3, 4))


def _embedder(model_name):
    tok = _Tokenizer()
    with (
        patch("transformers.AutoTokenizer.from_pretrained", return_value=tok),
        patch("transformers.AutoModel.from_pretrained", return_value=_Model()),
    ):
        return _PyTorchEmbeddings(model_name), tok


def test_e5_models_get_query_and_passage_prefixes():
    emb, tok = _embedder("intfloat/multilingual-e5-small")
    emb.embed_query("how long is the leave?")
    emb.embed_documents(["Leave is five days."])
    assert tok.seen == ["query: how long is the leave?", "passage: Leave is five days."]


def test_other_models_get_no_prefix():
    emb, tok = _embedder("BAAI/bge-small-en-v1.5")
    emb.embed_query("q")
    emb.embed_documents(["d"])
    assert tok.seen == ["q", "d"]
