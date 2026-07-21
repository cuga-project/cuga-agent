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
        from scripts.preload_models import DOCLING_LAYOUT_HERON_ONNX_REPO, preload_docling

        preload_docling()

    mock_download_models.assert_called_once_with(
        output_dir=tmp_path,
        with_code_formula=False,
        with_picture_classifier=False,
    )
    mock_download_hf.assert_called_once_with(
        repo_id=DOCLING_LAYOUT_HERON_ONNX_REPO,
        local_dir=tmp_path / "docling-project--docling-layout-heron-onnx",
    )
