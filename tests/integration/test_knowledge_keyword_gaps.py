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

# Target documents, one per language. Each query is a word a user would type: without the
# accents or vowel points the document has, in another case, or inside an unspaced sentence.
CORPUS = {
    "en": "Invoice ABC-123 was issued to Contoso on 2026-01-15 for the backup service.",
    "fr": "Le café de la gare ouvre à huit heures.",
    "de": "Die Straße wird morgen gesperrt.",
    "ru": "Резервное копирование выполняется каждую ночь.",
    "he": "המסמך מתאר את מדיניות הגיבוי של החברה.",
    "he_points": "שָׁלוֹם לְכֻלָּם.",
    "ar": "تُحْفَظُ النُّسْخَةُ كُلَّ لَيْلَةٍ.",
    "hi": "हिन्दी भाषा में यह दस्तावेज़ बैकअप नीति का वर्णन करता है।",
    "bn": "বাংলা ভাষায় এই নথিটি লেখা হয়েছে।",
    "te": "తెలుగు భాషలో ఈ పత్రం బ్యాకప్ విధానాన్ని వివరిస్తుంది.",
    "ta": "தமிழ் மொழியில் இந்த ஆவணம் காப்புப் பிரதி கொள்கையை விவரிக்கிறது.",
    "zh": "我们的数据备份策略每天执行一次。",
    "ja": "毎晩コーヒーを飲みながらバックアップを確認します。",
    "ko": "서울에서 백업 정책을 검토했습니다.",
    "th": "นโยบายการสำรองข้อมูลของบริษัท",
}
QUERIES = {
    "en": "ABC-123",
    "fr": "cafe",
    "de": "STRASSE",
    "ru": "КОПИРОВАНИЕ",
    "he": "הגיבוי",
    "he_points": "שלום",
    "ar": "النسخة",
    "hi": "हिन्दी",
    "bn": "বাংলা",
    "te": "తెలుగు",
    "ta": "தமிழ்",
    "zh": "备份策略",
    "ja": "バックアップ",
    "ko": "서울",
    "th": "ข้อมูล",
}


def _letters_only(word: str) -> str:
    """The word's letters, space-separated, without its marks.

    A tokenizer that splits words at combining marks, or matches single characters of an
    unspaced script, matches this distractor; a correct one does not.
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


@pytest.fixture
def make_adapter(tmp_path):
    """Build adapters for a test and drop their collections afterwards, pass or fail."""
    created = []

    def _make(backend: str, collection: str):
        adapter = _adapter(backend, tmp_path, collection)
        created.append(adapter)
        return adapter

    yield _make
    for adapter in created:
        adapter.drop()


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
        marks.append(
            pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"known gap, fixed by {fixed_by}")
        )
    return pytest.param(backend, lang, marks=marks, id=f"{backend}-{lang}")


@pytest.mark.parametrize(
    ("backend", "lang"),
    [_case(backend, lang) for backend in ("sqlite", "pgvector") for lang in CORPUS],
)
def test_keyword_query_finds_exactly_its_document(backend, lang, make_adapter, monkeypatch):
    """A whole-word keyword query returns its document and not a letters-only distractor."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kw")
    adapter = make_adapter(backend, collection)
    target = _add(adapter, collection, lang, CORPUS[lang])
    distractor = _add(adapter, collection, f"{lang}_letters", _letters_only(QUERIES[lang]))

    hits = _hit_sources(adapter, QUERIES[lang])

    assert target in hits, f"{backend}: keyword query {QUERIES[lang]!r} missed its document"
    assert distractor not in hits, f"{backend}: {QUERIES[lang]!r} matched a letters-only distractor"


BOTH_BACKENDS = [
    pytest.param("sqlite", marks=pytest.mark.unit),
    pytest.param("pgvector", marks=pytest.mark.pgvector),
]


@pytest.mark.parametrize(
    "query",
    ["K5A6FF80A", "user@example.com", "dental"],
    ids=["identifier", "email", "plain-word"],
)
@pytest.mark.parametrize("backend", BOTH_BACKENDS)
def test_sentence_final_word_is_found(backend, query, make_adapter, monkeypatch):
    """A word that ends a sentence (followed by a period) is still a keyword hit."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kws")
    adapter = make_adapter(backend, collection)
    source = _add(
        adapter,
        collection,
        "final",
        "Record K5A6FF80A. Contact user@example.com. The policy covers dental.",
    )

    assert source in _hit_sources(adapter, query), f"{backend}: sentence-final {query!r} not found"


@pytest.mark.parametrize(
    "query",
    ["abc-123", "ABC_123", "ＡＢＣ－１２３", "(ABC-123)"],
    ids=["lowercase", "underscore", "full-width", "punctuated"],
)
@pytest.mark.parametrize("backend", BOTH_BACKENDS)
def test_identifier_matches_however_it_is_written(backend, query, make_adapter, monkeypatch):
    """An identifier matches across case, separator and width, but a different one does not."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwi")
    adapter = make_adapter(backend, collection)
    source = _add(adapter, collection, "invoice", "Invoice ABC-123 was issued to Contoso.")

    assert source in _hit_sources(adapter, query), f"{backend}: {query!r} missed ABC-123"
    assert source not in _hit_sources(adapter, "ABC-124"), f"{backend}: ABC-124 matched ABC-123"


