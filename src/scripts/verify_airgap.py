"""Smoke-test bundled assets. Run with Docker's --network=none."""

import os
from importlib.util import find_spec
from importlib.metadata import distributions
from pathlib import Path


def main() -> None:
    if find_spec("stanza") is not None:
        raise RuntimeError("Stanza must not be installed in the image; bundled READI uses spaCy")

    forbidden = {"altk-evolve", "stanza", "udtools", "udapi"}
    installed = {dist.metadata["Name"].lower().replace("_", "-") for dist in distributions()}
    unwanted = sorted(name for name in installed if name in forbidden or name.startswith("arize-phoenix"))
    if unwanted:
        raise RuntimeError(f"Excluded image packages are installed: {unwanted}")

    from scripts.verify_image_licenses import verify_image_licenses

    verify_image_licenses(Path(__file__).resolve().parents[2])

    from scripts.preload_models import preload_evolve, preload_fastembed, preload_tiktoken

    from scripts.verify_evolve_hooks import verify_evolve_hooks

    verify_evolve_hooks()
    preload_evolve()
    from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_metric_instance

    get_metric_instance("sbert_small").sentence_transformer_model.encode(["airgap warmup"])
    print("Evolve consistency models loaded offline")
    preload_fastembed()
    preload_tiktoken()

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(artifacts_path=Path(os.environ["DOCLING_ARTIFACTS_PATH"]))
    options.ocr_options = EasyOcrOptions(use_gpu=False, download_enabled=False)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    converter.initialize_pipeline(InputFormat.PDF)
    print("Docling PDF layout, table and OCR models loaded offline")

    print("Airgap asset verification passed")


if __name__ == "__main__":
    main()
