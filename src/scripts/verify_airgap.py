"""Smoke-test bundled assets. Run with Docker's --network=none."""

import os
from pathlib import Path


def main() -> None:
    from scripts.preload_models import preload_evolve, preload_fastembed, preload_tiktoken

    preload_evolve()
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

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<h1>Offline browser works</h1>")
        assert page.text_content("h1") == "Offline browser works"
        browser.close()
    print("Playwright launched offline")
    print("Airgap asset verification passed")


if __name__ == "__main__":
    main()
