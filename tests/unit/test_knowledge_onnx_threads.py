"""ONNX Runtime gets one thread per CPU the container may use.

Without it, ONNX Runtime starts a thread per core of the node, and a container's CPU limit
throttles them: embedding in a 2-CPU container ran 1.3-2.7 times slower.
"""

from __future__ import annotations

import sys
import types

import pytest

from cuga.backend.knowledge import cpu_quota
from cuga.backend.knowledge import engine as engine_mod

pytestmark = pytest.mark.unit


@pytest.fixture
def cgroup(monkeypatch, tmp_path):
    """Point the helper at a fake cgroup tree; returns a writer for its files."""
    v2 = tmp_path / "cpu.max"
    v1 = tmp_path / "cpu"
    monkeypatch.setattr(cpu_quota, "_CGROUP_V2_CPU_MAX", v2)
    monkeypatch.setattr(cpu_quota, "_CGROUP_V1_DIRS", (v1,))
    monkeypatch.setattr(cpu_quota.os, "cpu_count", lambda: 10)
    cpu_quota.onnx_threads.cache_clear()

    def write(cpu_max=None, cfs_quota=None, cfs_period=None):
        if cpu_max is not None:
            v2.write_text(cpu_max + "\n")
        if cfs_quota is not None:
            v1.mkdir(exist_ok=True)
            (v1 / "cpu.cfs_quota_us").write_text(f"{cfs_quota}\n")
            (v1 / "cpu.cfs_period_us").write_text(f"{cfs_period}\n")

    yield write
    cpu_quota.onnx_threads.cache_clear()


@pytest.mark.parametrize(
    ("cpu_max", "threads"),
    [("200000 100000", 2), ("150000 100000", 2), ("50000 100000", 1), ("max 100000", None)],
)
def test_cgroup_v2_quota(cgroup, cpu_max, threads):
    cgroup(cpu_max=cpu_max)
    assert cpu_quota.onnx_threads() == threads


@pytest.mark.parametrize(("quota", "period", "threads"), [(400000, 100000, 4), (-1, 100000, None)])
def test_cgroup_v1_quota(cgroup, quota, period, threads):
    cgroup(cfs_quota=quota, cfs_period=period)
    assert cpu_quota.onnx_threads() == threads


def test_no_cgroup_files_means_no_quota(cgroup):
    """macOS, Windows, a VM: ONNX Runtime keeps its own default."""
    assert cpu_quota.onnx_threads() is None


def test_never_more_threads_than_visible_cores(cgroup):
    cgroup(cpu_max="1600000 100000")
    assert cpu_quota.onnx_threads() == 10


def _fake_text_embedding(monkeypatch, tmp_path) -> list[dict]:
    created: list[dict] = []

    class FakeTextEmbedding:
        def __init__(self, model_name, **kwargs):
            created.append(kwargs)

    fake = types.ModuleType("fastembed")
    fake.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.setattr(engine_mod._FastEmbedEmbeddings, "_detect_active_providers", lambda self: [])
    return created


def _fake_cross_encoder(monkeypatch, tmp_path) -> list[dict]:
    created: list[dict] = []

    class FakeTextCrossEncoder:
        def __init__(self, model_name, **kwargs):
            created.append(kwargs)

    fake = types.ModuleType("fastembed.rerank.cross_encoder")
    fake.TextCrossEncoder = FakeTextCrossEncoder
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", fake)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    return created


def test_the_embedder_uses_one_thread_per_allowed_cpu(cgroup, monkeypatch, tmp_path):
    cgroup(cpu_max="200000 100000")
    created = _fake_text_embedding(monkeypatch, tmp_path)

    engine_mod._FastEmbedEmbeddings("BAAI/bge-small-en-v1.5")

    assert created[0]["threads"] == 2


def test_the_embedder_keeps_onnx_defaults_without_a_cpu_limit(cgroup, monkeypatch, tmp_path):
    created = _fake_text_embedding(monkeypatch, tmp_path)

    engine_mod._FastEmbedEmbeddings("BAAI/bge-small-en-v1.5")

    assert "threads" not in created[0]


def test_the_reranker_uses_one_thread_per_allowed_cpu(cgroup, monkeypatch, tmp_path):
    from cuga.backend.knowledge import reranker

    cgroup(cpu_max="200000 100000")
    created = _fake_cross_encoder(monkeypatch, tmp_path)

    reranker._build_encoder("BAAI/bge-reranker-base")

    assert created[0]["threads"] == 2


def test_the_reranker_keeps_onnx_defaults_without_a_cpu_limit(cgroup, monkeypatch, tmp_path):
    from cuga.backend.knowledge import reranker

    created = _fake_cross_encoder(monkeypatch, tmp_path)

    reranker._build_encoder("BAAI/bge-reranker-base")

    assert "threads" not in created[0]
