from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

pytest.importorskip("sqlite_vec")

from cuga.backend.knowledge.storage.local import create_storage_local_knowledge_store


class _FixedEmbeddings(Embeddings):
    def __init__(self, dim: int = 4):
        self._dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float((i + j) % 7) / 7.0 for j in range(self._dim)] for i in range(len(texts))]

    def embed_query(self, text: str) -> list[float]:
        return [float(ord(c) % 7) / 7.0 for c in text[: self._dim].ljust(self._dim, "x")]


def test_storage_local_knowledge_add_search_delete(tmp_path: Path) -> None:
    db_path = str(tmp_path / "kb_store.db")
    store = create_storage_local_knowledge_store("kb_unit_test", _FixedEmbeddings(4), db_path)
    src = "kb_unit_test/note.pdf"
    docs = [
        Document(
            page_content="unique alpha chunk for retrieval",
            metadata={"source": src, "filename": "note.pdf", "page": 3},
        )
    ]
    r = store.add_documents(docs)
    assert r["num_added"] == 1

    hits = store.search("alpha retrieval", k=3)
    assert len(hits) >= 1
    doc, score = hits[0]
    assert "alpha" in doc.page_content
    assert doc.metadata.get("filename") == "note.pdf"
    assert doc.metadata.get("page") == 3
    assert 0.0 <= score <= 1.0

    store.delete_by_source(src)
    assert store.search("alpha", k=3) == []

    store.drop()
