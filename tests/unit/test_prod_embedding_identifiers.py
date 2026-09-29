import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig
from cuga.backend.storage.embedding.prod import ProdEmbeddingStore, _embedding_index_name

pytestmark = pytest.mark.unit


@pytest.fixture
def schema() -> EmbeddingSchemaConfig:
    return EmbeddingSchemaConfig(
        embedding_dim=3,
        id_column="id",
        metadata_columns={},
        auxiliary_columns={},
    )


def test_accepts_63_character_collection_name(schema: EmbeddingSchemaConfig) -> None:
    collection_name = "a" * 63

    store = ProdEmbeddingStore("postgresql://unused", collection_name, schema)

    assert store._collection_name == collection_name


@pytest.mark.parametrize(
    "collection_name",
    [
        "a" * 64,
        "Uppercase",
        "has-hyphen",
        "has space",
        "1starts_with_digit",
    ],
)
def test_rejects_oversized_or_unsafe_collection_name(
    schema: EmbeddingSchemaConfig, collection_name: str
) -> None:
    with pytest.raises(ValueError, match="not a valid PostgreSQL identifier"):
        ProdEmbeddingStore("postgresql://unused", collection_name, schema)


def test_embedding_index_name_is_bounded_deterministic_and_collision_resistant() -> None:
    first_collection = "a" * 62 + "b"
    second_collection = "a" * 62 + "c"

    first_name = _embedding_index_name(first_collection)
    second_name = _embedding_index_name(second_collection)

    assert first_name == _embedding_index_name(first_collection)
    assert first_name != second_name
    for index_name in (first_name, second_name):
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,62}", index_name, flags=re.ASCII)
        assert len(index_name.encode("ascii")) <= 63


@pytest.mark.asyncio
async def test_ensure_table_uses_derived_embedding_index_name(schema: EmbeddingSchemaConfig) -> None:
    collection_name = "a" * 63
    expected_index_name = _embedding_index_name(collection_name)
    execute = AsyncMock()
    connection_context = AsyncMock()
    connection_context.__aenter__.return_value = SimpleNamespace(execute=execute)
    pool = SimpleNamespace(acquire=lambda: connection_context)
    store = ProdEmbeddingStore("postgresql://unused", collection_name, schema)
    store._pool = pool

    await store._ensure_table()

    ddl_statements = [call.args[0] for call in execute.await_args_list]
    assert ddl_statements[1] == (
        f"CREATE INDEX IF NOT EXISTS {expected_index_name} "
        f"ON {collection_name} USING hnsw (embedding vector_cosine_ops)"
    )
    assert len(expected_index_name.encode("ascii")) <= 63