@pytest.mark.parametrize(
    ("query", "texts"),
    [
        ("Paris/London", ("The Paris office opens at nine.", "The London office opens at ten.")),
        ("IPv4/IPv6", ("Legacy hosts use IPv4.", "New hosts use IPv6 only.")),
    ],
    ids=["words", "identifiers"],
)
@pytest.mark.parametrize("backend", BOTH_BACKENDS)
def test_words_joined_by_ordinary_punctuation_find_either_word(
    backend, query, texts, make_adapter, monkeypatch
):
    """ "Paris/London" finds a document about either city, not only one with both words adjacent."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwp")
    adapter = make_adapter(backend, collection)
    first = _add(adapter, collection, "first", texts[0])
    second = _add(adapter, collection, "second", texts[1])

    hits = _hit_sources(adapter, query)
    assert {first, second} <= hits, f"{backend}: {query} found {hits}"


@pytest.mark.parametrize("backend", BOTH_BACKENDS)
def test_a_possessive_query_does_not_match_every_possessive(backend, make_adapter, monkeypatch):
    """ "MacDuff's" finds MacDuff, not every chunk with a possessive: its one-letter tail is no
    search term of its own."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwo")
    adapter = make_adapter(backend, collection)
    macduff = _add(adapter, collection, "macduff", "Tyler MacDuff joined the team in May.")
    _add(adapter, collection, "contoso", "Contoso's invoice was paid on time.")

    assert _hit_sources(adapter, "MacDuff's") == {macduff}


OLD_FTS_SCHEMAS = {
    # v0.3.0 to v0.4.5: unicode61 word rules, no tenant columns
    "unicode61": "chunk_id UNINDEXED, source UNINDEXED, filename UNINDEXED, page UNINDEXED, "
    "section_path UNINDEXED, chunk_text, tokenize=\"unicode61 remove_diacritics 2 tokenchars '._@-+'\"",
    # shared tokenizer without tenant columns
    "terms-no-tenant": "chunk_id UNINDEXED, source UNINDEXED, filename UNINDEXED, page UNINDEXED, "
    "section_path UNINDEXED, chunk_text UNINDEXED, terms_v1, "
    "tokenize=\"unicode61 remove_diacritics 0 categories 'L* M* N* Co'\"",
}


@pytest.mark.unit
@pytest.mark.parametrize("old", [None, *OLD_FTS_SCHEMAS], ids=["no-keyword-index", *OLD_FTS_SCHEMAS])
def test_existing_sqlite_collection_gets_a_complete_keyword_index(old, tmp_path, monkeypatch):
    """A collection whose keyword index is missing (ingested before v0.3.0) or built by an older
    schema is rebuilt from its vectors on first use, with every chunk, tenant scope and terms."""
    import sqlite3

    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwm")
    db_path = str(tmp_path / "kb.db")
    first = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db_path)
    en = _add(first, collection, "en", "The policy covers dental.")
    hi = _add(first, collection, "hi", CORPUS["hi"])
    _set_tenant(monkeypatch, "tenant_b")
    _add(first, collection, "other", "Tenant B also covers dental.")
    with sqlite3.connect(db_path) as conn:  # replace the index with what an older release left
        conn.execute(f"DROP TABLE {collection}__fts")
        if old:
            conn.execute(f"CREATE VIRTUAL TABLE {collection}__fts USING fts5({OLD_FTS_SCHEMAS[old]})")

    _set_tenant(monkeypatch, "tenant_a")
    fresh = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db_path)

    assert _hit_sources(fresh, "dental") == {en}
    assert _hit_sources(fresh, QUERIES["hi"]) == {hi}


@pytest.mark.unit
def test_deleting_a_document_keeps_other_tenants_keyword_rows(make_adapter, monkeypatch):
    """Tenants A and B both have "shared.txt"; A deleting it leaves B's keyword hits."""
    collection = unique_collection("kwd")
    _set_tenant(monkeypatch, "tenant_a")
    adapter = make_adapter("sqlite", collection)
    source = _add(adapter, collection, "shared", "Contract ZXQ-9001 renewal terms.")
    _set_tenant(monkeypatch, "tenant_b")
    _add(adapter, collection, "shared", "Contract ZXQ-9001 cancellation terms.")

    _set_tenant(monkeypatch, "tenant_a")
    adapter.delete_by_source(source)
    _set_tenant(monkeypatch, "tenant_b")

    assert _hit_sources(adapter, "ZXQ-9001") == {source}


@pytest.mark.unit
def test_an_old_keyword_table_is_rebuilt_even_when_its_name_mentions_the_terms_column(tmp_path, monkeypatch):
    """The table's columns decide whether it is current, not its CREATE text, which holds the name."""
    import sqlite3

    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("hr_terms_v1")
    db_path = str(tmp_path / "kb.db")
    first = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db_path)
    hi = _add(first, collection, "hi", CORPUS["hi"])
    with sqlite3.connect(db_path) as conn:  # the index an older release left
        conn.execute(f"DROP TABLE {collection}__fts")
        conn.execute(f"CREATE VIRTUAL TABLE {collection}__fts USING fts5({OLD_FTS_SCHEMAS['unicode61']})")
    fresh = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db_path)
    new = _add(fresh, collection, "new", "Invoice ZQX-4417 was paid.")

    assert _hit_sources(fresh, QUERIES["hi"]) == {hi}
    assert _hit_sources(fresh, "ZQX-4417") == {new}


