"""Unit tests for airgapped model preload helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


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
