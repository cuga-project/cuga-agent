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


@pytest.mark.unit
def test_keyword_index_from_an_older_tokenizer_is_rebuilt(tmp_path, monkeypatch):
    """A keyword table built before the shared tokenizer is re-indexed from its stored text."""
    import sqlite3

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwm")
    db_path = tmp_path / "kb.db"
    with sqlite3.connect(db_path) as conn:  # the schema shipped before the shared tokenizer
        conn.execute(
            f"CREATE VIRTUAL TABLE {collection}__fts USING fts5("
            "chunk_id UNINDEXED, source UNINDEXED, filename UNINDEXED, "
            "page UNINDEXED, section_path UNINDEXED, chunk_text, "
            "tokenize=\"unicode61 remove_diacritics 2 tokenchars '._@-+'\")"
        )
        conn.executemany(
            f"INSERT INTO {collection}__fts VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("c1", f"{collection}/en.txt", "en.txt", 1, "", "The policy covers dental."),
                ("c2", f"{collection}/hi.txt", "hi.txt", 1, "", CORPUS["hi"]),
            ],
        )
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    adapter = StorageBackedKnowledgeVectorStore(
        collection, DeterministicEmbeddings(), local_db_path=str(db_path)
    )

    assert _hit_sources(adapter, "dental") == {f"{collection}/en.txt"}
    assert _hit_sources(adapter, QUERIES["hi"]) == {f"{collection}/hi.txt"}


@pytest.mark.unit
def test_an_old_keyword_table_is_rebuilt_even_when_its_name_mentions_the_terms_column(tmp_path, monkeypatch):
    """The table's columns decide whether it is current, not its CREATE text, which holds the name.
    Left unmigrated, Hindi is unsearchable (old tokenizer) and new chunks get no keyword rows."""
    import sqlite3

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("hr_terms_v1")
    db_path = tmp_path / "kb.db"
    with sqlite3.connect(db_path) as conn:  # the schema shipped before the shared tokenizer
        conn.execute(
            f"CREATE VIRTUAL TABLE {collection}__fts USING fts5(chunk_id UNINDEXED, source UNINDEXED, "
            "filename UNINDEXED, page UNINDEXED, section_path UNINDEXED, chunk_text, "
            "tokenize=\"unicode61 remove_diacritics 2 tokenchars '._@-+'\")"
        )
        conn.execute(
            f"INSERT INTO {collection}__fts VALUES (?, ?, ?, ?, ?, ?)",
            ("c1", f"{collection}/hi.txt", "hi.txt", 1, "", CORPUS["hi"]),
        )
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    adapter = StorageBackedKnowledgeVectorStore(
        collection, DeterministicEmbeddings(), local_db_path=str(db_path)
    )
    new = _add(adapter, collection, "new", "Invoice ZQX-4417 was paid.")

    assert _hit_sources(adapter, QUERIES["hi"]) == {f"{collection}/hi.txt"}
    assert _hit_sources(adapter, "ZQX-4417") == {new}


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
    db_path = tmp_path / "kb.db"
    with sqlite3.connect(db_path) as conn:  # the schema shipped before the shared tokenizer
        conn.execute(
            f"CREATE VIRTUAL TABLE {collection}__fts USING fts5(chunk_id UNINDEXED, source UNINDEXED, "
            "filename UNINDEXED, page UNINDEXED, section_path UNINDEXED, chunk_text, "
            "tokenize=\"unicode61 remove_diacritics 2 tokenchars '._@-+'\")"
        )
        conn.executemany(
            f"INSERT INTO {collection}__fts VALUES (?, ?, ?, ?, ?, ?)",
            [
                (f"c{i}", f"{collection}/old{i}.txt", f"old{i}.txt", 1, "", f"The policy covers dental {i}.")
                for i in range(16)
            ],
        )
    adapter = StorageBackedKnowledgeVectorStore(
        collection, DeterministicEmbeddings(), local_db_path=str(db_path)
    )
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
        pytest.param(
            "sqlite",
            marks=[
                pytest.mark.unit,
                pytest.mark.xfail(
                    strict=True,
                    raises=AssertionError,
                    reason="known gap, fixed by PR B2 (tenant columns in FTS)",
                ),
            ],
        ),
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


@pytest.mark.pgvector
def test_postgres_failed_term_write_is_filled_by_the_next_search(make_adapter, monkeypatch):
    """A failed keyword-term write keeps the ingest, and the next keyword search fills the row."""
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwh")
    adapter = make_adapter("pgvector", collection)
    _add(adapter, collection, "first", "Invoice ABC-100 was issued.")  # the keyword column exists
    write = StorageBackedKnowledgeVectorStore._pg_write_terms

    def _fail(self, items):
        raise ConnectionError("connection reset")

    monkeypatch.setattr(StorageBackedKnowledgeVectorStore, "_pg_write_terms", _fail)
    source = _add(adapter, collection, "late", "The policy covers dental.")
    monkeypatch.setattr(StorageBackedKnowledgeVectorStore, "_pg_write_terms", write)

    assert _hit_sources(adapter, "dental") == {source}


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
def test_postgres_keyword_search_reuses_a_bounded_set_of_connections(make_adapter, monkeypatch):
    """Concurrent keyword searches share a small pool instead of opening a connection each."""
    from concurrent.futures import ThreadPoolExecutor

    import asyncpg

    from cuga.backend.knowledge.storage.adapter import _PG_KEYWORD_POOL_SIZE

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwc")
    adapter = make_adapter("pgvector", collection)
    source = _add(adapter, collection, "invoice", "Invoice ABC-123 was issued.")

    with ThreadPoolExecutor(max_workers=24) as workers:
        hits = list(workers.map(lambda _: _hit_sources(adapter, "ABC-123"), range(24)))

    async def _keyword_connections() -> int:
        conn = await asyncpg.connect(_pgvector_dsn())
        try:
            return await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'cuga-knowledge-keyword'"
            )
        finally:
            await conn.close()

    assert all(h == {source} for h in hits)
    assert 1 <= asyncio.run(_keyword_connections()) <= _PG_KEYWORD_POOL_SIZE


@pytest.mark.pgvector
def test_postgres_keyword_search_survives_dropped_connections(make_adapter, monkeypatch):
    """After the server drops the pool's connections (restart, idle timeout), the next keyword
    search reconnects instead of failing."""
    import asyncpg

    _set_tenant(monkeypatch, "tenant_a")
    collection = unique_collection("kwk")
    adapter = make_adapter("pgvector", collection)
    source = _add(adapter, collection, "invoice", "Invoice ABC-123 was issued.")
    assert _hit_sources(adapter, "ABC-123") == {source}  # the pool now holds a connection

    async def _terminate_keyword_connections() -> int:
        conn = await asyncpg.connect(_pgvector_dsn())
        try:
            return await conn.fetchval(
                "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                "WHERE application_name = 'cuga-knowledge-keyword'"
            )
        finally:
            await conn.close()

    assert asyncio.run(_terminate_keyword_connections()) >= 1

    assert _hit_sources(adapter, "ABC-123") == {source}


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