@pytest.mark.unit
def test_keyword_writes_recover_after_another_engine_drops_the_table(tmp_path, monkeypatch):
    """An adapter remembers its keyword table is ready; when another engine sharing the database
    drops it, the next write checks again instead of failing for good."""
    import sqlite3

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwd")
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    adapter = StorageBackedKnowledgeVectorStore(
        collection, DeterministicEmbeddings(), local_db_path=str(tmp_path / "kb.db")
    )
    _add(adapter, collection, "first", "The policy covers dental.")
    with sqlite3.connect(tmp_path / "kb.db") as conn:  # another engine drops the keyword table
        conn.execute(f"DROP TABLE {collection}__fts")
    source = _add(adapter, collection, "second", "Invoice ZQX-4417 was paid.")

    assert source in _hit_sources(adapter, "ZQX-4417")
    with sqlite3.connect(tmp_path / "kb.db") as conn:  # the rebuild already holds the new chunk
        counts = dict(conn.execute(f"SELECT source, count(*) FROM {collection}__fts GROUP BY source"))
    assert counts == {f"{collection}/first.txt": 1, source: 1}


@pytest.mark.unit
def test_keyword_search_recovers_after_another_engine_drops_the_table(tmp_path, monkeypatch):
    """A search that finds the keyword table gone checks the table again on the next search,
    which rebuilds it from the stored chunks. Before, keyword search stayed empty until a write."""
    import sqlite3

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kws")
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    adapter = StorageBackedKnowledgeVectorStore(
        collection, DeterministicEmbeddings(), local_db_path=str(tmp_path / "kb.db")
    )
    source = _add(adapter, collection, "first", "Invoice ZQX-4417 was paid.")
    assert _hit_sources(adapter, "ZQX-4417") == {source}
    with sqlite3.connect(tmp_path / "kb.db") as conn:  # another engine drops the keyword table
        conn.execute(f"DROP TABLE {collection}__fts")

    _hit_sources(adapter, "ZQX-4417")  # finds the table gone
    assert _hit_sources(adapter, "ZQX-4417") == {source}


@pytest.mark.unit
def test_a_keyword_rebuild_does_not_block_uploads_to_other_collections(tmp_path, monkeypatch):
    """Collections share one SQLite file. Rebuilding one collection's keyword table (about a minute
    at 50,000 chunks) must not hold the database's write lock while it tokenizes, or an upload to
    another collection fails with "database is locked" after SQLite's 5 s wait."""
    import sqlite3
    import threading
    import time

    from cuga.backend.knowledge import lexical
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    db = str(tmp_path / "kb.db")
    slow, other = unique_collection("kwrs"), unique_collection("kwro")
    seeded = StorageBackedKnowledgeVectorStore(slow, DeterministicEmbeddings(), local_db_path=db)
    for i in range(40):
        _add(seeded, slow, f"doc{i}", f"The policy covers dental {i}.")
    with sqlite3.connect(db) as conn:  # its keyword table is missing: the next search rebuilds it
        conn.execute(f"DROP TABLE {slow}__fts")
    real_terms = lexical.terms

    def slow_terms(text):  # 40 chunks x 0.3 s: a rebuild past SQLite's 5 s wait and the write's retry
        time.sleep(0.3)
        return real_terms(text)

    monkeypatch.setattr(lexical, "terms", slow_terms)
    rebuild = threading.Thread(
        target=_hit_sources,
        args=(StorageBackedKnowledgeVectorStore(slow, DeterministicEmbeddings(), local_db_path=db), "dental"),
    )
    rebuild.start()
    time.sleep(0.5)
    uploader = StorageBackedKnowledgeVectorStore(other, DeterministicEmbeddings(), local_db_path=db)
    source = _add(uploader, other, "invoice", "Invoice ZQX-4417 was paid.")  # must not raise
    rebuild.join(timeout=60)
    monkeypatch.setattr(lexical, "terms", real_terms)

    assert _hit_sources(uploader, "ZQX-4417") == {source}
    assert len(seeded.search_lexical("dental", k=60)) == 40


@pytest.mark.unit
def test_two_adapters_rebuilding_one_keyword_table_do_not_break_each_other(tmp_path, monkeypatch):
    """A cached adapter can be evicted while it rebuilds, and the next call gets a new adapter for
    the same collection. Both stage the rebuild in one table, so they must take turns: an upload
    through the second one waits for the first rebuild instead of replacing its staging table."""
    import sqlite3
    import threading
    import time

    from cuga.backend.knowledge.storage import adapter as adapter_mod
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    db = str(tmp_path / "kb.db")
    collection = unique_collection("kwtw")
    seeded = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db)
    for i in range(40):
        _add(seeded, collection, f"doc{i}", f"The policy covers dental {i}.")
    with sqlite3.connect(db) as conn:  # its keyword table is missing: the next call rebuilds it
        conn.execute(f"DROP TABLE {collection}__fts")

    first_batch, go = threading.Event(), threading.Event()
    rebuilding: list[threading.Thread] = []

    class _PausedClock:  # the first rebuild stops after its first committed batch
        def __getattr__(self, name):
            return getattr(time, name)

        def sleep(self, seconds):
            if rebuilding and threading.current_thread() is rebuilding[0] and not first_batch.is_set():
                first_batch.set()
                go.wait(10)

    monkeypatch.setattr(adapter_mod, "_FTS_BUILD_BATCH", 5)
    monkeypatch.setattr(adapter_mod, "time", _PausedClock())
    results: dict[str, object] = {}

    def search():
        evicted = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db)
        results["hits"] = len(evicted.search_lexical("dental", k=60))

    def upload():
        fresh = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db)
        try:
            results["source"] = _add(fresh, collection, "late", "Late dental note.")
        except Exception as exc:  # noqa: BLE001 — reported below
            results["upload_error"] = exc

    rebuilding.append(threading.Thread(target=search))
    rebuilding[0].start()
    assert first_batch.wait(10)
    uploader = threading.Thread(target=upload)
    uploader.start()
    uploader.join(timeout=1)
    waited = uploader.is_alive()
    go.set()
    rebuilding[0].join(timeout=30)
    uploader.join(timeout=30)

    assert "upload_error" not in results, results.get("upload_error")
    assert waited, "the upload waits for the rebuild in progress"
    assert results["hits"] == 40
    assert len(seeded.search_lexical("dental", k=60)) == 41


