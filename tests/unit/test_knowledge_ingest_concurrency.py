"""max_ingest_workers must actually bound concurrent parses (it used to be a
dead config knob — set everywhere, read nowhere). Guards the semaphore wired
into _run_ingest."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine


def _cfg(**over):
    d = dict(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-concurrency-")),
        embedding_provider="fastembed",
    )
    d.update(over)
    return KnowledgeConfig(**d)


def test_ingest_semaphore_caps_concurrent_parses():
    eng = KnowledgeEngine(_cfg(max_ingest_workers=2))

    live = 0
    peak = 0

    async def fake_inner(*_a, **_k):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.05)  # hold the slot so overlap is observable
        live -= 1

    async def noop():
        pass

    eng._ingest_inner = fake_inner
    eng._ensure_metadata_ready = noop

    async def run():
        await asyncio.gather(*(eng._run_ingest("c", Path("f"), f"f{i}", f"t{i}", True) for i in range(6)))

    asyncio.run(run())

    assert peak == 2, f"expected exactly max_ingest_workers (2) in flight, got peak={peak}"


def test_env_overrides_max_ingest_workers(monkeypatch):
    """CUGA_MAX_INGEST_WORKERS env wins over TOML/profile so CLI/container ops
    can tune concurrency without editing config."""
    from cuga.backend.knowledge.config import KnowledgeConfig

    monkeypatch.setenv("CUGA_MAX_INGEST_WORKERS", "7")
    # from_settings just needs a dynaconf-like .get("knowledge", {}) — a dict does.
    cfg = KnowledgeConfig.from_settings({"knowledge": {"enabled": True}})
    assert cfg.max_ingest_workers == 7
