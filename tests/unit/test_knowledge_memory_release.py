"""The knowledge server gives back the memory a large upload used.

ONNX Runtime's CPU arena kept the largest embedding batch's memory, and glibc kept freed
parse memory in its arenas, so the server's memory only grew from one upload to the next.
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.knowledge import engine as engine_mod

pytestmark = pytest.mark.unit


def test_fastembed_runs_without_the_onnx_cpu_arena(monkeypatch, tmp_path):
    created: list[dict] = []

    class FakeTextEmbedding:
        def __init__(self, model_name, **kwargs):
            created.append(kwargs)

    fake = types.ModuleType("fastembed")
    fake.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.setattr(engine_mod._FastEmbedEmbeddings, "_detect_active_providers", lambda self: [])

    engine_mod._FastEmbedEmbeddings("BAAI/bge-small-en-v1.5")

    assert created[0]["enable_cpu_mem_arena"] is False


def test_reranker_runs_without_the_onnx_cpu_arena(monkeypatch, tmp_path):
    from cuga.backend.knowledge import reranker

    created: list[dict] = []

    class FakeTextCrossEncoder:
        def __init__(self, model_name, **kwargs):
            created.append(kwargs)

    fake = types.ModuleType("fastembed.rerank.cross_encoder")
    fake.TextCrossEncoder = FakeTextCrossEncoder
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", fake)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))

    reranker._build_encoder("BAAI/bge-reranker-base")

    assert created[0]["enable_cpu_mem_arena"] is False


def test_release_freed_memory_calls_malloc_trim(monkeypatch):
    trim = MagicMock()
    monkeypatch.setattr(engine_mod, "_malloc_trim", None)
    monkeypatch.setattr("ctypes.CDLL", lambda name: SimpleNamespace(malloc_trim=trim))

    engine_mod._release_freed_memory()
    engine_mod._release_freed_memory()

    assert trim.call_args_list == [((0,),), ((0,),)]


def test_release_freed_memory_does_nothing_without_glibc(monkeypatch):
    def no_glibc(name):
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(engine_mod, "_malloc_trim", None)
    monkeypatch.setattr("ctypes.CDLL", no_glibc)

    engine_mod._release_freed_memory()  # no error

    assert engine_mod._malloc_trim is False


@pytest.mark.parametrize("fails", [False, True], ids=["indexed", "failed"])
def test_every_ingest_releases_memory_when_it_ends(monkeypatch, fails):
    released: list[int] = []
    monkeypatch.setattr(engine_mod, "_release_freed_memory", lambda: released.append(1))
    fake_engine = SimpleNamespace(
        _active_tasks={},
        _ensure_metadata_ready=AsyncMock(),
        _ingest_sem=asyncio.Semaphore(1),
        _ingest_inner=AsyncMock(side_effect=RuntimeError("parse failed") if fails else None),
    )

    async def run():
        await engine_mod.KnowledgeEngine._run_ingest(
            fake_engine, "kb_x", MagicMock(), "a.pdf", "task_1", True
        )

    if fails:
        with pytest.raises(RuntimeError):
            asyncio.run(run())
    else:
        asyncio.run(run())

    assert released == [1]