@pytest.mark.unit
def test_a_chunk_deleted_during_a_keyword_rebuild_is_not_indexed(tmp_path, monkeypatch):
    """The rebuild reads and tokenizes the stored chunks before it takes the write lock; a chunk
    deleted in between must not come back as a keyword hit."""
    import sqlite3

    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    db = str(tmp_path / "kb.db")
    collection = unique_collection("kwdl")
    adapter = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db)
    source = _add(adapter, collection, "kept", "Invoice ZQX-4417 was paid.")
    with sqlite3.connect(db) as conn:
        conn.execute(f"DROP TABLE {collection}__fts")
    read = StorageBackedKnowledgeVectorStore._fts_rows_from_vectors

    def read_then_delete(self, conn):  # a chunk read here is deleted before the lock is taken
        rows = read(self, conn)
        return rows + [
            (
                "gone",
                "tenant_a",
                rows[0][2],
                f"{collection}/gone.txt",
                "gone.txt",
                1,
                "",
                "Ghost QQX-9999.",
                "ghost qqx 9999",
            )
        ]

    monkeypatch.setattr(StorageBackedKnowledgeVectorStore, "_fts_rows_from_vectors", read_then_delete)
    fresh = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db)

    assert _hit_sources(fresh, "ZQX-4417") == {source}
    assert _hit_sources(fresh, "QQX-9999") == set()


@pytest.mark.unit
def test_during_a_reindex_search_skips_keywords_and_uploads_wait(tmp_path, monkeypatch):
    """While one call re-indexes an old keyword table (about a minute at 50,000 chunks), another
    search in the process skips the keyword leg at once instead of queueing for SQLite's write
    lock, and an upload waits for the re-index instead of failing with "database is locked"."""
    import sqlite3
    import threading
    import time

    from cuga.backend.knowledge import lexical
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwr")
    db_path = str(tmp_path / "kb.db")
    first = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db_path)
    for i in range(16):
        _add(first, collection, f"old{i}", f"The policy covers dental {i}.")
    with sqlite3.connect(db_path) as conn:  # the index an older release left
        conn.execute(f"DROP TABLE {collection}__fts")
        conn.execute(f"CREATE VIRTUAL TABLE {collection}__fts USING fts5({OLD_FTS_SCHEMAS['unicode61']})")
    adapter = StorageBackedKnowledgeVectorStore(collection, DeterministicEmbeddings(), local_db_path=db_path)
    real_terms = lexical.terms

    def slow_terms(text):  # 16 stored chunks x 0.5 s: a re-index well past SQLite's 5 s lock wait
        time.sleep(0.5)
        return real_terms(text)

    monkeypatch.setattr(lexical, "terms", slow_terms)
    reindex = threading.Thread(target=_hit_sources, args=(adapter, "dental"))
    reindex.start()
    time.sleep(0.5)

    upload: dict = {}

    def _upload():
        try:
            upload["source"] = _add(adapter, collection, "new", "Invoice ZQX-4417 was paid.")
        except Exception as exc:  # noqa: BLE001 — the assertion below reports it
            upload["error"] = repr(exc)

    uploader = threading.Thread(target=_upload)
    uploader.start()
    t = time.monotonic()
    during = _hit_sources(adapter, "dental")
    waited = time.monotonic() - t
    reindex.join(timeout=30)
    uploader.join(timeout=30)

    assert during == set() and waited < 1.0, f"search waited {waited:.1f}s for the re-index"
    assert "error" not in upload, upload.get("error")
    assert _hit_sources(adapter, "ZQX-4417") == {upload["source"]}
    assert len(adapter.search_lexical("dental", k=20)) == 16


def _two_tenant_hits(backend, make_adapter, monkeypatch) -> tuple[set[str], str, str]:
    """Tenants A and B share a collection name; return tenant B's hits and both sources."""
    collection = unique_collection("kwt")
    _set_tenant(monkeypatch, "tenant_a")
    adapter = make_adapter(backend, collection)
    source_a = _add(adapter, collection, "a", "Tenant A contract ZXQ-9001 renewal terms.")
    _set_tenant(monkeypatch, "tenant_b")
    source_b = _add(adapter, collection, "b", "Tenant B contract ZXQ-9001 cancellation terms.")
    return _hit_sources(adapter, "ZXQ-9001"), source_a, source_b


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param("sqlite", marks=pytest.mark.unit),
        pytest.param("pgvector", marks=pytest.mark.pgvector),
    ],
)
def test_keyword_search_finds_own_tenant_document(backend, make_adapter, monkeypatch):
    hits, _source_a, source_b = _two_tenant_hits(backend, make_adapter, monkeypatch)

    assert source_b in hits, f"{backend}: tenant B cannot find its own document"


