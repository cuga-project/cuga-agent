"""Embedding-model cache must be durable and self-healing.

Real-world failure this covers: fastembed's cache defaulted to $TMPDIR, macOS
purged the ONNX model, the re-download was interrupted, and every subsequent
start hard-failed with `NoSuchFile` — surfacing to the user as
"Embedder unavailable ... check its API key / connection" for a *local* model
that has neither. See fix/embedder-cache-resilience.
"""

import os
import sys
import types

import pytest

from cuga.backend.knowledge.engine import (
    _is_corrupt_model_cache_error,
    _model_cache_root_from_error,
    _purge_model_cache,
)

ONNX_MSG = (
    "[ONNXRuntimeError] : 3 : NO_SUCHFILE : Load model from /var/folders/xg/T/"
    "fastembed_cache/models--qdrant--bge-small-en-v1.5-onnx-q/snapshots/5239/"
    "model_optimized.onnx failed. File doesn't exist"
)


# --- error classification -------------------------------------------------


def test_onnx_missing_file_is_corrupt_cache():
    assert _is_corrupt_model_cache_error(RuntimeError(ONNX_MSG)) is True


def test_file_not_found_is_corrupt_cache():
    assert _is_corrupt_model_cache_error(FileNotFoundError("model.onnx")) is True


def test_truncated_onnx_is_corrupt_cache():
    assert _is_corrupt_model_cache_error(RuntimeError("Protobuf parsing failed")) is True


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("Model foo/bar is not supported in TextEmbedding"),
        RuntimeError("401 Client Error: Unauthorized for url https://api.example/v1"),
        RuntimeError("Connection refused"),
        RuntimeError("You are trying to access a gated repo"),
    ],
)
def test_real_faults_are_not_mistaken_for_corruption(exc):
    """Config/auth/network faults must surface, never trigger a silent re-download."""
    assert _is_corrupt_model_cache_error(exc) is False


# --- cache-root extraction ------------------------------------------------


def test_extracts_model_cache_root_from_error_path():
    root = _model_cache_root_from_error(RuntimeError(ONNX_MSG))
    assert root is not None
    assert root.name == "models--qdrant--bge-small-en-v1.5-onnx-q"


def test_returns_none_when_error_has_no_path():
    """No path means we cannot know which entry is bad — we must not guess."""
    assert _model_cache_root_from_error(RuntimeError("disk on fire")) is None


def test_path_outside_cache_dir_is_rejected(tmp_path):
    """The result is fed to rmtree; a path outside the cache must never match."""
    evil = RuntimeError("failed to load /etc/models--not-really-a-cache/model.onnx")
    assert _model_cache_root_from_error(evil, str(tmp_path)) is None


def test_path_inside_cache_dir_is_accepted(tmp_path):
    inside = tmp_path / "models--qdrant--x"
    inside.mkdir()
    exc = RuntimeError(f"failed to load {inside}/model.onnx")
    assert _model_cache_root_from_error(exc, str(tmp_path)) == inside


# --- purge ----------------------------------------------------------------


def test_purge_removes_model_dir_and_its_lock(tmp_path):
    cache = tmp_path / "fastembed_cache"
    model = cache / "models--qdrant--bge-small-en-v1.5-onnx-q"
    (model / "snapshots").mkdir(parents=True)
    lock = cache / ".locks" / "models--qdrant--bge-small-en-v1.5-onnx-q"
    lock.mkdir(parents=True)
    other = cache / "models--other--keep-me"
    other.mkdir(parents=True)

    _purge_model_cache(model)

    assert not model.exists()
    assert not lock.exists()
    assert other.exists(), "unrelated cached models must be left alone"


# --- end-to-end self-heal through _FastEmbedEmbeddings --------------------


@pytest.fixture()
def fake_fastembed(monkeypatch):
    """Stub fastembed.TextEmbedding so we can script failure then success."""
    calls: list[str] = []
    state = {"fail_times": 0, "error": RuntimeError(ONNX_MSG)}

    class FakeTextEmbedding:
        def __init__(self, model_name, **kwargs):
            calls.append(model_name)
            if len(calls) <= state["fail_times"]:
                raise state["error"]
            self.model_name = model_name

    mod = types.ModuleType("fastembed")
    mod.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", mod)
    return calls, state


def _make(monkeypatch, tmp_path):
    from cuga.backend.knowledge.engine import _FastEmbedEmbeddings

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.setattr(_FastEmbedEmbeddings, "_detect_active_providers", lambda self: [], raising=False)
    return _FastEmbedEmbeddings


