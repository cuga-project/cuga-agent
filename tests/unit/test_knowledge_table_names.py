"""Collection names that a backend cannot use as a table name map to one it can."""

from __future__ import annotations

import hashlib
import re

import pytest
from langchain_core.documents import Document

from cuga.backend.knowledge.session_provider import SessionProvider, session_collection_name
from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore, _table_name

pytestmark = pytest.mark.unit

_POSTGRES_TABLE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_WORDS = ["harbor", "glacier", "invoice"]


class _Embeddings:
    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        return [1.0 + text.count(word) for word in _WORDS]


def _earlier_mapping(collection: str) -> str:
    """The table the first version of the mapping gave a name it could not use as it is."""
    return f"{collection.lower()[:50]}_{hashlib.sha256(collection.encode()).hexdigest()[:12]}"


@pytest.mark.parametrize("postgres", [True, False])
def test_valid_names_are_unchanged(postgres):
    # Existing tables keep their names, so no data moves.
    assert _table_name("kb_agent_cuga_default_0123456789ab", postgres=postgres) == (
        "kb_agent_cuga_default_0123456789ab"
    )


def test_mixed_case_is_kept_on_sqlite_and_mapped_on_postgres():
    assert _table_name("kb_agent_RunG_0123456789ab", postgres=False) == "kb_agent_RunG_0123456789ab"
    table = _table_name("kb_agent_RunG_0123456789ab", postgres=True)
    assert _POSTGRES_TABLE.fullmatch(table)
    assert table.startswith("kbh_kb_agent_rung_0123456789ab_")


@pytest.mark.parametrize("postgres", [True, False])
def test_long_names_fit_in_63_characters(postgres):
    collection = "kb_agent_" + "a_very_long_agent_identifier_" * 3 + "0123456789ab"
    table = _table_name(collection, postgres=postgres)
    assert len(table) <= 63
    assert table == _table_name(collection, postgres=postgres)  # same table every time


def test_names_that_differ_only_in_case_get_different_tables():
    assert _table_name("kb_agent_RunG_x", postgres=True) != _table_name("kb_agent_runG_x", postgres=True)
    assert _table_name("kb_agent_RunG_x", postgres=True) != _table_name("kb_agent_rung_x", postgres=True)


def test_a_name_that_starts_with_a_digit_gets_a_letter_on_postgres():
    assert _POSTGRES_TABLE.fullmatch(_table_name("9lives", postgres=True))


@pytest.mark.parametrize(
    ("postgres", "collection"),
    [(True, "kb_sess_SupportChat42"), (False, "kb_sess_" + "long_thread_id_" * 4)],
    ids=["postgres-upper-case", "sqlite-too-long"],
)
def test_a_mapped_table_is_never_the_table_of_a_name_used_as_it_is(postgres, collection):
    mapped = _table_name(collection, postgres=postgres)
    # The table the first version mapped to is a valid name, so it now belongs to that name only ...
    earlier = _earlier_mapping(collection)
    assert _table_name(earlier, postgres=postgres) == earlier != mapped
    # ... and a collection named like the mapped table is mapped too, away from it.
    assert _table_name(mapped, postgres=postgres) != mapped


@pytest.mark.parametrize("postgres", [True, False])
def test_no_name_used_as_it_is_starts_with_the_mapped_prefix(postgres):
    for name in ("kbh_kb_agent_x", "KBH_kb_agent_x", "kbh_"):
        assert _table_name(name, postgres=postgres) != name


def test_two_owners_in_one_tenant_get_different_tables():
    # Session ids from the review: the second was the table the first version gave the first.
    first = "SupportChat42"
    second = _earlier_mapping(session_collection_name(first)).removeprefix("kb_sess_")
    provider = SessionProvider()

    assert provider.claim_session(first, "alice", "tenant-1")
    assert provider.claim_session(second, "bob", "tenant-1")
    tables = {_table_name(session_collection_name(thread), postgres=True) for thread in (first, second)}
    assert len(tables) == 2
    assert not provider.check_session_access(first, "bob", "tenant-1")
    assert not provider.check_session_access(second, "alice", "tenant-1")


@pytest.mark.parametrize("bad", ["kb agent", "kb;drop", "kb-agent", ""])
def test_unsafe_characters_are_still_rejected(bad):
    with pytest.raises(ValueError):
        StorageBackedKnowledgeVectorStore(bad, _Embeddings(), local_db_path="/tmp/unused.db")


def test_the_adapter_uses_the_mapped_table_on_postgres():
    adapter = StorageBackedKnowledgeVectorStore(
        "kb_agent_RunG_0123456789ab", _Embeddings(), postgres_url="postgresql://unused"
    )
    assert _POSTGRES_TABLE.fullmatch(adapter._collection)


def test_lookalike_collections_on_sqlite_search_and_drop_separately(tmp_path):
    """An over-length name, the table the first version gave it, and a name like its new table."""
    db = str(tmp_path / "knowledge.db")
    long_name = "kb_sess_" + "long_thread_id_" * 4
    names = [long_name, _earlier_mapping(long_name), _table_name(long_name, postgres=False)]
    adapters = [StorageBackedKnowledgeVectorStore(n, _Embeddings(), local_db_path=db) for n in names]
    assert len({a._collection for a in adapters}) == 3
    for adapter, word in zip(adapters, _WORDS):
        adapter.add_documents([Document(page_content=f"{word} notes", metadata={"source": word})])

    def texts(adapter):
        return [doc.page_content for doc, _ in adapter.search("notes", k=10)]

    assert [texts(a) for a in adapters] == [[f"{w} notes"] for w in _WORDS]
    adapters[0].drop()
    assert [texts(a) for a in adapters[1:]] == [["glacier notes"], ["invoice notes"]]
    adapters[1].drop()
    assert texts(adapters[2]) == ["invoice notes"]