@pytest.mark.pgvector
def test_postgres_rows_from_before_the_keyword_column_are_indexed(make_adapter, monkeypatch):
    """A Postgres collection ingested before keyword search existed is indexed on first use."""
    import asyncpg

    from cuga.backend.knowledge.storage.adapter import _TERMS_COLUMN

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwp")
    old = make_adapter("pgvector", collection)
    source = _add(old, collection, "hi", CORPUS["hi"])

    async def _drop_column():  # the table as it was before this column existed
        conn = await asyncpg.connect(_pgvector_dsn())
        try:
            await conn.execute(f"ALTER TABLE {collection} DROP COLUMN {_TERMS_COLUMN}")
        finally:
            await conn.close()

    asyncio.run(_drop_column())
    fresh = make_adapter("pgvector", collection)

    assert _hit_sources(fresh, QUERIES["hi"]) == {source}


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param("sqlite", marks=pytest.mark.unit),
        pytest.param("pgvector", marks=pytest.mark.pgvector),
    ],
)
def test_keyword_search_excludes_other_tenants(backend, make_adapter, monkeypatch):
    hits, source_a, _source_b = _two_tenant_hits(backend, make_adapter, monkeypatch)

    assert source_a not in hits, f"{backend}: tenant B sees tenant A's document"


