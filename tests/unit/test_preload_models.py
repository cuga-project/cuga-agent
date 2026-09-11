"""Unit tests for airgapped model preload helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_supported_image_builds_memory_ui_and_bakes_evolve_for_offline_runtime() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile.ubi").read_text()
    entrypoint = (REPO_ROOT / "scripts/docker-entrypoint.sh").read_text()

    assert "pnpm --filter ./frontend build" in dockerfile
    assert "altk-evolve[hooks,pii-regex]" in dockerfile
    assert "EVOLVE_REF=1b47f858c68b2658a8e321195f0965cb3e7cf901" in dockerfile
    assert dockerfile.count("@sha256:") >= 3
    assert (
        "ARG BASE_IMAGE=" in dockerfile
        and "ARG BASE_IMAGE=registry.access.redhat.com/ubi9/python-312-minimal@sha256:" in dockerfile
    )
    assert "ARG NODE_IMAGE=node:22-bookworm-slim@sha256:" in dockerfile
    assert "ARG UV_IMAGE=ghcr.io/astral-sh/uv:latest@sha256:" in dockerfile
    assert "PRELOAD_EVOLVE_MODELS=1" in dockerfile
    assert "SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers" in dockerfile
    assert "uv run --no-sync playwright install" in dockerfile
    assert "AS model-cache" in dockerfile
    assert "COPY --from=model-cache /app/.cache /app/.cache" in dockerfile
    assert "TRANSFORMERS_OFFLINE=1" in dockerfile
    assert "UV_OFFLINE=1" in dockerfile
    assert "CUGA_EMBEDDED_EVOLVE=false" in dockerfile
    assert "embedded-evolve-supervisor.py" in dockerfile
    assert "CUGA_EMBEDDED_EVOLVE:-false" in entrypoint
    assert not (REPO_ROOT / "Dockerfile.memory").exists()


@pytest.mark.unit
def test_preload_docling_downloads_onnx_layout_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path))
    monkeypatch.setenv("DOCLING_WITH_CODE_FORMULA", "0")
    monkeypatch.setenv("DOCLING_WITH_PICTURE_CLASSIFIER", "0")

    with (
        patch("docling.utils.model_downloader.download_models") as mock_download_models,
        patch("docling.models.utils.hf_model_download.download_hf_model") as mock_download_hf,
    ):
        from scripts.preload_models import docling_onnx_layout_repo_id, preload_docling

        onnx_repo = docling_onnx_layout_repo_id()
        preload_docling()

    mock_download_models.assert_called_once_with(
        output_dir=tmp_path,
        with_code_formula=False,
        with_picture_classifier=False,
    )
    mock_download_hf.assert_called_once_with(
        repo_id=onnx_repo,
        local_dir=tmp_path / onnx_repo.replace("/", "--"),
    )


def _required_layout_repo_ids_for_cuga() -> set[str]:
    """Repo IDs Docling will look up for every layout mode CUGA can select."""
    from docling.datamodel.object_detection_engine_options import (
        ObjectDetectionEngineType,
        OnnxRuntimeObjectDetectionEngineOptions,
        TransformersObjectDetectionEngineOptions,
    )
    from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions

    from cuga.backend.knowledge.engine import KnowledgeEngine

    required: set[str] = set()
    cases = (
        ("auto", "cpu"),
        ("auto", "mps"),
        ("auto", "cuda"),
        ("onnx", "cpu"),
        ("onnx", "mps"),
        ("transformers", "cpu"),
        ("transformers", "mps"),
    )
    for choice, device in cases:
        effective, _ = KnowledgeEngine._resolve_layout(choice, device)
        if effective == "onnx":
            opts = LayoutObjectDetectionOptions(engine_options=OnnxRuntimeObjectDetectionEngineOptions())
            override = opts.model_spec.engine_overrides[ObjectDetectionEngineType.ONNXRUNTIME]
            required.add(override.repo_id)
        else:
            opts = LayoutObjectDetectionOptions(engine_options=TransformersObjectDetectionEngineOptions())
            required.add(opts.model_spec.repo_id)
    return required


@pytest.mark.unit
def test_airgap_preload_covers_cuga_layout_engine_repos() -> None:
    """Required layout HF repos for CUGA modes must be in the airgap preload set.

    No downloads — compares Docling's live model specs to what preload guarantees.
    """
    from scripts.preload_models import docling_airgap_layout_repo_ids

    required = _required_layout_repo_ids_for_cuga()
    preloaded = docling_airgap_layout_repo_ids()
    missing = required - preloaded
    assert not missing, (
        f"Airgap preload missing layout repos required at runtime: {sorted(missing)}. "
        f"required={sorted(required)} preloaded={sorted(preloaded)}"
    )


@pytest.mark.unit
def test_preload_evolve_sentence_transformers_warms_all_required_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.preload_models import (
        EVOLVE_SENTENCE_TRANSFORMER_MODELS,
        preload_evolve_sentence_transformers,
    )

    monkeypatch.setenv("SENTENCE_TRANSFORMERS_HOME", str(tmp_path))
    models = [MagicMock() for _ in EVOLVE_SENTENCE_TRANSFORMER_MODELS]

    sentence_transformer = MagicMock(side_effect=models)
    fake_module = SimpleNamespace(SentenceTransformer=sentence_transformer)
    with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
        preload_evolve_sentence_transformers()

    assert sentence_transformer.call_args_list == [
        call(
            model_name,
            cache_folder=str(tmp_path),
            revision=revision,
            trust_remote_code=trust_remote_code,
        )
        for model_name, revision, trust_remote_code in EVOLVE_SENTENCE_TRANSFORMER_MODELS
    ]
    for model in models:
        model.encode.assert_called_once_with(["warmup"])


@pytest.mark.unit
def test_strict_preload_turns_optional_failure_into_build_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.preload_models import handle_preload_error

    monkeypatch.setenv("MODEL_PRELOAD_STRICT", "1")

    with pytest.raises(RuntimeError, match="docling preload failed"):
        handle_preload_error("docling", ValueError("download unavailable"))
