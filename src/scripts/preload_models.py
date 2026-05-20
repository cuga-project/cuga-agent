"""Preload all ML models needed for airgapped operation.

Run once during Docker image build (or on a connected machine before airgap):
    python src/scripts/preload_models.py

Models are cached to their default locations and will be available offline.
Set HF_HUB_OFFLINE=1 at runtime to prevent any accidental network access.
"""

import os
import sys


def preload_fastembed() -> None:
    print("→ Preloading fastembed models...")
    try:
        from fastembed import TextEmbedding

        cache_dir = os.environ.get("FASTEMBED_CACHE_PATH")
        models = [
            "BAAI/bge-small-en-v1.5",  # default embedding model
        ]
        for model_name in models:
            print(f"  Downloading {model_name}...")
            model = TextEmbedding(model_name, cache_dir=cache_dir)
            # Run one inference pass to ensure ONNX runtime initializes
            list(model.embed(["warmup"]))
            print(f"  ✓ {model_name}")
    except ImportError:
        print("  ✗ fastembed not installed, skipping")


def preload_docling() -> None:
    from pathlib import Path

    print("→ Preloading docling models...")
    try:
        from docling.utils.model_downloader import download_models

        output_dir = Path(os.environ.get("DOCLING_ARTIFACTS_PATH", Path.home() / ".cache" / "docling"))
        # Skip heavy/optional models by default (saves ~700MB). Override with env flags.
        with_code_formula = os.environ.get("DOCLING_WITH_CODE_FORMULA", "0") == "1"
        with_picture_classifier = os.environ.get("DOCLING_WITH_PICTURE_CLASSIFIER", "0") == "1"
        print(
            f"  Downloading models to {output_dir} (code_formula={with_code_formula}, picture_classifier={with_picture_classifier})..."
        )
        download_models(
            output_dir=output_dir,
            with_code_formula=with_code_formula,
            with_picture_classifier=with_picture_classifier,
        )
        print("  ✓ docling models ready")
    except ImportError:
        print("  ✗ docling not installed, skipping")
    except Exception as e:
        print(f"  ✗ docling download failed: {e}")


def preload_fastembed_tokenizer() -> None:
    """Pre-warm the fastembed Rust tokenizer used by docling HybridChunker."""
    print("→ Preloading fastembed tokenizer (for docling HybridChunker)...")
    try:
        from fastembed.text.onnx_embedding import OnnxTextEmbedding  # type: ignore[import]

        cache_dir = os.environ.get("FASTEMBED_CACHE_PATH")
        model = OnnxTextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache_dir)
        _ = model.tokenizer
        print("  ✓ tokenizer ready")
    except Exception as e:
        print(f"  ! tokenizer pre-warm skipped: {e}")


if __name__ == "__main__":
    print("Preloading models for airgapped operation...\n")
    preload_fastembed()
    preload_fastembed_tokenizer()
    preload_docling()
    print("\nDone. Set HF_HUB_OFFLINE=1 at runtime to enforce airgap.")
    sys.exit(0)
