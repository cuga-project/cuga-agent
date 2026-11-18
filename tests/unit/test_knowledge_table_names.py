"""Collection names that a backend cannot use as a table name map to one it can."""

from __future__ import annotations

import re

import pytest

from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore, _table_name

pytestmark = pytest.mark.unit

_POSTGRES_TABLE = re.compile(r"[a-z][a-z0-9_]{0,62}")


class _Embeddings:
    def embed_documents(self, texts):
        return [[0.0] for _ in texts]

    def embed_query(self, text):
        return [0.0]


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
    assert table.startswith("kb_agent_rung_0123456789ab_")


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


@pytest.mark.parametrize("bad", ["kb agent", "kb;drop", "kb-agent", ""])
def test_unsafe_characters_are_still_rejected(bad):
    with pytest.raises(ValueError):
        StorageBackedKnowledgeVectorStore(bad, _Embeddings(), local_db_path="/tmp/unused.db")


def test_the_adapter_uses_the_mapped_table_on_postgres():
    adapter = StorageBackedKnowledgeVectorStore(
        "kb_agent_RunG_0123456789ab", _Embeddings(), postgres_url="postgresql://unused"
    )
    assert _POSTGRES_TABLE.fullmatch(adapter._collection)
