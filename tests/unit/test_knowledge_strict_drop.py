"""Strict collection deletion must preserve retriable knowledge cleanup."""

from collections import OrderedDict
from threading import RLock
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_strict_drop_keeps_metadata_and_adapter_when_vector_drop_fails(tmp_path):
    from cuga.backend.knowledge.engine import KnowledgeEngine

    adapter = MagicMock()
    adapter.drop.side_effect = RuntimeError("vector store unavailable")
    metadata = SimpleNamespace(
        get_collection_config=AsyncMock(return_value={"embedding_provider": "fake"}),
        delete_collection_metadata=AsyncMock(),
    )
    engine = SimpleNamespace(
        _ensure_metadata_ready=AsyncMock(),
        _metadata=metadata,
        _vector_stores=OrderedDict({"kb_sess_test": adapter}),
        _record_managers={},
        _vector_store_lock=RLock(),
        _files_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="vector store unavailable"):
        await KnowledgeEngine.drop_collection(engine, "kb_sess_test", strict=True)

    assert engine._vector_stores["kb_sess_test"] is adapter
    metadata.delete_collection_metadata.assert_not_awaited()

    adapter.drop.side_effect = None
    await KnowledgeEngine.drop_collection(engine, "kb_sess_test", strict=True)
    metadata.delete_collection_metadata.assert_awaited_once_with("kb_sess_test")


@pytest.mark.asyncio
async def test_strict_drop_reopens_uncached_collection_after_restart(tmp_path):
    from cuga.backend.knowledge.engine import KnowledgeEngine

    adapter = MagicMock()
    stores = OrderedDict()
    ensure_cached = AsyncMock(side_effect=lambda _: stores.update({"kb_sess_test": adapter}))
    metadata = SimpleNamespace(
        get_collection_config=AsyncMock(return_value={"embedding_provider": "fake"}),
        delete_collection_metadata=AsyncMock(),
    )
    engine = SimpleNamespace(
        _ensure_metadata_ready=AsyncMock(),
        _ensure_vector_store_cached=ensure_cached,
        _metadata=metadata,
        _vector_stores=stores,
        _record_managers={},
        _vector_store_lock=RLock(),
        _files_dir=tmp_path,
    )

    await KnowledgeEngine.drop_collection(engine, "kb_sess_test", strict=True)

    ensure_cached.assert_awaited_once_with("kb_sess_test")
    adapter.drop.assert_called_once_with()
    metadata.delete_collection_metadata.assert_awaited_once_with("kb_sess_test")
