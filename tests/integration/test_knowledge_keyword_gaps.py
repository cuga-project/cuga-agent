"""Keyword (lexical) search gap map — the same checks on every storage backend.

Each case asserts the behaviour CUGA knowledge must have on *both* SQLite (local) and
pgvector (production). Cases that fail today are ``xfail(strict=True)`` with the PR that
fixes them; when that PR lands the case passes, strict mode turns the stale xfail into a
failure, and the PR removes the marker. A regression on a passing case fails immediately.

Runs at the storage-adapter level with deterministic embeddings, so it needs no model
download and isolates the keyword leg. pgvector cases need
``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING`` (CI job ``pgvector-integration``).
"""

from __future__ import annotations

import asyncio
import os
import unicodedata

import pytest
from langchain_core.documents import Document

from .helpers import DeterministicEmbeddings, unique_collection

# Target documents, one per script. Queries are whole words that appear in exactly one doc.
CORPUS = {
    "en": "Invoice ABC-123 was issued to Contoso on 2026-01-15 for the backup service.",
    "he": "המסמך מתאר את מדיניות הגיבוי של החברה.",
    "hi": "हिन्दी भाषा में यह दस्तावेज़ बैकअप नीति का वर्णन करता है।",
    "te": "తెలుగు భాషలో ఈ పత్రం బ్యాకప్ విధానాన్ని వివరిస్తుంది.",
    "ta": "தமிழ் மொழியில் இந்த ஆவணம் காப்புப் பிரதி கொள்கையை விவரிக்கிறது.",
}
QUERIES = {"en": "ABC-123", "he": "הגיבוי", "hi": "हिन्दी", "te": "తెలుగు", "ta": "தமிழ்"}


def _letters_only(word: str) -> str:
    """The word's base letters, space-separated, with its vowel signs and viramas removed.

    A tokenizer that splits words at combining marks indexes the target word as exactly
    these letters, so this distractor matches the query only when tokenization is broken.
    """
    return " ".join(ch for ch in word if unicodedata.category(ch).startswith("L"))


def _pgvector_dsn() -> str:
    dsn = os.environ.get("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING")
    if not dsn:
        pytest.skip("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING not set")
    return dsn


async def _enable_pgvector(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except asyncpg.exceptions.InsufficientPrivilegeError:
        pass
    finally:
        await conn.close()


def _set_tenant(monkeypatch, tenant: str) -> None:
    monkeypatch.setattr("cuga.backend.knowledge.storage.adapter.get_tenant_id", lambda: tenant)
    monkeypatch.setattr("cuga.backend.knowledge.storage.adapter.get_service_instance_id", lambda: "inst")


def _adapter(backend: str, tmp_path, collection: str):
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    if backend == "sqlite":
        return StorageBackedKnowledgeVectorStore(
            collection, DeterministicEmbeddings(), local_db_path=str(tmp_path / "kb.db")
        )
    dsn = _pgvector_dsn()
    pytest.importorskip("asyncpg")
    pytest.importorskip("pgvector.asyncpg")
    asyncio.run(_enable_pgvector(dsn))
    return StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), postgres_url=dsn)


def _add(adapter, collection: str, name: str, text: str) -> str:
    source = f"{collection}/{name}.txt"
    doc = Document(page_content=text, metadata={"source": source, "filename": f"{name}.txt", "page": 1})
    adapter.add_documents([doc])
    return source


def _hit_sources(adapter, query: str) -> set[str]:
    return {doc.metadata["source"] for doc, _score in adapter.search_lexical(query, k=10)}


def _case(backend: str, lang: str, fixed_by: str | None = None):
    marks = [pytest.mark.unit] if backend == "sqlite" else [pytest.mark.pgvector]
    if fixed_by:
        marks.append(pytest.mark.xfail(strict=True, reason=f"known gap, fixed by {fixed_by}"))
    return pytest.param(backend, lang, marks=marks, id=f"{backend}-{lang}")


NO_PG_KEYWORD = "PR B4 (Postgres keyword read path)"
INDIC_TOKENIZER = "PR B1 (Indic-correct FTS5 tokenizer and query sanitizer)"


@pytest.mark.parametrize(
    ("backend", "lang"),
    [
        _case("sqlite", "en"),
        _case("sqlite", "he"),
        _case("sqlite", "hi", INDIC_TOKENIZER),
        _case("sqlite", "te", INDIC_TOKENIZER),
        _case("sqlite", "ta", INDIC_TOKENIZER),
        _case("pgvector", "en", NO_PG_KEYWORD),
        _case("pgvector", "he", NO_PG_KEYWORD),
        _case("pgvector", "hi", NO_PG_KEYWORD),
        _case("pgvector", "te", NO_PG_KEYWORD),
        _case("pgvector", "ta", NO_PG_KEYWORD),
    ],
)
def test_keyword_query_finds_exactly_its_document(backend, lang, tmp_path, monkeypatch):
    """A whole-word keyword query returns its document and not a letters-only distractor."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kw")
    adapter = _adapter(backend, tmp_path, collection)
    target = _add(adapter, collection, lang, CORPUS[lang])
    distractor = _add(adapter, collection, f"{lang}_letters", _letters_only(QUERIES[lang]))

    hits = _hit_sources(adapter, QUERIES[lang])

    assert target in hits, f"{backend}: keyword query {QUERIES[lang]!r} missed its document"
    assert distractor not in hits, f"{backend}: {QUERIES[lang]!r} matched a letters-only distractor"


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param(
            "sqlite",
            marks=[
                pytest.mark.unit,
                pytest.mark.xfail(strict=True, reason="known gap, fixed by PR B2 (tenant columns in FTS)"),
            ],
        ),
        pytest.param(
            "pgvector",
            marks=[
                pytest.mark.pgvector,
                pytest.mark.xfail(strict=True, reason=f"known gap, fixed by {NO_PG_KEYWORD}"),
            ],
        ),
    ],
)
def test_keyword_search_is_tenant_isolated(backend, tmp_path, monkeypatch):
    """Two tenants share a collection name; each sees only its own keyword hits."""
    collection = unique_collection("kwt")
    _set_tenant(monkeypatch, "tenant_a")
    adapter = _adapter(backend, tmp_path, collection)
    source_a = _add(adapter, collection, "a", "Tenant A contract ZXQ-9001 renewal terms.")
    _set_tenant(monkeypatch, "tenant_b")
    source_b = _add(adapter, collection, "b", "Tenant B contract ZXQ-9001 cancellation terms.")

    hits = _hit_sources(adapter, "ZXQ-9001")

    assert source_b in hits, f"{backend}: tenant B cannot find its own document"
    assert source_a not in hits, f"{backend}: tenant B sees tenant A's document"