def _poison(tmp_path, state):
    """Create a cache entry and point the scripted error at it, as ONNX would."""
    model_dir = tmp_path / "models--qdrant--bge-small-en-v1.5-onnx-q"
    (model_dir / "snapshots").mkdir(parents=True)
    state["error"] = RuntimeError(
        f"[ONNXRuntimeError] : 3 : NO_SUCHFILE : Load model from "
        f"{model_dir}/snapshots/5239/model_optimized.onnx failed. File doesn't exist"
    )
    return model_dir


def test_corrupt_cache_is_purged_and_model_reloads(fake_fastembed, monkeypatch, tmp_path):
    """The regression: a poisoned cache must heal invisibly, not brick the app."""
    calls, state = fake_fastembed
    state["fail_times"] = 1
    model_dir = _poison(tmp_path, state)

    cls = _make(monkeypatch, tmp_path)
    emb = cls("BAAI/bge-small-en-v1.5")

    assert len(calls) == 2, "should retry exactly once after purging"
    assert not model_dir.exists(), "the poisoned cache entry must actually be deleted"
    assert emb._model.model_name == "BAAI/bge-small-en-v1.5"


def test_self_heal_retries_only_once(fake_fastembed, monkeypatch, tmp_path):
    """A second failure is a genuine fault and must surface, not loop."""
    calls, state = fake_fastembed
    state["fail_times"] = 99
    _poison(tmp_path, state)

    cls = _make(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError):
        cls("BAAI/bge-small-en-v1.5")

    assert len(calls) == 2, "must not retry more than once"


def test_non_corruption_error_is_not_retried(fake_fastembed, monkeypatch, tmp_path):
    calls, state = fake_fastembed
    state["fail_times"] = 99
    state["error"] = ValueError("Model foo is not supported in TextEmbedding")

    cls = _make(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        cls("foo")

    assert len(calls) == 1, "a config error must fail immediately, no re-download"


def test_offline_mode_never_redownloads(fake_fastembed, monkeypatch, tmp_path):
    """HF_HUB_OFFLINE=1 is an explicit promise not to hit the network."""
    calls, state = fake_fastembed
    state["fail_times"] = 99
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    model_dir = _poison(tmp_path, state)

    cls = _make(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError):
        cls("BAAI/bge-small-en-v1.5")

    assert len(calls) == 1
    assert model_dir.exists(), "offline mode must not delete the cache either"


# --- #1 persistent cache dir ---------------------------------------------


def test_cache_dir_defaults_outside_tmpdir():
    """The original trigger: a $TMPDIR cache is deleted by the OS behind us."""
    import tempfile

    from cuga.config import FASTEMBED_CACHE_DIR

    tmp_root = os.path.realpath(tempfile.gettempdir())
    assert not os.path.realpath(FASTEMBED_CACHE_DIR).startswith(tmp_root), (
        f"model cache {FASTEMBED_CACHE_DIR} must not live under {tmp_root}"
    )


def test_explicit_cache_path_env_wins(monkeypatch):
    """Containers pin FASTEMBED_CACHE_PATH; our default must not override it."""
    import importlib

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "/opt/preloaded/fastembed")
    import cuga.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.FASTEMBED_CACHE_DIR == "/opt/preloaded/fastembed"
    finally:
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        importlib.reload(cfg)


# --- #4 "preparing" is not "broken" --------------------------------------


def _probe(default_embeddings, initializing, enabled=True):
    """Drive probe_active_embedder with a minimal stand-in engine."""
    import asyncio
    from types import SimpleNamespace

    from cuga.backend.knowledge.engine import KnowledgeEngine

    fake = SimpleNamespace(
        _config=SimpleNamespace(
            enabled=enabled,
            embedding_provider="fastembed",
            embedding_model="BAAI/bge-small-en-v1.5",
            vector_config_hash=lambda: "h",
        ),
        _default_embeddings=default_embeddings,
        _embedder_initializing=initializing,
        _embedder_probe_cache=None,
        _scrub_secret_text=lambda s: s,
        # Already-initialized in these cases, so this is a no-op.
        _ensure_embeddings=lambda: None,
    )
    return asyncio.run(KnowledgeEngine.probe_active_embedder(fake))


def test_cold_start_reports_preparing_not_unavailable():
    """A first run downloading its model must never render as a red error."""
    out = _probe(default_embeddings=None, initializing=True)

    assert out["state"] == "preparing"
    # The banner keys off `available === false`; None keeps it suppressed.
    assert out["available"] is None
    assert out["error"] is None


def test_disabled_knowledge_reports_disabled():
    out = _probe(default_embeddings=None, initializing=False, enabled=False)
    assert out["state"] == "disabled"
    assert out["available"] is None


def test_ready_embedder_reports_available():
    class _Emb:
        def embed_query(self, _):
            return [0.0, 1.0]

    out = _probe(default_embeddings=_Emb(), initializing=False)
    assert out["state"] == "available"
    assert out["available"] is True
