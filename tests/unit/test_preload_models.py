"""Unit tests for airgapped model preload helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_supported_image_bakes_evolve_for_offline_runtime() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile.ubi").read_text()
    entrypoint = (REPO_ROOT / "scripts/docker-entrypoint.sh").read_text()

    project = (REPO_ROOT / "pyproject.toml").read_text()
    assert "altk-evolve[fastembed,pii-regex]" in project
    assert "altk-evolve[fastembed,pii-regex]>=1.6.1,<2" in project
    assert "github.com/AgentToolkit/altk-evolve/archive/" not in project
    assert "--frozen --no-editable --no-dev" in dockerfile
    assert "--group evolve-image" in dockerfile
    assert "uv pip install" not in dockerfile
    assert "SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers" in dockerfile
    assert "uv run --no-sync playwright install" in dockerfile
    assert "RUN uv run --no-sync python src/scripts/preload_models.py" in dockerfile
    assert "MODEL_PRELOAD_STRICT=1" in dockerfile
    assert "RUN --network=none /app/.venv/bin/python /app/src/scripts/verify_airgap.py" in dockerfile
    assert "TRANSFORMERS_OFFLINE=1" in dockerfile
    assert "UV_OFFLINE=1" in dockerfile
    assert "CUGA_EMBEDDED_EVOLVE=false" in dockerfile
    assert "embedded-evolve-supervisor.py" not in dockerfile
    assert "container_services.py" in entrypoint
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
        with_easyocr=True,
    )
    mock_download_hf.assert_called_once_with(
        repo_id=onnx_repo,
        local_dir=tmp_path / onnx_repo.replace("/", "--"),
    )


@pytest.mark.unit
def test_preload_docling_fails_build_when_download_fails() -> None:
    from scripts.preload_models import preload_docling

    with patch("docling.utils.model_downloader.download_models", side_effect=RuntimeError("download failed")):
        with pytest.raises(RuntimeError, match="download failed"):
            preload_docling()


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
@pytest.mark.parametrize("override", [None, "custom-consistency-model"])
def test_preload_evolve_uses_configured_models_without_extra_defaults(override) -> None:
    from scripts.preload_models import preload_evolve

    loader = MagicMock()
    export = MagicMock()
    modules = {
        "altk_evolve.config.guidelines": SimpleNamespace(
            guidelines_settings=SimpleNamespace(
                consistency_embedding_model_small="BAAI/bge-small-en-v1.5",
                consistency_embedding_model_large=override or "BAAI/bge-small-en-v1.5",
                consistency_embedding_trust_remote_code=False,
            )
        ),
        "altk_evolve.config.milvus": SimpleNamespace(
            milvus_other_settings=SimpleNamespace(embedding_model="BAAI/bge-small-en-v1.5")
        ),
        "altk_evolve.config.postgres": SimpleNamespace(
            postgres_db_settings=SimpleNamespace(embedding_model="BAAI/bge-small-en-v1.5")
        ),
        "altk_evolve.embeddings": SimpleNamespace(
            get_embedding_model=loader,
            EmbeddingSettings=lambda: SimpleNamespace(embedding_provider="fastembed"),
        ),
        "altk_evolve.export_embeddings": SimpleNamespace(export_coderank=export),
        "altk_evolve.embedding_assets": SimpleNamespace(
            CODERANK_MODEL="nomic-ai/CodeRankEmbed",
            MINILM_MODEL="sentence-transformers/all-MiniLM-L6-v2",
        ),
    }
    with patch.dict(sys.modules, modules):
        preload_evolve()
    export.assert_not_called()

    expected = sorted({"BAAI/bge-small-en-v1.5", override or "BAAI/bge-small-en-v1.5"})
    assert loader.call_args_list == [call(model, trust_remote_code=False) for model in expected]
    assert loader.return_value.encode.call_count == len(expected)


@pytest.mark.unit
def test_strict_preload_turns_optional_failure_into_build_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.preload_models import handle_preload_error

    monkeypatch.setenv("MODEL_PRELOAD_STRICT", "1")

    with pytest.raises(RuntimeError, match="docling preload failed"):
        handle_preload_error("docling", ValueError("download unavailable"))


@pytest.mark.unit
@pytest.mark.parametrize("stage", ["builder", "runtime"])
def test_image_evolve_embedding_settings_match_preloaded_bge(stage, monkeypatch) -> None:
    """Read each image stage's defaults through Evolve's real settings classes."""
    import shlex

    milvus = pytest.importorskip("altk_evolve.config.milvus")
    postgres = pytest.importorskip("altk_evolve.config.postgres")
    guidelines = pytest.importorskip("altk_evolve.config.guidelines")

    dockerfile = (REPO_ROOT / "Dockerfile.ubi").read_text()
    stages = dockerfile.split("FROM ${BASE_IMAGE}")
    source = stages[1 if stage == "builder" else 2].replace(chr(92) + "\n", " ")
    env = {}
    for line in source.splitlines():
        if line.startswith("ENV "):
            for assignment in shlex.split(line[4:]):
                if "=" in assignment:
                    key, value = assignment.split("=", 1)
                    env[key] = value
    for key in (
        "EVOLVE_EMBEDDING_MODEL",
        "EVOLVE_PG_EMBEDDING_MODEL",
        "EVOLVE_CONSISTENCY_EMBEDDING_MODEL_SMALL",
        "EVOLVE_CONSISTENCY_EMBEDDING_MODEL_LARGE",
    ):
        monkeypatch.delenv(key, raising=False)
        if key in env:
            monkeypatch.setenv(key, env[key])

    assert env["EVOLVE_EMBEDDING_PROVIDER"] == "fastembed"
    expected = "BAAI/bge-small-en-v1.5"
    milvus_settings = milvus.MilvusOtherSettings(_env_file=None)
    assert milvus_settings.embedding_model == expected
    assert milvus.MilvusDBSettings(_env_file=None).embedding_model == expected
    assert postgres.PostgresDBSettings(_env_file=None).embedding_model == expected
    settings = guidelines.GuidelinesSettings(_env_file=None)
    assert settings.consistency_embedding_model_small == expected
    assert settings.consistency_embedding_model_large == expected
