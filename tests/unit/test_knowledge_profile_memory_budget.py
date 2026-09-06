"""Profile defaults must fit a memory-constrained pod.

A local ONNX embedder allocates intermediate tensors that scale as
batch x sequence^2, and onnxruntime's arena does not return that memory to the
OS — so ``embedding_batch_size`` sets the process's resident high-water mark,
not just its working set. Two ingest workers each carry a batch, so peak
tracks ``batch_size * max_ingest_workers``.

Measured on 5x104KB markdown, 512-token chunks, 2 workers (#712):

    batch 128 -> 4,642 MB peak  (OOM-killed a 4 GB pod)
    batch  64 -> 3,258 MB peak
    batch  32 -> 2,452 MB peak  (~8% more wall time than 128)
    batch  16 -> 1,720 MB peak  (2.4x slower — past the knee)

These tests pin the budget so a future profile edit cannot quietly
reintroduce an OOM on a 4 GB deployment.
"""

from __future__ import annotations

import pytest

from cuga.backend.knowledge.config import VALID_PROFILES, load_profile

# batch_size * max_ingest_workers. 256 units is what OOM'd a 4 GB pod, so the
# ceiling sits below it with room for the ~1.4 GB idle baseline.
MAX_CONCURRENT_BATCH_UNITS = 128

# Below this the adapter makes enough extra embed calls that wall time
# degrades sharply for little further memory saving.
MIN_BATCH_SIZE = 16


def _profile_budget(name: str) -> tuple[int, int]:
    d = load_profile(name)
    batch = d.get("embeddings", {}).get("batch_size")
    workers = d.get("engine", {}).get("max_ingest_workers")
    assert batch is not None, f"{name}: profile must pin embeddings.batch_size"
    assert workers is not None, f"{name}: profile must pin engine.max_ingest_workers"
    return int(batch), int(workers)


@pytest.mark.unit
@pytest.mark.parametrize("name", VALID_PROFILES)
def test_profile_stays_within_the_memory_budget(name):
    batch, workers = _profile_budget(name)
    units = batch * workers
    assert units <= MAX_CONCURRENT_BATCH_UNITS, (
        f"{name}: batch_size={batch} x max_ingest_workers={workers} = {units} units "
        f"exceeds {MAX_CONCURRENT_BATCH_UNITS}. At 256 units a 4 GB pod OOMs on five "
        f"100KB uploads (#712). Lower batch_size or max_ingest_workers."
    )


@pytest.mark.unit
@pytest.mark.parametrize("name", VALID_PROFILES)
def test_profile_stays_above_the_throughput_knee(name):
    batch, _ = _profile_budget(name)
    assert batch >= MIN_BATCH_SIZE, (
        f"{name}: batch_size={batch} is below {MIN_BATCH_SIZE}. Past that knee the "
        f"extra embed calls cost more wall time than the memory saving is worth."
    )


@pytest.mark.unit
def test_speed_profile_may_carry_more_memory_than_standard():
    """The profiles stay ordered by intent, not accidentally equal.

    ``speed`` is allowed a larger batch than ``standard`` — that is the
    trade it exists to make — but it is still held to the same ceiling.
    """
    speed, _ = _profile_budget("speed")
    standard, _ = _profile_budget("standard")
    assert speed >= standard, "speed profile should not embed in smaller batches than standard"