@pytest.mark.unit
def test_ingest_fails_before_any_vector_when_the_keyword_table_cannot_be_prepared(tmp_path, monkeypatch):
    """A failed one-time keyword-table setup (for example a lock timeout during a re-index) fails
    the ingest with nothing written, so a retry indexes the document for both legs. Swallowing it
    after the vectors commit would leave chunks that keyword search can never find."""
    import sqlite3

    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwl")
    adapter = StorageBackedKnowledgeVectorStore(
        collection, DeterministicEmbeddings(), local_db_path=str(tmp_path / "kb.db")
    )
    prepare = StorageBackedKnowledgeVectorStore._ensure_fts_table

    def _locked(self, conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(StorageBackedKnowledgeVectorStore, "_ensure_fts_table", _locked)
    with pytest.raises(sqlite3.OperationalError):
        _add(adapter, collection, "late", "The policy covers dental.")
    assert adapter.search("dental", k=5) == [], "a vector was written before the failure"

    monkeypatch.setattr(StorageBackedKnowledgeVectorStore, "_ensure_fts_table", prepare)
    source = _add(adapter, collection, "late", "The policy covers dental.")

    assert _hit_sources(adapter, "dental") == {source}
    assert [doc.metadata["source"] for doc, _ in adapter.search("dental", k=5)] == [source]


def _rows_without_terms(collection: str) -> int:
    import asyncpg

    from cuga.backend.knowledge.storage.adapter import _TERMS_COLUMN

    async def _count() -> int:
        conn = await asyncpg.connect(_pgvector_dsn())
        try:
            return await conn.fetchval(f"SELECT count(*) FROM {collection} WHERE {_TERMS_COLUMN} IS NULL")
        finally:
            await conn.close()

    return asyncio.run(_count())


@pytest.mark.pgvector
def test_postgres_terms_are_written_with_their_vectors(make_adapter, monkeypatch):
    """Keyword terms are written in the transaction that inserts the chunks: when writing them
    fails, the chunks are not stored either, and the retry indexes both. Written afterwards, a
    failure left rows without terms that an adapter which had already searched never filled."""
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwh")
    writer = make_adapter("pgvector", collection)
    _add(writer, collection, "first", "Invoice ABC-100 was issued.")
    reader = make_adapter("pgvector", collection)
    assert _hit_sources(reader, "ABC-100")  # warm: its keyword column is checked
    batch_terms = StorageBackedKnowledgeVectorStore._pg_batch_terms

    def _failing(self, batch, bare):
        async def _write(conn):
            raise ConnectionError("connection reset")

        return _write

    monkeypatch.setattr(StorageBackedKnowledgeVectorStore, "_pg_batch_terms", _failing)
    with pytest.raises(ConnectionError):
        _add(writer, collection, "late", "The policy covers dental.")
    monkeypatch.setattr(StorageBackedKnowledgeVectorStore, "_pg_batch_terms", batch_terms)

    assert _rows_without_terms(collection) == 0
    dense_hits = {doc.metadata["source"] for doc, _ in reader.search("dental", k=5)}
    assert f"{collection}/late.txt" not in dense_hits, "the chunks were stored without their terms"
    source = _add(writer, collection, "late", "The policy covers dental.")  # the retry
    assert _hit_sources(reader, "dental") == {source}


@pytest.mark.pgvector
def test_postgres_terms_go_in_the_insert_not_a_second_update(make_adapter, monkeypatch):
    """Once the keyword column exists, an upload writes each chunk's terms in its INSERT. A second
    UPDATE of the same rows cannot be done in place (the terms column is indexed), so it wrote
    every chunk into the vector (HNSW) index twice and left a dead row behind."""
    import asyncpg.connection

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwin")
    adapter = make_adapter("pgvector", collection)
    _add(adapter, collection, "first", "Invoice ABC-100 was issued.")  # the keyword column now exists
    statements: list[str] = []
    for name in ("execute", "executemany"):
        real = getattr(asyncpg.connection.Connection, name)

        async def spy(self, query, *args, _real=real, **kwargs):
            statements.append(" ".join(str(query).split()))
            return await _real(self, query, *args, **kwargs)

        monkeypatch.setattr(asyncpg.connection.Connection, name, spy)
    source = _add(adapter, collection, "late", "The policy covers dental.")

    inserts = [q for q in statements if q.startswith(f"INSERT INTO {collection} ")]
    assert inserts and all("terms_v1" in q for q in inserts), inserts
    assert not [q for q in statements if q.startswith(f"UPDATE {collection} ")]
    assert _hit_sources(adapter, "dental") == {source}


@pytest.mark.pgvector
def test_postgres_upload_into_a_busy_table_without_the_keyword_column(make_adapter, monkeypatch):
    """An upload into a collection from before keyword search never waits to add the keyword
    column, even while another session has the table open; its chunks are indexed when a search
    adds the column."""
    import time

    import asyncpg

    from cuga.backend.knowledge.storage.adapter import _TERMS_COLUMN

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwbu")
    old = make_adapter("pgvector", collection)
    first = _add(old, collection, "first", "Invoice ZQX-4417 was paid.")
    fresh = make_adapter("pgvector", collection)

    async def upload_while_the_table_is_open() -> float:
        conn = await asyncpg.connect(_pgvector_dsn())
        try:
            await conn.execute(f"ALTER TABLE {collection} DROP COLUMN {_TERMS_COLUMN}")  # before the upgrade
            tx = conn.transaction()
            await tx.start()
            await conn.execute(f"SELECT 1 FROM {collection} LIMIT 1")  # holds the table open
            started = time.monotonic()
            await asyncio.to_thread(_add, fresh, collection, "late", "Invoice QQR-9921 was paid.")
            elapsed = time.monotonic() - started
            await tx.rollback()
            return elapsed
        finally:
            await conn.close()

    elapsed = asyncio.run(upload_while_the_table_is_open())

    assert elapsed < 5, f"the upload waited {elapsed:.1f}s for the busy table"
    reader = make_adapter("pgvector", collection)
    assert _hit_sources(reader, "ZQX-4417") == {first}
    assert _hit_sources(reader, "QQR-9921") == {f"{collection}/late.txt"}
    assert _rows_without_terms(collection) == 0


@pytest.mark.pgvector
def test_postgres_rows_an_older_version_stores_after_the_backfill_are_indexed(make_adapter, monkeypatch):
    """A rolling upgrade: a new replica has filled every row's terms and stopped checking, and an
    old replica, which does not know the column, keeps storing chunks without them. The new
    replica checks again, at most once a minute, and indexes them."""
    import time

    import asyncpg

    from cuga.backend.knowledge.storage import adapter as adapter_mod
    from cuga.backend.knowledge.storage.adapter import _TERMS_COLUMN

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwru")
    new = make_adapter("pgvector", collection)
    _add(new, collection, "seed", "Invoice ABC-100 was issued.")
    assert _hit_sources(new, "ABC-100")  # the backfill ran; this adapter now trusts its terms
    late = _add(new, collection, "late", "Invoice ZQX-4417 was paid.")

    async def _as_an_old_replica_wrote_it():
        conn = await asyncpg.connect(_pgvector_dsn())
        try:
            await conn.execute(
                f"UPDATE {collection} SET {_TERMS_COLUMN} = NULL WHERE meta_json::jsonb->>'source' = $1", late
            )
        finally:
            await conn.close()

    asyncio.run(_as_an_old_replica_wrote_it())
    assert _hit_sources(new, "ZQX-4417") == set(), "not checked again within the minute"

    assert new._pg_recheck_at - time.monotonic() <= adapter_mod._PG_TERMS_RECHECK_S
    new._pg_recheck_at = time.monotonic()  # a minute later
    assert _hit_sources(new, "ZQX-4417") == {late}


@pytest.mark.pgvector
def test_postgres_backfill_batches_skip_rows_already_filled(make_adapter, monkeypatch):
    """Each backfill batch starts where the last one ended: its cost does not grow with the rows
    already filled. Filtering on "terms IS NULL" re-read every filled row on every batch."""
    import json as _json

    import asyncpg

    from cuga.backend.knowledge.storage.adapter import _PG_FILL_BATCH, _TERMS_COLUMN

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwbc")
    adapter = make_adapter("pgvector", collection)
    _add(adapter, collection, "seed", "Invoice ABC-100 was issued.")
    batch = _PG_FILL_BATCH.format(table=collection)

    async def buffers_read(conn, after) -> int:
        plan = await conn.fetchval(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {batch}", *after)
        top = (_json.loads(plan) if isinstance(plan, str) else plan)[0]["Plan"]
        return top["Shared Hit Blocks"] + top["Shared Read Blocks"]

    async def first_and_last_batch() -> tuple[int, int]:
        conn = await asyncpg.connect(_pgvector_dsn())
        try:
            columns = [
                r["attname"]
                for r in await conn.fetch(
                    "SELECT attname FROM pg_attribute WHERE attrelid = $1::regclass AND attnum > 0 "
                    "AND NOT attisdropped ORDER BY attnum",
                    collection,
                )
            ]
            copied = ", ".join(
                {"id": "'r' || lpad(g::text, 5, '0')", _TERMS_COLUMN: "NULL"}.get(c, c) for c in columns
            )
            await conn.execute(
                f"INSERT INTO {collection} ({', '.join(columns)}) "
                f"SELECT {copied} FROM {collection}, generate_series(1, 5000) AS g"
            )
            await conn.execute(f"ANALYZE {collection}")
            first = await buffers_read(conn, ("", "", ""))
            cut = await conn.fetchrow(
                f"SELECT tenant_id, instance_id, id FROM {collection} ORDER BY tenant_id, instance_id, id "
                "OFFSET 4500 LIMIT 1"
            )
            await conn.execute(  # the batches before the cut are done
                f"UPDATE {collection} SET {_TERMS_COLUMN} = ''::tsvector "
                "WHERE (tenant_id, instance_id, id) < ($1, $2, $3)",
                *cut,
            )
            return first, await buffers_read(conn, tuple(cut))
        finally:
            await conn.close()

    first, last = asyncio.run(first_and_last_batch())
    assert last <= 3 * first, f"the last batch read {last} buffers, the first {first}"


@pytest.mark.parametrize("backend", BOTH_BACKENDS)
def test_rare_word_outranks_common_words(backend, make_adapter, monkeypatch):
    """In a natural-language question, the chunk with the rare word ranks first, not the chunks
    that repeat the question's common words."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwr")
    adapter = make_adapter(backend, collection)
    for i in range(8):
        _add(
            adapter,
            collection,
            f"common_{i}",
            "What is the value of the record? The value of the record is in the report.",
        )
    target = _add(adapter, collection, "target", "Record ZQX-4471 holds a value.")

    hits = adapter.search_lexical("what is the value of the record ZQX-4471", k=3)

    assert hits and hits[0][0].metadata["source"] == target, (
        f"{backend}: ranked {[d.metadata['source'] for d, _ in hits]}"
    )


@pytest.mark.pgvector
def test_postgres_ranks_keyword_hits_like_sqlite(make_adapter, monkeypatch):
    """Both backends rank a natural-language question's keyword hits with FTS5's BM25 formula, so
    these chunks come back in the same order: a word in half or more of the chunks adds nothing,
    a repeated word saturates, and a long chunk is not favoured for its length. Postgres measures
    a chunk's length in distinct terms, not tokens, so a chunk that repeats many of its words can
    rank differently than on SQLite; these chunks repeat few."""
    _set_tenant(monkeypatch, "tenant_a")
    chunks = {
        "short_rare": "Zambezi river film notes.",
        "long_rare": "The documentary about the Zambezi follows fishermen, traders and children "
        "along villages where boats carry maize, timber and news between distant markets.",
        "repeats": "River river river river river delta flooding season.",
        "common_long": "The film was shown in the town hall and the film club met every week "
        "to discuss the film and other films with the director of the club.",
        "common_short": "The film director spoke.",
        "unrelated": "Copper prices rose in the second quarter.",
    }
    query = "who directed the film about the Zambezi river"
    orders = {}
    for backend in ("sqlite", "pgvector"):
        collection = unique_collection("kwbm25")
        adapter = make_adapter(backend, collection)
        sources = {_add(adapter, collection, name, text): name for name, text in chunks.items()}
        orders[backend] = [sources[doc.metadata["source"]] for doc, _ in adapter.search_lexical(query, k=6)]

    assert orders["pgvector"] == orders["sqlite"]


@pytest.mark.pgvector
def test_postgres_keyword_setup_gives_up_on_a_busy_table(make_adapter, monkeypatch):
    """Adding the keyword column to a table another session has open waits at most a few seconds:
    the keyword leg is empty meanwhile, and the next call, once the table is free, completes it.
    Without the timeout the ALTER queues behind that session and every query on the table
    queues behind the ALTER."""
    import time

    import asyncpg

    from cuga.backend.knowledge.storage.adapter import _TERMS_COLUMN

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwlk")
    adapter = make_adapter("pgvector", collection)
    source = _add(adapter, collection, "inv", "Invoice ZQX-4417 was paid.")
    dsn = _pgvector_dsn()

    async def search_while_the_table_is_open() -> tuple[list, float]:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f"ALTER TABLE {collection} DROP COLUMN {_TERMS_COLUMN}")  # before the upgrade
            adapter._fts_ready = False
            tx = conn.transaction()
            await tx.start()
            await conn.execute(f"SELECT 1 FROM {collection} LIMIT 1")  # holds the table open
            started = time.monotonic()
            hits = await asyncio.to_thread(adapter.search_lexical, "ZQX-4417")
            elapsed = time.monotonic() - started
            await tx.rollback()
            return hits, elapsed
        finally:
            await conn.close()

    hits, elapsed = asyncio.run(search_while_the_table_is_open())

    assert hits == [] and elapsed < 10, f"keyword setup waited {elapsed:.1f}s on a busy table"
    assert source in _hit_sources(adapter, "ZQX-4417")


@pytest.mark.pgvector
def test_concurrent_keyword_calls_skip_a_running_backfill(make_adapter, monkeypatch):
    """While one call backfills keyword terms, others skip it instead of repeating the scan."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwbf")
    adapter = make_adapter("pgvector", collection)
    source = _add(adapter, collection, "inv", "Invoice ZQX-4417 was paid.")
    adapter._fts_ready = False  # a fresh adapter on an existing collection

    adapter._pg_ensure_lock.acquire()  # another call is backfilling
    try:
        assert adapter.search_lexical("ZQX-4417") == []
    finally:
        adapter._pg_ensure_lock.release()
    assert source in _hit_sources(adapter, "ZQX-4417")


_RANKING_DOCS = {
    "a": "Invoice ABC-123 was issued to Contoso for the backup service and the backup policy.",
    "b": "The backup policy runs every night; backup backup backup logs are kept for a week.",
    "c": "Contoso renewed the service contract. The invoice was paid on time.",
    "d": "我们的数据备份策略每天执行一次。备份日志保存一周。",
    "e": "A short note about the weather and the office plants.",
}
_RANKING_QUERIES = ["backup policy", "ABC-123 invoice", "the service", "备份策略", "Contoso contract invoice"]


@pytest.mark.unit
def test_sqlite_keyword_ranking_is_bm25_sorted_inside_fts5(make_adapter, monkeypatch):
    """Ordering by FTS5's rank column returns the same rows and scores as bm25(), per tenant, and
    lets FTS5 sort in its index instead of a temporary B-tree."""
    _set_tenant(monkeypatch, "tenant_a")
    adapter = make_adapter("sqlite", unique_collection("kw_rank"))
    for name, text in _RANKING_DOCS.items():
        _add(adapter, "kw_rank", name, text)
    _set_tenant(monkeypatch, "tenant_b")  # a second scope in the same table
    for name, text in _RANKING_DOCS.items():
        _add(adapter, "kw_rank", f"b_{name}", text + " Tenant B copy.")
    _set_tenant(monkeypatch, "tenant_a")

    conn = adapter._fts_connect()
    try:
        table = adapter._fts_table()
        for query in _RANKING_QUERIES:
            expected = conn.execute(
                f"SELECT chunk_text, bm25({table}) AS r FROM {table} "
                f"WHERE {table} MATCH ? AND tenant_id = ? AND instance_id = ? ORDER BY r LIMIT 10",
                (adapter._fts_match(query), "tenant_a", "inst"),
            ).fetchall()
            got = adapter.search_lexical(query, k=10)
            assert [d.page_content for d, _ in got] == [text for text, _ in expected], query
            assert [round(s, 12) for _, s in got] == [
                round(abs(r) / (1 + abs(r)), 12) for _, r in expected
            ], query
        statements: list[str] = []
        connect = adapter._fts_connect

        def traced():
            c = connect()
            c.set_trace_callback(statements.append)
            return c

        monkeypatch.setattr(adapter, "_fts_connect", traced)
        adapter.search_lexical("backup policy", k=10)
        search_sql = next(
            st for st in statements if " MATCH " in st and st.lstrip().upper().startswith("SELECT")
        )
        plan = " ".join(str(row[-1]) for row in conn.execute(f"EXPLAIN QUERY PLAN {search_sql}"))
        assert "TEMP B-TREE" not in plan, plan
    finally:
        conn.close()


@pytest.mark.unit
def test_postgres_keyword_query_runs_with_the_keyword_budget_as_its_time_limit(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from cuga.backend.knowledge.engine import _KEYWORD_LEG_BUDGET_S
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    adapter = StorageBackedKnowledgeVectorStore(
        "kb_budget", DeterministicEmbeddings(), postgres_url="postgresql://u@h/db"
    )
    conn = MagicMock(fetch=AsyncMock(return_value=[]))
    monkeypatch.setattr(adapter, "_pg_ensure_terms", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_pg_keyword_run", lambda work: asyncio.run(work(conn)))

    adapter._pg_search_lexical([["backup"]], 5)

    assert conn.fetch.await_args.kwargs["timeout"] == _KEYWORD_LEG_BUDGET_S


# The Postgres keyword query before each candidate's terms were read once (#1118 at e0f899bf);
# the current query must score every row exactly as it did.
_HEAD_PG_KEYWORD_SQL = """
WITH n AS (
    SELECT count(*)::float8 AS n, greatest(avg(length({t})), 1)::float8 AS avgdl
    FROM {c} WHERE tenant_id = $2 AND instance_id = $3
),
w AS MATERIALIZED (
    SELECT g.q::tsquery AS q, greatest(ln((n.n - df.df + 0.5) / (df.df + 0.5)), 1e-6) AS idf
    FROM unnest($1::text[]) AS g(q) CROSS JOIN n
    CROSS JOIN LATERAL (
        SELECT count(*)::float8 AS df FROM {c}
        WHERE tenant_id = $2 AND instance_id = $3 AND {t} @@ g.q::tsquery
    ) df
)
SELECT c.chunk_text, c.meta_json,
       (SELECT sum(w.idf * t.tf * 2.2 / (t.tf + 1.2 * (0.25 + 0.75 * length(c.{t}) / n.avgdl)))
        FROM w CROSS JOIN LATERAL (
            SELECT ts_rank_cd('{{1,1,1,1}}'::float4[], c.{t}, w.q, 0) AS tf
        ) t
        WHERE c.{t} @@ w.q) AS rank
FROM {c} c CROSS JOIN n
WHERE c.tenant_id = $2 AND c.instance_id = $3 AND c.{t} @@ $5::text::tsquery
ORDER BY rank DESC, c.id LIMIT $4
"""


@pytest.mark.pgvector
def test_postgres_keyword_scores_are_unchanged_by_reading_terms_once(make_adapter, monkeypatch):
    import asyncpg

    from cuga.backend.knowledge import lexical
    from cuga.backend.knowledge.storage.adapter import _TERMS_COLUMN, _pg_tsquery

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kw_once")
    adapter = make_adapter("pgvector", collection)
    for name, text in _RANKING_DOCS.items():
        _add(adapter, collection, name, text)
    sql = _HEAD_PG_KEYWORD_SQL.format(t=_TERMS_COLUMN, c=adapter._collection)

    async def head(groups):
        conn = await asyncpg.connect(_pgvector_dsn())
        try:
            rows = await conn.fetch(
                sql,
                [_pg_tsquery(g) for g in groups],
                "tenant_a",
                "inst",
                10,
                " | ".join(f"({_pg_tsquery(g)})" for g in groups),
            )
            return [(r["chunk_text"], float(r["rank"])) for r in rows]
        finally:
            await conn.close()

    for query in _RANKING_QUERIES:
        groups = lexical.query_groups(query)
        expected = asyncio.run(head(groups))
        assert expected, query
        assert [(t, r) for t, _m, r in adapter._pg_search_lexical(groups, 10)] == expected, query


@pytest.mark.pgvector
def test_postgres_keyword_search_stops_at_its_time_limit(make_adapter, monkeypatch):
    """A keyword search slower than the engine's budget is cancelled in Postgres and returns
    nothing, so it frees its connection instead of running on."""
    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kw_slow")
    adapter = make_adapter("pgvector", collection)
    _add(adapter, collection, "a", _RANKING_DOCS["a"])
    assert adapter.search_lexical("backup policy", k=5)

    monkeypatch.setattr("cuga.backend.knowledge.engine._KEYWORD_LEG_BUDGET_S", 0.000001)
    assert adapter.search_lexical("backup policy", k=5) == []
